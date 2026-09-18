"""Black-box test of a running GridWise API (local or hosted).

Usage (from the repo root):
    python scripts/check_live.py https://cse-hackathon-xd9l.onrender.com
    python scripts/check_live.py http://localhost:8000 --burst 5

Checks: /health, three bad-input cases (expect 400), all 10 public samples
(cost + interpretation vs reference, latency), and a parallel burst with unique
notes so the LLM is really called (cache cannot hide slowness).
The sample and burst steps use your Mistral quota (10 + burst calls on a cold cache).
"""

import asyncio
import copy
import json
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "docs" / "BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json"


def p95(xs):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(0.95 * (len(xs) - 1))))] if xs else 0.0


async def post(client, base, body):
    t = time.monotonic()
    try:
        r = await client.post(base + "/optimize-energy", json=body)
        return r, time.monotonic() - t
    except Exception as e:  # noqa: BLE001
        return e, time.monotonic() - t


async def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    base = (args[0] if args else "http://localhost:8000").rstrip("/")
    burst = int(sys.argv[sys.argv.index("--burst") + 1]) if "--burst" in sys.argv else 5
    cases = json.load(open(SAMPLES, encoding="utf-8"))["cases"]
    fails = 0

    async with httpx.AsyncClient(timeout=40.0) as c:
        print(f"Target: {base}\n")

        # 1. health
        t = time.monotonic()
        try:
            r = await c.get(base + "/health")
            ok = r.status_code == 200 and r.json() == {"status": "ok"}
        except Exception as e:  # noqa: BLE001
            ok, r = False, e
        print(f"[{'PASS' if ok else 'FAIL'}] GET /health  ({time.monotonic() - t:.2f}s)")
        fails += not ok
        if not ok:
            print("   cannot reach the service:", r)
            sys.exit(1)

        # 2. bad input must be 400
        good = cases[0]["input"]
        short = copy.deepcopy(good)
        short["hours"] = short["hours"][:23]
        for name, body, raw in [
            ("malformed JSON", None, b"{bad"),
            ("missing fields", {"scenario_id": "x"}, None),
            ("23 hours", short, None),
        ]:
            if raw is not None:
                r = await c.post(base + "/optimize-energy", content=raw, headers={"content-type": "application/json"})
            else:
                r = await c.post(base + "/optimize-energy", json=body)
            ok = r.status_code == 400
            fails += not ok
            print(f"[{'PASS' if ok else 'FAIL'}] bad input: {name} -> {r.status_code} (want 400)")

        # 3. the 10 public samples, one at a time
        if "--burst-only" in sys.argv:
            cases_to_run = []
        else:
            cases_to_run = cases
        print("\nPublic samples (sequential):")
        print(f"{'case':<10} {'status':<7} {'time':>6}  {'cost':>9} {'ref':>9}  interp  note")
        lat = []
        for cs in cases_to_run:
            r, dt = await post(c, base, cs["input"])
            lat.append(dt)
            if isinstance(r, Exception) or r.status_code != 200:
                fails += 1
                print(f"{cs['id']:<10} {'ERR':<7} {dt:6.2f}  {r if isinstance(r, Exception) else r.text[:80]}")
                continue
            j = r.json()
            ref = cs["expected_output"]
            want = [(d["directive_type"], d["structured_adjustment"]) for d in ref["directive_interpretation"]]
            got = [(d["directive_type"], d["structured_adjustment"]) for d in j["directive_interpretation"]]
            cost_ok = abs(j["total_cost_bdt"] - ref["total_cost_bdt"]) <= 0.05
            interp_ok = got == want
            fallback = "rule-based" in j["plan_summary"]
            ok = cost_ok and interp_ok
            fails += not ok
            note = "USED RULE FALLBACK (LLM did not answer)" if fallback else ""
            print(f"{cs['id']:<10} {r.status_code:<7} {dt:6.2f}  {j['total_cost_bdt']:9.2f} {ref['total_cost_bdt']:9.2f}  "
                  f"{'ok' if interp_ok else 'DIFF':<6}  {note}")
        if lat:
            print(f"latency: max {max(lat):.2f}s  p95 {p95(lat):.2f}s  mean {sum(lat) / len(lat):.2f}s  (rubric: p95 <= 5s is full marks)")

        # 4. parallel burst with unique notes -> real LLM calls
        print(f"\nBurst: {burst} parallel requests with unique notes")
        base_case = cases[5]["input"]  # SAMPLE-06 (3 notes, last one is a distractor)
        bodies = []
        for i in range(burst):
            b = copy.deepcopy(base_case)
            b["scenario_id"] = f"BURST-{i}"
            b["operator_notes"][2] = f"{b['operator_notes'][2]} (ref {int(time.time())}-{i})"
            bodies.append(b)
        t0 = time.monotonic()
        res = await asyncio.gather(*[post(c, base, b) for b in bodies])
        wall = time.monotonic() - t0
        blat, bad = [], 0
        for i, (r, dt) in enumerate(res):
            blat.append(dt)
            good_resp = (not isinstance(r, Exception)) and r.status_code == 200 and abs(r.json()["total_cost_bdt"] - 34090) <= 0.05
            bad += not good_resp
            if not good_resp:
                print(f"   burst #{i} WRONG after {dt:.1f}s:")
                print("     note sent:", bodies[i]["operator_notes"][2])
                if isinstance(r, Exception):
                    print("     error:", repr(r))
                elif r.status_code != 200:
                    print("     status:", r.status_code, r.text[:200])
                else:
                    j = r.json()
                    print("     cost:", j["total_cost_bdt"], "(want 34090)")
                    for d in j["directive_interpretation"]:
                        print("     ", d["note_index"], d["directive_type"], d["structured_adjustment"], "|", d["explanation"][:90])
        fails += bad
        print(f"[{'PASS' if not bad else 'FAIL'}] {burst - bad}/{burst} correct; wall {wall:.1f}s, "
              f"max {max(blat):.1f}s, p95 {p95(blat):.1f}s (hard limit is 30s per request)")

    print("\nRESULT:", "ALL CHECKS PASSED" if not fails else f"{fails} CHECK(S) FAILED")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    asyncio.run(main())
