"""Main optimization service orchestrating the full pipeline."""

import logging
from typing import List

from app.schemas.request import OptimizeRequest
from app.schemas.response import (
    OptimizeResponse,
    DirectiveInterpretationResponse,
)
from app.schemas.directives import ParsedDirective
from app.llm.base import LLMProvider, LLMError
from app.llm.parser import parse_llm_output
from app.guardrails.validator import validate_directives
from app.optimization.solver import solve_schedule, OptimizationError
from app.validation.replay import validate_schedule, ValidationError

logger = logging.getLogger(__name__)


class OptimizeService:
    """Orchestrates the full LLM → Guardrails → Optimizer → Validator pipeline."""

    def __init__(self, llm_provider: LLMProvider):
        self._llm = llm_provider

    async def optimize(self, request: OptimizeRequest) -> OptimizeResponse:
        """
        Run the complete optimization pipeline.

        Steps:
        1. Call LLM to interpret operator notes
        2. Parse and normalize LLM output
        3. Apply deterministic guardrails
        4. Solve the optimization problem
        5. Replay/validate the schedule
        6. Build and return the response
        """
        num_notes = len(request.operator_notes)

        # Step 1: LLM interpretation
        logger.info(f"Interpreting {num_notes} operator notes via LLM")
        try:
            raw_interpretations = await self._llm.interpret_notes(
                operator_notes=request.operator_notes,
                battery_capacity_kwh=request.battery.capacity_kwh,
            )
        except LLMError as e:
            logger.error(f"LLM interpretation failed: {e}")
            raise

        # Step 2: Parse LLM output
        logger.info("Parsing LLM output")
        directives = parse_llm_output(raw_interpretations, num_notes)

        # Step 3: Guardrails
        logger.info("Applying deterministic guardrails")
        directives = validate_directives(
            directives,
            num_notes=num_notes,
            battery_capacity_kwh=request.battery.capacity_kwh,
        )

        # Step 4: Optimize
        logger.info("Running optimizer")
        try:
            plan = solve_schedule(request, directives)
        except OptimizationError as e:
            logger.error(f"Optimization failed: {e}")
            raise

        # Step 5: Validate
        logger.info("Validating schedule")
        try:
            total_grid, total_cost, peak_grid = validate_schedule(
                plan, request, directives
            )
        except ValidationError as e:
            logger.error(f"Schedule validation failed: {e}")
            raise

        # Step 6: Build response
        logger.info(
            f"Schedule validated. Cost: {total_cost:.2f} BDT, "
            f"Grid: {total_grid:.2f} kWh, Peak: {peak_grid:.2f} kWh"
        )

        directive_responses = _build_directive_responses(directives)
        summary = _build_summary(directives, total_cost, total_grid)

        return OptimizeResponse(
            scenario_id=request.scenario_id,
            directive_interpretation=directive_responses,
            hourly_plan=plan,
            total_grid_kwh=round(total_grid, 2),
            total_cost_bdt=round(total_cost, 2),
            peak_grid_kwh=round(peak_grid, 2),
            plan_summary=summary,
        )


def _build_directive_responses(
    directives: List[ParsedDirective],
) -> List[DirectiveInterpretationResponse]:
    """Convert internal directives to response format."""
    return [
        DirectiveInterpretationResponse(
            note_index=d.note_index,
            applies=d.applies,
            directive_type=d.directive_type.value,
            structured_adjustment=d.structured_adjustment,
            explanation=d.explanation,
        )
        for d in directives
    ]


def _build_summary(
    directives: List[ParsedDirective],
    total_cost: float,
    total_grid: float,
) -> str:
    """Build a human-readable plan summary."""
    applied = [d for d in directives if d.applies]
    ignored = [d for d in directives if not d.applies]

    parts = []
    if applied:
        types = [d.directive_type.value for d in applied]
        parts.append(f"Applied {len(applied)} directive(s): {', '.join(types)}.")
    if ignored:
        parts.append(f"Ignored {len(ignored)} irrelevant note(s).")
    parts.append(
        f"Optimized schedule achieves {total_cost:.2f} BDT total cost "
        f"using {total_grid:.2f} kWh from grid."
    )
    return " ".join(parts)
