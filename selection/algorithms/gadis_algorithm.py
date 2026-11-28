"""Budget-constrained GADIS-inspired genetic index selection for hyrise/index_selection_evaluation.

This is an adaptation, NOT a faithful reproduction of the SEKE 2019 GADIS
fitness function: it uses PostgreSQL/HypoPG estimated workload cost instead
of physical query runtime / TPC-H QPHH.
"""
import logging
import random
# from .algorithms.gadis_algorithm import GadisAlgorithm

from ..selection_algorithm import SelectionAlgorithm
from ..utils import mb_to_b

DEFAULT_PARAMETERS = {
    "budget_MB": 250,
    "population_size": 12,
    "generations": 8,
    "mutation_probability": 0.05,
    "elite_size": 2,
    "tournament_size": 3,
    "max_candidates": 24,
    "seed": 42,
}


class GadisAlgorithm(SelectionAlgorithm):
    def __init__(self, database_connector, parameters=None):
        super().__init__(database_connector, parameters or {}, DEFAULT_PARAMETERS)
        self.budget = mb_to_b(self.parameters["budget_MB"])
        self.rng = random.Random(self.parameters["seed"])

    def _calculate_best_indexes(self, workload):
        p = self.parameters
        population_size = int(p["population_size"])
        generations = int(p["generations"])
        elite_size = int(p["elite_size"])
        tournament_size = int(p["tournament_size"])
        mutation_probability = float(p["mutation_probability"])
        if population_size < 2 or not 1 <= elite_size < population_size:
            raise ValueError("Require population_size >= 2 and 1 <= elite_size < population_size")
        if generations < 0 or tournament_size < 1:
            raise ValueError("Require generations >= 0 and tournament_size >= 1")
        if not 0 <= mutation_probability <= 1:
            raise ValueError("mutation_probability must be between 0 and 1")

        candidates = sorted(workload.potential_indexes(), key=str)
        candidates = candidates[: int(p["max_candidates"])]
        n = len(candidates)
        if not n:
            return []

        # HypoPG fills estimated_size during cost evaluation with store_size=True.
        # Measure individual sizes once; combinations reuse these estimates.
        for index in candidates:
            self.cost_evaluation.calculate_cost(workload, [index], store_size=True)
        sizes = [index.estimated_size for index in candidates]
        if any(size is None for size in sizes):
            raise RuntimeError("Could not estimate candidate index sizes")

        cache = {}

        def evaluate(bits):
            bits = tuple(bits)
            if bits in cache:
                return cache[bits]
            total_size = sum(size for bit, size in zip(bits, sizes) if bit)
            if total_size > self.budget:
                cache[bits] = float("inf")
                return cache[bits]
            indexes = [index for bit, index in zip(bits, candidates) if bit]
            cost = self.cost_evaluation.calculate_cost(workload, indexes, store_size=True)
            cache[bits] = cost
            return cost

        def repair(bits):
            bits = list(bits)
            total = sum(size for bit, size in zip(bits, sizes) if bit)
            selected = [i for i, bit in enumerate(bits) if bit]
            self.rng.shuffle(selected)
            for i in selected:
                if total <= self.budget:
                    break
                bits[i] = 0
                total -= sizes[i]
            return tuple(bits)

        zero = tuple(0 for _ in range(n))
        population = [zero]
        while len(population) < population_size:
            population.append(repair([self.rng.randrange(2) for _ in range(n)]))

        def tournament(scored):
            sample = self.rng.choices(scored, k=tournament_size)
            return min(sample, key=lambda x: x[0])[1]

        for generation in range(generations + 1):
            scored = sorted(((evaluate(bits), bits) for bits in population), key=lambda x: x[0])
            logging.info("GADIS generation %d/%d: best cost %.2f", generation, generations, scored[0][0])
            if generation == generations:
                break
            next_population = [bits for _, bits in scored[:elite_size]]
            while len(next_population) < population_size:
                a, b = tournament(scored), tournament(scored)
                if n >= 3:
                    left, right = sorted(self.rng.sample(range(1, n), 2))
                    child = list(a[:left] + b[left:right] + a[right:])
                else:
                    child = list(a)
                for i in range(n):
                    if self.rng.random() < mutation_probability:
                        child[i] ^= 1
                next_population.append(repair(child))
            population = next_population

        best_cost, best_bits = min(scored, key=lambda x: x[0])
        indexes = [index for bit, index in zip(best_bits, candidates) if bit]
        logging.info("GADIS final cost %.2f; indexes %s", best_cost, indexes)
        return indexes
