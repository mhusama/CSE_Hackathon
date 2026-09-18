"""Measure LLM interpretation accuracy against ground truth (the rubric's 25-point category).

Scores the same axes as the judge: relevance (applies/no_op), directive_type, hours, value.
Sources of ground truth: the 10 public sample cases + tests/data/paraphrases.json.

Usage:
    python scripts/eval_interpretation.py            # real Mistral path (needs MISTRAL_API_KEY)
    python scripts/eval_interpretation.py --rules    # emergency fallback parser only, no key needed
    python scripts/eval_interpretation.py --hard-only  # only the harder 37-note set
Exit code is 1 if any note is wrong, so it can gate a deploy.
"""

import asyncio
import json
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config import get_settings  # noqa: E402
from app.guardrails.validator import validate_directives  # noqa: E402
from app.llm.interpreter import NoteInterpreter, build_interpreter  # noqa: E402

ROOT = Path(__file__).parent.parent


def value_of(adj):
    adj = adj or {}
    return adj.get("factor", adj.get("minimum_energy_kwh", adj.get("max_grid_kwh")))


def load_items():
    """(note_text_list, capacity, [expected dicts]) per scenario."""
    items = []
    samples = json.load(open(ROOT / "docs" / "BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json"))
    for c in samples["cases"]:
        exp = [
            {"type": d["directive_type"], "hours": (d["structured_adjustment"] or {}).get("hours"),
             "value": value_of(d["structured_adjustment"])}
            for d in c["expected_output"]["directive_interpretation"]
        ]
        items.append((c["id"], c["input"]["operator_notes"], c["input"]["battery"]["capacity_kwh"], exp))
    files = [("PARA", "paraphrases.json"), ("HARD", "hard_paraphrases.json")]
    if "--hard-only" in sys.argv:
        items = []
        files = files[1:]
    for tag, fname in files:
        for k, c in enumerate(json.load(open(ROOT / "tests" / "data" / fname))["cases"]):
            items.append((f"{tag}-{k:02d}", [c["note"]], c["capacity"],
                          [{"type": c["type"], "hours": c.get("hours"), "value": c.get("value")}]))
    return items


async def main():
    use_rules = "--rules" in sys.argv
    if use_rules:
        interp = NoteInterpreter([])  # no providers -> rule parser
    else:
        s = get_settings()
        if not s.mistral_api_key:
            print("MISTRAL_API_KEY not set. Use --rules to test the fallback parser only.")
            sys.exit(2)
        from app.llm.provider import get_mistral_provider
        interp = build_interpreter(get_mistral_provider(s.llm_model))
        interp._cache = None
        print(f"Model: {s.llm_model} (fallback {s.llm_fallback_model})\n")

    sources = Counter()
    lat = []
    axes = {"relevance": [0, 0], "type": [0, 0], "hours": [0, 0], "value": [0, 0]}
    bad = 0
    for sid, notes, cap, exp in load_items():
        t0 = time.monotonic()
        res = await interp.interpret(notes, cap)
        lat.append(time.monotonic() - t0)
        sources[res.source] += 1
        got = validate_directives(res.directives, len(notes), cap)
        for i, (g, e) in enumerate(zip(got, exp)):
            gtype = g.directive_type.value
            ok = {"relevance": (gtype == "no_op") == (e["type"] == "no_op"), "type": gtype == e["type"]}
            if e["type"] != "no_op":
                gh = (g.structured_adjustment or {}).get("hours")
                ok["hours"] = gh == e["hours"]
                if e["value"] is not None:
                    gv = value_of(g.structured_adjustment)
                    ok["value"] = gv is not None and abs(gv - e["value"]) <= 0.011
            for a, v in ok.items():
                axes[a][1] += 1
                axes[a][0] += int(v)
            if not all(ok.values()):
                bad += 1
                print(f"MISS {sid} note {i} [{res.source}]: {notes[i][:90]!r}\n     got {gtype} {g.structured_adjustment}"
                      f"\n     want {e['type']} hours={e['hours']} value={e['value']}")
    print("\nWho answered (per scenario):", dict(sources))
    if not use_rules and sources.get("rules-fallback"):
        print("WARNING: some answers came from the emergency rule parser, NOT the LLM. Accuracy below is not an LLM score.")
    print("\nAccuracy per axis:")
    for a, (k, n) in axes.items():
        print(f"  {a:<10} {k}/{n}  ({100 * k / max(n, 1):.1f}%)")
    if lat:
        lat.sort()
        print(f"\nInterpretation latency per scenario: mean {sum(lat) / len(lat):.2f}s, p95 {lat[int(0.95 * (len(lat) - 1))]:.2f}s, max {lat[-1]:.2f}s")
    print(f"\n{bad} note(s) wrong.")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    asyncio.run(main())
