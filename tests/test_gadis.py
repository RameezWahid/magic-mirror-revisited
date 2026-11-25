import itertools
import unittest

from selection.algorithms.gadis_algorithm import GadisAlgorithm
from selection.index import Index
from selection.utils import mb_to_b
from selection.workload import Column, Query, Table, Workload


class MockConnector:
    def drop_indexes(self):
        pass


# Deterministic cost model: every column has a size and a benefit; an index saves its
# benefit once per query that touches its first column. Multi-column indexes save a
# bit more than their leading column alone. Some columns have no benefit at all.
class FakeCostEvaluation:
    def __init__(self, sizes_mb, benefits, base_cost=1000):
        self.sizes_mb = sizes_mb
        self.benefits = benefits
        self.base_cost = base_cost
        self.cost_requests = 0
        self.cache_hits = 0

    def estimate_size(self, index):
        size = sum(self.sizes_mb[column.name] for column in index.columns)
        index.estimated_size = mb_to_b(size)

    def calculate_cost(self, workload, indexes, store_size=False):
        self.cost_requests += 1
        total = 0
        for query in workload.queries:
            saving = 0
            for index in indexes:
                if index.columns[0] in query.columns:
                    bonus = 1.1 if len(index.columns) > 1 else 1.0
                    saving = max(saving, self.benefits[index.columns[0].name] * bonus)
            total += self.base_cost - saving
        return total

    def complete_cost_estimation(self):
        pass


def make_workload():
    table = Table("t")
    columns = [Column(f"c{i}") for i in range(8)]
    table.add_columns(columns)
    queries = [
        Query(0, "q0", [columns[0], columns[1]]),
        Query(1, "q1", [columns[2], columns[3], columns[4]]),
        Query(2, "q2", [columns[5]]),
        Query(3, "q3", [columns[6], columns[7]]),
    ]
    return Workload(queries), columns


SIZES = {f"c{i}": s for i, s in enumerate([10, 40, 25, 5, 30, 20, 15, 35])}
BENEFITS = {f"c{i}": b for i, b in enumerate([100, 300, 250, 10, 0, 200, 50, 0])}


def make_algorithm(**parameters):
    defaults = {"population_size": 20, "generations": 30, "seed": 1}
    defaults.update(parameters)
    algorithm = GadisAlgorithm(MockConnector(), defaults)
    algorithm.cost_evaluation = FakeCostEvaluation(SIZES, BENEFITS)
    return algorithm


def brute_force_cost(workload, budget_mb):
    evaluation = FakeCostEvaluation(SIZES, BENEFITS)
    candidates = workload.potential_indexes()
    for candidate in candidates:
        evaluation.estimate_size(candidate)
    best = evaluation.calculate_cost(workload, [])
    for r in range(1, len(candidates) + 1):
        for combination in itertools.combinations(candidates, r):
            if sum(i.estimated_size for i in combination) <= mb_to_b(budget_mb):
                best = min(best, evaluation.calculate_cost(workload, combination))
    return best


