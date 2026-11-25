#!/usr/bin/env bash
# Show the latest progress of the TPC-H SF10 real-execution run.
# Usage: bash scripts/show_logs.sh [number_of_lines]   (default 15)
N=${1:-15}
cd "$(dirname "$0")/.." || exit 1
LOG=benchmark_results/logs/real_execution_tpch_sf10.log
CSV=benchmark_results/real_execution_tpch_sf10.csv
TOTAL=39

echo "=== Time now: $(date '+%F %T') ==="
if pgrep -f execute_budget_runs.py >/dev/null; then
  echo "Status: RUNNING (PID $(pgrep -f execute_budget_runs.py | head -1))"
else
  echo "Status: NOT running (finished or stopped)"
fi

if [ -f "$CSV" ]; then
  DONE=$(tail -n +2 "$CSV" | cut -d, -f1,2 | sort -u | wc -l)
  echo "Configs done: $DONE / $TOTAL"
  echo "Configs finished so far:"
  tail -n +2 "$CSV" | cut -d, -f1,2 | sort -u | sed 's/,/  setting=/; s/^/  - /'
fi

echo
echo "=== Last $N lines of $LOG ==="
tail -n "$N" "$LOG"
