import logging
from itertools import combinations
import numpy as np
from scipy.optimize import linprog
from ..selection_algorithm import SelectionAlgorithm
from ..utils import mb_to_b
from ..workload import Workload

DEFAULT_PARAMETERS = {"budget_MB": 250, "k_precision": 0.001, "max_k": 1048576}


def assign_benefits(n, observations, precision=0.001, max_k=1048576):
    """Find an approximately minimum K-tolerant vector for sampled subsets."""
    if not observations or not any(v > 0 for _, v in observations):
        return np.zeros(n), 1.0
    scale = max(v for _, v in observations)
    a = np.zeros((len(observations), n))
    for r, (subset, _) in enumerate(observations):
        a[r, list(subset)] = 1
    f = np.array([v / scale for _, v in observations])
    def solve(k):
        result = linprog(np.zeros(n), A_ub=np.vstack((a, -a)),
                         b_ub=np.concatenate((k*f, -f/k)),
                         bounds=(0, None), method='highs')
        if result.status == 2:
            return None
        if not result.success:
            raise RuntimeError(result.message)
        return result.x * scale
    lo = hi = 1.0
    best = solve(hi)
    while best is None and hi < max_k:
        lo, hi = hi, min(2*hi, max_k)
        best = solve(hi)
    if best is None:
        raise ValueError('No finite K-tolerant vector found: zero-benefit singleton / positive pair synergy or inconsistent estimates may cause infeasibility.')
    while hi-lo > precision:
        mid = (lo+hi)/2
        trial = solve(mid)
        if trial is None:
            lo = mid
        else:
            hi, best = mid, trial
    return best, hi


class BenKnapAlgorithm(SelectionAlgorithm):
    def __init__(self, database_connector, parameters=None):
        super().__init__(database_connector, parameters or {}, DEFAULT_PARAMETERS)
        self.budget = mb_to_b(self.parameters['budget_MB'])

    def _calculate_best_indexes(self, workload):
        candidates = sorted(workload.potential_indexes(), key=str)
        if not candidates:
            return []
        for index in candidates:
            self.cost_evaluation.calculate_cost(workload, [index], store_size=True)
        if any(i.estimated_size is None or i.estimated_size <= 0 for i in candidates):
            raise RuntimeError('Missing or invalid index size')
        candidates = [i for i in candidates if i.estimated_size <= self.budget]
        benefits = {i: 0.0 for i in candidates}
        for query in workload.queries:
            single = Workload([query])
            relevant = set(single.potential_indexes())
            indexes = [i for i in candidates if i in relevant]
            if not indexes:
                continue
            baseline = self.cost_evaluation.calculate_cost(single, [])
            observations = []
            for width in (1, 2):
                for subset in combinations(range(len(indexes)), width):
                    cost = self.cost_evaluation.calculate_cost(single, [indexes[j] for j in subset])
                    # Read-only workload: clamp negative optimizer-estimate artifacts.
                    observations.append((subset, max(0.0, baseline-cost)))
            try:
                vector, k = assign_benefits(len(indexes), observations,
                    float(self.parameters['k_precision']), float(self.parameters['max_k']))
            except ValueError:
                # Explicit adaptation for zero-benefit synergy, where the paper's
                # multiplicative LP has no finite feasible solution.
                logging.warning('BEN_KNAP: infeasible multiplicative LP; using additive minimax fallback for query %s', query)
                a = np.zeros((len(observations), len(indexes)))
                for r, (subset, _) in enumerate(observations):
                    a[r, list(subset)] = 1
                f = np.array([v for _, v in observations])
                scale = max(1.0, max(f))
                slack = -np.ones((len(f), 1))
                result = linprog(np.r_[np.zeros(len(indexes)), 1.0],
                    A_ub=np.vstack((np.hstack((a, slack)), np.hstack((-a, slack)))),
                    b_ub=np.r_[f/scale, -f/scale], bounds=(0,None), method='highs')
                if not result.success:
                    raise RuntimeError(result.message)
                vector, k = result.x[:-1]*scale, float('inf')
            for index, benefit in zip(indexes, vector):
                benefits[index] += float(benefit)
            logging.info('BEN_KNAP query %s: candidates=%d observations=%d K=%.4f', query, len(indexes), len(observations), k)
        chosen = []
        used = 0
        for index in sorted(candidates, key=lambda i: (-benefits[i]/i.estimated_size, str(i))):
            if benefits[index] > 0 and used + index.estimated_size <= self.budget:
                chosen.append(index)
                used += index.estimated_size
        # Compare with the best feasible singleton for the additive knapsack objective.
        if candidates:
            best = max(candidates, key=lambda i: benefits[i])
            if benefits[best] > sum(benefits[i] for i in chosen):
                chosen = [best]
        cost = self.cost_evaluation.calculate_cost(workload, chosen, store_size=True)
        logging.info('BEN_KNAP final cost %.2f; storage %d bytes; indexes %s', cost, sum(i.estimated_size for i in chosen), chosen)
        return chosen
