"""Compare all algorithms of one run (optimizer-estimated costs).

Usage: python scripts/compare_all.py <benchmark> <#queries> [scale_factor] [results_dir]
e.g.   python scripts/compare_all.py tpch 19 10
"""

import csv
import glob
import json
import os
import re
import sys

csv.field_size_limit(sys.maxsize)


def label(name, p):
    if name == "two_phase_greedy":
        return "TPG+Wii" if p.get("interception") == "wii" else "TPG"
    if name == "gadis":
        return f"GADIS w={p.get('max_index_width')}"
    return {
        "extend": "Extend", "relaxation": "Relaxation", "anytime": "Anytime",
        "cophy": "CoPhy", "db2advis": "DB2Advis", "auto_admin": "AutoAdmin",
        "drop": "Drop", "dexter": "Dexter", "no_index": "No index",
    }.get(name, name)


def load(directory, benchmark, n_queries, sf):
    runs = []
    pattern = f"{directory}/results_*_{benchmark}_{n_queries}_queries.csv"
    for path in sorted(glob.glob(pattern)):
        name = re.match(rf"results_(.*)_{benchmark}_", os.path.basename(path))[1]
        with open(path) as f:
            rows = list(csv.DictReader(f, delimiter=";"))
        queries = [c for c in rows[0] if re.fullmatch(r"q\d+|\d+[a-z]\.sql", c)] if rows else []
        for r in rows:
            if sf is not None and r["scale factor"] != str(sf):
                continue
            p = json.loads(r["parameters"].replace("True", "true").replace("False", "false"))
            runs.append({
                "algo": label(name, p), "p": p,
                "cost": sum(json.loads(r[q])["Cost"] for q in queries),
                "runtime": float(r["algorithm runtime"]),
                "size": int(r["memory consumption"]) / 1e6,
                "n": int(r["#indexes"]),
                "requests": int(r["cost requests"]),
            })
    return runs


def main():
    benchmark, n_queries = sys.argv[1], sys.argv[2]
    sf = sys.argv[3] if len(sys.argv) > 3 else None
    directory = sys.argv[4] if len(sys.argv) > 4 else "benchmark_results"
    runs = load(directory, benchmark, n_queries, sf)
    base = [r for r in runs if r["algo"] == "No index"][-1]["cost"]

    order = ["Extend", "GADIS w=1", "GADIS w=2", "Relaxation", "Anytime", "CoPhy",
             "DB2Advis", "TPG", "TPG+Wii"]
    by_budget = {}
    for r in runs:
        if "budget_MB" in r["p"] and r["p"]["budget_MB"] is not None:
            by_budget.setdefault(r["p"]["budget_MB"], {})[r["algo"]] = r
    algos = [a for a in order if any(a in v for v in by_budget.values())]

    print(f"### {benchmark} SF{sf}: relative estimated cost (% of no-index cost {base:,.0f}); best per budget in bold\n")
    print("| Budget MB | " + " | ".join(algos) + " |")
    print("|---" * (len(algos) + 1) + "|")
    for b in sorted(by_budget):
        row = by_budget[b]
        best = min(row[a]["cost"] for a in algos if a in row)
        cells = []
        for a in algos:
            if a not in row:
                cells.append("—")
                continue
            v = f"{row[a]['cost'] / base * 100:.1f}"
            cells.append(f"**{v}**" if abs(row[a]["cost"] - best) < 1e-6 else v)
        print(f"| {b} | " + " | ".join(cells) + " |")

    print("\n### Total algorithm runtime and cost requests (all budgets/settings)\n")
    print("| Algorithm | Runs | Total runtime | Max runtime | Cost requests |")
    print("|---|---|---|---|---|")
    for a in algos + ["AutoAdmin", "Drop", "Dexter"]:
        rs = [r for r in runs if r["algo"] == a]
        if rs:
            t = sum(r["runtime"] for r in rs)
            print(f"| {a} | {len(rs)} | {t / 60:.1f} min | {max(r['runtime'] for r in rs):.1f} s | "
                  f"{sum(r['requests'] for r in rs):,} |")

    print("\n### Algorithms without a storage budget (size used, relative cost)\n")
    print("| Algorithm | Setting | #Indexes | Size MB | Rel. cost |")
    print("|---|---|---|---|---|")
    for a, key in [("AutoAdmin", "max_indexes"), ("Drop", "max_indexes"), ("Dexter", "min_saving_percentage")]:
        for r in sorted((r for r in runs if r["algo"] == a), key=lambda r: r["p"].get(key)):
            print(f"| {a} | {key}={r['p'].get(key)} | {r['n']} | {r['size']:,.0f} | {r['cost'] / base * 100:.1f}% |")


if __name__ == "__main__":
    main()
