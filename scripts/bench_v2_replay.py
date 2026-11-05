#!/usr/bin/env python3
"""Deterministic MODEL-BASED replay comparing V1 and V2 slot policies.

IMPORTANT: this simulates scheduling/slot policy with fixed, stated assumptions (task durations drawn from a
seeded RNG, fixed per-child overhead). It does NOT run workers or models, so it can NOT prove the Plan V2
performance gates (Heavy median -20%, PI p95 <= baseline). Those need real replay/Practice Match data.
What it does show: how the policy change (PI-only reserve, Heavy off the PI reserve, children limited to
normal capacity) shifts PI wait time and what a Heavy DAG could gain under limited slots.

    python scripts/bench_v2_replay.py --seed 20261105 --runs 200 --out docs/V2_BENCHMARK_MODEL.json
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from mtm_competition.tasks.heavy_plan import HeavyPlan, critical_path  # noqa: E402

PLAN = {
    "planVersion": 1, "parentBacklogItemKey": "CR-05", "parentAgentId": "heavy-CR-05", "baseRevision": "x", "deadlineEpoch": 1,
    "subtasks": [
        {"subtaskId": "A", "description": "a", "dependsOn": [], "ownedPaths": ["a/"], "expectedOutputs": [], "estimatedSeconds": 180},
        {"subtaskId": "B", "description": "b", "dependsOn": [], "ownedPaths": ["b/"], "expectedOutputs": [], "estimatedSeconds": 210},
        {"subtaskId": "C", "description": "c", "dependsOn": ["A", "B"], "ownedPaths": ["c/"], "expectedOutputs": [], "estimatedSeconds": 120},
    ],
}
TOTAL_SLOTS, NORMAL, RESERVED = 5, 3, 2
PI_ARRIVAL = 100.0


def pctl(values, q):
    v = sorted(values)
    return v[min(len(v) - 1, int(round(q * (len(v) - 1))))]


def pi_wait(policy: str, rng: random.Random) -> float:
    """Seconds from PI reveal to start. Heavy items are running when the PI arrives (V1: in reserved slots)."""
    normals_busy_until = [PI_ARRIVAL + rng.uniform(150, 600) for _ in range(NORMAL)]
    heavy_busy_until = [PI_ARRIVAL + rng.uniform(100, 700) for _ in range(min(RESERVED, rng.choice([0, 1, 2])))]
    if policy == "v1":
        # Reserved slots shared with Heavy; Normal could also borrow an idle reserved slot near a deadline.
        reserved_free_at = [PI_ARRIVAL] * RESERVED
        for i, t in enumerate(heavy_busy_until):
            reserved_free_at[i] = t
        if rng.random() < 0.3:  # an imminent-deadline Normal task borrowed an idle reserved slot
            idle = [i for i, t in enumerate(reserved_free_at) if t <= PI_ARRIVAL]
            if idle:
                reserved_free_at[idle[0]] = PI_ARRIVAL + rng.uniform(100, 400)
        start = min(reserved_free_at)
    else:
        start = PI_ARRIVAL  # PI-only reserve is never occupied by Normal/Heavy
    return max(0.0, start - PI_ARRIVAL)


def heavy_makespan(policy: str, rng: random.Random, overhead: float = 30.0) -> dict:
    plan = HeavyPlan.from_dict(PLAN)
    sequential = sum(s["estimatedSeconds"] for s in PLAN["subtasks"])
    if policy == "v1":
        return {"seconds": float(sequential), "children": 0}  # one worker does everything
    others_busy = rng.choice([0, 1, 2])
    free_for_children = max(0, NORMAL - 1 - others_busy)  # parent takes one normal slot
    width = max(1, free_for_children)
    cp = critical_path(plan)["seconds"]
    est = [s["estimatedSeconds"] for s in PLAN["subtasks"]]
    if width == 1:
        # degraded: no parallel children, still pays parent planning overhead
        return {"seconds": float(sequential) + overhead, "children": 0, "degraded": True}
    parallel = max(cp, sum(est) / width) + overhead * len(est)
    return {"seconds": min(parallel, float(sequential) + overhead), "children": width, "degraded": False}


def run(seed: int, runs: int) -> dict:
    out = {"model": "slot-policy model, NOT measured execution", "seed": seed, "runs": runs,
           "assumptions": {"slots": TOTAL_SLOTS, "normal": NORMAL, "reserved": RESERVED, "perChildOverheadSeconds": 30,
                           "piArrivalSeconds": PI_ARRIVAL}}
    for policy in ("v1", "v2"):
        rng = random.Random(f"{seed}:{policy}")
        waits = [pi_wait(policy, rng) for _ in range(runs)]
        rng2 = random.Random(f"{seed}:heavy:{policy}")
        heavy = [heavy_makespan(policy, rng2) for _ in range(runs)]
        secs = [h["seconds"] for h in heavy]
        out[policy] = {
            "piWaitSeconds": {"median": statistics.median(waits), "p95": pctl(waits, 0.95), "max": max(waits)},
            "heavySeconds": {"median": statistics.median(secs), "p95": pctl(secs, 0.95)},
            "heavyDegradedRate": sum(1 for h in heavy if h.get("degraded")) / runs,
        }
    v1h, v2h = out["v1"]["heavySeconds"]["median"], out["v2"]["heavySeconds"]["median"]
    out["comparison"] = {
        "piWaitP95_v2_not_worse": out["v2"]["piWaitSeconds"]["p95"] <= out["v1"]["piWaitSeconds"]["p95"],
        "heavyMedianReductionPercent": round(100.0 * (v1h - v2h) / v1h, 2),
        "meets20PercentHeavyTarget_IN_MODEL": (v1h - v2h) / v1h >= 0.20,
        "caveat": "Model only. Real acceptance needs measured end-to-end replay and Practice Match data (Plan 14).",
    }
    return out


def main() -> int:
    global NORMAL, RESERVED, TOTAL_SLOTS
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=20261105)
    ap.add_argument("--runs", type=int, default=200)
    ap.add_argument("--out", default="")
    ap.add_argument("--normal", type=int, default=3, help="Normal+Heavy slots (sensitivity analysis)")
    ap.add_argument("--reserved", type=int, default=2, help="PI-only reserved slots (>=1)")
    a = ap.parse_args()
    NORMAL, RESERVED = a.normal, max(1, a.reserved)
    TOTAL_SLOTS = NORMAL + RESERVED
    res = run(a.seed, a.runs)
    text = json.dumps(res, indent=2, sort_keys=True)
    if a.out:
        Path(a.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
