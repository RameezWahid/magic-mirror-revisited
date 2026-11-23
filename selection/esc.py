import logging
import time


class EarlyStoppingChecker:
    """Esc-B: early-stopping checker for budget-aware index tuning (Wang et al.,
    "Esc: An Early-Stopping Checker for Budget-aware Index Tuning", PVLDB 2025).

    The tuner may stop at what-if call t if
        eta_U(final best) - eta_L(best at t) <= epsilon,
    where eta is the percentage improvement 1 - cost(W, C) / cost(W, empty).

    eta_L (what we already have for sure): simulated greedy search that finishes
        the selection with derived costs only (no what-if calls). Derived costs
        are upper bounds of the real cost, so this improvement is a lower bound.
    eta_U (the most any final configuration could reach): the K indexes with the
        largest workload-level MCI upper bound u(W, z) = sum_q u(q, z); the cost
        cannot drop by more than their sum (Eq. 10 of the paper).

    Only the variant without index interaction (Esc-B) is implemented.
    Checked at the start of every greedy step of Phase 2 of two-phase greedy.
    """

    def __init__(
        self,
        oracle,
        max_indexes,
        epsilon=0.05,
        clamp_omega=False,
        storage_budget=None,
        index_size=None,
        storage_bound=True,
    ):
        self.oracle = oracle
        self.max_indexes = max_indexes
        # The simulated greedy must obey the same storage budget as the real
        # one. Its configuration is what we return when we stop.
        self.storage_budget = storage_budget
        self.index_size = index_size
        # Use the storage-aware (fractional knapsack) upper bound. Switchable only
        # to compare it with the paper's count-only bound.
        self.storage_bound = storage_bound
        self.epsilon = epsilon
        # Optional, not in the paper: the workload is assumed to cost at least the
        # sum of the per-query costs with all candidates at once (Omega), so the
        # upper bound on the improvement is capped there. That holds only if
        # adding indexes never raises the estimated cost, which Postgres does not
        # guarantee. Where the cap falls below the improvement already reached
        # (a violation of that assumption) the bound is raised to the reached
        # improvement and the event is counted in `violations`. With the clamp the
        # checker is therefore a heuristic, not a guarantee.
        self.clamp_omega = clamp_omega
        self.violations = 0
        self.trace = []  # (calls, selected so far, eta_L, eta_U)
        self.checks = 0
        self.time = 0.0
        self.stopped_at_calls = None

    # Lower bound on the improvement of the best configuration at this point.
    def simulated_greedy(self, selected, candidates):
        oracle = self.oracle
        queries = oracle.workload.queries
        configuration = frozenset(selected)
        per_query = {
            q: oracle.derived_cost(q, oracle.relevant(q, configuration))
            for q in queries
        }
        affected = {
            z: [q for q in queries if oracle.relevant(q, [z])] for z in candidates
        }
        used = 0
        if self.storage_budget is not None:
            used = sum(self.index_size(z) for z in configuration)
        while len(configuration) < self.max_indexes:
            best, best_gain, best_costs = None, 0.0, None
            for z in candidates:
                if z in configuration:
                    continue
                if (
                    self.storage_budget is not None
                    and used + self.index_size(z) > self.storage_budget
                ):
                    continue
                trial = configuration | {z}
                costs = {
                    q: oracle.derived_cost(q, oracle.relevant(q, trial))
                    for q in affected[z]
                }
                gain = sum(per_query[q] - costs[q] for q in affected[z])
                if gain > best_gain:
                    best, best_gain, best_costs = z, gain, costs
            if best is None:
                break
            configuration = configuration | {best}
            per_query.update(best_costs)
            if self.storage_budget is not None:
                used += self.index_size(best)
        return configuration, sum(per_query.values())

    def _knapsack_bound(self, saving, candidates):
        sized = []
        for z in candidates:
            if saving[z] > 0:
                sized.append((saving[z], max(0, self.index_size(z) or 0), z))
        # Indexes of no known size come first (always fit); then by saving per byte.
        sized.sort(key=lambda t: (t[1] > 0, -(t[0] / t[1]) if t[1] > 0 else 0, t[2]))
        room, total = self.storage_budget, 0.0
        for value, size, _ in sized:
            if size <= room:
                total += value
                room -= size
            else:
                total += value * room / size
                break
        return total

    # Upper bound on the improvement of the final best configuration.
    def improvement_upper_bound(self, candidates):
        oracle = self.oracle
        queries = oracle.workload.queries
        empty = sum(oracle.empty_cost[q] for q in queries)
        saving = {}
        for z in candidates:
            total = 0.0
            for q in queries:
                if oracle.relevant(q, [z]):
                    bound = max(0.0, oracle.mci_bound(q, z))
                    total += bound
            saving[z] = total
        top = sorted(candidates, key=lambda z: (-saving[z], z))[: self.max_indexes]
        top = [z for z in top if saving[z] > 0]
        # Eq. 10: the K largest workload-level bounds, summed. No per-query floor:
        # with floors, picking the K largest sums would no longer be the best case.
        best_saving = sum(saving[z] for z in top)
        if self.storage_bound and self.storage_budget is not None and self.index_size is not None:
            # Not in the paper: with a storage budget the final configuration also
            # has to fit in it. Relaxing the count limit, the best total saving is
            # the fractional knapsack bound (best saving per byte first, the last
            # index counted in part). Both relaxations are upper bounds, so the
            # smaller one is used.
            best_saving = min(best_saving, self._knapsack_bound(saving, candidates))
        lower_cost = max(0.0, empty - best_saving)
        if self.clamp_omega:
            # Every query costs at least c(q, Omega_q), whatever is selected.
            lower_cost = max(
                lower_cost, sum(oracle.omega_cost.get(q, 0.0) for q in queries)
            )
        return 1 - lower_cost / empty

    def check(self, selected, candidates):
        """Returns the configuration to stop with, or None to keep tuning."""
        start = time.time()
        oracle = self.oracle
        empty = sum(oracle.empty_cost.values())
        configuration, derived_cost = self.simulated_greedy(selected, candidates)
        eta_lower = 1 - derived_cost / empty
        eta_upper = self.improvement_upper_bound(candidates)
        if self.clamp_omega and eta_upper < eta_lower:
            self.violations += 1
            eta_upper = eta_lower
        self.checks += 1
        self.trace.append((oracle.calls, len(selected), eta_lower, eta_upper))
        self.time += time.time() - start
        if eta_upper - eta_lower <= self.epsilon:
            self.stopped_at_calls = oracle.calls
            logging.info(
                f"Esc: stop at {oracle.calls} calls, "
                f"eta_L={eta_lower:.4f}, eta_U={eta_upper:.4f}"
            )
            return configuration
        return None
