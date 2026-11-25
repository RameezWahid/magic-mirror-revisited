"""Greedy / Wii / Esc / Wii+Esc over repetitions (run_repetitions.sh output).

Usage: python scripts/compare_new_algos.py <run dir> [benchmark=tpch] [#queries=19]
"""
import ast
import csv
import glob
import json
import re
import statistics as st
import sys

csv.field_size_limit(sys.maxsize)
D = sys.argv[1]
BENCH = sys.argv[2] if len(sys.argv) > 2 else "tpch"
NQ = sys.argv[3] if len(sys.argv) > 3 else "19"


def lit(s):
    return ast.literal_eval(s.replace("null", "None").replace("true", "True").replace("false", "False"))


def load(rep, a):
    rows = list(csv.DictReader(open(f"{rep}/results_{a}_{BENCH}_{NQ}_queries.csv"), delimiter=";"))
    qs = [c for c in rows[0] if re.fullmatch(r"q\d+|\d+[a-z]\.sql", c)]
    return [(lit(r["parameters"]), sum(json.loads(r[q])["Cost"] for q in qs),
             int(r["memory consumption"]) / 1e6, float(r["algorithm runtime"])) for r in rows]


def name(p):
    return ("Wii" if p.get("interception") == "wii" else "Greedy") + ("+Esc" if p.get("esc", "none") != "none" else "")


reps = sorted(glob.glob(f"{D}/rep_*"))
data, base = {}, {}
for rep in reps:
    base[rep] = load(rep, "no_index")[0][1]
    for p, c, s, t in load(rep, "two_phase_greedy"):
        data.setdefault((name(p), p["budget_MB"]), []).append(
            {"rel": c / base[rep] * 100, "size": s, "t": t, "calls": p.get("whatif_calls"),
             "stop": p.get("esc_stopped_early"), "budget": p["budget_MB"]})
print(f"{len(reps)} repetitions; no-index cost per repetition:", ", ".join(f"{v:,.0f}" for v in base.values()))
V = ["Greedy", "Wii", "Greedy+Esc", "Wii+Esc"]
print("\nRelative estimated cost % (mean [min-max] over repetitions) and what-if calls (mean)\n")
print("| Budget MB | " + " | ".join(V) + " |")
print("|---" * (len(V) + 1) + "|")
for b in sorted({k[1] for k in data}):
    cells = []
    for v in V:
        x = data.get((v, b), [])
        if not x:
            cells.append("—")
            continue
        r = [e["rel"] for e in x]
        cells.append(f"{st.mean(r):.1f} [{min(r):.1f}–{max(r):.1f}] · {st.mean(e['calls'] for e in x):,.0f}")
    print(f"| {b} | " + " | ".join(cells) + " |")
viol = [(k, e["size"]) for k, x in data.items() for e in x if e["size"] > e["budget"] + 1e-6]
print(f"\nRuns over the storage budget: {len(viol)}", viol[:5])
for v in ("Greedy+Esc", "Wii+Esc"):
    stops = [e["stop"] for (vv, b), x in data.items() if vv == v for e in x]
    print(f"{v}: esc_stopped_early = {sum(1 for s in stops if s)} of {len(stops)} runs")
for v in V:
    print(f"{v}: total runtime {sum(e['t'] for (vv, b), x in data.items() if vv == v for e in x) / len(reps):.1f} s per repetition")
