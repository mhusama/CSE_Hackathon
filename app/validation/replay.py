"""Deterministic post-solve replay validator.

Replays the hourly plan hour-by-hour and verifies every constraint.
"""

import logging
from typing import List, Dict, Tuple

from app.schemas.directives import DirectiveType, ParsedDirective
from app.schemas.request import OptimizeRequest
from app.schemas.response import HourlyPlanEntry

logger = logging.getLogger(__name__)

TOLERANCE = 0.01


class ValidationError(Exception):
    """Raised when the replay validator finds a constraint violation."""
    pass


def validate_schedule(
    plan: List[HourlyPlanEntry],
    request: OptimizeRequest,
    directives: List[ParsedDirective],
) -> Tuple[float, float, float]:
    """
    Replay and validate the complete 24-hour schedule.

    Returns: (total_grid_kwh, total_cost_bdt, peak_grid_kwh) calculated from the plan.

    Raises ValidationError if any constraint is violated.
    """
    errors = []
    hours_data = {h.hour: h for h in request.hours}
    battery = request.battery

    # 1. Check plan has exactly 24 hours
    if len(plan) != 24:
        errors.append(f"Plan has {len(plan)} entries, expected 24")

    plan_hours = [p.hour for p in plan]
    if sorted(plan_hours) != list(range(24)):
        errors.append(f"Plan hours are not 0-23: {sorted(plan_hours)}")

    # Sort plan by hour
    plan = sorted(plan, key=lambda p: p.hour)

    # Compute effective solar
    effective_solar = _compute_effective_solar(hours_data, directives)

    # Collect directive constraints
    no_charge_hours = set()
    no_discharge_hours = set()
    min_reserve: Dict[int, float] = {}
    max_grid: Dict[int, float] = {}

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

    # Replay hour by hour
    total_grid = 0.0
    total_cost = 0.0
    peak_grid = 0.0
    energy_before = battery.initial_energy_kwh

    for entry in plan:
        h = entry.hour
        hour_data = hours_data[h]

        # Non-negative values
        if entry.grid_kwh < -TOLERANCE:
            errors.append(f"Hour {h}: grid_kwh ({entry.grid_kwh}) is negative")
        if entry.solar_used_kwh < -TOLERANCE:
            errors.append(f"Hour {h}: solar_used_kwh ({entry.solar_used_kwh}) is negative")
        if entry.battery_kwh < -TOLERANCE:
            errors.append(f"Hour {h}: battery_kwh ({entry.battery_kwh}) is negative")

        # Solar used <= effective solar
        eff_solar = effective_solar.get(h, hour_data.solar_kwh)
        if entry.solar_used_kwh > eff_solar + TOLERANCE:
            errors.append(
                f"Hour {h}: solar_used_kwh ({entry.solar_used_kwh}) exceeds "
                f"effective solar ({eff_solar})"
            )

        # Determine charge/discharge from battery_action
        if entry.battery_action == "charge":
            charge_kwh = entry.battery_kwh
            discharge_kwh = 0.0
        elif entry.battery_action == "discharge":
            charge_kwh = 0.0
            discharge_kwh = entry.battery_kwh
        elif entry.battery_action == "idle":
            charge_kwh = 0.0
            discharge_kwh = 0.0
            if entry.battery_kwh > TOLERANCE:
                errors.append(
                    f"Hour {h}: battery_kwh must be 0 for idle, got {entry.battery_kwh}"
                )
        else:
            errors.append(f"Hour {h}: invalid battery_action '{entry.battery_action}'")
            charge_kwh = 0.0
            discharge_kwh = 0.0

        # Energy balance: grid + solar + discharge = demand + charge
        lhs = entry.grid_kwh + entry.solar_used_kwh + discharge_kwh
        rhs = hour_data.demand_kwh + charge_kwh
        if abs(lhs - rhs) > TOLERANCE:
            errors.append(
                f"Hour {h}: energy balance violated. "
                f"grid({entry.grid_kwh}) + solar({entry.solar_used_kwh}) + discharge({discharge_kwh}) = {lhs} != "
                f"demand({hour_data.demand_kwh}) + charge({charge_kwh}) = {rhs}"
            )

        # Charge rate limit
        if charge_kwh > battery.max_charge_kwh_per_hour + TOLERANCE:
            errors.append(
                f"Hour {h}: charge {charge_kwh} exceeds max {battery.max_charge_kwh_per_hour}"
            )

        # Discharge rate limit
        if discharge_kwh > battery.max_discharge_kwh_per_hour + TOLERANCE:
            errors.append(
                f"Hour {h}: discharge {discharge_kwh} exceeds max {battery.max_discharge_kwh_per_hour}"
            )

        # No-charge directive
        if h in no_charge_hours and charge_kwh > TOLERANCE:
            errors.append(f"Hour {h}: charging ({charge_kwh}) during no-charge window")

        # No-discharge directive
        if h in no_discharge_hours and discharge_kwh > TOLERANCE:
            errors.append(f"Hour {h}: discharging ({discharge_kwh}) during no-discharge window")

        # Grid cap directive
        if h in max_grid and entry.grid_kwh > max_grid[h] + TOLERANCE:
            errors.append(
                f"Hour {h}: grid ({entry.grid_kwh}) exceeds cap ({max_grid[h]})"
            )

        # Battery state transition
        expected_energy = energy_before + charge_kwh - discharge_kwh
        if abs(entry.battery_energy_after_kwh - expected_energy) > TOLERANCE:
            errors.append(
                f"Hour {h}: battery_energy_after ({entry.battery_energy_after_kwh}) != "
                f"expected ({expected_energy}) [before={energy_before}, charge={charge_kwh}, discharge={discharge_kwh}]"
            )

        # Battery bounds
        e_min = battery.minimum_energy_kwh
        if h in min_reserve:
            e_min = max(e_min, min_reserve[h])

        if entry.battery_energy_after_kwh < e_min - TOLERANCE:
            errors.append(
                f"Hour {h}: battery_energy ({entry.battery_energy_after_kwh}) below minimum ({e_min})"
            )
        if entry.battery_energy_after_kwh > battery.capacity_kwh + TOLERANCE:
            errors.append(
                f"Hour {h}: battery_energy ({entry.battery_energy_after_kwh}) exceeds capacity ({battery.capacity_kwh})"
            )

        # Update state
        energy_before = entry.battery_energy_after_kwh
        total_grid += entry.grid_kwh
        total_cost += entry.grid_kwh * hour_data.tariff_bdt_per_kwh
        peak_grid = max(peak_grid, entry.grid_kwh)

    # End-of-day neutrality
    if abs(energy_before - battery.initial_energy_kwh) > TOLERANCE:
        errors.append(
            f"End-of-day: battery ({energy_before}) != initial ({battery.initial_energy_kwh})"
        )

    if errors:
        error_msg = "; ".join(errors[:10])  # Limit error detail
        raise ValidationError(f"Schedule validation failed: {error_msg}")

    return round(total_grid, 6), round(total_cost, 6), round(peak_grid, 6)


def _compute_effective_solar(
    hours_data: Dict[int, object],
    directives: List[ParsedDirective],
) -> Dict[int, float]:
    """Compute effective solar per hour after solar_reduction directives."""
    effective = {h: hours_data[h].solar_kwh for h in hours_data}

    for d in directives:
        if d.applies and d.directive_type == DirectiveType.SOLAR_REDUCTION:
            adj = d.structured_adjustment
            if adj and "hours" in adj and "factor" in adj:
                for h in adj["hours"]:
                    if h in effective:
                        effective[h] = effective[h] * adj["factor"]

    return effective
