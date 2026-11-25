#!/usr/bin/env bash
# Real-execution run on TPC-H SF10 (objective 3). Run from the repo root, with .venv present
# and Postgres running (PGHOST=/var/run/postgresql PGPORT=5432). Resumable: rerun after a stop.
# Rough time: no_index ~8-10 min, each indexed configuration ~12 min (Q9 hits the 5-min cap).
set -euo pipefail
PY=.venv/bin/python
CFG=configs/tpch_sf10_new_algos.json
CSV=benchmark_results/execution/tpch_sf10_pg18.6.csv
OLD=benchmark_results/tpch_sf10_paper_pg18.6/rep_1       # Extend, Relaxation, ...
NEW=benchmark_results/tpch_sf10_new_algos_pg18.6/rep_1   # greedy, Wii, Esc, Wii+Esc
export BUDGETS_MB=5000 REPEATS=1
mkdir -p benchmark_results/execution

# 0. Sanity: both result folders must exist.
ls "$OLD" >/dev/null && ls "$NEW" >/dev/null

# 1. Remove an incomplete no_index baseline (fewer than 19 queries) so it is rerun.
$PY - <<'PYEOF'
import csv, os
p = "benchmark_results/execution/tpch_sf10_pg18.6.csv"
if os.path.exists(p):
    rows = list(csv.reader(open(p)))
    n = sum(1 for r in rows[1:] if r[0] == "no_index")
    if 0 < n < 19:
        keep = [rows[0]] + [r for r in rows[1:] if r[0] != "no_index"]
        csv.writer(open(p, "w", newline="")).writerows(keep)
        print(f"removed partial no_index ({n} queries); it will be rerun")
PYEOF

# 2. no_index + Extend + Relaxation at 5000 MB (skips what is already complete).
ALGOS=extend,relaxation $PY scripts/execute_selected_indexes.py $CFG "$OLD" $CSV

# 3. Greedy, Wii, Esc, Wii+Esc at 5000 MB.
ALGOS=two_phase_greedy,two_phase_greedy_wii,two_phase_greedy_esc,two_phase_greedy_wii_esc \
  $PY scripts/execute_selected_indexes.py $CFG "$NEW" $CSV

# 4. Per-query table and totals (paste this output to Arijit).
$PY - <<'PYEOF'
import csv, collections
d = collections.defaultdict(dict); tot = collections.Counter(); n = collections.Counter()
for r in csv.DictReader(open("benchmark_results/execution/tpch_sf10_pg18.6.csv")):
    t = float(r["runtime_ms"]) / 1000
    d[int(r["query"])][r["algorithm"]] = t; tot[r["algorithm"]] += t; n[r["algorithm"]] += 1
algos = list(tot)
print("Q   " + "  ".join(f"{a[:14]:>14}" for a in algos))
for q in sorted(d):
    print(f"{q:<3} " + "  ".join(f"{d[q].get(a, float('nan')):14.1f}" for a in algos))
print("tot " + "  ".join(f"{tot[a]:14.0f}" for a in algos))
print("n   " + "  ".join(f"{n[a]:14d}" for a in algos), "(each must be 19; 300.0 = hit the 5-min cap)")
PYEOF

# 5. Commit the CSV (it is gitignored, so force-add) and the analysis output.
$PY scripts/analyze_results.py out/analysis || true
git add -f $CSV out/analysis 2>/dev/null || true
echo "Now: git commit -m 'Real execution results, SF10, 5000 MB' && git push"
