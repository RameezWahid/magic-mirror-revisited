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


def run(seed, interception, epsilon=None, budget=None, k=5):
    qs, costs, base, candidates = build(seed)
    oracle = BudgetAwareCostOracle(
        costs, Workload(qs), budget=budget, interception=interception,
        need_omega=epsilon is not None,
    )
    oracle.candidate_counts = {}
    oracle.initialize(candidates)
    search = TwoPhaseGreedy(oracle, candidates, k)
    if epsilon is not None:
        search.checker = EarlyStoppingChecker(oracle, k, epsilon=epsilon)
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
