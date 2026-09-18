"""Small deterministic time-window reader.

NOT the interpreter. It has two narrow jobs:
  1. cross-check the hours the LLM returned (a mismatch triggers a retry with feedback)
  2. power the emergency fallback parser (app/llm/rules.py) when the LLM is down
Windows are start-inclusive, end-exclusive: "1 PM to 3 PM" -> [13, 14].
"""

import re
from dataclasses import dataclass
from typing import List, Optional

_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}
_WORD_RE = "|".join(_WORDS)
_AP = r"(?:[ap]\.?\s?m\.?)"

# one time expression; groups: num / min / ap  OR  word / wap  OR  special
_TIME = (
    r"(?:(?P<{p}special>noon|midnight)"
    r"|(?P<{p}num>\d{{1,2}})(?::(?P<{p}min>\d{{2}}))?(?:\s*(?P<{p}ap>" + _AP + r"))?(?!\d)(?!\s*(?:kwh|kw|%|percent))"
    r"|(?P<{p}word>" + _WORD_RE + r")(?:\s*(?P<{p}wap>" + _AP + r"))?)"
)
_SEP = r"(?:\s*(?:to|until|till|through|thru|-|–|—)\s*)"

_RANGE_RE = re.compile(r"\b" + _TIME.format(p="a_") + _SEP + _TIME.format(p="b_") + r"(?![\d:])", re.I)
_BETWEEN_RE = re.compile(
    r"\bbetween\s+" + _TIME.format(p="a_") + r"\s+and\s+" + _TIME.format(p="b_") + r"(?![\d:])", re.I
)


@dataclass
class Window:
    hours: List[int]
    confident: bool  # False when am/pm had to be guessed


def _clean_ap(ap: Optional[str]) -> Optional[str]:
    if not ap:
        return None
    return "pm" if ap.lower().startswith("p") else "am"


def _parse_side(m: "re.Match", p: str):
    """Return (hour, ap, is_midnight, is_24h_clock)."""
    special = m.group(p + "special")
    if special:
        special = special.lower()
        if special == "noon":
            return 12, "pm", False, False
        return 0, "am", True, False
    if m.group(p + "num") is not None:
        h = int(m.group(p + "num"))
        mins = m.group(p + "min")
        ap = _clean_ap(m.group(p + "ap"))
        is24 = bool(mins) and ap is None and (h >= 13 or h == 0 or mins is not None)
        if h > 23:
            return None
        if mins is not None and ap is None:
            # "13:00" style is a 24h clock; "1:00" without am/pm stays ambiguous
            is24 = h >= 13 or h == 0 or len(m.group(p + "num")) == 2 and m.group(p + "num").startswith("0")
        if ap is None and h >= 13:
            is24 = True
        return h, ap, False, is24
    if m.group(p + "word"):
        return _WORDS[m.group(p + "word").lower()], _clean_ap(m.group(p + "wap")), False, False
    return None


def _to24(h: int, ap: Optional[str]) -> int:
    if ap == "am":
        return 0 if h == 12 else h
    if ap == "pm":
        return 12 if h == 12 else h + 12
    return h


def _window_from(m: "re.Match") -> Optional[Window]:
    a = _parse_side(m, "a_")
    b = _parse_side(m, "b_")
    if a is None or b is None:
        return None
    ah, aap, a_mid, a_24 = a
    bh, bap, b_mid, b_24 = b
    confident = True

    if b_mid:
        end = 24
        bap_final = "am"
    else:
        end = None
        bap_final = bap

    # start
    if a_mid:
        start = 0
    elif a_24 or (aap is None and ah >= 13):
        start = ah
    elif aap:
        start = _to24(ah, aap)
    else:
        start = None

    if end is None:
        if b_24 or (bap is None and bh >= 13):
            end = bh
        elif bap:
            end = _to24(bh, bap)

    # inherit am/pm from the other side
    if start is None and end is not None:
        cand = _to24(ah, "pm" if end >= 12 and not b_mid else "am")
        alt = _to24(ah, "am" if end >= 12 else "pm")
        start = cand if cand < end else alt
        if not (start < end):
            start = alt
    elif end is None and start is not None:
        cand = _to24(bh, "pm" if start >= 12 else "am")
        end = cand if cand > start else _to24(bh, "am" if start >= 12 else "pm")
    elif start is None and end is None:
        # no am/pm anywhere ("one until three"): office-hours guess, low confidence
        confident = False
        if 1 <= ah <= 6:
            start, end = _to24(ah, "pm"), _to24(bh, "pm")
        elif ah == 12:
            start, end = 12, _to24(bh, "pm")
        else:
            start, end = ah, bh if bh > ah else _to24(bh, "pm")

    if start is None or end is None or not (0 <= start <= 23) or not (0 <= end <= 24):
        return None
    if start == end:
        return None
    if end > start:
        hours = list(range(start, end))
    else:  # crosses midnight, e.g. 10 PM to 2 AM
        hours = sorted(list(range(start, 24)) + list(range(0, end)))
    return Window(hours=hours, confident=confident)


def extract_windows(text: str) -> List[Window]:
    """All time windows found in the text, in reading order, without duplicates."""
    found = []
    spans = []
    for rx in (_BETWEEN_RE, _RANGE_RE):
        for m in rx.finditer(text):
            if any(m.start() < e and s < m.end() for s, e in spans):
                continue
            w = _window_from(m)
            if w:
                spans.append((m.start(), m.end()))
                found.append((m.start(), w))
    found.sort(key=lambda x: x[0])
    return [w for _, w in found]
