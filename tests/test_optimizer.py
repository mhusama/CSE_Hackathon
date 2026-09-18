"""Unit tests for the OR-Tools LP optimizer."""

import pytest
from app.schemas.directives import DirectiveType, ParsedDirective
from app.optimization.solver import solve_schedule, OptimizationError
from app.validation.replay import validate_schedule


def test_optimizer_baseline(sample_request):
    """Test standard solve with no directives."""
    directives = []
    plan = solve_schedule(sample_request, directives)
    assert len(plan) == 24

    # Validate that schedule passes replay validator
    total_grid, total_cost, peak_grid = validate_schedule(plan, sample_request, directives)
    assert total_grid >= 0
    assert total_cost >= 0
    assert peak_grid >= 0


def test_optimizer_solar_reduction(sample_request):
    """Test solver respects solar reduction."""
    directives = [
        ParsedDirective(
            note_index=0,
            applies=True,
            directive_type=DirectiveType.SOLAR_REDUCTION,
            structured_adjustment={"hours": [11, 12, 13], "factor": 0.0},  # Completely cut solar
            explanation="Cut solar to 0",
        )
    ]
    plan = solve_schedule(sample_request, directives)
    # Check that in hours 11, 12, 13, solar_used_kwh is 0
    for entry in plan:
        if entry.hour in [11, 12, 13]:
            assert entry.solar_used_kwh == 0.0

    # Ensure valid according to replay
    validate_schedule(plan, sample_request, directives)


def test_optimizer_no_charge_window(sample_request):
    """Test solver respects no-charge window."""
    directives = [
        ParsedDirective(
            note_index=0,
            applies=True,
            directive_type=DirectiveType.NO_CHARGE_WINDOW,
            structured_adjustment={"hours": [1, 2, 3]},
            explanation="No charge during night",
        )
    ]
    plan = solve_schedule(sample_request, directives)
    for entry in plan:
        if entry.hour in [1, 2, 3]:
            assert entry.battery_action != "charge"

    validate_schedule(plan, sample_request, directives)


def test_optimizer_no_discharge_window(sample_request):
    """Test solver respects no-discharge window."""
    directives = [
        ParsedDirective(
            note_index=0,
            applies=True,
            directive_type=DirectiveType.NO_DISCHARGE_WINDOW,
            structured_adjustment={"hours": [18, 19, 20]},
            explanation="No discharge during peak",
        )
    ]
    plan = solve_schedule(sample_request, directives)
    for entry in plan:
        if entry.hour in [18, 19, 20]:
            assert entry.battery_action != "discharge"

    validate_schedule(plan, sample_request, directives)


def test_optimizer_minimum_battery_reserve(sample_request):
    """Test solver respects minimum battery reserve."""
    reserve_target = 350.0  # Above initial 200
    directives = [
        ParsedDirective(
            note_index=0,
            applies=True,
            directive_type=DirectiveType.MINIMUM_BATTERY_RESERVE,
            structured_adjustment={"hours": [15, 16], "minimum_energy_kwh": reserve_target},
            explanation="Keep high reserve",
        )
    ]
    plan = solve_schedule(sample_request, directives)
    for entry in plan:
        if entry.hour in [15, 16]:
            assert entry.battery_energy_after_kwh >= reserve_target - 0.01

    validate_schedule(plan, sample_request, directives)


def test_optimizer_max_grid_window(sample_request):
    """Test solver respects grid import cap."""
    cap = 50.0
    directives = [
        ParsedDirective(
            note_index=0,
            applies=True,
            directive_type=DirectiveType.MAX_GRID_WINDOW,
            structured_adjustment={"hours": [8, 9], "max_grid_kwh": cap},
            explanation="Cap grid import",
        )
    ]
    plan = solve_schedule(sample_request, directives)
    for entry in plan:
        if entry.hour in [8, 9]:
            assert entry.grid_kwh <= cap + 0.01

    validate_schedule(plan, sample_request, directives)
