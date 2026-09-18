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
