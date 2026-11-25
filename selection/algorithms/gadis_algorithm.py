import logging
import random
import time

from selection.candidate_generation import (
    candidates_per_query,
    syntactically_relevant_indexes,
)
from selection.selection_algorithm import DEFAULT_PARAMETER_VALUES, SelectionAlgorithm
from selection.utils import b_to_mb, mb_to_b

# budget_MB: storage budget. Individuals that exceed it are repaired (see below).
# max_index_width: 1 reproduces the paper (one gene per indexable column). Values > 1
#                  add multi-column candidates (syntactically relevant permutations).
# population_size, generations, elite_size: paper uses 50, 50, 4.
# crossover_probability (cp): chance that a pair of parents is recombined with the
#                  two-point operator. Paper: 0.6 for GADIS-[T], 0.8 for GADIS-[Q].
# mutation_probability (mp): chance that an individual is mutated. Paper: 0.9.
# mutation_rate: chance that a single gene of a mutated individual is flipped.
#                Paper: 0.05.
# mutation_decay (md): mp is reduced linearly by md per generation (floored at 0).
#                None disables decay. Paper: 0.05 for GADIS-[Q], none for GADIS-[T].
# tournament_size: number of individuals competing in each tournament.
# init_probability: probability of a gene being 1 in the random initial population.
#                Paper: bits sampled uniformly, i.e., 0.5.
# seed: random seed, so that runs are reproducible.
# max_runtime_minutes: stop after the generation in which this is exceeded
#                (None = run all generations).
# prune_unused: after the search, drop indexes whose removal does not increase cost.
DEFAULT_PARAMETERS = {
    "budget_MB": DEFAULT_PARAMETER_VALUES["budget_MB"],
    "max_index_width": 1,
    "population_size": 50,
    "generations": 50,
    "elite_size": 4,
    "crossover_probability": 0.6,
    "mutation_probability": 0.9,
    "mutation_rate": 0.05,
    "mutation_decay": None,
    "tournament_size": 3,
    "init_probability": 0.5,
    "seed": 0,
    "max_runtime_minutes": None,
    "prune_unused": True,
}


