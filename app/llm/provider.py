"""Mistral provider for operator-note interpretation (REST, JSON mode)."""

import asyncio
import json
import logging
import re
import time
from functools import lru_cache
from typing import List, Optional

import httpx

from app.config import get_settings
from app.llm.base import LLMError, LLMProvider, RateLimitError
from app.llm.prompts import SYSTEM_PROMPT, build_user_prompt

logger = logging.getLogger(__name__)

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.I)
_LIST_KEYS = ("interpretations", "directives", "results", "data", "notes")


_gate = {"loop": None, "lock": None, "last": 0.0}


async def _throttle(min_interval: float) -> None:
    """Space out Mistral calls (shared by all models) so bursts do not trigger 429s."""
    if min_interval <= 0:
        return
    loop = asyncio.get_running_loop()
    if _gate["loop"] is not loop:
        _gate.update(loop=loop, lock=asyncio.Lock(), last=0.0)
    async with _gate["lock"]:
        wait = _gate["last"] + min_interval - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        _gate["last"] = time.monotonic()


def _content_text(content) -> str:
    """Message content is a string, or (newer API) a list of chunks with a 'text' field."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(c.get("text", "") for c in content if isinstance(c, dict))
    return ""


def parse_json_answer(text: str) -> List[dict]:
    """Turn the model text into the list of raw entries. Accepts an object wrapper or a bare list."""
    text = _FENCE.sub("", (text or "").strip()).strip()
    if not text:
        raise LLMError("Empty response from Mistral")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as e:
        raise LLMError(f"Mistral returned invalid JSON: {e}")
    if isinstance(parsed, dict):
        for key in _LIST_KEYS:
            if isinstance(parsed.get(key), list):
                return parsed[key]
        if "directive_type" in parsed:  # single bare entry
            return [parsed]
        raise LLMError(f"Expected interpretations array, got object with keys {list(parsed.keys())}")
    if not isinstance(parsed, list):
        raise LLMError(f"Expected JSON array or object, got {type(parsed).__name__}")
    return parsed


class MistralProvider(LLMProvider):
    """Concrete provider using the Mistral chat-completions API."""

    def __init__(self, model: Optional[str] = None, transport: Optional[httpx.AsyncBaseTransport] = None):
        s = get_settings()
        if not s.mistral_api_key:
            raise LLMError("MISTRAL_API_KEY environment variable is not set")
        self.model = model or s.llm_model
        self._min_interval = s.mistral_min_interval_seconds
        self._client = httpx.AsyncClient(
            base_url=s.mistral_base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {s.mistral_api_key}", "Content-Type": "application/json"},
            timeout=httpx.Timeout(s.llm_timeout_seconds),
            transport=transport,
        )
        self._json_mode = True

    async def interpret_notes(
        self,
        operator_notes: List[str],
        battery_capacity_kwh: float,
        feedback: Optional[str] = None,
    ) -> List[dict]:
        """One Mistral call. Retries, timeouts and model failover live in NoteInterpreter."""
        payload = {
            "model": self.model,
            "temperature": 0.0,
            "max_tokens": 1500,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_user_prompt(operator_notes, battery_capacity_kwh, feedback)},
            ],
        }
        if self._json_mode:
            payload["response_format"] = {"type": "json_object"}

        await _throttle(self._min_interval)
        try:
            resp = await self._client.post("/chat/completions", json=payload)
        except httpx.HTTPError as e:
            raise LLMError(f"Mistral request failed: {type(e).__name__}")

        if resp.status_code == 400 and self._json_mode and "response_format" in resp.text.lower():
            # This model rejects JSON mode: keep going without it (the prompt still demands JSON)
            logger.warning(f"{self.model} rejected response_format; using plain mode")
            self._json_mode = False
            return await self.interpret_notes(operator_notes, battery_capacity_kwh, feedback)
        if resp.status_code == 429:
            try:
                retry_after = float(resp.headers.get("retry-after", ""))
            except ValueError:
                retry_after = None
            raise RateLimitError("Mistral HTTP 429: rate limited", retry_after=retry_after)
        if resp.status_code >= 400:
            # Body is the API error message; the key is never echoed by the API or logged here
            raise LLMError(f"Mistral HTTP {resp.status_code}: {resp.text[:200]}")

        try:
            content = resp.json()["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError):
            raise LLMError("Unexpected Mistral response shape")
        return parse_json_answer(_content_text(content))


@lru_cache(maxsize=8)
def get_mistral_provider(model: Optional[str] = None) -> MistralProvider:
    """One shared HTTP client per model (a new client per request would waste connections)."""
    return MistralProvider(model)
