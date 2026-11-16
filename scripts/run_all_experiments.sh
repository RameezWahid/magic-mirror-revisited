#!/bin/bash

# Runs every experiment of the project, unattended:
#   queue A (platform runs, strictly one after another because they share
#            benchmark_results/results_*.csv), all on the newest PostgreSQL
#            (18.6): TPC-H SF1 (3 repetitions), real execution on SF1, TPC-DS
#            SF10 and TPC-H SF10 reproductions.
# Jobs that use the same database hold a lock (flock), because every job starts
# with its own ANALYZE and would otherwise change another job's statistics.
#
# Usage (from the repo root): scripts/run_all_experiments.sh A
# Logs: benchmark_results/pipeline_<queue>.log

set -uo pipefail
queue=$1
locks="<folder for job lock files>"
mkdir -p "$locks"
log=benchmark_results/pipeline_$queue.log
pg18=5418  # PostgreSQL 18.6

say() { echo "$(date '+%F %T') $*" | tee -a "$log"; }

locked() {  # locked <database> <command...>
    local db=$1; shift
    say "start [$db]: $*"
    flock "$locks/$db.lock" "$@" >> "$log" 2>&1
    say "end   [$db] exit $?: $*"
}

if [ "$queue" = A ]; then
    locked tpch1_$pg18 \
        scripts/run_repetitions.sh configs/tpch_sf1_paper.json tpch_sf1_paper_pg18.6 $pg18 3
    mkdir -p benchmark_results/execution
    PGHOST=/tmp PGPORT=$pg18 PYTHONPATH=. locked tpch1_$pg18 \
        .venv/bin/python scripts/execute_selected_indexes.py configs/tpch_sf1_paper.json \
        benchmark_results/tpch_sf1_paper_pg18.6/rep_1 benchmark_results/execution/tpch_sf1_pg18.6.csv
    locked tpcds10_$pg18 \
        scripts/run_repetitions.sh configs/tpcds_sf10_paper.json tpcds_sf10_paper_pg18.6 $pg18 1
    locked tpch10_$pg18 \
        scripts/run_repetitions.sh configs/tpch_sf10_paper.json tpch_sf10_paper_pg18.6 $pg18 1
fi

say "queue $queue finished"
