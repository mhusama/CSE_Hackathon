"""Unit tests for deterministic guardrail validator."""

import pytest
from app.schemas.directives import DirectiveType, ParsedDirective
from app.guardrails.validator import validate_directives, GuardrailError


def test_no_op_enforces_applies_false():
    directives = [
        ParsedDirective(
            note_index=0,
            applies=True,  # Should be forced to False
            directive_type=DirectiveType.NO_OP,
            structured_adjustment={"something": "here"},  # Should be cleared to None
            explanation="Irrelevant note",
        )
    ]
    validated = validate_directives(directives, num_notes=1, battery_capacity_kwh=500.0)
    assert len(validated) == 1
    assert validated[0].applies is False
    assert validated[0].directive_type == DirectiveType.NO_OP
    assert validated[0].structured_adjustment is None


def test_solar_reduction_validation():
    directives = [
        ParsedDirective(
            note_index=0,
            applies=True,
            directive_type=DirectiveType.SOLAR_REDUCTION,
            structured_adjustment={"hours": [14, 13, 13], "factor": 0.25},
            explanation="Cleaning panels",
        )
    ]
    validated = validate_directives(directives, num_notes=1, battery_capacity_kwh=500.0)
    assert validated[0].applies is True
    # Hours must be sorted and deduplicated: [13, 14]
    assert validated[0].structured_adjustment["hours"] == [13, 14]
    assert validated[0].structured_adjustment["factor"] == 0.25


def test_invalid_solar_reduction_factor_falls_back_to_no_op():
    directives = [
        ParsedDirective(
            note_index=0,
            applies=True,
            directive_type=DirectiveType.SOLAR_REDUCTION,
            structured_adjustment={"hours": [12, 13], "factor": 1.5},  # Factor > 1 is invalid
            explanation="Bad factor",
        )
    ]
    validated = validate_directives(directives, num_notes=1, battery_capacity_kwh=500.0)
    assert validated[0].applies is False
    assert validated[0].directive_type == DirectiveType.NO_OP
    assert validated[0].structured_adjustment is None


def test_min_battery_reserve_exceeding_capacity():
    directives = [
        ParsedDirective(
            note_index=0,
            applies=True,
            directive_type=DirectiveType.MINIMUM_BATTERY_RESERVE,
            structured_adjustment={"hours": [18, 19], "minimum_energy_kwh": 600.0},
            explanation="Reserve higher than 500 kWh capacity",
        )
    ]
    validated = validate_directives(directives, num_notes=1, battery_capacity_kwh=500.0)
    # Exceeding capacity should fall back safely to no_op
    assert validated[0].applies is False
    assert validated[0].directive_type == DirectiveType.NO_OP


def test_no_charge_window():
    directives = [
        ParsedDirective(
            note_index=0,
            applies=True,
            directive_type=DirectiveType.NO_CHARGE_WINDOW,
            structured_adjustment={"hours": [14, 15]},
            explanation="Peak tariff, avoid charge",
        )
    ]
    validated = validate_directives(directives, num_notes=1, battery_capacity_kwh=500.0)
    assert validated[0].applies is True
    assert validated[0].structured_adjustment["hours"] == [14, 15]


def test_max_grid_window():
    directives = [
        ParsedDirective(
            note_index=0,
            applies=True,
            directive_type=DirectiveType.MAX_GRID_WINDOW,
            structured_adjustment={"hours": [17, 18], "max_grid_kwh": 40.0},
            explanation="Transformer limit",
        )
    ]
    validated = validate_directives(directives, num_notes=1, battery_capacity_kwh=500.0)
    assert validated[0].applies is True
    assert validated[0].structured_adjustment["hours"] == [17, 18]
    assert validated[0].structured_adjustment["max_grid_kwh"] == 40.0


def test_note_count_mismatch_and_indexing():
    # Only 1 directive provided for 2 notes
    directives = [
        ParsedDirective(
            note_index=5,  # Bad index
            applies=True,
            directive_type=DirectiveType.NO_CHARGE_WINDOW,
            structured_adjustment={"hours": [10]},
            explanation="Only one provided",
        )
    ]
    validated = validate_directives(directives, num_notes=2, battery_capacity_kwh=500.0)
    assert len(validated) == 2
    assert validated[0].note_index == 0
    assert validated[1].note_index == 1
    assert validated[1].applies is False
    assert validated[1].directive_type == DirectiveType.NO_OP
