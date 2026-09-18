"""Request schema for POST /optimize-energy."""

from typing import List
from pydantic import BaseModel, field_validator, model_validator
import math


class HourEntry(BaseModel):
    """One hour of the 24-hour scenario."""
    hour: int
    demand_kwh: float
    solar_kwh: float
    tariff_bdt_per_kwh: float

    @field_validator("hour")
    @classmethod
    def hour_in_range(cls, v: int) -> int:
        if not (0 <= v <= 23):
            raise ValueError(f"hour must be 0-23, got {v}")
        return v

    @field_validator("demand_kwh", "solar_kwh", "tariff_bdt_per_kwh")
    @classmethod
    def non_negative_finite(cls, v: float, info) -> float:
        if not math.isfinite(v):
            raise ValueError(f"{info.field_name} must be finite, got {v}")
        if v < 0:
            raise ValueError(f"{info.field_name} must be non-negative, got {v}")
        return v


class BatteryConfig(BaseModel):
    """Battery specification for the scenario."""
    capacity_kwh: float
    initial_energy_kwh: float
    minimum_energy_kwh: float
    max_charge_kwh_per_hour: float
    max_discharge_kwh_per_hour: float

    @field_validator(
        "capacity_kwh",
        "initial_energy_kwh",
        "minimum_energy_kwh",
        "max_charge_kwh_per_hour",
        "max_discharge_kwh_per_hour",
    )
    @classmethod
    def non_negative_finite(cls, v: float, info) -> float:
        if not math.isfinite(v):
            raise ValueError(f"{info.field_name} must be finite, got {v}")
        if v < 0:
            raise ValueError(f"{info.field_name} must be non-negative, got {v}")
        return v

    @model_validator(mode="after")
    def battery_consistent(self):
        if self.initial_energy_kwh > self.capacity_kwh:
            raise ValueError("initial_energy_kwh cannot exceed capacity_kwh")
        if self.minimum_energy_kwh > self.capacity_kwh:
            raise ValueError("minimum_energy_kwh cannot exceed capacity_kwh")
        if self.initial_energy_kwh < self.minimum_energy_kwh:
            raise ValueError("initial_energy_kwh cannot be below minimum_energy_kwh")
        return self


class OptimizeRequest(BaseModel):
    """Full request body for POST /optimize-energy."""
    scenario_id: str
    operator_notes: List[str]
    hours: List[HourEntry]
    battery: BatteryConfig

    @field_validator("operator_notes")
    @classmethod
    def validate_notes(cls, v: List[str]) -> List[str]:
        if not (1 <= len(v) <= 3):
            raise ValueError(f"operator_notes must contain 1-3 entries, got {len(v)}")
        for i, note in enumerate(v):
            if not note or not note.strip():
                raise ValueError(f"operator_notes[{i}] must be a non-empty string")
        return v

    @field_validator("hours")
    @classmethod
    def validate_hours(cls, v: List[HourEntry]) -> List[HourEntry]:
        if len(v) != 24:
            raise ValueError(f"hours must contain exactly 24 entries, got {len(v)}")
        seen = set()
        for entry in v:
            if entry.hour in seen:
                raise ValueError(f"Duplicate hour: {entry.hour}")
            seen.add(entry.hour)
        expected = set(range(24))
        if seen != expected:
            missing = expected - seen
            raise ValueError(f"Missing hours: {sorted(missing)}")
        # Sort by hour
        return sorted(v, key=lambda h: h.hour)
