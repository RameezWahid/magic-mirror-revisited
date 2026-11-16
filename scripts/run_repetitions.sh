#!/bin/bash

# Runs a config several times. Every run performs a fresh ANALYZE (see
# selection/index_selection_evaluation.py), whose sampling is not deterministic, so the
# repetitions show how much the results vary with PostgreSQL's statistics.
#
# Results of repetition i are moved to benchmark_results/<run name>/rep_<i>/, together
# with the run's log, query plans, and a snapshot of the statistics (pg_stats).
#
# Usage (from the repo root):
#   scripts/run_repetitions.sh <config> <run name> <port> [repetitions=5] [host=/tmp]

set -euo pipefail

config=$1
name=$2
port=$3
repetitions=${4:-5}
host=${5:-/tmp}

out="benchmark_results/$name"
if [ -e "$out" ]; then
    echo "$out already exists" >&2
    exit 1
fi
# Results are appended to these files, so they must not exist before a run
if compgen -G "benchmark_results/results_*.csv" > /dev/null \
    || compgen -G "benchmark_results/indexes_*.pickle" > /dev/null; then
    echo "Move the existing benchmark_results/results_*.csv and indexes_*.pickle files first" >&2
    exit 1
fi

database=$(.venv/bin/python -c "
import json, sys
config = json.load(open(sys.argv[1]))
print(f\"indexselection_{config['benchmark_name']}___\" + str(config['scale_factor']).replace('.', '_'))
" "$config")

mkdir -p "$out" benchmark_results/plans
cp "$config" "$out/config.json"
{
    echo "date: $(date --iso-8601=seconds)"
    echo "host: $host, port: $port, database: $database"
    psql -h "$host" -p "$port" -d postgres -Atc "select version()"
    echo "hypopg: $(psql -h "$host" -p "$port" -d "$database" -Atc \
        "select extversion from pg_extension where extname = 'hypopg'")"
    echo "dexter: $(dexter --version 2>&1)"
} > "$out/environment.txt"

for i in $(seq 1 "$repetitions"); do
    rep="$out/rep_$i"
    mkdir -p "$rep/plans"
    touch "$rep/.start"
    echo "Repetition $i / $repetitions"

    PGHOST="$host" PGPORT="$port" .venv/bin/python -m selection "$config" > "$rep/run.log" 2>&1

    mv benchmark_results/results_*.csv "$rep/"
    # Selected indexes per algorithm and parameter set (needed for actual execution)
    mv benchmark_results/indexes_*.pickle "$rep/" 2> /dev/null || true
    find benchmark_results/plans -maxdepth 1 -name "*.json" -newer "$rep/.start" \
        -exec mv {} "$rep/plans/" \;
    psql -h "$host" -p "$port" -d "$database" -c "\copy (
        select tablename, attname, null_frac, avg_width, n_distinct,
            most_common_vals::text, most_common_freqs::text,
            histogram_bounds::text, correlation
        from pg_stats where schemaname = 'public'
        order by tablename, attname) to '$rep/pg_stats.csv' csv header" > /dev/null
    rm "$rep/.start"
done
