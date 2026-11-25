"""Optimizer-estimated cost of all 15 algorithm variants on TPC-H SF10, merged from
the separate runs. Each run's costs are divided by that run's own no-index cost
(ANALYZE is re-run per run, so the baseline differs by <0.2%).

Usage: python scripts/compare_optimizer_all.py [--csv out.csv]
"""
import csv
import glob
import json
import re
import statistics
import sys
from collections import defaultdict

csv.field_size_limit(sys.maxsize)
R = "benchmark_results"
# (directory, algorithm file, filter on parameters, label)
SOURCES = [
    (f"{R}/gadis_tpch_sf10_run1", "extend", lambda p: True, "Extend"),
    (f"{R}/gadis_tpch_sf10_run1", "gadis", lambda p: p.get("max_index_width") == 1, "GADIS w=1"),
    (f"{R}/gadis_tpch_sf10_run1", "gadis", lambda p: p.get("max_index_width") == 2, "GADIS w=2"),
    (f"{R}/tpch_sf10_all_partial", "relaxation", lambda p: True, "Relaxation"),
    (f"{R}/tpch_sf10_all_partial", "anytime", lambda p: True, "Anytime"),
    (R, "anytime", lambda p: True, "Anytime"),
    (R, "cophy", lambda p: True, "CoPhy"),
    (R, "db2advis", lambda p: True, "DB2Advis"),
    (R, "auto_admin", lambda p: True, "AutoAdmin"),
    (R, "drop", lambda p: True, "Drop"),
    (R, "dexter", lambda p: True, "Dexter"),
]
for rep in (1, 2, 3):
    d = f"{R}/tpch_sf10_new_algos/rep_{rep}"
    for wii in (False, True):
        for esc in (False, True):
            name = "TPG" + ("+Wii" if wii else "") + ("+Esc" if esc else "")
            SOURCES.append((d, "two_phase_greedy",
                            lambda p, wii=wii, esc=esc: (p.get("interception") == "wii") == wii
                            and (p.get("esc", "none") != "none") == esc, name))


def params(s):
    s = s.replace("True", "true").replace("False", "false").replace("None", "null")
    return json.loads(s.replace("(", "[").replace(")", "]"))


def load(directory, algo):
    rows = list(csv.DictReader(open(f"{directory}/results_{algo}_tpch_19_queries.csv"), delimiter=";"))
    qs = [c for c in rows[0] if re.fullmatch(r"q\d+", c)]
    return [(params(r["parameters"]), sum(json.loads(r[q])["Cost"] for q in qs), r) for r in rows
            if r["scale factor"] == "10"]


def base(directory):
    return load(directory, "no_index")[-1][1]


data = defaultdict(list)  # (label, setting) -> list of dicts
for directory, algo, keep, label in SOURCES:
    b = base(directory)
    for p, cost, r in load(directory, algo):
        if not keep(p):
            continue
        setting = p.get("budget_MB") or p.get("max_indexes") or p.get("min_saving_percentage")
        data[(label, setting)].append({"rel": cost / b * 100, "size": int(r["memory consumption"]) / 1e6,
                                       "n": int(r["#indexes"]), "rt": float(r["algorithm runtime"]),
                                       "req": int(r["cost requests"])})


def agg(lst):
    return {k: statistics.mean(x[k] for x in lst) for k in lst[0]}


A = {k: agg(v) for k, v in data.items()}
budget_algos = ["Extend", "GADIS w=1", "GADIS w=2", "Relaxation", "Anytime", "CoPhy", "DB2Advis",
                "TPG", "TPG+Wii", "TPG+Esc", "TPG+Wii+Esc"]
budgets = sorted({s for (l, s) in A if l in budget_algos})
short = {"Relaxation": "Relax", "DB2Advis": "DB2Adv", "TPG+Wii+Esc": "TPG+W+E"}
print("### Relative estimated cost (% of no-index), lower is better; * = best at that budget\n")
print("| Budget MB | " + " | ".join(short.get(a, a) for a in budget_algos) + " |")
print("|---" * (len(budget_algos) + 1) + "|")
wins = defaultdict(int)
for b in budgets:
    vals = {a: A[(a, b)]["rel"] for a in budget_algos if (a, b) in A}
    best = min(vals.values())
    cells = []
    for a in budget_algos:
        if a not in vals:
            cells.append("—")
        elif vals[a] - best < 0.05:
            wins[a] += 1
            cells.append(f"**{vals[a]:.1f}***")
        else:
            cells.append(f"{vals[a]:.1f}")
    print(f"| {b} | " + " | ".join(cells) + " |")

print("\n### Summary per algorithm (16 budgets)\n")
print("| Algorithm | Times best (±0.05) | Mean rel. cost | Mean rank | Total runtime | Max runtime | Cost requests |")
print("|---|---|---|---|---|---|---|")
ranks = defaultdict(list)
for b in budgets:
    order = sorted((A[(a, b)]["rel"], a) for a in budget_algos if (a, b) in A)
    for i, (_, a) in enumerate(order, 1):
        ranks[a].append(i)
for a in sorted(budget_algos, key=lambda a: statistics.mean(ranks[a])):
    rs = [A[(a, b)] for b in budgets if (a, b) in A]
    print(f"| {a} | {wins[a]} | {statistics.mean(r['rel'] for r in rs):.1f}% | {statistics.mean(ranks[a]):.1f} | "
          f"{sum(r['rt'] for r in rs):.0f} s | {max(r['rt'] for r in rs):.1f} s | {sum(r['req'] for r in rs):,.0f} |")

print("\n### Algorithms without a storage budget\n")
print("| Algorithm | Setting | #Idx | Size MB | Rel. cost | Runtime |")
print("|---|---|---|---|---|---|")
for a, key in [("AutoAdmin", "max_indexes"), ("Drop", "max_indexes"), ("Dexter", "min_saving_%")]:
    for (l, s), r in sorted(((k, v) for k, v in A.items() if k[0] == a), key=lambda x: x[0][1]):
        print(f"| {a} | {key}={s} | {r['n']:.0f} | {r['size']:,.0f} | {r['rel']:.1f}% | {r['rt']:.1f} s |")

if "--csv" in sys.argv:
    out = sys.argv[sys.argv.index("--csv") + 1]
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["algorithm", "setting", "rel_cost_pct", "size_mb", "n_indexes", "runtime_s", "cost_requests", "repetitions"])
        for (l, s), r in sorted(A.items(), key=lambda x: (x[0][0], x[0][1])):
            w.writerow([l, s, round(r["rel"], 3), round(r["size"], 1), round(r["n"], 1), round(r["rt"], 2), round(r["req"]), len(data[(l, s)])])
    print("\nwritten", out)
