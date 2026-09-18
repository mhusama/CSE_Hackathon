"""Tests for retry-with-feedback, timeouts, failover, cache and the emergency fallback."""

import asyncio
import json
import time
from pathlib import Path
from typing import List, Optional

import pytest

from app.guardrails.validator import validate_directives
from app.llm.base import LLMError, LLMProvider, RateLimitError
from app.llm.interpreter import NoteInterpreter, _LRU
from app.llm.parser import parse_llm_output
from app.llm.rules import interpret_with_rules

NOTES = ["Do not charge the battery between 2 PM and 4 PM.", "The cafeteria menu changes tomorrow."]
NO_OP = {"note_index": 1, "applies": False, "directive_type": "no_op", "structured_adjustment": None, "explanation": "x"}


def entry(**kw):
    base = {"note_index": 0, "applies": True, "directive_type": "no_charge_window", "explanation": "x"}
    base.update(kw)
    return base


class Scripted(LLMProvider):
    """Returns queued answers (or raises queued exceptions), records feedback."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.feedbacks: List[Optional[str]] = []
        self.calls = 0

    async def interpret_notes(self, operator_notes, battery_capacity_kwh, feedback=None):
        self.calls += 1
        self.feedbacks.append(feedback)
        a = self.answers.pop(0)
        if isinstance(a, BaseException):
            raise a
        if callable(a):
            return await a()
        return a


def make(*providers, **kw):
    kw.setdefault("backoff", 0.0)
    return NoteInterpreter([(f"p{i}", p) for i, p in enumerate(providers)], **kw)


def run(coro):
    return asyncio.run(coro)


def test_good_first_answer_no_retry():
    p = Scripted([entry(hours=[14, 15]), NO_OP])
    r = run(make(p).interpret(NOTES, 200))
    assert p.calls == 1 and r.source == "llm:p0"
    assert r.directives[0].structured_adjustment == {"hours": [14, 15]}


def test_retry_with_feedback_on_wrong_hours():
    # first answer uses an inclusive end hour (14,15,16); the time window in the note says [14, 15]
    p = Scripted([entry(hours=[14, 15, 16]), NO_OP], [entry(hours=[14, 15]), NO_OP])
    r = run(make(p).interpret(NOTES, 200))
    assert p.calls == 2
    assert p.feedbacks[0] is None and "[14, 15]" in p.feedbacks[1]
    assert r.directives[0].structured_adjustment["hours"] == [14, 15]


def test_bad_factor_retried_not_silently_dropped():
    notes = ["Expect an 80% reduction in rooftop solar between 11 AM and 2 PM.", NOTES[1]]
    bad = {"note_index": 0, "applies": True, "directive_type": "solar_reduction",
           "hours": [11, 12, 13], "factor": 80, "explanation": "x"}
    good = dict(bad, factor=0.2)
    p = Scripted([bad, NO_OP], [good, NO_OP])
    r = run(make(p).interpret(notes, 200))
    assert p.calls == 2
    assert r.directives[0].structured_adjustment == {"hours": [11, 12, 13], "factor": 0.2}


def test_still_wrong_after_retry_keeps_best_and_guardrails_neutralise():
    bad = entry(directive_type="solar_reduction", hours=[13], factor=5)
    p = Scripted([bad, NO_OP], [bad, NO_OP])
    r = run(make(p).interpret(["Solar drops to 20% at 1 PM to 2 PM", NOTES[1]], 200))
    out = validate_directives(r.directives, 2, 200)
    assert out[0].applies is False  # guardrail fallback, not a crash


def test_hour_24_is_repaired_by_guardrails():
    p = Scripted([entry(hours=[22, 23, 24]), NO_OP])
    r = run(make(p).interpret(["No charging from 10 PM until midnight.", NOTES[1]], 200))
    out = validate_directives(r.directives, 2, 200)
    assert out[0].directive_type.value == "no_charge_window"
    assert out[0].structured_adjustment["hours"] == [22, 23]


def test_percent_reserve_converted_in_code():
    raw = [{"note_index": 0, "applies": True, "directive_type": "minimum_battery_reserve",
            "hours": [18, 19, 20], "reserve_value": 50, "reserve_unit": "percent_of_capacity", "explanation": "x"}]
    d = parse_llm_output(raw, 1, 200.0)[0]
    assert d.structured_adjustment == {"hours": [18, 19, 20], "minimum_energy_kwh": 100.0}


def test_flat_fields_do_not_leak_into_adjustment():
    raw = [{"note_index": 0, "applies": True, "directive_type": "no_charge_window",
            "hours": [1], "factor": None, "reserve_value": None, "max_grid_kwh": None, "explanation": "x"}]
    assert parse_llm_output(raw, 1, 100.0)[0].structured_adjustment == {"hours": [1]}


def test_failover_to_second_model():
    p1 = Scripted(LLMError("quota"), LLMError("quota"))
    p2 = Scripted([entry(hours=[14, 15]), NO_OP])
    r = run(make(p1, p2, max_attempts=2).interpret(NOTES, 200))
    assert r.source == "llm:p1" and p1.calls == 2 and p2.calls == 1


def test_all_llm_calls_fail_uses_rule_fallback_not_500():
    p = Scripted(LLMError("down"), LLMError("down"))
    r = run(make(p).interpret(NOTES, 200))
    assert r.source == "rules-fallback"
    assert r.directives[0].directive_type.value == "no_charge_window"
    assert r.directives[0].structured_adjustment["hours"] == [14, 15]
    assert r.directives[1].directive_type.value == "no_op"


def test_timeout_is_enforced():
    async def hang():
        await asyncio.sleep(30)

    p = Scripted(hang, hang)
    t0 = time.monotonic()
    r = run(make(p, per_call_timeout=0.2, total_budget=3.0).interpret(NOTES, 200))
    assert time.monotonic() - t0 < 2.5
    assert r.source == "rules-fallback"


def test_cache_hit_skips_llm():
    p = Scripted([entry(hours=[14, 15]), NO_OP])
    it = make(p, cache=_LRU(8))
    run(it.interpret(NOTES, 200))
    r = run(it.interpret(NOTES, 200))
    assert r.source == "cache" and p.calls == 1


def test_wrong_entry_count_triggers_retry():
    p = Scripted([entry(hours=[14, 15])], [entry(hours=[14, 15]), NO_OP])
    r = run(make(p).interpret(NOTES, 200))
    assert p.calls == 2 and len(r.directives) == 2


def test_rules_fallback_on_public_sample_notes():
    data = json.load(open(Path(__file__).parent.parent / "docs" / "BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json"))
    for case in data["cases"]:
        got = interpret_with_rules(case["input"]["operator_notes"], case["input"]["battery"]["capacity_kwh"])
        for g, want in zip(got, case["expected_output"]["directive_interpretation"]):
            assert g["directive_type"] == want["directive_type"], case["id"]
            assert g["structured_adjustment"] == want["structured_adjustment"], case["id"]


def test_rules_fallback_on_paraphrase_set():
    cases = json.load(open(Path(__file__).parent / "data" / "paraphrases.json"))["cases"]
    for c in cases:
        if not c["rules"]:
            continue
        r = interpret_with_rules([c["note"]], c["capacity"])[0]
        adj = r["structured_adjustment"] or {}
        val = adj.get("factor", adj.get("minimum_energy_kwh", adj.get("max_grid_kwh")))
        assert r["directive_type"] == c["type"], c["note"]
        assert adj.get("hours") == c.get("hours"), c["note"]
        if c.get("value") is not None:
            assert abs(val - c["value"]) < 0.011, c["note"]


def test_429_waits_before_retrying_same_model():
    p = Scripted(RateLimitError("429", retry_after=0.4), [entry(hours=[14, 15]), NO_OP])
    t0 = time.monotonic()
    r = run(make(p).interpret(NOTES, 200))
    assert time.monotonic() - t0 >= 0.39
    assert r.source == "llm:p0" and p.calls == 2


def test_inclusive_end_hour_is_repaired_when_retry_does_not_fix_it():
    notes = ["Cloud cover will leave about half of the forecast solar output from 10 AM until noon.", NOTES[1]]
    bad = {"note_index": 0, "applies": True, "directive_type": "solar_reduction",
           "hours": [10, 11, 12], "factor": 0.5, "explanation": "x"}
    p = Scripted([bad, NO_OP], [bad, NO_OP])
    r = run(make(p).interpret(notes, 200))
    assert p.calls == 2  # it did ask again first
    assert r.directives[0].structured_adjustment["hours"] == [10, 11]


def test_throttle_wait_does_not_count_against_call_timeout():
    """Five parallel requests, 0.25 s apart, 0.4 s per-call limit: all must still succeed."""
    class Quick(LLMProvider):
        async def interpret_notes(self, operator_notes, battery_capacity_kwh, feedback=None):
            await asyncio.sleep(0.05)
            return [entry(hours=[14, 15]), NO_OP]

    it = NoteInterpreter([("q", Quick())], per_call_timeout=0.4, total_budget=10.0, min_interval=0.25, backoff=0.0)

    async def go():
        return await asyncio.gather(*[it.interpret([f"Do not charge from 2 PM to 4 PM. {i}", NOTES[1]], 200) for i in range(5)])

    res = asyncio.run(go())
    assert all(r.source == "llm:q" for r in res)
