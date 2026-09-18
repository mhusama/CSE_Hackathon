"""Unit tests for the deterministic post-solve replay validator."""

import pytest
from app.schemas.response import HourlyPlanEntry
from app.schemas.directives import ParsedDirective, DirectiveType
from app.validation.replay import validate_schedule, ValidationError


def test_valid_schedule_replay(sample_request):
    # Construct a trivial valid schedule where grid satisfies demand directly and battery is idle
    plan = [
        HourlyPlanEntry(
            hour=h.hour,
            grid_kwh=h.demand_kwh,
            solar_used_kwh=0.0,
            battery_action="idle",
            battery_kwh=0.0,
            battery_energy_after_kwh=sample_request.battery.initial_energy_kwh,
        )
        for h in sample_request.hours
    ]
    directives = []
    total_grid, total_cost, peak_grid = validate_schedule(plan, sample_request, directives)
    assert total_grid > 0
    assert total_cost > 0
    assert peak_grid > 0


def test_energy_balance_violation_raises(sample_request):
    plan = [
        HourlyPlanEntry(
            hour=h.hour,
            grid_kwh=h.demand_kwh - 10.0,  # 10 kWh short of demand
            solar_used_kwh=0.0,
            battery_action="idle",
            battery_kwh=0.0,
            battery_energy_after_kwh=sample_request.battery.initial_energy_kwh,
        )
        for h in sample_request.hours
    ]
    directives = []
    with pytest.raises(ValidationError, match="energy balance violated"):
        validate_schedule(plan, sample_request, directives)


def test_battery_neutrality_violation_raises(sample_request):
    plan = [
        HourlyPlanEntry(
            hour=h.hour,
            grid_kwh=h.demand_kwh,
            solar_used_kwh=0.0,
            battery_action="idle",
            battery_kwh=0.0,
            # End with different battery state
            battery_energy_after_kwh=sample_request.battery.initial_energy_kwh + (50.0 if h.hour == 23 else 0.0),
        )
        for h in sample_request.hours
    ]
    directives = []
    with pytest.raises(ValidationError, match="End-of-day"):
        validate_schedule(plan, sample_request, directives)


def test_solar_used_exceeding_effective_solar_raises(sample_request):
    # Apply solar reduction factor 0.2 to hour 12
    directives = [
        ParsedDirective(
            note_index=0,
            applies=True,
            directive_type=DirectiveType.SOLAR_REDUCTION,
            structured_adjustment={"hours": [12], "factor": 0.2},
            explanation="Cut solar",
        )
    ]
    h12 = next(h for h in sample_request.hours if h.hour == 12)
    effective_solar_12 = h12.solar_kwh * 0.2

    plan = []
    for h in sample_request.hours:
        if h.hour == 12:
            # Try to use raw solar instead of reduced solar
            plan.append(
                HourlyPlanEntry(
                    hour=12,
                    grid_kwh=max(0.0, h.demand_kwh - h.solar_kwh),
                    solar_used_kwh=h.solar_kwh,  # Exceeds effective solar!
                    battery_action="idle",
                    battery_kwh=0.0,
                    battery_energy_after_kwh=sample_request.battery.initial_energy_kwh,
                )
            )
        else:
            plan.append(
                HourlyPlanEntry(
                    hour=h.hour,
                    grid_kwh=h.demand_kwh,
                    solar_used_kwh=0.0,
                    battery_action="idle",
                    battery_kwh=0.0,
                    battery_energy_after_kwh=sample_request.battery.initial_energy_kwh,
                )
            )

    if h12.solar_kwh > 0:
        with pytest.raises(ValidationError, match="exceeds effective solar"):
            validate_schedule(plan, sample_request, directives)
