import logging
import math
import random
import time

from selection.workload import Workload

INTERCEPTION_MODES = ("none", "wii", "wii_coverage", "random")


class BudgetAwareCostOracle:
    """Answers "what does query q cost with indexes C?" using at most `budget`
    what-if calls. Out of budget, or skipped by Wii: returns the derived cost."""

    def __init__(
        self,
        cost_evaluation,
        workload,
        budget=None,
        interception="none",
        alpha=0.9,
        skip_probability=0.9,
        seed=0,
        record_gaps=False,
        table_rows=None,
        need_omega=False,
    ):
        assert interception in INTERCEPTION_MODES, interception
        self.cost_evaluation = cost_evaluation
        self.workload = workload
        self.budget = budget  # None: unlimited
        self.interception = interception
        self.alpha = alpha
        self.skip_probability = skip_probability
        self.random = random.Random(seed)
        self.record_gaps = record_gaps
        self.table_rows = table_rows or {}
        # Esc's bounds need c(q, Omega_q) too, also when Wii itself is off.
        self.need_omega = need_omega

        self.query_columns = {q: set(q.columns) for q in workload.queries}
        # Every cost we already paid for, per query. Asking twice is free.
        self.known = {q: {} for q in workload.queries}
        self.empty_cost = {}
        self.omega_cost = {}
        self.omega_indexes = {}
        # (query, index) -> the most that index can still save. u_global holds for
        # any configuration; u_context only inside the current greedy run.
        self.u_global = {}
        self.u_context = {}

        self.calls = 0
        self.calls_init = 0
        self.skipped = 0
        self.derived_after_budget = 0
        self.bound_time = 0.0
        self.whatif_time = 0.0
        self.gaps = []  # (derived cost before the call, what-if cost)

        self._feature_cache = {}
        self._query_vectors = {}

    def remaining(self):
        if self.budget is None:
            return math.inf
        return self.budget - self.calls

    # An index on columns the query never touches can't change its plan. Drop
    # those, so the same question never gets paid for twice.
    def relevant(self, query, configuration):
        columns = self.query_columns[query]
        return frozenset(
            index
            for index in configuration
            if any(column in columns for column in index.columns)
        )

    def _whatif(self, query, configuration):
        start = time.time()
        cost = self.cost_evaluation.calculate_cost(Workload([query]), configuration)
        self.whatif_time += time.time() - start
        self.calls += 1
        self.known[query][frozenset(configuration)] = cost
        return cost

    # Best guess without asking: the cheapest known cost of any subset. More
    # indexes shouldn't make a query slower, so the real cost is at most this.
    def derived_cost(self, query, configuration):
        best = self.empty_cost[query]
        for subset, cost in self.known[query].items():
            if cost < best and subset <= configuration:
                best = cost
        return best

    def known_cost(self, query, configuration):
        return self.known[query].get(configuration)

    # The most this index can still save for this query. Take the tightest
    # bound we have.
    def mci_bound(self, query, index):
        key = (query, index)
        return min(
            self.u_global.get(key, math.inf),
            self.u_context.get(key, math.inf),
            self._initial_bound(query),
        )

    # Procedure-1 bounds only hold inside one greedy run. Start each run clean.
    def begin_greedy(self):
        self.u_context = {}

    # Before we know anything: one index can't save more than all candidates
    # together (Omega). Without Omega, the whole no-index cost.
    def _initial_bound(self, query):
        if query in self.omega_cost:
            return self.empty_cost[query] - self.omega_cost[query]
        return self.empty_cost[query]

    def initialize(self, candidates_per_query):
        """c(q, empty) for every query; for Wii also c(q, Omega_q), Omega_q
        (Algorithm 1 of Wii).

        These calls count against the budget.
        """
        for query in self.workload.queries:
            self.empty_cost[query] = self._whatif(query, frozenset())
        # Wii also wants the cost with every candidate at once. No subset gets
        # cheaper than that, so it's the floor for all of Wii's bounds.
        if self.interception in ("wii", "wii_coverage") or self.need_omega:
            for query, candidates in zip(self.workload.queries, candidates_per_query):
                if not candidates:
                    self.omega_cost[query] = self.empty_cost[query]
                    self.omega_indexes[query] = frozenset()
                    continue
                start = time.time()
                utilized, cost = self.cost_evaluation.which_indexes_utilized_and_cost(
                    query, candidates
                )
                self.whatif_time += time.time() - start
                self.calls += 1
                self.omega_cost[query] = cost
                self.omega_indexes[query] = frozenset(utilized)
                self.known[query][frozenset(candidates)] = cost
        self.calls_init = self.calls

    # --- Wii (Algorithm 2) ------------------------------------------------------

    def eval_cost(self, query, configuration, subset=frozenset()):
        """Cost of query with configuration. `subset` is what greedy has selected
        so far; its cost is known, and the lower bound starts from it."""
        configuration = self.relevant(query, configuration)
        subset = self.relevant(query, subset)
        cost = self.known_cost(query, configuration)
        # Already know it. Free.
        if cost is not None:
            self._update_bounds(query, configuration, subset)
            return cost
        # Out of budget: guess instead of asking.
        if self.remaining() <= 0:
            self.derived_after_budget += 1
            return self.derived_cost(query, configuration)

        # Wii: the real cost is somewhere between lower and upper. If those are
        # close, asking can't tell us much. Skip it and use the upper bound.
        upper = self.derived_cost(query, configuration)
        if self.interception in ("wii", "wii_coverage"):
            start = time.time()
            lower = self.lower_bound(query, configuration, subset)
            self.bound_time += time.time() - start
            if upper > 0 and lower / upper >= self.alpha:
                self.skipped += 1
                return upper
        # Skip at random instead: the baseline Wii is compared against. Only here
        # to show Wii's choice beats luck.
        elif self.interception == "random":
            if self.random.random() < self.skip_probability:
                self.skipped += 1
                return upper

        cost = self._whatif(query, configuration)
        # For the "gaps" experiment: how far off the guess would have been.
        if self.record_gaps:
            self.gaps.append((upper, cost))
        self._update_bounds(query, configuration, subset)
        return cost

    # The lowest the cost can possibly be: the known cost of the selection so far,
    # minus the most each new index could save. Never below Omega's cost.
    def lower_bound(self, query, configuration, subset):
        base = self.known_cost(query, subset)
        if base is None:
            subset, base = frozenset(), self.empty_cost[query]
        improvement = sum(
            self._effective_bound(query, index) for index in configuration - subset
        )
        return max(0.0, self.omega_cost.get(query, 0.0), base - improvement)

    def _update_bounds(self, query, configuration, subset):
        cost = self.known_cost(query, configuration)
        if cost is None:
            return
        # What a known configuration saves in total caps what each of its indexes
        # can save. True for any configuration (Definition 5).
        improvement = max(0.0, self.empty_cost[query] - cost)
        for index in configuration:
            key = (query, index)
            self.u_global[key] = min(self.u_global.get(key, math.inf), improvement)
        # Same, measured from greedy's current selection (Procedure 1).
        base = self.known_cost(query, subset)
        if subset and base is not None:
            improvement = max(0.0, base - cost)
            for index in configuration - subset:
                key = (query, index)
                self.u_context[key] = min(self.u_context.get(key, math.inf), improvement)

    # --- coverage refinement (Wii, Section 4.2) ---------------------------------

    # Coverage: an index that covers only part of the columns Omega's indexes
    # use can't get all of Omega's gain. Scale that gain down accordingly.
    def _effective_bound(self, query, index):
        bound = self.mci_bound(query, index)
        if self.interception != "wii_coverage":
            return bound
        # We paid for this index on its own already, so its bound is exact.
        if frozenset([index]) in self.known[query]:
            return bound
        coverage = self.coverage(query, index)
        omega_gain = self.empty_cost[query] - self.omega_cost[query]
        return min(bound, coverage * omega_gain)

    def index_vector(self, index):
        # j-th key column gets weight 1 / 2^(j-1)
        return {column: 1 / 2**position for position, column in enumerate(index.columns)}

    def configuration_vector(self, configuration):
        vector = {}
        for index in configuration:
            for column, weight in self.index_vector(index).items():
                vector[column] = max(vector.get(column, 0.0), weight)
        return vector

    def query_vector(self, query):
        """Column weights: table size times the number of candidates using the column."""
        if query not in self._query_vectors:
            counts = getattr(self, "candidate_counts", {})
            max_rows = max(self.table_rows.values()) if self.table_rows else 1
            max_count = max(counts.values()) if counts else 1
            vector = {}
            for column in query.columns:
                rows = self.table_rows.get(column.table.name, max_rows)
                vector[column] = (rows / max_rows) * (counts.get(column, 1) / max_count)
            self._query_vectors[query] = vector
        return self._query_vectors[query]

    # How much of what Omega's indexes do for this query this one index covers,
    # from 0 (nothing) to 1 (all of it).
    def coverage(self, query, index):
        key = (query, index)
        if key not in self._feature_cache:
            q_vector = self.query_vector(query)
            z = {
                column: weight * q_vector.get(column, 0.0)
                for column, weight in self.index_vector(index).items()
            }
            omega = {
                column: weight * q_vector.get(column, 0.0)
                for column, weight in self.configuration_vector(
                    self.omega_indexes.get(query, frozenset())
                ).items()
            }
            norm = sum(value * value for value in omega.values())
            if norm == 0:
                value = 1.0  # no information: keep the plain bound
            else:
                dot = sum(weight * omega.get(column, 0.0) for column, weight in z.items())
                value = min(1.0, max(0.0, dot / norm))
            self._feature_cache[key] = value
        return self._feature_cache[key]


