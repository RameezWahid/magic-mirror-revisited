import logging
import time

from selection.budget_aware import (
    BudgetAwareCostOracle,
    TwoPhaseGreedy,
    candidate_column_counts,
    log_summary,
    table_row_counts,
)
from selection.esc import EarlyStoppingChecker
from selection.candidate_generation import (
    candidates_per_query,
    syntactically_relevant_indexes,
)
from selection.selection_algorithm import DEFAULT_PARAMETER_VALUES, SelectionAlgorithm
from selection.utils import mb_to_b

# max_indexes: cardinality constraint K.
# whatif_budget: budget B on the number of what-if calls (None = unlimited).
# interception: "none" (baseline), "wii", "wii_coverage", or "random".
# budget_MB: optional storage constraint (None = only K).
DEFAULT_PARAMETERS = {
    "max_indexes": 20,
    "whatif_budget": None,
    "interception": "none",
    "alpha": 0.9,
    "skip_probability": 0.9,
    "seed": 0,
    "budget_MB": None,
    "max_index_width": DEFAULT_PARAMETER_VALUES["max_index_width"],
    "record_gaps": False,
    # Esc early stopping: "none" or "esc_b"; epsilon is the tolerated loss of
    # improvement (0.05 = 5 percentage points). esc_clamp: see EarlyStoppingChecker.
    "esc": "none",
    "esc_epsilon": 0.05,
    "esc_clamp": False,
    # Storage-aware (fractional knapsack) upper bound; false = the paper's count-only
    # bound. Only meant for the ablation in configs/tpch_sf10_esc_ablation.json.
    "esc_storage_bound": True,
}


# Two-phase greedy with a what-if budget, plus Wii, as a platform algorithm.
# The algorithm itself is in selection/budget_aware.py.
class TwoPhaseGreedyAlgorithm(SelectionAlgorithm):
    def __init__(self, database_connector, parameters=None):
        if parameters is None:
            parameters = {}
        SelectionAlgorithm.__init__(
            self, database_connector, parameters, DEFAULT_PARAMETERS
        )
        self.oracle = None
        self.search = None

    def _calculate_best_indexes(self, workload):
        start = time.time()
        parameters = self.parameters
        # Candidates per query: its own columns, combined up to max_index_width wide.
        candidates = candidates_per_query(
            workload, parameters["max_index_width"], syntactically_relevant_indexes
        )
        # Every cost question goes through the oracle. It counts the calls and
        # enforces the budget.
        self.oracle = BudgetAwareCostOracle(
            self.cost_evaluation,
            workload,
            budget=parameters["whatif_budget"],
            interception=parameters["interception"],
            alpha=parameters["alpha"],
            skip_probability=parameters["skip_probability"],
            seed=parameters["seed"],
            record_gaps=parameters["record_gaps"],
            table_rows=table_row_counts(self.database_connector),
            need_omega=parameters["esc"] != "none",
        )
        # Column counts for the coverage refinement. Only coverage uses them, but
        # they're cheap: no optimizer calls.
        self.oracle.candidate_counts = candidate_column_counts(candidates)
        # Starting costs (Algorithm 1). These calls come out of the budget too.
        self.oracle.initialize(candidates)
        storage = None
        if parameters["budget_MB"] is not None:
            storage = mb_to_b(parameters["budget_MB"])
        self.search = TwoPhaseGreedy(
            self.oracle,
            candidates,
            parameters["max_indexes"],
            storage_budget=storage,
            index_size=self._index_size,
        )
        if parameters["esc"] == "esc_b":
            self.search.checker = EarlyStoppingChecker(
                self.oracle,
                parameters["max_indexes"],
                epsilon=parameters["esc_epsilon"],
                clamp_omega=parameters["esc_clamp"],
                storage_budget=storage,
                index_size=self._index_size,
                storage_bound=parameters["esc_storage_bound"],
            )
        indexes = self.search.run()
        log_summary(self.oracle, self.search)
        self._store_statistics(time.time() - start)
        return list(indexes)

    # Only estimate an index's size when we need it, and keep it on the index.
    def _index_size(self, index):
        if index.estimated_size is None:
            self.cost_evaluation.estimate_size(index)
        return index.estimated_size

    # Anything put in self.parameters lands in the results CSV. That's how the
    # call counts get to analyze_results.py.
    def _store_statistics(self, runtime):
        oracle = self.oracle
        self.parameters.update(
            {
                "whatif_calls": oracle.calls,
                "whatif_calls_init": oracle.calls_init,
                "phase1_calls": self.search.phase1_calls,
                "phase2_candidates": self.search.phase2_candidates,
                "skipped_calls": oracle.skipped,
                "derived_after_budget": oracle.derived_after_budget,
                "whatif_time_s": round(oracle.whatif_time, 3),
                "bound_time_s": round(oracle.bound_time, 4),
                "total_time_s": round(runtime, 3),
            }
        )
        checker = self.search.checker
        if checker is not None:
            self.parameters.update(
                {
                    "esc_stopped_early": self.search.stopped_early,
                    "esc_checks": checker.checks,
                    "esc_violations": checker.violations,
                    "esc_time_s": round(checker.time, 4),
                    "esc_trace": checker.trace,
                }
            )
        logging.debug(f"Two-phase greedy statistics: {self.parameters}")
