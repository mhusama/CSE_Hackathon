"""Shared test fixtures for GridWise test suite."""

import json
from pathlib import Path
from typing import List, Optional
import pytest

from app.schemas.request import OptimizeRequest, HourEntry, BatteryConfig
from app.schemas.directives import ParsedDirective, DirectiveType
from app.llm.base import LLMProvider


class MockLLMProvider(LLMProvider):
    """Configurable mock LLM provider for testing without API calls."""

    def __init__(self, canned_responses: Optional[List[dict]] = None):
        self._canned_responses = canned_responses
        self.call_history = []

    def set_responses(self, responses: List[dict]):
        self._canned_responses = responses

    async def interpret_notes(
        self,
        operator_notes: List[str],
        battery_capacity_kwh: float,
        feedback: Optional[str] = None,
    ) -> List[dict]:
        self.call_history.append((operator_notes, battery_capacity_kwh))
        if self._canned_responses is not None:
            return self._canned_responses

        # Default fallback: return no_op for each note
        return [
            {
                "note_index": i,
                "applies": False,
                "directive_type": "no_op",
                "structured_adjustment": None,
                "explanation": "Default mock no-op",
            }
            for i in range(len(operator_notes))
        ]


@pytest.fixture
def mock_llm() -> MockLLMProvider:
    return MockLLMProvider()


@pytest.fixture
def sample_battery() -> BatteryConfig:
    return BatteryConfig(
        capacity_kwh=500.0,
        initial_energy_kwh=200.0,
        minimum_energy_kwh=50.0,
        max_charge_kwh_per_hour=100.0,
        max_discharge_kwh_per_hour=100.0,
    )


@pytest.fixture
def sample_hours() -> List[HourEntry]:
    # 24 hours of typical synthetic demand, solar, and tariff
    entries = []
    for h in range(24):
        # Solar peak during noon (hours 10-15)
        solar = 50.0 * max(0.0, 1.0 - abs(h - 12) / 4.0) if 8 <= h <= 16 else 0.0
        # Demand higher in daytime/evening
        demand = 100.0 + (30.0 if 9 <= h <= 21 else 0.0)
        # Tariff higher during evening peak
        tariff = 10.0 if 17 <= h <= 22 else 6.0
        entries.append(
            HourEntry(
                hour=h,
                demand_kwh=round(demand, 2),
                solar_kwh=round(solar, 2),
                tariff_bdt_per_kwh=tariff,
            )
        )
    return entries


@pytest.fixture
def sample_request(sample_battery, sample_hours) -> OptimizeRequest:
    return OptimizeRequest(
        scenario_id="TEST-SCENARIO-01",
        operator_notes=[
            "Solar output will drop to about 20% from 1 PM to 3 PM.",
            "Do not charge the battery between 2 PM and 4 PM.",
            "The cafeteria menu changes tomorrow.",
        ],
        hours=sample_hours,
        battery=sample_battery,
    )


@pytest.fixture(scope="session")
def public_sample_cases() -> dict:
    json_path = Path(__file__).parent.parent / "docs" / "BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json"
    with open(json_path, "r", encoding="utf-8") as f:
        return json.load(f)
