"""Integration tests for FastAPI endpoints."""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.api.routes import get_llm_provider
from app.schemas.request import OptimizeRequest
from tests.conftest import MockLLMProvider


@pytest.fixture
def client(mock_llm: MockLLMProvider):
    # Override LLM provider dependency in FastAPI
    app.dependency_overrides[get_llm_provider] = lambda: mock_llm
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_health_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_optimize_energy_success(client, sample_request: OptimizeRequest):
    payload = sample_request.model_dump()
    response = client.post("/optimize-energy", json=payload)
    assert response.status_code == 200
    data = response.json()

    assert data["scenario_id"] == sample_request.scenario_id
    assert len(data["directive_interpretation"]) == len(sample_request.operator_notes)
    assert len(data["hourly_plan"]) == 24
    assert data["total_grid_kwh"] >= 0
    assert data["total_cost_bdt"] >= 0
    assert data["peak_grid_kwh"] >= 0
    assert isinstance(data["plan_summary"], str)


def test_optimize_energy_invalid_hour_count(client, sample_request: OptimizeRequest):
    payload = sample_request.model_dump()
    # Remove one hour to make it 23 hours instead of 24
    payload["hours"] = payload["hours"][:23]

    response = client.post("/optimize-energy", json=payload)
    assert response.status_code == 400
    data = response.json()
    assert "error" in data


def test_optimize_energy_empty_notes(client, sample_request: OptimizeRequest):
    payload = sample_request.model_dump()
    payload["operator_notes"] = []

    response = client.post("/optimize-energy", json=payload)
    assert response.status_code == 400


# ---- new behaviour -------------------------------------------------------------

from tests.conftest import MockLLMProvider as _Mock  # noqa: E402


def test_infeasible_directive_is_relaxed_not_422(sample_request, mock_llm):
    """A grid cap that cannot be met is dropped, and the request still returns a valid 200 plan."""
    mock_llm.set_responses([
        {"note_index": 0, "applies": True, "directive_type": "max_grid_window",
         "structured_adjustment": {"hours": list(range(24)), "max_grid_kwh": 1}, "explanation": "x"},
        {"note_index": 1, "applies": False, "directive_type": "no_op", "structured_adjustment": None, "explanation": "x"},
        {"note_index": 2, "applies": False, "directive_type": "no_op", "structured_adjustment": None, "explanation": "x"},
    ])
    app.dependency_overrides[get_llm_provider] = lambda: mock_llm
    with TestClient(app) as c:
        r = c.post("/optimize-energy", json=sample_request.model_dump())
    app.dependency_overrides.clear()
    assert r.status_code == 200
    body = r.json()
    assert body["directive_interpretation"][0]["directive_type"] == "max_grid_window"  # interpretation reported as read
    assert "relaxed" in body["plan_summary"]
    assert len(body["hourly_plan"]) == 24


def test_bad_body_is_400_even_without_llm_key(monkeypatch, sample_request):
    monkeypatch.setenv("MISTRAL_API_KEY", "")
    app.dependency_overrides.clear()
    with TestClient(app) as c:
        r = c.post("/optimize-energy", json={"scenario_id": "x"})
    assert r.status_code == 400


def test_valid_request_without_llm_key_still_answers(monkeypatch, sample_request):
    monkeypatch.setenv("MISTRAL_API_KEY", "")
    app.dependency_overrides.clear()
    with TestClient(app) as c:
        r = c.post("/optimize-energy", json=sample_request.model_dump())
    assert r.status_code == 200
    body = r.json()
    types = [d["directive_type"] for d in body["directive_interpretation"]]
    assert types == ["solar_reduction", "no_charge_window", "no_op"]
    assert "rule-based" in body["plan_summary"]
