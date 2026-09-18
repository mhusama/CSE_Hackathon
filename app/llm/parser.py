"""Parse and normalize raw LLM output into validated directive structures."""

import logging
from typing import List, Optional

from app.schemas.directives import DirectiveType, ParsedDirective

logger = logging.getLogger(__name__)


def parse_llm_output(
    raw_interpretations: List[dict],
    num_notes: int,
    battery_capacity_kwh: Optional[float] = None,
) -> List[ParsedDirective]:
    """
    Parse raw LLM JSON output into ParsedDirective objects.

    Performs basic structural normalization before guardrail validation.
    """
    directives: List[ParsedDirective] = []

    for i in range(num_notes):
        if i < len(raw_interpretations):
            raw = raw_interpretations[i]
        else:
            # Missing interpretation - create a safe no_op fallback
            logger.warning(f"Missing interpretation for note {i}, defaulting to no_op")
            raw = {
                "note_index": i,
                "applies": False,
                "directive_type": "no_op",
                "structured_adjustment": None,
                "explanation": "No interpretation available from LLM.",
            }

        try:
            directive = _parse_single(raw, i, battery_capacity_kwh)
            directives.append(directive)
        except Exception as e:
            logger.warning(f"Failed to parse interpretation for note {i}: {e}")
            # Safe fallback
            directives.append(
                ParsedDirective(
                    note_index=i,
                    applies=False,
                    directive_type=DirectiveType.NO_OP,
                    structured_adjustment=None,
                    explanation=f"Parse error: {e}",
                )
            )

    return directives


def _parse_single(raw: dict, expected_index: int, battery_capacity_kwh: Optional[float] = None) -> ParsedDirective:
    """Parse a single raw interpretation dict."""
    if not isinstance(raw, dict):
        raise ValueError(f"Expected dict, got {type(raw).__name__}")

    # Extract note_index, defaulting to expected
    note_index = raw.get("note_index", expected_index)
    if not isinstance(note_index, int):
        try:
            note_index = int(note_index)
        except (ValueError, TypeError):
            note_index = expected_index

    # Extract directive_type
    dtype_str = raw.get("directive_type") or "no_op"
    if not isinstance(dtype_str, str):
        dtype_str = str(dtype_str)

    try:
        directive_type = DirectiveType(dtype_str.lower().strip())
    except ValueError:
        logger.warning(f"Unknown directive type '{dtype_str}', treating as no_op")
        directive_type = DirectiveType.NO_OP

    # Extract applies
    applies = raw.get("applies", directive_type != DirectiveType.NO_OP)
    if not isinstance(applies, bool):
        applies = bool(applies)

    # Enforce applies semantics
    if directive_type == DirectiveType.NO_OP:
        applies = False
        structured_adjustment = None
    else:
        applies = True
        structured_adjustment = raw.get("structured_adjustment")
        if not isinstance(structured_adjustment, dict):
            structured_adjustment = _assemble_adjustment(raw, directive_type, battery_capacity_kwh)

    # Extract explanation
    explanation = raw.get("explanation", "")
    if not isinstance(explanation, str):
        explanation = str(explanation)

    return ParsedDirective(
        note_index=note_index,
        applies=applies,
        directive_type=directive_type,
        structured_adjustment=structured_adjustment,
        explanation=explanation,
    )


def _assemble_adjustment(raw: dict, dtype: DirectiveType, capacity: Optional[float]) -> Optional[dict]:
    """Build the official structured_adjustment from the flat fields the LLM returns.

    Only the official keys are copied, so extra fields never leak into the response.
    Percent-of-capacity reserves are converted here, in code, not by the model.
    """
    hours = raw.get("hours")
    if dtype == DirectiveType.SOLAR_REDUCTION:
        return {"hours": hours, "factor": raw.get("factor")}
    if dtype == DirectiveType.MINIMUM_BATTERY_RESERVE:
        value = raw.get("reserve_value", raw.get("minimum_energy_kwh"))
        unit = str(raw.get("reserve_unit") or "kwh").lower()
        if isinstance(value, (int, float)) and not isinstance(value, bool) and unit.startswith("percent"):
            value = value / 100.0 * capacity if capacity is not None else None
        return {"hours": hours, "minimum_energy_kwh": value}
    if dtype == DirectiveType.MAX_GRID_WINDOW:
        return {"hours": hours, "max_grid_kwh": raw.get("max_grid_kwh")}
    if dtype in (DirectiveType.NO_CHARGE_WINDOW, DirectiveType.NO_DISCHARGE_WINDOW):
        return {"hours": hours}
    return None