# Reimplementation of GADIS, a genetic algorithm for index selection:
# Priscilla Neuhaus, Julia Couto, Jonatas Wehrmann, Duncan D. Ruiz, Felipe Meneguzzi:
# GADIS: A Genetic Algorithm for Database Index Selection. SEKE 2019: 39-42.
#
# Adaptations for this evaluation platform (the paper measures fitness by executing
# TPC-H on a running database and has no storage budget):
# - Fitness is the paper's speed-up function S(x) = T(x_I) / T(x), where T is the
#   optimizer-estimated workload cost (what-if/HypoPG) instead of measured runtime
#   and x_I is the configuration without (secondary) indexes. The QphH-based fitness
#   Q(x) is not used because the platform's workloads contain no INSERT/DELETE.
# - A storage budget is enforced by a repair operator: genes of an over-budget
#   individual are switched off in ascending order of (standalone benefit / size)
#   until it fits. The repaired genome replaces the original (Lamarckian repair).
# - Optionally, genes can represent multi-column indexes (max_index_width > 1).
class GadisAlgorithm(SelectionAlgorithm):
    def __init__(self, database_connector, parameters=None):
        if parameters is None:
            parameters = {}
        SelectionAlgorithm.__init__(
            self, database_connector, parameters, DEFAULT_PARAMETERS
        )
        self.budget = mb_to_b(self.parameters["budget_MB"])
        self.max_index_width = self.parameters["max_index_width"]
        self.population_size = self.parameters["population_size"]
        self.generations = self.parameters["generations"]
        self.elite_size = self.parameters["elite_size"]
        self.crossover_probability = self.parameters["crossover_probability"]
        self.mutation_probability = self.parameters["mutation_probability"]
        self.mutation_rate = self.parameters["mutation_rate"]
        self.mutation_decay = self.parameters["mutation_decay"]
        self.tournament_size = self.parameters["tournament_size"]
        self.init_probability = self.parameters["init_probability"]
        self.max_runtime_minutes = self.parameters["max_runtime_minutes"]
        self.prune_unused = self.parameters["prune_unused"]
        self.random = random.Random(self.parameters["seed"])

        assert self.population_size >= 2, "Population needs at least 2 individuals."
        assert 0 <= self.elite_size <= self.population_size
        assert self.tournament_size >= 1

        self.workload = None
        self.candidates = []
        # Genes ordered from least to most valuable (benefit per byte), for repair.
        self.repair_order = []
        self.initial_cost = None
        self.fitness_cache = {}
        # One entry per generation, e.g., to plot convergence (cf. Fig. 1 of paper).
        self.history = []

    def _calculate_best_indexes(self, workload):
        logging.info("Calculating best indexes GADIS")
        start_time = time.time()
        self.workload = workload
        self.initial_cost = self.cost_evaluation.calculate_cost(workload, [])

        self.candidates = self._generate_candidates()
        logging.info(f"GADIS: {len(self.candidates)} genes (candidate indexes)")
        if not self.candidates or self.initial_cost == 0:
            return []
        self._compute_repair_order()

        population = [self._random_genome() for _ in range(self.population_size)]
        population = [self._repair(genome) for genome in population]

        best_genome, best_cost = None, None
        mutation_probability = self.mutation_probability
        for generation in range(self.generations):
            costs = [self._cost(genome) for genome in population]
            ranked = sorted(zip(costs, population), key=lambda x: x[0])
            if best_cost is None or ranked[0][0] < best_cost:
                best_cost, best_genome = ranked[0]
            self._log_generation(generation, costs, best_cost)

            if self._timed_out(start_time) or generation == self.generations - 1:
                break

            elite = [genome for _, genome in ranked[: self.elite_size]]
            offspring = self._breed(population, costs, mutation_probability)
            population = elite + offspring[: self.population_size - self.elite_size]

            if self.mutation_decay is not None:
                mutation_probability = max(
                    0.0, mutation_probability - self.mutation_decay
                )

        indexes = self._genome_to_indexes(best_genome)
        if self.prune_unused:
            indexes = self._prune(indexes, best_cost)
        size = sum(index.estimated_size for index in indexes)
        logging.info(
            f"GADIS result: {len(indexes)} indexes, {b_to_mb(size):.2f} MB, "
            f"speed-up {self.initial_cost / self._cost_of(indexes):.3f}, "
            f"{len(self.fitness_cache)} distinct configurations evaluated"
        )
        return indexes

    # Genes: single-column indexes on all indexable columns (as in the paper) or,
    # for max_index_width > 1, all syntactically relevant (multi-column) indexes.
    # Candidates that alone exceed the budget can never be part of a solution.
    def _generate_candidates(self):
        if self.max_index_width == 1:
            candidates = set(self.workload.potential_indexes())
        else:
            candidates = set()
            for query_candidates in candidates_per_query(
                self.workload, self.max_index_width, syntactically_relevant_indexes
            ):
                candidates |= set(query_candidates)

        within_budget = []
        for candidate in sorted(candidates):
            self.cost_evaluation.estimate_size(candidate)
            if candidate.estimated_size <= self.budget:
                within_budget.append(candidate)
        return within_budget

    # Standalone benefit of every candidate; used as repair heuristic.
    def _compute_repair_order(self):
        ratios = []
        for gene, candidate in enumerate(self.candidates):
            cost = self.cost_evaluation.calculate_cost(self.workload, [candidate])
            benefit = self.initial_cost - cost
            ratios.append((benefit / max(candidate.estimated_size, 1), gene))
        self.repair_order = [gene for _, gene in sorted(ratios)]

    def _random_genome(self):
        return tuple(
            1 if self.random.random() < self.init_probability else 0
            for _ in self.candidates
        )

    def _size(self, genome):
        return sum(
            self.candidates[gene].estimated_size for gene, bit in enumerate(genome) if bit
        )

    def _repair(self, genome):
        size = self._size(genome)
        if size <= self.budget:
            return genome
        genome = list(genome)
        for gene in self.repair_order:
            if size <= self.budget:
                break
            if genome[gene]:
                genome[gene] = 0
                size -= self.candidates[gene].estimated_size
        return tuple(genome)

    def _genome_to_indexes(self, genome):
        return [self.candidates[gene] for gene, bit in enumerate(genome) if bit]

    # Fitness is S(x) = initial_cost / cost(x); internally, we minimize cost(x).
    def _cost(self, genome):
        if genome not in self.fitness_cache:
            self.fitness_cache[genome] = self._cost_of(self._genome_to_indexes(genome))
        return self.fitness_cache[genome]

    def _cost_of(self, indexes):
        return self.cost_evaluation.calculate_cost(self.workload, indexes)

    def _tournament(self, population, costs):
        contestants = [
            self.random.randrange(len(population)) for _ in range(self.tournament_size)
        ]
        winner = min(contestants, key=lambda i: costs[i])
        return population[winner]

    def _two_point_crossover(self, parent_1, parent_2):
        length = len(parent_1)
        if length < 2:
            return parent_1, parent_2
        point_1, point_2 = sorted(self.random.sample(range(1, length + 1), 2))
        child_1 = parent_1[:point_1] + parent_2[point_1:point_2] + parent_1[point_2:]
        child_2 = parent_2[:point_1] + parent_1[point_1:point_2] + parent_2[point_2:]
        return child_1, child_2

    def _mutate(self, genome):
        return tuple(
            1 - bit if self.random.random() < self.mutation_rate else bit
            for bit in genome
        )

    def _breed(self, population, costs, mutation_probability):
        offspring = []
        needed = self.population_size - self.elite_size
        while len(offspring) < needed:
            parent_1 = self._tournament(population, costs)
            parent_2 = self._tournament(population, costs)
            if self.random.random() < self.crossover_probability:
                children = self._two_point_crossover(parent_1, parent_2)
            else:
                children = (parent_1, parent_2)
            for child in children:
                if self.random.random() < mutation_probability:
                    child = self._mutate(child)
                offspring.append(self._repair(child))
        return offspring

    # Removes indexes that do not reduce the estimated workload cost.
    def _prune(self, indexes, cost):
        indexes = list(indexes)
        # Try to remove large indexes first because they free most storage.
        for index in sorted(indexes, key=lambda i: i.estimated_size, reverse=True):
            remaining = [i for i in indexes if i != index]
            remaining_cost = self._cost_of(remaining)
            if remaining_cost <= cost:
                indexes, cost = remaining, remaining_cost
        return indexes

    def _timed_out(self, start_time):
        if self.max_runtime_minutes is None:
            return False
        return time.time() - start_time > self.max_runtime_minutes * 60

    def _log_generation(self, generation, costs, best_cost):
        entry = {
            "generation": generation,
            "best_cost": best_cost,
            "generation_best_cost": min(costs),
            "mean_cost": sum(costs) / len(costs),
            "speedup": self.initial_cost / best_cost,
            "evaluated_configurations": len(self.fitness_cache),
        }
        self.history.append(entry)
        logging.debug(
            f"GADIS generation {generation}: best speed-up {entry['speedup']:.4f}, "
            f"generation mean cost {entry['mean_cost']:.2f}, "
            f"{entry['evaluated_configurations']} configurations evaluated"
        )
