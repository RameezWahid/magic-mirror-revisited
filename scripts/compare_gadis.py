"""Compare GADIS against Extend per storage budget.

Usage: python scripts/compare_gadis.py <benchmark> <#queries> [scale_factor] [results_dir]
e.g.   python scripts/compare_gadis.py tpch 19 10
Relative cost = estimated workload cost / estimated cost without indexes.
"""

import csv
import json
import re
import sys


def load(path, scale_factor):
    csv.field_size_limit(sys.maxsize)
    with open(path) as f:
        rows = list(csv.reader(f, delimiter=";"))
    header = rows[0]
    queries = [c for c in header if re.fullmatch(r"q\d+[a-z]?|\d+[a-z]?", c)]
    out = []
    for row in rows[1:]:
        d = dict(zip(header, row))
        if scale_factor is not None and d["scale factor"] != str(scale_factor):
            continue
        parameters = json.loads(d["parameters"].replace("True", "true").replace("False", "false"))
        cost = sum(json.loads(d[q])["Cost"] for q in queries)
        out.append(
            {
                "budget": parameters.get("budget_MB"),
                "width": parameters.get("max_index_width"),
                "cost": cost,
                "runtime": float(d["algorithm runtime"]),
                "size_mb": int(d["memory consumption"]) / 1e6,
                "requests": int(d["cost requests"]),
            }
        )
    return out


def main():
    benchmark, n_queries = sys.argv[1], sys.argv[2]
    scale_factor = sys.argv[3] if len(sys.argv) > 3 else None
    directory = sys.argv[4] if len(sys.argv) > 4 else "benchmark_results"
    name = f"{directory}/results_{{}}_{benchmark}_{n_queries}_queries.csv"

    base = load(name.format("no_index"), scale_factor)[-1]["cost"]
    extend = {r["budget"]: r for r in load(name.format("extend"), scale_factor)}
    gadis = load(name.format("gadis"), scale_factor)
    variants = sorted({r["width"] for r in gadis})
    by_width = {w: {r["budget"]: r for r in gadis if r["width"] == w} for w in variants}

    titles = ["Extend"] + [f"GADIS w={w}" for w in variants]
    print(f"{benchmark} ({n_queries} queries), no-index cost {base:,.0f}")
    print("| Budget (MB) | " + " | ".join(titles) + " |")
    print("|---" * (len(titles) + 1) + "|")
    for budget in sorted(extend):
        cells = []
        candidates = [extend.get(budget)] + [by_width[w].get(budget) for w in variants]
        best = min(r["cost"] for r in candidates if r)
        for r in candidates:
            if r is None:
                cells.append("—")
                continue
            value = f"{r['cost'] / base * 100:.1f}%"
            if abs(r["cost"] - best) < 1e-6:
                value = f"**{value}**"
            cells.append(f"{value} · {r['runtime']:.1f}s")
        print(f"| {budget} | " + " | ".join(cells) + " |")


if __name__ == "__main__":
    main()
