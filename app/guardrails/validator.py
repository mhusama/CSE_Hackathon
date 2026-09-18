"""Deterministic guardrail validation for LLM-produced directives."""

import logging
import math
from typing import List

from app.schemas.directives import DirectiveType, ParsedDirective

logger = logging.getLogger(__name__)


class GuardrailError(Exception):
    """Raised when guardrail validation fails fatally."""
    pass


def validate_directives(
    directives: List[ParsedDirective],
    num_notes: int,
    battery_capacity_kwh: float,
) -> List[ParsedDirective]:
    """
    Validate and sanitize LLM-produced directives.

    This is the deterministic guardrail layer that runs AFTER the LLM
    and BEFORE the optimizer. It enforces all structural and semantic rules.

    Returns validated directives (may fix minor issues) or raises GuardrailError.
    """
    # 1. Check count
    if len(directives) != num_notes:
        logger.warning(
            f"Expected {num_notes} directives, got {len(directives)}. Adjusting."
        )
        directives = _fix_count(directives, num_notes)

    # 2. Check and fix note_index ordering
    directives = _fix_indices(directives, num_notes)

    # 3. Validate each directive
    validated = []
    for d in directives:
        validated.append(_validate_single(d, battery_capacity_kwh))

    return validated


def _fix_count(directives: List[ParsedDirective], num_notes: int) -> List[ParsedDirective]:
    """Ensure we have exactly num_notes directives."""
    result = list(directives[:num_notes])
    while len(result) < num_notes:
        idx = len(result)
        result.append(
            ParsedDirective(
                note_index=idx,
                applies=False,
                directive_type=DirectiveType.NO_OP,
                structured_adjustment=None,
                explanation="Missing interpretation, defaulting to no_op.",
            )
        )
    return result


def _fix_indices(directives: List[ParsedDirective], num_notes: int) -> List[ParsedDirective]:
    """Ensure note_index values are 0..N-1 in order."""
    seen = set()
    for d in directives:
        seen.add(d.note_index)

    # If indices are already correct, return as-is
    expected = set(range(num_notes))
    if seen == expected and all(d.note_index == i for i, d in enumerate(directives)):
        return directives

    # Try to sort by existing indices
    if seen == expected:
        return sorted(directives, key=lambda d: d.note_index)

    # Force-assign indices in order
    logger.warning(f"Fixing note_index values: {[d.note_index for d in directives]}")
    for i, d in enumerate(directives):
        d.note_index = i
    return directives


def _validate_single(
    directive: ParsedDirective,
    battery_capacity_kwh: float,
) -> ParsedDirective:
    """Validate a single directive, fixing minor issues or falling back to no_op."""

    # Validate directive_type is in the allowed set
    if directive.directive_type not in DirectiveType:
        logger.warning(f"Invalid directive_type: {directive.directive_type}")
        return _make_no_op(directive)

    # Enforce applies semantics
    if directive.directive_type == DirectiveType.NO_OP:
        directive.applies = False
        directive.structured_adjustment = None
        return directive

    # For all non-no_op directives, applies must be true
    directive.applies = True

    # Validate structured_adjustment based on directive_type
    adj = directive.structured_adjustment
    if not isinstance(adj, dict):
        logger.warning(f"Note {directive.note_index}: missing structured_adjustment for {directive.directive_type}")
        return _make_no_op(directive)

    try:
        if directive.directive_type == DirectiveType.SOLAR_REDUCTION:
            _validate_solar_reduction(adj, directive.note_index)
        elif directive.directive_type == DirectiveType.MINIMUM_BATTERY_RESERVE:
            _validate_min_reserve(adj, directive.note_index, battery_capacity_kwh)
        elif directive.directive_type == DirectiveType.NO_CHARGE_WINDOW:
            _validate_hours_only(adj, directive.note_index)
        elif directive.directive_type == DirectiveType.NO_DISCHARGE_WINDOW:
            _validate_hours_only(adj, directive.note_index)
        elif directive.directive_type == DirectiveType.MAX_GRID_WINDOW:
            _validate_max_grid(adj, directive.note_index)
    except GuardrailError as e:
        logger.warning(f"Guardrail failed for note {directive.note_index}: {e}")
        return _make_no_op(directive)

    return directive


