"""Parse and normalize raw LLM output into validated directive structures."""

import logging
from typing import List, Optional

from app.schemas.directives import DirectiveType, ParsedDirective

logger = logging.getLogger(__name__)


def parse_llm_output(raw_interpretations: List[dict], num_notes: int) -> List[ParsedDirective]:
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
            directive = _parse_single(raw, i)
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


def _parse_single(raw: dict, expected_index: int) -> ParsedDirective:
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
    dtype_str = raw.get("directive_type", "no_op")
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
