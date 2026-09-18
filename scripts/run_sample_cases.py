"""Run and verify all 10 public sample cases.

Usage:
    python scripts/run_sample_cases.py [--use-llm]
"""

import sys
import os
import json
import asyncio
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.schemas.request import OptimizeRequest
from app.schemas.directives import ParsedDirective, DirectiveType
from app.optimization.solver import solve_schedule
from app.validation.replay import validate_schedule
from app.services.optimize_service import OptimizeService
from app.config import get_settings


async def main():
    use_llm = "--use-llm" in sys.argv
    settings = get_settings()

    cases_path = Path(__file__).parent.parent / "docs" / "BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json"
    if not cases_path.exists():
        print(f"Error: Sample cases file not found at {cases_path}")
        sys.exit(1)

    with open(cases_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    cases = data["cases"]
    print(f"\n=======================================================")
    print(f"  GridWise Sample Case Verification (Total: {len(cases)} cases)")
    print(f"=======================================================")

    llm_service = None
    if use_llm:
        if not settings.gemini_api_key:
            print("Warning: --use-llm was specified but GEMINI_API_KEY is not set. Falling back to reference directives.\n")
        else:
            try:
                from app.llm.provider import GeminiProvider
                llm_service = OptimizeService(llm_provider=GeminiProvider())
                print(f"Using Gemini LLM model: {settings.llm_model}\n")
            except Exception as e:
                print(f"Failed to initialize GeminiProvider: {e}. Falling back to reference directives.\n")

    passed = 0
    failed = 0

    print(f"{'Case ID':<12} | {'Directives':<14} | {'Calc Cost':<11} | {'Ref Cost':<11} | {'Diff (BDT)':<11} | Status")
    print("-" * 75)

    for case in cases:
        case_id = case["id"]
        req_data = case["input"]
        expected = case["expected_output"]
        ref_cost = expected["total_cost_bdt"]
        ref_grid = expected["total_grid_kwh"]

        try:
            req = OptimizeRequest.model_validate(req_data)

            if llm_service is not None:
                # Full end-to-end with LLM
                response = await llm_service.optimize(req)
                calc_cost = response.total_cost_bdt
                calc_grid = response.total_grid_kwh
                directives_count = len(response.directive_interpretation)
            else:
                # Using reference ground-truth directives
                directives = [
                    ParsedDirective(
                        note_index=d["note_index"],
                        applies=d["applies"],
                        directive_type=DirectiveType(d["directive_type"]),
                        structured_adjustment=d.get("structured_adjustment"),
                        explanation=d.get("explanation", ""),
                    )
                    for d in expected["directive_interpretation"]
                ]
                plan = solve_schedule(req, directives)
                calc_grid, calc_cost, peak_grid = validate_schedule(plan, req, directives)
                directives_count = len(directives)

            diff = round(calc_cost - ref_cost, 2)
            # Optimal if cost <= ref_cost + 0.05
            is_valid = (calc_cost <= ref_cost + 0.05)

            status_str = "PASS" if is_valid else "FAIL"
            if is_valid:
                passed += 1
            else:
                failed += 1

            print(f"{case_id:<12} | {directives_count:<14} | {calc_cost:<11.2f} | {ref_cost:<11.2f} | {diff:<+11.2f} | {status_str}")

        except Exception as e:
            failed += 1
            print(f"{case_id:<12} | ERROR: {str(e)[:45]} | FAIL")

    print("-" * 75)
    print(f"Summary: {passed}/{len(cases)} PASSED, {failed} FAILED.\n")
    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