def _validate_hours(hours, note_index: int) -> List[int]:
    """Validate hours array: integers, 0-23, unique, ascending."""
    if not isinstance(hours, list) or len(hours) == 0:
        raise GuardrailError(f"Note {note_index}: hours must be a non-empty list")

    int_hours = []
    for h in hours:
        if not isinstance(h, (int, float)):
            raise GuardrailError(f"Note {note_index}: hour {h} is not numeric")
        h_int = int(h)
        if h_int != h:
            logger.warning(f"Note {note_index}: non-integer hour {h}, rounding to {h_int}")
        if not (0 <= h_int <= 23):
            raise GuardrailError(f"Note {note_index}: hour {h_int} out of range 0-23")
        int_hours.append(h_int)

    # Remove duplicates and sort
    int_hours = sorted(set(int_hours))
    return int_hours


def _validate_solar_reduction(adj: dict, note_index: int):
    """Validate solar_reduction structured_adjustment."""
    if "hours" not in adj:
        raise GuardrailError(f"Note {note_index}: solar_reduction missing 'hours'")
    if "factor" not in adj:
        raise GuardrailError(f"Note {note_index}: solar_reduction missing 'factor'")

    adj["hours"] = _validate_hours(adj["hours"], note_index)

    factor = adj["factor"]
    if not isinstance(factor, (int, float)):
        raise GuardrailError(f"Note {note_index}: factor must be numeric, got {type(factor)}")
    if not math.isfinite(factor):
        raise GuardrailError(f"Note {note_index}: factor must be finite")
    if not (0.0 <= factor <= 1.0):
        raise GuardrailError(f"Note {note_index}: factor must be 0-1, got {factor}")
    adj["factor"] = float(factor)


def _validate_min_reserve(adj: dict, note_index: int, battery_capacity: float):
    """Validate minimum_battery_reserve structured_adjustment."""
    if "hours" not in adj:
        raise GuardrailError(f"Note {note_index}: minimum_battery_reserve missing 'hours'")
    if "minimum_energy_kwh" not in adj:
        raise GuardrailError(f"Note {note_index}: minimum_battery_reserve missing 'minimum_energy_kwh'")

    adj["hours"] = _validate_hours(adj["hours"], note_index)

    val = adj["minimum_energy_kwh"]
    if not isinstance(val, (int, float)):
        raise GuardrailError(f"Note {note_index}: minimum_energy_kwh must be numeric")
    if not math.isfinite(val):
        raise GuardrailError(f"Note {note_index}: minimum_energy_kwh must be finite")
    if val < 0:
        raise GuardrailError(f"Note {note_index}: minimum_energy_kwh must be >= 0")
    if val > battery_capacity:
        raise GuardrailError(f"Note {note_index}: minimum_energy_kwh ({val}) exceeds battery capacity ({battery_capacity})")
    adj["minimum_energy_kwh"] = float(val)


def _validate_hours_only(adj: dict, note_index: int):
    """Validate no_charge_window / no_discharge_window structured_adjustment."""
    if "hours" not in adj:
        raise GuardrailError(f"Note {note_index}: missing 'hours'")
    adj["hours"] = _validate_hours(adj["hours"], note_index)


def _validate_max_grid(adj: dict, note_index: int):
    """Validate max_grid_window structured_adjustment."""
    if "hours" not in adj:
        raise GuardrailError(f"Note {note_index}: max_grid_window missing 'hours'")
    if "max_grid_kwh" not in adj:
        raise GuardrailError(f"Note {note_index}: max_grid_window missing 'max_grid_kwh'")

    adj["hours"] = _validate_hours(adj["hours"], note_index)

    val = adj["max_grid_kwh"]
    if not isinstance(val, (int, float)):
        raise GuardrailError(f"Note {note_index}: max_grid_kwh must be numeric")
    if not math.isfinite(val):
        raise GuardrailError(f"Note {note_index}: max_grid_kwh must be finite")
    if val < 0:
        raise GuardrailError(f"Note {note_index}: max_grid_kwh must be >= 0")
    adj["max_grid_kwh"] = float(val)


def _make_no_op(directive: ParsedDirective) -> ParsedDirective:
    """Convert a failed directive to a safe no_op."""
    directive.applies = False
    directive.directive_type = DirectiveType.NO_OP
    directive.structured_adjustment = None
    directive.explanation = f"Guardrail fallback: {directive.explanation}"
    return directive
