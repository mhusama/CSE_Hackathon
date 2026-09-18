"""Tests verifying all 10 public sample cases against the optimizer and replay validator."""

import pytest
from app.schemas.request import OptimizeRequest
from app.schemas.directives import ParsedDirective, DirectiveType
from app.optimization.solver import solve_schedule
from app.validation.replay import validate_schedule


def test_all_10_public_sample_cases(public_sample_cases):
    cases = public_sample_cases["cases"]
    assert len(cases) == 10, f"Expected 10 cases, got {len(cases)}"

    for case in cases:
        case_id = case["id"]
        req_data = case["input"]
        expected_output = case["expected_output"]

        # Parse request with Pydantic
        request = OptimizeRequest.model_validate(req_data)

        # Parse expected directive interpretations into ParsedDirective
        expected_directives = []
        for d in expected_output["directive_interpretation"]:
            expected_directives.append(
                ParsedDirective(
                    note_index=d["note_index"],
                    applies=d["applies"],
                    directive_type=DirectiveType(d["directive_type"]),
                    structured_adjustment=d.get("structured_adjustment"),
                    explanation=d.get("explanation", ""),
                )
            )

        # Solve schedule
        plan = solve_schedule(request, expected_directives)
        assert len(plan) == 24, f"{case_id}: Plan must have 24 hours"

        # Validate with deterministic post-solve replay validator
        total_grid, total_cost, peak_grid = validate_schedule(plan, request, expected_directives)

        ref_cost = expected_output["total_cost_bdt"]
        ref_grid = expected_output["total_grid_kwh"]

        # Cost should be optimal: <= reference cost within 0.05 tolerance
        assert total_cost <= ref_cost + 0.05, (
            f"{case_id}: Solver cost {total_cost} is worse than reference cost {ref_cost}"
        )

        # Cost difference should be very close to reference (either identical or slightly better)
        cost_diff = abs(total_cost - ref_cost)
        assert cost_diff < 0.05 or total_cost < ref_cost, (
            f"{case_id}: Expected cost ~{ref_cost}, got {total_cost} (diff {cost_diff})"
        )
