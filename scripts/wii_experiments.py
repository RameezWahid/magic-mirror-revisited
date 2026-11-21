import json
import logging
import os
import sys
import time

from selection.algorithms.two_phase_greedy_algorithm import TwoPhaseGreedyAlgorithm
from selection.candidate_generation import (
    candidates_per_query,
    syntactically_relevant_indexes,
)
from selection.cost_evaluation import CostEvaluation
from selection.index_selection_evaluation import IndexSelection
from selection.workload import Workload

SETTINGS = {
    # benchmark: (budgets B for the main experiment, B for single-budget experiments,
    #             B for the "best possible" reference)
    "tpch": ([500, 1000, 2000], 1000, 5000),
    "tpcds": ([500, 1000, 2000, 5000], 5000, 20000),
}


class Runner:
    def __init__(self, config_file, out_dir):
        with open(config_file) as f:
            self.config = json.load(f)
        self.benchmark = self.config["benchmark_name"]
        self.out_dir = out_dir
        os.makedirs(out_dir, exist_ok=True)
        self.runs_file = os.path.join(out_dir, "runs.jsonl")
        self.done = set()
        if os.path.exists(self.runs_file):
            with open(self.runs_file) as f:
                self.done = {json.loads(line)["run_id"] for line in f if line.strip()}

        self.selection = IndexSelection()
        self.selection._setup_config(self.config)
        # A copy of the database, so this can run next to a platform run.
        override = os.environ.get("WII_DATABASE")
        if override:
            self.selection.setup_db_connector(override, "postgres")
        self.db = self.selection.db_connector
        self.workload = self.selection.workload
        self.db.drop_indexes()
        # VACUUM first. Otherwise autovacuum kicks in mid-experiment, changes the
        # planner's costs, and two identical runs give different answers.
        self.db.commit()
        self.db._connection.autocommit = True
        self.db.exec_only("vacuum")
        self.db._connection.autocommit = self.db.autocommit
        self.db.create_statistics()
        self.db.commit()
        self.empty_cost = self._true_cost([])
        logging.info(
            f"{self.benchmark}: {len(self.workload.queries)} queries, "
            f"no-index cost {self.empty_cost:.0f}"
        )

    # Score a result with fresh what-if calls, never with the algorithm's own
    # numbers. Those may be guesses, and a guess can make a run look better.
    def _true_cost(self, indexes):
        evaluation = CostEvaluation(self.db)
        per_query = {
            q.nr: evaluation.calculate_cost(Workload([q]), indexes, store_size=True)
            for q in self.workload.queries
        }
        size = sum(i.estimated_size or 0 for i in indexes)
        evaluation.complete_cost_estimation()
        self.last_per_query = per_query
        self.last_size = size
        return sum(per_query.values())

    # One tuning run, written as one line of runs.jsonl. Runs already in the
    # file are skipped, so a crash costs one run, not the whole experiment.
    def run(self, run_id, parameters):
        if run_id in self.done:
            return
        algorithm = TwoPhaseGreedyAlgorithm(self.db, dict(parameters))
        start = time.time()
        indexes = algorithm.calculate_best_indexes(self.workload)
        runtime = time.time() - start
        cost = self._true_cost(indexes)
        record = {
            "run_id": run_id,
            "benchmark": self.benchmark,
            "scale_factor": self.config["scale_factor"],
            "queries": len(self.workload.queries),
            "parameters": parameters,
            # Whatever the algorithm added to its parameters: calls, skips, times.
            "statistics": {
                k: v for k, v in algorithm.parameters.items() if k not in parameters
            },
            "runtime_s": round(runtime, 3),
            "cost": cost,
            "empty_cost": self.empty_cost,
            "improvement": 1 - cost / self.empty_cost,
            "per_query_cost": self.last_per_query,
            "storage_bytes": self.last_size,
            "indexes": [str(i) for i in indexes],
            "itc": algorithm.search.curve,
        }
        if parameters.get("record_gaps"):
            record["gaps"] = algorithm.oracle.gaps
        with open(self.runs_file, "a") as f:
            f.write(json.dumps(record, default=str) + "\n")
        self.done.add(run_id)
        logging.info(
            f"{run_id}: improvement {100 * record['improvement']:.2f}%, "
            f"calls {record['statistics']['whatif_calls']}, "
            f"skipped {record['statistics']['skipped_calls']}, {runtime:.1f}s"
        )

    # Main result (Wii Fig. 6): every mode at every budget, K = 10 and 20.
    # The largest budget is the reference; coverage isn't run there.
    def wii(self):
        budgets, _, best_budget = SETTINGS[self.benchmark]
        for k in (10, 20):
            for budget in budgets + [best_budget]:
                for mode in ("none", "wii", "wii_coverage"):
                    if budget == best_budget and mode == "wii_coverage":
                        continue
                    self.run(
                        f"wii_k{k}_b{budget}_{mode}",
                        {"max_indexes": k, "whatif_budget": budget, "interception": mode},
                    )

    # How much does alpha, the "close enough to skip" line, matter? (Fig. 9/10)
    def alpha(self):
        _, budget, _ = SETTINGS[self.benchmark]
        for mode in ("wii", "wii_coverage"):
            for alpha in (0.5, 0.8, 0.9, 0.95):
                self.run(
                    f"alpha_k20_b{budget}_{mode}_{alpha}",
                    {
                        "max_indexes": 20,
                        "whatif_budget": budget,
                        "interception": mode,
                        "alpha": alpha,
                    },
                )

    # Random skipping at the same rate, to compare against Wii (Fig. 13). If Wii
    # can't beat this, its bounds are useless.
    def random(self):
        _, budget, _ = SETTINGS[self.benchmark]
        for seed in range(3):
            self.run(
                f"random_k20_b{budget}_seed{seed}",
                {
                    "max_indexes": 20,
                    "whatif_budget": budget,
                    "interception": "random",
                    "skip_probability": 0.9,
                    "seed": seed,
                },
            )

    # For every real call, record how far off the derived guess was (Fig. 2).
    # If it's usually close, many calls are a waste. That's why Wii works.
    def gaps(self):
        _, budget, _ = SETTINGS[self.benchmark]
        self.run(
            f"gaps_k20_b{budget}_none",
            {
                "max_indexes": 20,
                "whatif_budget": budget,
                "interception": "none",
                "record_gaps": True,
            },
        )

    # No budget (Table 4): does Wii keep the quality, and how much time does it save?
    def unlimited(self):
        self.run("unlimited_k20_none", {"max_indexes": 20, "interception": "none"})
        for alpha in (0.8, 0.9):
            self.run(
                f"unlimited_k20_wii_{alpha}",
                {"max_indexes": 20, "interception": "wii", "alpha": alpha},
            )

    def assumptions(self):
        """Does PostgreSQL's cost model behave as Wii assumes? (Algorithm 6)"""
        path = os.path.join(self.out_dir, "assumptions.json")
        if os.path.exists(path):
            return
        candidates = candidates_per_query(
            self.workload, 2, syntactically_relevant_indexes
        )
        evaluation = CostEvaluation(self.db)
        mono, sub = [], []
        start = time.time()
        for query, query_candidates in zip(self.workload.queries, candidates):
            one = Workload([query])
            empty = evaluation.calculate_cost(one, [])
            single = {z: evaluation.calculate_cost(one, [z]) for z in query_candidates}
            if not single:
                continue
            # Take the best single index, then pair it with every other one.
            # Each pair gives one data point for each of the two checks.
            first = min(single, key=single.get)
            for z in query_candidates:
                if z == first:
                    continue
                pair = evaluation.calculate_cost(one, [first, z])
                mono.append((query.nr, empty, single[z], pair, single[first]))
                sub.append((query.nr, empty, single[z], single[first], pair))
        evaluation.complete_cost_estimation()
        result = {
            "benchmark": self.benchmark,
            "queries": len(self.workload.queries),
            "seconds": time.time() - start,
            # (query, c(empty), c({z}), c({x,z}), c({x}))
            "monotonicity_points": mono,
            # (query, c(empty), c({z}), c({x}), c({x,z}))
            "submodularity_points": sub,
        }
        with open(path, "w") as f:
            json.dump(result, f)
        logging.info(f"assumption data points: {len(mono)}")


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    config_file, out_dir, experiments = sys.argv[1], sys.argv[2], sys.argv[3:]
    runner = Runner(config_file, out_dir)
    for experiment in experiments:
        logging.info(f"=== experiment {experiment}")
        getattr(runner, experiment)()


if __name__ == "__main__":
    main()
