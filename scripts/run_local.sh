#!/bin/bash

# Runs experiments of the project, unattended. Every platform run goes through
# scripts/run_repetitions.sh. Jobs that use the same database hold a lock
# (flock), because every job starts with its own ANALYZE and would otherwise
# change another job's statistics.
#
# Queues (from the repo root):
#   scripts/run_all_experiments.sh tpch10   only the TPC-H SF10 reproduction
#   scripts/run_all_experiments.sh A        everything: TPC-H SF1 (3 repetitions),
#                                           real execution on SF1, TPC-DS SF10, TPC-H SF10
#
# Settings (environment variables; defaults suit a default Ubuntu install):
#   PGHOST     socket directory or host of the cluster   (default /var/run/postgresql)
#   PGPORT     port of the cluster                       (default 5432)
#   REPS       repetitions for queue tpch10              (default 3)
#   LOCKS_DIR  folder for the per-database lock files    (default /tmp/index_selection_locks)
#
# Logs: benchmark_results/pipeline_<queue>.log
# Results: benchmark_results/<run name>/rep_<i>/

set -uo pipefail
queue=${1:?usage: scripts/run_all_experiments.sh <tpch10|A>}

PGHOST=${PGHOST:-/var/run/postgresql}
PGPORT=${PGPORT:-5432}
REPS=${REPS:-3}
locks=${LOCKS_DIR:-/tmp/index_selection_locks}
export PGHOST PGPORT

mkdir -p "$locks" benchmark_results
log=benchmark_results/pipeline_$queue.log

say() { echo "$(date '+%F %T') $*" | tee -a "$log"; }

case "$queue" in
    tpch10) configs=(configs/tpch_sf10_paper.json) ;;
    A) configs=(configs/tpch_sf1_paper.json configs/tpcds_sf10_paper.json configs/tpch_sf10_paper.json) ;;
    *) echo "unknown queue: $queue (use tpch10 or A)" >&2; exit 1 ;;
esac

# Fail now, not hours into a run.
preflight() {
    local ok=1
    fail() { echo "preflight: $*" >&2; ok=0; }

    [ -x .venv/bin/python ] || fail ".venv/bin/python not found (run from the repo root, venv must be named .venv)"
    command -v psql > /dev/null || fail "psql not on PATH"
    command -v dexter > /dev/null || fail "dexter not on PATH (needed by the dexter algorithm)"

    if psql -h "$PGHOST" -p "$PGPORT" -d postgres -Atc "select 1" > /dev/null 2>&1; then
        local n
        n=$(psql -h "$PGHOST" -p "$PGPORT" -d postgres -Atc \
            "select count(*) from pg_available_extensions where name = 'hypopg'")
        [ "$n" = 1 ] || fail "extension hypopg is not installed in this cluster"
    else
        fail "cannot connect to PostgreSQL (host=$PGHOST port=$PGPORT)"
    fi

    if compgen -G "benchmark_results/results_*.csv" > /dev/null \
        || compgen -G "benchmark_results/indexes_*.pickle" > /dev/null; then
        fail "move benchmark_results/results_*.csv and indexes_*.pickle out of the way first"
    fi

    if grep -q '"solver": "gurobi"' "${configs[@]}" 2> /dev/null; then
        # A pip-only gurobipy has a size-limited license: it imports fine but fails on
        # models over 2000 variables, i.e. in the middle of a CoPhy run.
        local gout
        if ! gout=$(.venv/bin/python -c "
import gurobipy as g
print('gurobipy', '.'.join(map(str, g.gurobi.version())))
m = g.Model()
m.addVars(2100, name='x'); m.optimize()
" 2>&1); then
            fail "Gurobi check failed. Last lines of its output:"
            echo "$gout" | tail -6 | sed 's/^/    /' >&2
            echo "    (license file: gurobi.lic in your home folder, or the path in GRB_LICENSE_FILE; its major version must match gurobipy's; or set \"solver\": \"highs\" in the configs)" >&2
        fi
    fi
    return $((1 - ok))
}

preflight || { say "preflight failed, nothing was started"; exit 1; }

pgver=$(psql -h "$PGHOST" -p "$PGPORT" -d postgres -Atc "show server_version" | awk '{print $1}')
say "queue $queue: PostgreSQL $pgver at host=$PGHOST port=$PGPORT"

locked() {  # locked <database> <command...>
    local db=$1; shift
    say "start [$db]: $*"
    flock "$locks/$db.lock" "$@" >> "$log" 2>&1
    say "end   [$db] exit $?: $*"
}

if [ "$queue" = tpch10 ]; then
    locked tpch10_$PGPORT \
        scripts/run_repetitions.sh configs/tpch_sf10_paper.json "tpch_sf10_paper_pg$pgver" "$PGPORT" "$REPS" "$PGHOST"
fi

if [ "$queue" = A ]; then
    locked tpch1_$PGPORT \
        scripts/run_repetitions.sh configs/tpch_sf1_paper.json "tpch_sf1_paper_pg$pgver" "$PGPORT" 3 "$PGHOST"
    mkdir -p benchmark_results/execution
    PYTHONPATH=. locked tpch1_$PGPORT \
        .venv/bin/python scripts/execute_selected_indexes.py configs/tpch_sf1_paper.json \
        "benchmark_results/tpch_sf1_paper_pg$pgver/rep_1" "benchmark_results/execution/tpch_sf1_pg$pgver.csv"
    locked tpcds10_$PGPORT \
        scripts/run_repetitions.sh configs/tpcds_sf10_paper.json "tpcds_sf10_paper_pg$pgver" "$PGPORT" 1 "$PGHOST"
    locked tpch10_$PGPORT \
        scripts/run_repetitions.sh configs/tpch_sf10_paper.json "tpch_sf10_paper_pg$pgver" "$PGPORT" 1 "$PGHOST"
fi

say "queue $queue finished"
