"""Esc results: calls saved and improvement lost against the never-stopping reference.

Usage: python scripts/esc_summary.py <runs.jsonl> [<runs.jsonl> ...]
Several files = several repetitions; the table shows the mean over them.
"""
import json
import sys
from collections import defaultdict


def load(path):
    with open(path) as f:
        return {r["run_id"]: r for r in map(json.loads, filter(str.strip, f))}


def main(paths):
    rows = defaultdict(list)
    for path in paths:
        runs = load(path)
        refs = {}
        for run in runs.values():
            parameters = run["parameters"]
            if parameters.get("esc") == "esc_b" and parameters["esc_epsilon"] < 0:
                refs[group(parameters)] = run
        for run in runs.values():
            parameters = run["parameters"]
            if parameters.get("esc") != "esc_b" or parameters["esc_epsilon"] < 0:
                continue
            ref = refs.get(group(parameters))
            if ref is None:
                continue
            statistics = run["statistics"]
            key = group(parameters) + (parameters["esc_epsilon"],)
            rows[key].append(
                (
                    100 * (1 - statistics["whatif_calls"] / ref["statistics"]["whatif_calls"]),
                    100 * (ref["improvement"] - run["improvement"]),
                    statistics["esc_stopped_early"],
                    statistics["esc_time_s"],
                )
            )
    print("K   mode  clamp  epsilon  calls saved %  improvement lost (pts)  stopped early  check time (s)")
    for key in sorted(rows):
        values = rows[key]
        n = len(values)
        print(
            f"{key[0]:<3} {key[1]:<5} {str(key[2]):<6} {key[3]:<7} "
            f"{sum(v[0] for v in values) / n:>13.1f}  "
            f"{sum(v[1] for v in values) / n:>22.2f}  "
            f"{sum(v[2] for v in values)}/{n:<12} "
            f"{sum(v[3] for v in values) / n:.3f}"
        )


def group(parameters):
    return (
        parameters["max_indexes"],
        parameters["interception"],
        bool(parameters.get("esc_clamp", False)),
    )


if __name__ == "__main__":
    main(sys.argv[1:])