class TwoPhaseGreedy:
    """Budget-aware two-phase greedy search (Algorithms 3 and 4 of Wii)."""

    def __init__(
        self,
        oracle,
        candidates_per_query,
        max_indexes,
        storage_budget=None,
        index_size=None,
    ):
        self.oracle = oracle
        self.candidates_per_query = candidates_per_query
        self.max_indexes = max_indexes
        self.storage_budget = storage_budget
        self.index_size = index_size
        self.curve = []  # (calls, estimated workload cost of the best configuration)
        self.phase1_calls = None
        self.phase2_candidates = 0
        self.checker = None  # optional EarlyStoppingChecker, used in phase 2
        self.stopped_early = False

    def _size(self, configuration):
        return sum(self.index_size(index) for index in configuration)

    def greedy(self, queries, candidates, phase):
        oracle = self.oracle
        oracle.begin_greedy()
        selected = frozenset()
        per_query = {q: oracle.eval_cost(q, selected) for q in queries}
        best_cost = sum(per_query.values())
        # Sorted, so ties are broken the same way on every run.
        candidates = sorted(set(candidates))
        # A new index only changes the cost of queries that use its columns.
        # Only those get re-costed; the rest keep their cost.
        relevant = {
            index: [q for q in queries if oracle.relevant(q, [index])]
            for index in candidates
        }
        # Each round, add the one index that lowers the total cost most.
        # Stop at K indexes, or when nothing helps any more.
        while len(selected) < self.max_indexes:
            # Esc: stop tuning if the best configuration found so far is close
            # enough to the best one that continuing could possibly reach.
            if phase == 2 and self.checker is not None:
                stop = self.checker.check(selected, candidates)
                if stop is not None:
                    self.stopped_early = True
                    return stop
            step_best, step_cost, step_per_query = None, best_cost, None
            used = self._size(selected) if self.storage_budget is not None else 0
            for index in candidates:
                if index in selected:
                    continue
                if (
                    self.storage_budget is not None
                    and used + self.index_size(index) > self.storage_budget
                ):
                    continue
                configuration = selected | {index}
                costs = dict(per_query)
                for query in relevant[index]:
                    costs[query] = oracle.eval_cost(query, configuration, selected)
                cost = sum(costs.values())
                if cost < step_cost:
                    step_best, step_cost, step_per_query = index, cost, costs
            if step_best is None:
                break
            selected = selected | {step_best}
            best_cost, per_query = step_cost, step_per_query
            if phase == 2:
                self.curve.append((oracle.calls, best_cost))
        return selected

    def run(self):
        oracle = self.oracle
        workload_candidates = set()
        # Phase 1: best indexes for each query on its own. Their union is the
        # short list for phase 2.
        for query, candidates in zip(oracle.workload.queries, self.candidates_per_query):
            workload_candidates |= self.greedy([query], candidates, phase=1)
        self.phase1_calls = oracle.calls
        self.phase2_candidates = len(workload_candidates)
        # Tuning curve starts at "no indexes".
        self.curve.append((oracle.calls, sum(oracle.empty_cost.values())))
        # Phase 2: greedy over the whole workload, using only phase 1's picks.
        return self.greedy(oracle.workload.queries, workload_candidates, phase=2)


# Table sizes from the planner's statistics. Coverage weighs columns of big
# tables more.
def table_row_counts(db_connector):
    rows = db_connector.exec_fetch(
        "select c.relname, c.reltuples from pg_class c join pg_namespace n "
        "on n.oid = c.relnamespace where n.nspname = 'public' and c.relkind = 'r'",
        one=False,
    )
    return {name: max(1.0, float(tuples)) for name, tuples in rows}


# How many candidate indexes use each column. Coverage weighs popular columns more.
def candidate_column_counts(candidates_per_query):
    counts = {}
    for index in set().union(*map(set, candidates_per_query)):
        for column in index.columns:
            counts[column] = counts.get(column, 0) + 1
    return counts


def log_summary(oracle, search):
    logging.info(
        f"Budget-aware tuning: calls={oracle.calls} (init {oracle.calls_init}), "
        f"skipped={oracle.skipped}, derived after budget={oracle.derived_after_budget}"
    )
