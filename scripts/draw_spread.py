"""Spread of the Wii experiments over independent statistics draws.

Usage: python scripts/draw_spread.py out/draw1/runs.jsonl out/draw2/runs.jsonl ...

Each file is one invocation of scripts/wii_experiments.py with a fresh ANALYZE, i.e. one
statistics draw. For every run id the table shows the mean, minimum and maximum
improvement (%) and the mean number of what-if calls over the files. A second block
shows the difference between Wii (alpha 0.9) and the budgeted baseline per draw,
which moves much less than the absolute values.
"""
import json
import sys
from collections import defaultdict


def load(path):
    with open(path) as f:
        return {r["run_id"]: r for r in map(json.loads, filter(str.strip, f))}


def main(paths):
    draws = [load(p) for p in paths]
    ids = []
    for draw in draws:
        for run_id in draw:
            if run_id not in ids:
                ids.append(run_id)
    print(f"draws: {len(draws)}; no-index cost: "
          + ", ".join(f"{next(iter(d.values()))['empty_cost']:.0f}" for d in draws))
    print(f"{'run':<42}{'n':>2} {'mean %':>8} {'min':>7} {'max':>7} {'calls':>8}")
    for run_id in ids:
        runs = [d[run_id] for d in draws if run_id in d]
        imp = [100 * r["improvement"] for r in runs]
        calls = [r["statistics"]["whatif_calls"] for r in runs]
        print(f"{run_id:<42}{len(runs):>2} {sum(imp) / len(imp):>8.2f} {min(imp):>7.2f} "
              f"{max(imp):>7.2f} {sum(calls) / len(calls):>8.0f}")
    print("\nWii (alpha 0.9, run wii_k20_b500_wii) minus baseline at budget B, per draw (points):")
    diffs = defaultdict(list)
    for draw in draws:
        wii = draw.get("wii_k20_b500_wii")
        for budget in (500, 1000, 2000):
            base = draw.get(f"wii_k20_b{budget}_none")
            if wii and base:
                diffs[budget].append(100 * (wii["improvement"] - base["improvement"]))
    for budget, values in diffs.items():
        print(f"  B={budget}: " + ", ".join(f"{v:+.2f}" for v in values))


if __name__ == "__main__":
    main(sys.argv[1:])
