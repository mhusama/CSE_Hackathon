"""Emergency fallback interpreter.

Used ONLY when every LLM call failed (quota, outage, bad key, timeout), so a
valid request still gets a valid schedule instead of a 500. The normal path is
always the LLM. Output goes through the same guardrails and optimizer.
Best effort: it understands common phrasings, not every paraphrase.
"""

import re
from typing import List, Optional

from app.guardrails.timeparse import extract_windows

_NEG = re.compile(
    r"\b(not|no|never|cannot|can't|unavailable|disabled|isolated|offline|out of service|outage|"
    r"suspended|prohibited|blocked|halted|inhibited|locked|inspect\w*|maintenance|disconnected|off)\b"
)
_GRID_WORDS = re.compile(r"\b(grid|import|intake|feeder|transformer|substation|utility)\b")
_LIMIT_WORDS = re.compile(
    r"(exceed|at or below|no more than|not more than|at most|limit|cap\b|capped|maximum|max\b|below|under|ceiling|restricted)"
)
_KWH = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*k\s?wh\b")
_PCT = re.compile(r"(\d+(?:\.\d+)?)\s*(?:%|percent|per cent)")
_REDUCTION = re.compile(r"(reduction|reduced by|reduce by|cut by|cut of|lower(?:ed)? by|decrease|drop(?:s|ped)? by|down by|loss of|fall(?:s)? by)")
_FRACTIONS = [
    (r"three[- ]quarters?", 0.75), (r"two[- ]thirds?", 2 / 3), (r"one[- ]quarter|a quarter|quarter", 0.25),
    (r"one[- ]third|a third", 1 / 3), (r"one[- ]fifth|a fifth", 0.2), (r"one[- ]tenth|a tenth", 0.1),
    (r"half", 0.5),
]
_SOLAR_KW = re.compile(r"\b(solar|pv|photovoltaic|panels?|rooftop|sunlight|irradiance)\b")


def _no_op(i: int, why: str) -> dict:
    return {
        "note_index": i, "applies": False, "directive_type": "no_op",
        "structured_adjustment": None, "explanation": f"Fallback parser: {why}",
    }


def _entry(i: int, dtype: str, adj: dict, why: str) -> dict:
    return {
        "note_index": i, "applies": True, "directive_type": dtype,
        "structured_adjustment": adj, "explanation": f"Fallback parser (LLM unavailable): {why}",
    }


def _kwh(low: str) -> Optional[float]:
    if re.search(r"\bmwh\b", low):
        return None
    m = _KWH.search(low)
    return float(m.group(1).replace(",", "")) if m else None


def _solar_factor(low: str) -> Optional[float]:
    frac = None
    for rx, v in _FRACTIONS:
        if re.search(rx, low):
            frac = v
            break
    pct = _PCT.search(low)
    x = float(pct.group(1)) / 100.0 if pct else frac
    if x is None:
        if re.search(r"(no solar|zero|offline|unavailable|out of service|completely|fully)", low):
            return 0.0
        return None
    if x > 1:
        return None
    is_reduction = bool(_REDUCTION.search(low)) and not re.search(r"(to about|to roughly|leave|left|remain)", low)
    return round(1 - x if is_reduction else x, 6)


def interpret_with_rules(notes: List[str], battery_capacity_kwh: float) -> List[dict]:
    out = []
    for i, note in enumerate(notes):
        low = note.lower()
        wins = extract_windows(note)
        hours = wins[0].hours if wins else None

        def need_hours(dtype, extra=None):
            if not hours:
                return _no_op(i, "could not read a clear time window")
            adj = {"hours": hours}
            if extra:
                adj.update(extra)
            return _entry(i, dtype, adj, f"matched {dtype}")

        kwh = _kwh(low)
        if _GRID_WORDS.search(low) and _LIMIT_WORDS.search(low) and kwh is not None and not re.search(r"\b(battery|reserve)\b", low):
            out.append(need_hours("max_grid_window", {"max_grid_kwh": kwh}))
        elif re.search(r"(reserve|remain|keep|maintain|hold|retain|stored|at least)", low) and re.search(r"(battery|reserve|capacity)", low) \
                and not re.search(r"(?<!dis)charg", low) and (kwh is not None or _PCT.search(low)):
            if kwh is not None:
                val = kwh
            else:
                val = float(_PCT.search(low).group(1)) / 100.0 * battery_capacity_kwh
            out.append(need_hours("minimum_battery_reserve", {"minimum_energy_kwh": round(val, 6)}))
        elif "discharg" in low and _NEG.search(low):
            out.append(need_hours("no_discharge_window"))
        elif re.search(r"(?<!dis)charg", low) and _NEG.search(low):
            out.append(need_hours("no_charge_window"))
        elif _SOLAR_KW.search(low) and _solar_factor(low) is not None:
            out.append(need_hours("solar_reduction", {"factor": _solar_factor(low)}))
        else:
            out.append(_no_op(i, "no energy directive recognised"))
    return out
