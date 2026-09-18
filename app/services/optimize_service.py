"""Main optimization service orchestrating the full pipeline."""

import copy
import logging
from itertools import combinations
from typing import List, Optional, Tuple

from app.guardrails.validator import validate_directives
from app.llm.interpreter import NoteInterpreter
from app.optimization.solver import OptimizationError, solve_schedule
from app.schemas.directives import DirectiveType, ParsedDirective
from app.schemas.request import OptimizeRequest
from app.schemas.response import DirectiveInterpretationResponse, HourlyPlanEntry, OptimizeResponse
from app.validation.replay import ValidationError, validate_schedule

logger = logging.getLogger(__name__)


class OptimizeService:
    """Orchestrates LLM -> Guardrails -> Optimizer -> Replay validator."""

    def __init__(self, interpreter: NoteInterpreter):
        self._interpreter = interpreter

    async def optimize(self, request: OptimizeRequest) -> OptimizeResponse:
        num_notes = len(request.operator_notes)

        # 1-2. LLM interpretation (retries, model failover, emergency fallback inside)
        result = await self._interpreter.interpret(request.operator_notes, request.battery.capacity_kwh)
        logger.info(f"Interpretation source: {result.source}")

        # 3. Deterministic guardrails
        directives = validate_directives(
            result.directives, num_notes=num_notes, battery_capacity_kwh=request.battery.capacity_kwh
        )

        # 4. Optimize (relax conflicting directives instead of failing the request)
        plan, applied, relaxed = self._solve_with_relaxation(request, directives)

        # 5. Replay against exactly what was applied
        total_grid, total_cost, peak_grid = validate_schedule(plan, request, applied)

        logger.info(f"Schedule validated. Cost: {total_cost:.2f} BDT, Grid: {total_grid:.2f} kWh, Peak: {peak_grid:.2f} kWh")

        return OptimizeResponse(
            scenario_id=request.scenario_id,
            directive_interpretation=_build_directive_responses(directives),
            hourly_plan=plan,
            total_grid_kwh=round(total_grid, 4),
            total_cost_bdt=round(total_cost, 4),
            peak_grid_kwh=round(peak_grid, 4),
            plan_summary=_build_summary(directives, total_cost, total_grid, relaxed, result.source),
        )

    @staticmethod
    def _solve_with_relaxation(
        request: OptimizeRequest, directives: List[ParsedDirective]
    ) -> Tuple[List[HourlyPlanEntry], List[ParsedDirective], List[int]]:
        try:
            return solve_schedule(request, directives), directives, []
        except OptimizationError as first_error:
            active = [i for i, d in enumerate(directives) if d.applies]
            if not active:
                raise
            logger.warning(f"Infeasible with directives {active}; trying to relax: {first_error}")
            for k in range(1, len(active) + 1):
                feasible = []
                for drop in combinations(active, k):
                    trial = [_as_no_op(d) if i in drop else d for i, d in enumerate(directives)]
                    try:
                        plan = solve_schedule(request, trial)
                    except OptimizationError:
                        continue
                    cost = sum(p.grid_kwh * h.tariff_bdt_per_kwh for p, h in zip(plan, sorted(request.hours, key=lambda x: x.hour)))
                    feasible.append((cost, plan, trial, list(drop)))
                if feasible:
                    cost, plan, trial, drop = min(feasible, key=lambda t: t[0])
                    logger.warning(f"Relaxed directives for notes {drop}")
                    return plan, trial, drop
            raise first_error


def _as_no_op(d: ParsedDirective) -> ParsedDirective:
    c = copy.deepcopy(d)
    c.applies = False
    c.directive_type = DirectiveType.NO_OP
    c.structured_adjustment = None
    return c


def _build_directive_responses(directives: List[ParsedDirective]) -> List[DirectiveInterpretationResponse]:
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
    relaxed: Optional[List[int]] = None,
    source: str = "",
) -> str:
    applied = [d for i, d in enumerate(directives) if d.applies and i not in (relaxed or [])]
    ignored = [d for d in directives if not d.applies]
    parts = []
    if applied:
        parts.append(f"Applied {len(applied)} directive(s): {', '.join(d.directive_type.value for d in applied)}.")
    if ignored:
        parts.append(f"Ignored {len(ignored)} irrelevant note(s).")
    if relaxed:
        parts.append(
            f"Note(s) {relaxed} could not be satisfied together with the others (infeasible), so they were relaxed."
        )
    parts.append(f"Optimized schedule achieves {total_cost:.2f} BDT total cost using {total_grid:.2f} kWh from grid.")
    if source == "rules-fallback":
        parts.append("Notes were read by the emergency rule-based parser because the LLM was unavailable.")
    return " ".join(parts)
