"""Mistral provider tests using a mocked HTTP transport (no network, no real key)."""

import asyncio
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.routes import get_llm_provider
from app.llm.base import LLMError, RateLimitError
from app.llm.provider import MistralProvider, parse_json_answer

NOTES = ["Do not charge the battery between 2 PM and 4 PM.", "The cafeteria menu changes tomorrow."]
ANSWER = {"interpretations": [
    {"note_index": 0, "applies": True, "directive_type": "no_charge_window", "hours": [14, 15],
     "factor": None, "reserve_value": None, "reserve_unit": None, "max_grid_kwh": None, "explanation": "x"},
    {"note_index": 1, "applies": False, "directive_type": "no_op", "hours": None, "explanation": "x"},
]}


def reply(content, status=200):
    body = {"choices": [{"index": 0, "message": {"role": "assistant", "content": content}}]}
    return httpx.Response(status, json=body)


@pytest.fixture(autouse=True)
def key(monkeypatch):
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key-123")
    monkeypatch.setenv("MISTRAL_MIN_INTERVAL_SECONDS", "0")


def provider(handler, model="mistral-small-latest"):
    return MistralProvider(model, transport=httpx.MockTransport(handler))


def call(p, notes=NOTES, feedback=None):
    return asyncio.run(p.interpret_notes(notes, 200.0, feedback))


def test_success_and_request_shape():
    seen = {}

    def handler(req: httpx.Request):
        seen["url"] = str(req.url)
        seen["auth"] = req.headers["authorization"]
        seen["body"] = json.loads(req.content)
        return reply(json.dumps(ANSWER))

    out = call(provider(handler))
    assert seen["url"] == "https://api.mistral.ai/v1/chat/completions"
    assert seen["auth"] == "Bearer test-key-123"
    b = seen["body"]
    assert b["model"] == "mistral-small-latest" and b["temperature"] == 0.0
    assert b["response_format"] == {"type": "json_object"}
    assert "2 PM and 4 PM" in b["messages"][1]["content"]
    assert len(out) == 2 and out[0]["hours"] == [14, 15]


def test_feedback_is_sent():
    seen = {}

    def handler(req):
        seen["user"] = json.loads(req.content)["messages"][1]["content"]
        return reply(json.dumps(ANSWER))

    call(provider(handler), feedback="- Note 0: hours wrong")
    assert "hours wrong" in seen["user"]


def test_rate_limit_raises_llm_error_without_leaking_key():
    p = provider(lambda r: httpx.Response(429, json={"message": "Rate limit exceeded"}))
    with pytest.raises(LLMError) as e:
        call(p)
    assert "429" in str(e.value) and "test-key-123" not in str(e.value)
    assert isinstance(e.value, RateLimitError)


def test_network_error_is_llm_error():
    def handler(req):
        raise httpx.ConnectTimeout("boom")

    with pytest.raises(LLMError):
        call(provider(handler))


def test_json_mode_rejected_falls_back_to_plain():
    calls = []

    def handler(req):
        body = json.loads(req.content)
        calls.append("response_format" in body)
        if "response_format" in body:
            return httpx.Response(400, json={"message": "response_format not supported for this model"})
        return reply(json.dumps(ANSWER))

    out = call(provider(handler, "open-mistral-nemo"))
    assert calls == [True, False] and len(out) == 2


def test_content_chunks_and_code_fences():
    fenced = "```json\n" + json.dumps(ANSWER) + "\n```"
    assert len(call(provider(lambda r: reply(fenced)))) == 2
    chunks = [{"type": "text", "text": json.dumps(ANSWER)}]
    assert len(call(provider(lambda r: reply(chunks)))) == 2


def test_bad_output_shapes():
    for bad in ("not json", "", '{"foo": 1}', "42"):
        with pytest.raises(LLMError):
            call(provider(lambda r, b=bad: reply(b)))
    with pytest.raises(LLMError):
        call(provider(lambda r: httpx.Response(200, json={"unexpected": True})))


def test_parse_json_answer_variants():
    assert parse_json_answer(json.dumps(ANSWER["interpretations"])) == ANSWER["interpretations"]
    assert parse_json_answer(json.dumps({"directives": [1, 2]})) == [1, 2]


def test_no_key_raises(monkeypatch):
    monkeypatch.setenv("MISTRAL_API_KEY", "")
    with pytest.raises(LLMError):
        MistralProvider("mistral-small-latest")


def test_full_pipeline_through_mistral_provider():
    """HTTP -> FastAPI -> Mistral (mocked) -> guardrails -> LP -> replay, on public SAMPLE-06."""
    from app.main import app

    data = json.load(open(Path(__file__).parent.parent / "docs" / "BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json"))
    case = data["cases"][5]  # SAMPLE-06: solar_reduction + no_charge_window + distractor
    canned = {"interpretations": [
        {"note_index": 0, "applies": True, "directive_type": "solar_reduction", "hours": [10, 11], "factor": 0.5, "explanation": "x"},
        {"note_index": 1, "applies": True, "directive_type": "no_charge_window", "hours": [14, 15], "explanation": "x"},
        {"note_index": 2, "applies": False, "directive_type": "no_op", "explanation": "x"},
    ]}
    p = provider(lambda r: reply(json.dumps(canned)))
    app.dependency_overrides[get_llm_provider] = lambda: p
    try:
        with TestClient(app) as c:
            r = c.post("/optimize-energy", json=case["input"])
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 200
    j = r.json()
    assert j["total_cost_bdt"] == pytest.approx(case["expected_output"]["total_cost_bdt"], abs=0.05)
    assert [d["structured_adjustment"] for d in j["directive_interpretation"]] == \
        [d["structured_adjustment"] for d in case["expected_output"]["directive_interpretation"]]
    assert "rule-based" not in j["plan_summary"]


def test_retry_after_header_is_read():
    p = provider(lambda r: httpx.Response(429, headers={"retry-after": "2"}, json={"message": "slow down"}))
    with pytest.raises(RateLimitError) as e:
        call(p)
    assert e.value.retry_after == 2.0


def test_calls_are_spaced_by_min_interval(monkeypatch):
    import time
    monkeypatch.setenv("MISTRAL_MIN_INTERVAL_SECONDS", "0.3")
    p = provider(lambda r: reply(json.dumps(ANSWER)))

    async def two():
        t0 = time.monotonic()
        await p.interpret_notes(NOTES, 200.0)
        await p.interpret_notes(NOTES, 200.0)
        return time.monotonic() - t0

    assert asyncio.run(two()) >= 0.29
