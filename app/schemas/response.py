"""Response schema for POST /optimize-energy."""

from typing import List, Optional, Any
from pydantic import BaseModel

from app.schemas.directives import BatteryAction


class DirectiveInterpretationResponse(BaseModel):
    """One directive interpretation entry in the response."""
    note_index: int
    applies: bool
    directive_type: str
    structured_adjustment: Optional[Any] = None
    explanation: str


class HourlyPlanEntry(BaseModel):
    """One hour of the 24-hour plan in the response."""
    hour: int
    grid_kwh: float
    solar_used_kwh: float
    battery_action: str
    battery_kwh: float
    battery_energy_after_kwh: float


class OptimizeResponse(BaseModel):
    """Full response body for POST /optimize-energy."""
    scenario_id: str
    directive_interpretation: List[DirectiveInterpretationResponse]
    hourly_plan: List[HourlyPlanEntry]
    total_grid_kwh: float
    total_cost_bdt: float
    peak_grid_kwh: float
    plan_summary: str


class HealthResponse(BaseModel):
    """Response for GET /health."""
    status: str = "ok"


class ErrorResponse(BaseModel):
    """Error response body."""
    error: str
    detail: Optional[str] = None
