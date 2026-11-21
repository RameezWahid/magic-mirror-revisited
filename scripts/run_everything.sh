#!/bin/bash

# Runs the Wii experiments unattended on the newest PostgreSQL (18.6, port 5418),
# then writes figures and RESULTS.txt. The 8-algorithm reproduction (queue A of
# run_all_experiments.sh) is not part of this script.
# Start it from the repo root, detached from the terminal:
#   nohup setsid scripts/run_everything.sh > benchmark_results/run_everything.log 2>&1 &
# Progress:  tail -f benchmark_results/run_everything.log benchmark_results/pipeline_B.log
# Safe to restart: complete databases and finished runs are skipped.
#
# Steps
#  1. mount check, start PostgreSQL 18.6
#  2. PG 18.6: verify TPC-H/TPC-DS SF10 databases, (re)load them otherwise; Wii
#     starts on TPC-H while TPC-DS loads (TPC-DS lock held during loading)
#  3. scripts/analyze_results.py -> $RESULTS

set -uo pipefail
cd "$(dirname "$0")/.."
PG="<folder with PostgreSQL builds, data and logs>"
RESULTS="<output folder for figures and RESULTS.txt>"
PORT=5418
say() { echo "$(date '+%F %T') $*"; }

# Keep the machine awake while this runs (if systemd-inhibit is available).
if [ -z "${INHIBITED:-}" ] && command -v systemd-inhibit > /dev/null; then
    INHIBITED=1 exec systemd-inhibit --what=sleep:idle --who=index-selection \
        --why="index selection experiments" "$0" "$@"
fi

# --- 1. storage and servers ---------------------------------------------------
if ! findmnt "<mount point of the data disk>" > /dev/null; then
    udisksctl mount -b "<partition of the data disk>" || { say "data disk not mounted"; exit 1; }
fi
$PG/pg18.6/bin/pg_ctl -D $PG/data/pg18.6 status > /dev/null \
    || $PG/pg18.6/bin/pg_ctl -D $PG/data/pg18.6 -l $PG/logs/server_pg18.6.log -w start
psqlx() { psql -h /tmp -p $PORT -X -Atq "$@"; }

# --- 2. SF10 databases on PG 18.6 and experiments ---------------------------------
complete() {  # complete <database> <raw file dir> <pattern>
    local db=$1 dir=$2 pattern=$3
    psqlx -d postgres -c "select 1 from pg_database where datname='$db'" | grep -q 1 || return 1
    psqlx -d "$db" -c "select 1 from pg_extension where extname='hypopg'" | grep -q 1 || return 1
    compgen -G "$dir/$pattern" > /dev/null && return 1  # leftover raw files: load was cut
    local tables table
    tables=$(psqlx -d "$db" -c "select relname from pg_class c join pg_namespace n
        on n.oid = c.relnamespace where n.nspname = 'public' and c.relkind = 'r'")
    [ -n "$tables" ] || return 1
    for table in $tables; do
        psqlx -d "$db" -c "select exists (select 1 from $table)" | grep -q t || return 1
    done
}

load() {  # load <database> <config> <raw file dir> <pattern> <log>
    local db=$1 config=$2 dir=$3 pattern=$4 log=$5
    if complete "$db" "$dir" "$pattern"; then
        say "$db complete"
        return
    fi
    say "$db incomplete -> loading into PG 18.6"
    psqlx -d postgres -c "drop database if exists $db"
    rm -f $dir/$pattern
    PGHOST=/tmp PGPORT=$PORT .venv/bin/python -m selection "$config" > "$log" 2>&1
    complete "$db" "$dir" "$pattern" || { say "loading $db failed, see $log"; exit 1; }
}

mkdir -p benchmark_results/load_runs $PG/locks $PG/logs/load
keep_load_results() {
    mv benchmark_results/results_*.csv benchmark_results/indexes_*.pickle \
        benchmark_results/load_runs/ 2> /dev/null || true
}

load indexselection_tpch___10 configs/tpch_sf10_load.json tpch-kit/dbgen "*.tbl" \
    $PG/logs/load/tpch_sf10_pg18.log
keep_load_results

# Hold the TPC-DS lock while loading, so the TPC-DS experiments wait for the data.
exec 9> $PG/locks/tpcds10_$PORT.lock
flock 9
say "starting Wii on PG 18.6"
LOCKS=$PG/locks scripts/run_all_experiments.sh B $PORT benchmark_results/wii_pg18 9>&- &
b=$!
load indexselection_tpcds___10 configs/tpcds_sf10_load.json tpcds-kit/tools "*.dat" \
    $PG/logs/load/tpcds_sf10_pg18.log
keep_load_results
flock -u 9
exec 9>&-
wait $b; say "Wii on PG 18.6 done"

# --- 3. analysis --------------------------------------------------------------
mkdir -p "$RESULTS"
PYTHONPATH=. .venv/bin/python scripts/analyze_results.py "$RESULTS" > /dev/null \
    && say "analysis written to $RESULTS" || say "ANALYSIS FAILED"
say "ALL DONE"
