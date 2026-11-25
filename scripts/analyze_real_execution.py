"""Summarise real-execution results per algorithm and budget.

Usage: python scripts/analyze_real_execution.py [csv]
"""
import ast
import csv
import sys
from collections import defaultdict

path = sys.argv[1] if len(sys.argv) > 1 else "benchmark_results/real_execution_tpch_sf10.csv"
TIMEOUT = 300_000
runs = defaultdict(list)
for r in csv.DictReader(open(path)):
    runs[(r["algorithm"], r["setting"])].append(r)

def summary(rows):
    times = [ast.literal_eval(r["runtimes_ms"]) for r in rows]
    return {
        "n": int(rows[0]["indexes"]),
        "est": sum(float(r["estimated_cost"]) for r in rows),
        "real": sum(float(r["runtime_ms"]) for r in rows) / 1000,
        "timeouts": sum(1 for r in rows if float(r["runtime_ms"]) >= TIMEOUT),
        "build": float(rows[0]["index_build_s"]),
        "spread": max((max(t) - min(t)) / 1000 for t in times),
        "per_q": {r["query"]: float(r["runtime_ms"]) / 1000 for r in rows},
    }

S = {k: summary(v) for k, v in runs.items()}
base = S[("no_index", "-")]
print(f"No index: real {base['real']:.0f} s (timeouts {base['timeouts']}), est {base['est']:,.0f}\n")
names = {"extend": "Extend", "gadis_w1": "GADIS w=1", "gadis_w2": "GADIS w=2", "relaxation": "Relaxation",
         "anytime": "Anytime", "cophy": "CoPhy", "db2advis": "DB2Advis", "two_phase_greedy": "Greedy (TPG)",
         "two_phase_greedy_wii": "TPG+Wii", "two_phase_greedy_esc": "TPG+Esc", "two_phase_greedy_wii_esc": "TPG+Wii+Esc",
         "auto_admin": "AutoAdmin", "drop": "Drop", "dexter": "Dexter"}
for setting in ["1500MB", "5000MB", "10000MB"]:
    rows = sorted(((names[a], s) for (a, st), s in S.items() if st == setting), key=lambda x: x[1]["real"])
    print(f"## Budget {setting}  (sorted by real runtime)")
    print("| Rank | Algorithm | #Idx | Real runtime s | Real % of no-index | Est. cost % | Timeouts | Build s |")
    print("|---|---|---|---|---|---|---|---|")
    for i, (n, s) in enumerate(rows, 1):
        print(f"| {i} | {n} | {s['n']} | {s['real']:.0f} | {s['real']/base['real']*100:.1f}% | "
              f"{s['est']/base['est']*100:.1f}% | {s['timeouts']} | {s['build']:.0f} |")
    print()
print("## No budget (index count / threshold)")
print("| Algorithm | Setting | #Idx | Real s | Real % | Est % | Timeouts | Build s |")
print("|---|---|---|---|---|---|---|---|")
for (a, st), s in sorted(S.items()):
    if st.endswith("MB") or a == "no_index":
        continue
    print(f"| {names[a]} | {st} | {s['n']} | {s['real']:.0f} | {s['real']/base['real']*100:.1f}% | "
          f"{s['est']/base['est']*100:.1f}% | {s['timeouts']} | {s['build']:.0f} |")
print("\n## Per-query real runtime (s) of no-index, and timeouts")
print({q: round(t, 1) for q, t in base["per_q"].items()})
print("\n## Largest run-to-run spread (s) per config (sleep check)")
for k, s in sorted(S.items(), key=lambda x: -x[1]["spread"])[:6]:
    print(k, round(s["spread"], 1))
