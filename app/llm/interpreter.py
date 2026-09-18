"""Note interpretation with retries, feedback, model failover and an emergency fallback.

Order of work for one request:
  cache -> primary model -> (retry with feedback) -> fallback model -> rule parser
The LLM is always tried first. Whatever comes out still passes through the
deterministic guardrails afterwards.
"""

import asyncio
import copy
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import List, Optional, Tuple

from app.config import get_settings
from app.guardrails.validator import find_problems
from app.llm.base import LLMProvider, RateLimitError
from app.llm.parser import parse_llm_output
from app.llm.rules import interpret_with_rules
from app.schemas.directives import ParsedDirective

logger = logging.getLogger(__name__)


@dataclass
class InterpretationResult:
    directives: List[ParsedDirective]
    source: str  # "llm:<model>", "cache", "rules-fallback"


class _LRU:
    def __init__(self, size: int):
        self._size = size
        self._d: "OrderedDict" = OrderedDict()

    def get(self, key):
        if key in self._d:
            self._d.move_to_end(key)
            return copy.deepcopy(self._d[key])
        return None

    def put(self, key, value):
        self._d[key] = copy.deepcopy(value)
        self._d.move_to_end(key)
        while len(self._d) > self._size:
            self._d.popitem(last=False)


_SHARED_CACHE = _LRU(get_settings().llm_cache_size)


class NoteInterpreter:
    def __init__(
        self,
        providers: List[Tuple[str, LLMProvider]],
        max_attempts: int = 2,
        per_call_timeout: float = 8.0,
        total_budget: float = 18.0,
        cache: Optional[_LRU] = None,
        backoff: float = 0.4,
        rate_limit_wait: float = 1.5,
    ):
        self._providers = providers
        self._max_attempts = max(1, max_attempts)
        self._per_call = per_call_timeout
        self._budget = total_budget
        self._cache = cache
        self._backoff = backoff
        self._rate_limit_wait = rate_limit_wait

    async def interpret(self, notes: List[str], capacity: float) -> InterpretationResult:
        key = (tuple(notes), round(capacity, 6))
        if self._cache is not None:
            hit = self._cache.get(key)
            if hit is not None:
                return InterpretationResult(hit, "cache")

        n = len(notes)
        deadline = time.monotonic() + self._budget
        best: Optional[Tuple[List[ParsedDirective], int, str]] = None  # (directives, #problems, model)

        for name, provider in self._providers:
            feedback: Optional[str] = None
            for attempt in range(self._max_attempts):
                remaining = deadline - time.monotonic()
                if remaining < 1.0:
                    break
                try:
                    call = (
                        provider.interpret_notes(notes, capacity)
                        if feedback is None
                        else provider.interpret_notes(notes, capacity, feedback=feedback)
                    )
                    raw = await asyncio.wait_for(call, timeout=min(self._per_call, remaining))
                except Exception as e:  # noqa: BLE001  (timeouts, quota, bad JSON ...)
                    logger.warning(f"LLM {name} attempt {attempt + 1}/{self._max_attempts} failed: {type(e).__name__}: {e}")
                    delay = self._backoff * (attempt + 1)
                    if isinstance(e, RateLimitError):  # 429: wait what the API asks, not a token 0.4 s
                        delay = max(delay, min(e.retry_after or self._rate_limit_wait, 5.0))
                    await asyncio.sleep(min(delay, max(0.0, deadline - time.monotonic() - 1.0)))
                    continue

                directives = parse_llm_output(raw, n, capacity)
                problems = find_problems(directives, notes, capacity)
                if isinstance(raw, list) and len(raw) != n:
                    for i in range(n):
                        problems.setdefault(i, f"you returned {len(raw)} entries for {n} notes; return exactly one per note")

                if best is None or len(problems) < best[1]:
                    best = (directives, len(problems), name)
                if not problems:
                    if self._cache is not None:
                        self._cache.put(key, directives)
                    return InterpretationResult(directives, f"llm:{name}")

                feedback = "\n".join(f"- Note {i} ({notes[i][:80]!r}): {msg}" for i, msg in sorted(problems.items()))
                logger.info(f"LLM {name} answer needs correction: {feedback}")

            if best is not None:
                # This model answered at least once. Accept the best answer; guardrails
                # will repair or neutralise whatever is still wrong. No second model needed.
                break

        if best is not None:
            return InterpretationResult(best[0], f"llm:{best[2]}")

        logger.error("All LLM attempts failed; using emergency rule-based parser")
        raw = interpret_with_rules(notes, capacity)
        return InterpretationResult(parse_llm_output(raw, n, capacity), "rules-fallback")


def build_interpreter(primary: Optional[LLMProvider]) -> NoteInterpreter:
    """Build the interpreter for a request from the configured provider."""
    from app.llm.provider import MistralProvider, get_mistral_provider  # local: keeps tests light

    s = get_settings()
    providers: List[Tuple[str, LLMProvider]] = []
    real = isinstance(primary, MistralProvider)
    if primary is not None:
        providers.append((getattr(primary, "model", "primary"), primary))
    if real and s.llm_fallback_model and s.llm_fallback_model != primary.model:
        try:
            providers.append((s.llm_fallback_model, get_mistral_provider(s.llm_fallback_model)))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Fallback model unavailable: {e}")
    return NoteInterpreter(
        providers,
        max_attempts=s.llm_max_retries,
        per_call_timeout=s.llm_timeout_seconds,
        total_budget=s.llm_total_budget_seconds,
        cache=_SHARED_CACHE if real else None,
    )
