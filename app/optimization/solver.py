"""OR-Tools LP solver for the 24-hour energy scheduling problem."""

import logging
from typing import List, Dict, Optional

from ortools.linear_solver import pywraplp

from app.schemas.directives import DirectiveType, ParsedDirective, BatteryAction
from app.schemas.request import OptimizeRequest, HourEntry, BatteryConfig
from app.schemas.response import HourlyPlanEntry

logger = logging.getLogger(__name__)

TOLERANCE = 0.01


class OptimizationError(Exception):
    """Raised when the optimizer fails."""
    pass


def solve_schedule(
    request: OptimizeRequest,
    directives: List[ParsedDirective],
) -> List[HourlyPlanEntry]:
    """
    Solve the 24-hour energy scheduling problem using OR-Tools LP.

    Returns the optimal hourly plan.
    """
    hours = sorted(request.hours, key=lambda h: h.hour)
    battery = request.battery

    # Compute effective solar after solar_reduction directives
    effective_solar = _compute_effective_solar(hours, directives)

    # Collect directive constraints
    no_charge_hours = set()
    no_discharge_hours = set()
    min_reserve: Dict[int, float] = {}  # hour -> minimum energy
    max_grid: Dict[int, float] = {}  # hour -> max grid kwh

    for d in directives:
        if not d.applies or d.directive_type == DirectiveType.NO_OP:
            continue
        adj = d.structured_adjustment
        if not adj:
            continue

        if d.directive_type == DirectiveType.NO_CHARGE_WINDOW:
            for h in adj["hours"]:
                no_charge_hours.add(h)

        elif d.directive_type == DirectiveType.NO_DISCHARGE_WINDOW:
            for h in adj["hours"]:
                no_discharge_hours.add(h)

        elif d.directive_type == DirectiveType.MINIMUM_BATTERY_RESERVE:
            for h in adj["hours"]:
                min_reserve[h] = max(min_reserve.get(h, 0), adj["minimum_energy_kwh"])

        elif d.directive_type == DirectiveType.MAX_GRID_WINDOW:
            for h in adj["hours"]:
                if h in max_grid:
                    max_grid[h] = min(max_grid[h], adj["max_grid_kwh"])
                else:
                    max_grid[h] = adj["max_grid_kwh"]

    # Build LP model
    solver = pywraplp.Solver.CreateSolver("GLOP")
    if not solver:
        raise OptimizationError("Failed to create GLOP solver")

    INF = solver.infinity()
    N = 24

    # Decision variables for each hour
    grid = []
    solar_used = []
    charge = []
    discharge = []
    energy_after = []

    for h in range(N):
        hour_data = hours[h]

        # Grid: non-negative, optionally capped
        grid_ub = max_grid.get(h, INF)
        grid.append(solver.NumVar(0.0, grid_ub, f"grid_{h}"))

        # Solar used: 0 to effective solar
        solar_used.append(solver.NumVar(0.0, effective_solar[h], f"solar_{h}"))

        # Charge: 0 to max_charge (0 if no-charge window)
        charge_ub = 0.0 if h in no_charge_hours else battery.max_charge_kwh_per_hour
        charge.append(solver.NumVar(0.0, charge_ub, f"charge_{h}"))

        # Discharge: 0 to max_discharge (0 if no-discharge window)
        discharge_ub = 0.0 if h in no_discharge_hours else battery.max_discharge_kwh_per_hour
        discharge.append(solver.NumVar(0.0, discharge_ub, f"discharge_{h}"))

        # Battery energy after: between minimum and capacity
        e_min = battery.minimum_energy_kwh
        if h in min_reserve:
            e_min = max(e_min, min_reserve[h])
        energy_after.append(solver.NumVar(e_min, battery.capacity_kwh, f"energy_{h}"))

    # Constraints
    for h in range(N):
        hour_data = hours[h]

        # Energy balance: grid + solar_used + discharge = demand + charge
        solver.Add(
            grid[h] + solar_used[h] + discharge[h]
            == hour_data.demand_kwh + charge[h],
            f"balance_{h}",
        )

        # Battery state transition
        if h == 0:
            e_before = battery.initial_energy_kwh
        else:
            e_before = energy_after[h - 1]

        solver.Add(
            energy_after[h] == e_before + charge[h] - discharge[h],
            f"battery_transition_{h}",
        )

    # End-of-day battery neutrality: energy_after[23] = initial_energy_kwh
    solver.Add(
        energy_after[23] == battery.initial_energy_kwh,
        "end_of_day_neutrality",
    )

    # Objective: minimize total grid cost
    objective = solver.Objective()
    for h in range(N):
        objective.SetCoefficient(grid[h], hours[h].tariff_bdt_per_kwh)
    objective.SetMinimization()

    # Solve
    status = solver.Solve()

    if status not in (pywraplp.Solver.OPTIMAL, pywraplp.Solver.FEASIBLE):
        raise OptimizationError(
            f"Solver failed with status {status}. "
            "The problem may be infeasible with the given directives."
        )

    # Extract solution
    plan = []
    for h in range(N):
        g = _round_val(grid[h].solution_value())
        s = _round_val(solar_used[h].solution_value())
        c = _round_val(charge[h].solution_value())
        d = _round_val(discharge[h].solution_value())
        e = _round_val(energy_after[h].solution_value())

        # Determine battery action
        if c > TOLERANCE and d > TOLERANCE:
            # Simultaneous charge/discharge shouldn't happen in optimal LP,
            # but handle it by netting
            if c > d:
                c = c - d
                d = 0.0
            else:
                d = d - c
                c = 0.0

        if c > TOLERANCE:
            action = BatteryAction.CHARGE
            battery_kwh = c
        elif d > TOLERANCE:
            action = BatteryAction.DISCHARGE
            battery_kwh = d
        else:
            action = BatteryAction.IDLE
            battery_kwh = 0.0

        plan.append(HourlyPlanEntry(
            hour=h,
            grid_kwh=g,
            solar_used_kwh=s,
            battery_action=action.value,
            battery_kwh=battery_kwh,
            battery_energy_after_kwh=e,
        ))

    return plan


def _compute_effective_solar(
    hours: List[HourEntry],
    directives: List[ParsedDirective],
) -> Dict[int, float]:
    """Compute effective solar for each hour after applying solar_reduction directives."""
    effective = {h.hour: h.solar_kwh for h in hours}

    for d in directives:
        if d.applies and d.directive_type == DirectiveType.SOLAR_REDUCTION:
            adj = d.structured_adjustment
            if adj and "hours" in adj and "factor" in adj:
                for h in adj["hours"]:
                    if h in effective:
                        effective[h] = effective[h] * adj["factor"]

    return effective


def _round_val(v: float) -> float:
    """Round a value to avoid floating-point noise."""
    return round(v, 6)
