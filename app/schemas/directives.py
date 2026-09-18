"""Directive type and battery action enums, and structured adjustment models."""

from enum import Enum
from typing import Optional, List

from pydantic import BaseModel, field_validator


class DirectiveType(str, Enum):
    """The six supported directive types."""
    SOLAR_REDUCTION = "solar_reduction"
    MINIMUM_BATTERY_RESERVE = "minimum_battery_reserve"
    NO_CHARGE_WINDOW = "no_charge_window"
    NO_DISCHARGE_WINDOW = "no_discharge_window"
    MAX_GRID_WINDOW = "max_grid_window"
    NO_OP = "no_op"


class BatteryAction(str, Enum):
    """Allowed battery actions."""
    CHARGE = "charge"
    DISCHARGE = "discharge"
    IDLE = "idle"


class SolarReductionAdjustment(BaseModel):
    """Structured adjustment for solar_reduction directive."""
    hours: List[int]
    factor: float


class MinimumBatteryReserveAdjustment(BaseModel):
    """Structured adjustment for minimum_battery_reserve directive."""
    hours: List[int]
    minimum_energy_kwh: float


class NoChargeWindowAdjustment(BaseModel):
    """Structured adjustment for no_charge_window directive."""
    hours: List[int]


class NoDischargeWindowAdjustment(BaseModel):
    """Structured adjustment for no_discharge_window directive."""
    hours: List[int]


class MaxGridWindowAdjustment(BaseModel):
    """Structured adjustment for max_grid_window directive."""
    hours: List[int]
    max_grid_kwh: float


# Type alias for all possible structured adjustments
StructuredAdjustment = (
    SolarReductionAdjustment
    | MinimumBatteryReserveAdjustment
    | NoChargeWindowAdjustment
    | NoDischargeWindowAdjustment
    | MaxGridWindowAdjustment
    | None
)


class ParsedDirective(BaseModel):
    """Internal representation of a parsed directive from LLM output."""
    note_index: int
    applies: bool
    directive_type: DirectiveType
    structured_adjustment: Optional[dict] = None
    explanation: str = ""
