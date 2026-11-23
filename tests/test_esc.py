import random
import unittest

from selection.budget_aware import BudgetAwareCostOracle, TwoPhaseGreedy
from selection.esc import EarlyStoppingChecker
from selection.index import Index
from selection.workload import Query, Workload


class Table:
    def __init__(self, name):
        self.name = name

    def __hash__(self):
        return hash(self.name)

    def __eq__(self, other):
        return self.name == other.name

    def __lt__(self, other):
        return self.name < other.name


class Column:
    def __init__(self, table, name):
        self.table, self.name = table, name

    def __hash__(self):
        return hash((self.table.name, self.name))

    def __eq__(self, other):
        return (self.table.name, self.name) == (other.table.name, other.name)

    def __lt__(self, other):
        return (self.table.name, self.name) < (other.table.name, other.name)

    def __repr__(self):
        return f"{self.table.name}.{self.name}"


class SyntheticCosts:
    """Monotone, submodular what-if cost: every covered leading column multiplies
    the query's cost by (1 - benefit)."""

    def __init__(self, benefit, base):
        self.benefit, self.base = benefit, base

    def cost(self, query, configuration):
        covered = {}
        for index in configuration:
            lead = index.columns[0]
            if lead in query.columns:
                covered[lead] = max(covered.get(lead, 0), self.benefit[(query.nr, lead)])
        cost = self.base[query.nr]
        for benefit in covered.values():
            cost *= 1 - benefit
        return cost

    def calculate_cost(self, workload, configuration, store_size=False):
        return sum(self.cost(q, configuration) for q in workload.queries)

    def which_indexes_utilized_and_cost(self, query, configuration):
        return set(configuration), self.cost(query, configuration)


def build(seed, queries=12, tables=4, columns=5):
    rng = random.Random(seed)
    cols = [Column(Table(f"t{t}"), f"c{c}") for t in range(tables) for c in range(columns)]
    qs = [Query(i, "q", rng.sample(cols, rng.randint(3, 7))) for i in range(queries)]
    benefit = {(q.nr, c): rng.uniform(0.02, 0.5) for q in qs for c in q.columns}
    base = {q.nr: rng.uniform(1e3, 1e5) for q in qs}
    candidates = [
        [Index((c,)) for c in q.columns]
        + [Index((a, b)) for a in q.columns for b in q.columns
           if a != b and a.table == b.table]
        for q in qs
    ]
    return qs, SyntheticCosts(benefit, base), base, candidates


def size_of(index):
    return 1 + (len(index.columns) * 3 + hash(index.columns[0].name) % 5)


def run(seed, interception, epsilon=None, budget=None, k=5, clamp=False, storage=None, aware=True):
    qs, costs, base, candidates = build(seed)
    oracle = BudgetAwareCostOracle(
        costs, Workload(qs), budget=budget, interception=interception,
        need_omega=epsilon is not None,
    )
    oracle.candidate_counts = {}
    oracle.initialize(candidates)
    search = TwoPhaseGreedy(
        oracle, candidates, k,
        storage_budget=storage, index_size=size_of if storage else None,
    )
    if epsilon is not None:
        search.checker = EarlyStoppingChecker(
            oracle, k, epsilon=epsilon, clamp_omega=clamp,
            storage_budget=storage, index_size=size_of if storage else None, storage_bound=aware,
        )
    selected = search.run()
    improvement = 1 - sum(costs.cost(q, selected) for q in qs) / sum(base.values())
    return improvement, oracle, search