class TestGadisAlgorithm(unittest.TestCase):
    def setUp(self):
        self.workload, self.columns = make_workload()

    def _size(self, indexes):
        return sum(index.estimated_size for index in indexes)

    def test_respects_budget(self):
        for budget in [0, 5, 20, 50, 100, 1000]:
            algorithm = make_algorithm(budget_MB=budget)
            indexes = algorithm.calculate_best_indexes(self.workload)
            self.assertLessEqual(self._size(indexes), mb_to_b(budget))

    def test_finds_optimum_on_small_instance(self):
        for budget in [20, 50, 75, 100]:
            algorithm = make_algorithm(budget_MB=budget)
            indexes = algorithm.calculate_best_indexes(self.workload)
            cost = algorithm.cost_evaluation.calculate_cost(self.workload, indexes)
            self.assertEqual(cost, brute_force_cost(self.workload, budget))

    def test_deterministic_for_seed(self):
        results = []
        for _ in range(2):
            algorithm = make_algorithm(budget_MB=60, seed=7)
            results.append(algorithm.calculate_best_indexes(self.workload))
        self.assertEqual(results[0], results[1])

    def test_prune_removes_useless_indexes(self):
        algorithm = make_algorithm(budget_MB=1000)
        indexes = algorithm.calculate_best_indexes(self.workload)
        names = {index.columns[0].name for index in indexes}
        # c4 and c7 have no benefit; a query only profits from its best index, so
        # c0 is dominated by c1 (q0) and c3 by c2 (q1).
        self.assertEqual(names, {"c1", "c2", "c5", "c6"})

    def test_without_prune_may_keep_useless_indexes(self):
        algorithm = make_algorithm(budget_MB=1000, prune_unused=False)
        indexes = algorithm.calculate_best_indexes(self.workload)
        self.assertLessEqual(self._size(indexes), mb_to_b(1000))

    def test_candidates_exceeding_budget_are_excluded(self):
        algorithm = make_algorithm(budget_MB=12)
        algorithm.workload = self.workload
        candidates = algorithm._generate_candidates()
        self.assertEqual(
            {c.columns[0].name for c in candidates}, {"c0", "c3"}  # 10 and 5 MB
        )

    def test_multi_column_candidates(self):
        algorithm = make_algorithm(budget_MB=1000, max_index_width=2)
        algorithm.workload = self.workload
        candidates = algorithm._generate_candidates()
        widths = {len(c.columns) for c in candidates}
        self.assertEqual(widths, {1, 2})
        # Permutations per query: q0: 2+2, q1: 3+6, q2: 1, q3: 2+2
        self.assertEqual(len(candidates), 18)
        self.assertIn(Index([self.columns[1], self.columns[0]]), candidates)

    def test_repair_drops_worst_ratio_first(self):
        algorithm = make_algorithm(budget_MB=50)
        algorithm.workload = self.workload
        algorithm.initial_cost = algorithm.cost_evaluation.calculate_cost(
            self.workload, []
        )
        algorithm.candidates = algorithm._generate_candidates()
        algorithm._compute_repair_order()
        names = [c.columns[0].name for c in algorithm.candidates]
        # Select c0 (10MB, 10/MB), c1 (40MB, 7.5/MB), c4 (30MB, 0/MB): 80MB > 50MB
        genome = tuple(1 if n in ("c0", "c1", "c4") else 0 for n in names)
        repaired = algorithm._repair(genome)
        selected = {names[g] for g, bit in enumerate(repaired) if bit}
        self.assertEqual(selected, {"c0", "c1"})

    def test_two_point_crossover_keeps_genes(self):
        algorithm = make_algorithm()
        parent_1, parent_2 = (0,) * 10, (1,) * 10
        for _ in range(50):
            child_1, child_2 = algorithm._two_point_crossover(parent_1, parent_2)
            self.assertEqual(len(child_1), 10)
            for g in range(10):
                self.assertEqual(child_1[g] + child_2[g], 1)

    def test_history_is_monotonic(self):
        algorithm = make_algorithm(budget_MB=60)
        algorithm.calculate_best_indexes(self.workload)
        self.assertEqual(len(algorithm.history), 30)
        best_costs = [entry["best_cost"] for entry in algorithm.history]
        self.assertEqual(best_costs, sorted(best_costs, reverse=True))

    def test_max_runtime_stops_early(self):
        algorithm = make_algorithm(budget_MB=60, max_runtime_minutes=0)
        algorithm.calculate_best_indexes(self.workload)
        self.assertEqual(len(algorithm.history), 1)

    def test_no_candidates(self):
        algorithm = make_algorithm(budget_MB=0)
        self.assertEqual(algorithm.calculate_best_indexes(self.workload), [])


if __name__ == "__main__":
    unittest.main()
