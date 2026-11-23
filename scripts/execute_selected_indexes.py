# Run the queries for real, with the indexes the algorithms picked.
#
# Every algorithm in this platform is graded on the optimizer's guess of how
# fast the queries would be. It never actually runs anything. This script does: it
# builds the chosen indexes for real, runs every query, and times it.

# WARNING: this drops EVERY index in the database, before and after each run.


import ast
import json
import csv
import glob
import logging
import os
import pickle
import re
import statistics
import sys
import time

from selection.index_selection_evaluation import IndexSelection


REPEATS = 3
# 5 minutes. A query that takes longer is killed and written down as 5 minutes.
TIMEOUT_MS = 300_000

# Running every algorithm at every budget for real would take a lot of time. So we only
# run a small and a large setting of each:
#   storage budgets in MB  (most algorithms)
#   number of indexes      (AutoAdmin, Drop)
#   min. saving in %       (Dexter)
BUDGETS_MB = {250, 1000}
INDEX_COUNTS = {3, 10}
DEXTER_THRESHOLDS = {10}


# The platform doesn't write one pickle file per run. It keeps appending to the
# same file, one pickle per run. So we keep reading until we hit the end.
def load_pickles(path):
    runs = []
    with open(path, "rb") as f:
        while True:
            try:
                runs.append(pickle.load(f))
            except EOFError:
                return runs


def parse_parameters(text):
    text = text.replace("null", "None").replace("true", "True").replace("false", "False")
    return ast.literal_eval(text)


# Find the runs we want to execute, and hand back (algorithm, setting, indexes).
#
# The CSV says what each run was (algorithm, budget). The pickle file says which
# indexes it picked. Row 1 goes with pickle 1, row 2 with pickle 2, and so on.
# If the counts don't match, a run died half-way and we can't tell which
# indexes belong to which row. Then we stop.
def selected_runs(rep_dir):
    for path in sorted(glob.glob(os.path.join(rep_dir, "results_*.csv"))):
        name = re.search(r"results_(.*)_(tpch|tpcds)_", os.path.basename(path))[1]
        rows = list(csv.DictReader(open(path), delimiter=";"))
        pickles = load_pickles(path.replace("results_", "indexes_").replace(".csv", ".pickle"))
        assert len(rows) == len(pickles), path
        for row, indexes in zip(rows, pickles):
            p = parse_parameters(row["parameters"])
            label = name
            # Two-phase greedy with and without Wii and Esc end up in the same
            # file. Give each variant its own name so they don't get mixed up.
            if name == "two_phase_greedy":
                label += "_wii" if p.get("interception") == "wii" else ""
                label += "_esc" if p.get("esc", "none") != "none" else ""
            if name == "no_index":
                # No indexes at all. Everything else is compared against this.
                yield label, "-", indexes
            elif p.get("budget_MB") in BUDGETS_MB:
                yield label, f"{p['budget_MB']}MB", indexes
            elif "budget_MB" not in p and p.get("max_indexes") in INDEX_COUNTS:
                yield label, f"{p['max_indexes']}idx", indexes
            elif p.get("min_saving_percentage") in DEXTER_THRESHOLDS:
                yield label, f"{p['min_saving_percentage']}%", indexes


# For each run: drop all indexes, build the ones it picked, then run and time
# every query. Write the times down, drop the indexes again, next run.
#
# We also ask the optimizer for its estimate while the real indexes exist.
def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    config_file, rep_dir, out_csv = sys.argv[1:4]

    # Let the platform set up the database connection and the queries, instead
    # of doing it all again here.
    selection = IndexSelection()
    selection._setup_config(json.load(open(config_file)))
    db = selection.db_connector
    workload = selection.workload

    # If the script was stopped half-way, don't redo what's already in the file.
    done = set()
    if os.path.exists(out_csv):
        done = {(r["algorithm"], r["setting"]) for r in csv.DictReader(open(out_csv))}
    new_file = not os.path.exists(out_csv)

    with open(out_csv, "a", newline="") as f:
        writer = csv.writer(f)
        if new_file:
            writer.writerow(
                ["algorithm", "setting", "indexes", "query", "estimated_cost",
                 "runtime_ms", "runtimes_ms", "index_build_s"]
            )
        for algorithm, setting, indexes in selected_runs(rep_dir):
            if (algorithm, setting) in done:
                continue

            # Start clean. Leftover indexes from the last run would make
            # this run look faster than it is.
            db.drop_indexes()
            db.commit()

            start = time.time()
            for index in indexes:
                db.create_index(index)
            db.commit()
            build = time.time() - start
            logging.info(f"{algorithm} {setting}: {len(indexes)} indexes in {build:.0f}s")

            for query in workload.queries:
                # The optimizer's guess. It's in "cost units", not milliseconds,
                # so compare it relatively
                estimated = db.get_cost(query)
                db.exec_query(query, timeout=TIMEOUT_MS)
                times = []
                for _ in range(REPEATS):
                    runtime, _ = db.exec_query(query, timeout=TIMEOUT_MS)
                    times.append(runtime if runtime is not None else TIMEOUT_MS)
                writer.writerow(
                    [algorithm, setting, len(indexes), query.nr, estimated,
                     statistics.median(times), times, round(build, 1)]
                )
                f.flush()

            db.drop_indexes()
            db.commit()


if __name__ == "__main__":
    main()