class TestEsc(unittest.TestCase):
    def test_loss_never_exceeds_epsilon(self):
        for seed in range(15):
            reference, _, _ = run(seed, "none")
            for epsilon in (0.01, 0.05, 0.10):
                improvement, _, _ = run(seed, "none", epsilon)
                self.assertLessEqual(reference - improvement, epsilon + 1e-9)

    def test_upper_bound_never_below_final_improvement(self):
        for seed in range(15):
            reference, _, _ = run(seed, "none")
            _, _, search = run(seed, "none", 0.05)
            for _, _, lower, upper in search.checker.trace:
                self.assertGreaterEqual(upper, reference - 1e-9)
                self.assertGreaterEqual(upper, lower - 1e-9)

    def test_clamped_bound_is_valid_and_not_looser(self):
        for seed in range(15):
            reference, _, _ = run(seed, "none")
            _, _, plain = run(seed, "none", 0.05)
            _, _, clamped = run(seed, "none", 0.05, clamp=True)
            first_plain, first_clamped = plain.checker.trace[0], clamped.checker.trace[0]
            self.assertLessEqual(first_clamped[3], first_plain[3] + 1e-12)
            for _, _, _, upper in clamped.checker.trace:
                self.assertGreaterEqual(upper, reference - 1e-9)

    def test_clamp_violation_is_counted_and_bound_stays_consistent(self):
        # Simulate non-monotone costs: Omega cost far above what is reachable.
        qs, costs, base, candidates = build(4)
        oracle = BudgetAwareCostOracle(
            costs, Workload(qs), budget=None, interception="none", need_omega=True
        )
        oracle.candidate_counts = {}
        oracle.initialize(candidates)
        for q, per_query in zip(qs, candidates):
            for z in per_query:
                oracle.eval_cost(q, frozenset([z]))
        for q in qs:
            oracle.omega_cost[q] = oracle.empty_cost[q]  # claims no improvement is possible
        checker = EarlyStoppingChecker(oracle, 5, epsilon=0.05, clamp_omega=True)
        flat = sorted({z for per_query in candidates for z in per_query})
        checker.check([], flat)
        self.assertEqual(checker.violations, 1)
        _, _, lower, upper = checker.trace[0]
        self.assertGreaterEqual(upper, lower)

    def test_storage_aware_bound_is_valid_and_not_looser(self):
        for seed in range(15):
            ref, _, _ = run(seed, "none", epsilon=-1.0, k=100, storage=40)
            _, _, aware = run(seed, "none", epsilon=0.05, k=100, storage=40)
            _, _, count_only = run(seed, "none", epsilon=0.05, k=100, storage=40, aware=False)
            for _, _, _, upper in aware.checker.trace:
                self.assertGreaterEqual(upper + 1e-9, ref)
            self.assertLessEqual(aware.checker.trace[0][3], count_only.checker.trace[0][3] + 1e-9)

    def test_storage_aware_bound_stops_at_least_as_often(self):
        aware_stops = count_stops = 0
        for seed in range(15):
            _, _, aware = run(seed, "none", epsilon=0.05, k=100, storage=40)
            _, _, count_only = run(seed, "none", epsilon=0.05, k=100, storage=40, aware=False)
            aware_stops += aware.stopped_early
            count_stops += count_only.stopped_early
        self.assertGreaterEqual(aware_stops, count_stops)

    def test_returned_configuration_fits_storage_budget(self):
        for seed in range(15):
            qs, costs, base, candidates = build(seed)
            oracle = BudgetAwareCostOracle(
                costs, Workload(qs), budget=None, interception="none", need_omega=True
            )
            oracle.candidate_counts = {}
            oracle.initialize(candidates)
            flat = sorted({z for per_query in candidates for z in per_query})
            checker = EarlyStoppingChecker(
                oracle, 100, epsilon=0.05, storage_budget=40, index_size=size_of
            )
            configuration, _ = checker.simulated_greedy([], flat)
            self.assertLessEqual(sum(size_of(z) for z in configuration), 40)

    def test_never_stops_with_negative_epsilon(self):
        _, _, search = run(3, "none", -1.0)
        self.assertFalse(search.stopped_early)

    def test_stops_with_large_epsilon(self):
        _, oracle, search = run(3, "none", 1.0)
        self.assertTrue(search.stopped_early)
        self.assertEqual(search.checker.stopped_at_calls, oracle.calls)

    def test_budget_respected_with_esc(self):
        _, oracle, _ = run(2, "wii", 0.05, budget=150)
        self.assertLessEqual(oracle.calls, 150)


if __name__ == "__main__":
    unittest.main()
