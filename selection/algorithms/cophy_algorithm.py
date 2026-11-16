# CoPhy solved inside the platform. Upstream only writes the input to a file and
# returns no indexes; this builds the input, solves it, and returns the indexes.
# The model is the authors' cophy_ampl_model.mod, copied rule for rule. Don't change it.
import logging
import time

import pulp

from selection.algorithms.cophy_input_generation import CoPhyInputGeneration
from selection.selection_algorithm import DEFAULT_PARAMETER_VALUES
from selection.utils import mb_to_b

DEFAULT_PARAMETERS = {
    "budget_MB": DEFAULT_PARAMETER_VALUES["budget_MB"],  # storage budget in MB
    "max_index_width": DEFAULT_PARAMETER_VALUES["max_index_width"],
    "max_indexes_per_query": 1,
    "enumeration": "query-based",
    "solver": "highs",  # "highs" or "cbc" (free), "gurobi" (license, as in the paper)
    "solver_time_limit_s": None,  # stop early and keep the best answer; None = no limit
    "reuse_input": True,  # build the input once, reuse it for every budget
    "output_folder": None,
    "overwrite": True,
}

# Cost for a (query, combination) pair we have no number for: so big the solver
# never picks it. Same value as the AMPL model.
DEFAULT_COST = 99999999999999

# Inputs already built in this process, so other budgets skip the what-if calls.
# (database, query numbers, enumeration parameters) -> (cophy_dict, index_by_id)
_INPUT_CACHE = {}


class CoPhyAlgorithm(CoPhyInputGeneration):
    def __init__(self, database_connector, parameters=None):
        if parameters is None:
            parameters = {}
        for key, value in DEFAULT_PARAMETERS.items():
            if key not in parameters:
                parameters[key] = value
        CoPhyInputGeneration.__init__(self, database_connector, parameters)
        self.budget = mb_to_b(self.parameters["budget_MB"])

    def _calculate_best_indexes(self, workload):
        cophy_dict, index_by_id, reused = self._input(workload)
        if self.parameters["output_folder"]:
            self._write_output_files(cophy_dict)

        time_start = time.time()
        selected_ids, status = self._solve(cophy_dict)
        solve_time = time.time() - time_start

        # Goes into the results CSV. CoPhy's real runtime is what_if_time_s +
        # solve_time_s; the platform's runtime column only has the solve time
        # when the input is reused.
        self.parameters["input_reused"] = reused
        self.parameters["what_if_time_s"] = round(cophy_dict["what_if_time"], 2)
        self.parameters["what_if_cost_requests"] = cophy_dict["cost_requests"]
        self.parameters["solve_time_s"] = round(solve_time, 2)
        self.parameters["solver_status"] = status
        logging.info(
            f"CoPhy solved in {solve_time:.2f} s ({status}), "
            f"selected {len(selected_ids)} indexes"
        )

        return [index_by_id[index_id] for index_id in sorted(selected_ids)]

    # Return the input for this workload, building it only if we have to. The key
    # must hold everything the input depends on, or a different setup gets the wrong one.
    def _input(self, workload):
        key = (
            self.database_connector.db_name,
            tuple(query.nr for query in workload.queries),
            self.parameters["max_index_width"],
            self.parameters["max_indexes_per_query"],
            self.parameters["enumeration"],
        )
        if self.parameters["reuse_input"] and key in _INPUT_CACHE:
            logging.info("Reusing CoPhy input of a previous run")
            cophy_dict, index_by_id = _INPUT_CACHE[key]
            return cophy_dict, index_by_id, True

        cophy_dict = self.generate_input(workload)
        if self.parameters["reuse_input"]:
            _INPUT_CACHE[key] = (cophy_dict, self.index_by_id)
        return cophy_dict, self.index_by_id, False

    # Pick which indexes to build so the total query cost is lowest and they fit the
    # budget. Each query uses exactly one built combination (0 = no index, always ok).
    # Names match the AMPL model so the two can be checked line by line.
    def _solve(self, cophy_dict):
        queries = cophy_dict["queries"]
        indexes = range(1, cophy_dict["number_of_indexes"] + 1)
        combinations = range(0, cophy_dict["number_of_index_combinations"] + 1)

        size = {
            entry["index_id"]: entry["estimated_size"]
            for entry in cophy_dict["index_sizes"]
        }
        indexes_per_combination = {
            entry["combination_id"]: [int(i) for i in entry["index_ids"].split()]
            for entry in cophy_dict["index_combinations"]
        }
        # Only useful combinations have a cost in the input; the rest get DEFAULT_COST.
        costs = {
            (query, combination): DEFAULT_COST
            for query in queries
            for combination in combinations
        }
        for entry in cophy_dict["query_costs"]:
            costs[(entry["query_number"], entry["combination_id"])] = entry["costs"]

        problem = pulp.LpProblem("cophy", pulp.LpMinimize)
        # x[i] = 1: index i is built
        x = {i: pulp.LpVariable(f"x_{i}", cat="Binary") for i in indexes}
        # y[c] = 1: every index of combination c is built
        y = {c: pulp.LpVariable(f"y_{c}", cat="Binary") for c in combinations}
        # z[c, q] = 1: query q uses combination c
        z = {
            (c, q): pulp.LpVariable(f"z_{c}_{q}", cat="Binary")
            for c in combinations
            for q in queries
        }

        problem += (
            pulp.lpSum(costs[q, c] * z[c, q] for c in combinations for q in queries),
            "overall_costs_of_queries",
        )

        for q in queries:
            problem += (
                pulp.lpSum(z[c, q] for c in combinations) == 1,
                f"one_combination_per_query_{q}",
            )
        # y[c] can only be 1 if all of c's indexes are built.
        for c in combinations:
            problem += (
                pulp.lpSum(x[i] for i in indexes_per_combination[c])
                >= len(indexes_per_combination[c]) * y[c],
                f"applicable_combination_{c}",
            )
        for c in combinations:
            for q in queries:
                problem += z[c, q] <= y[c], f"usable_combination_{c}_{q}"
        # Sizes and budget are both in bytes.
        problem += (
            pulp.lpSum(x[i] * size[i] for i in indexes) <= self.budget,
            "memory_consumption",
        )

        problem.solve(self._solver())
        status = pulp.LpStatus[problem.status]
        # Combination 0 always fits, so "no answer" means something is broken. Crash.
        assert problem.status == pulp.LpStatusOptimal or problem.sol_status in (
            pulp.LpSolutionIntegerFeasible,
            pulp.LpSolutionOptimal,
        ), f"CoPhy: no feasible solution found ({status})"
        # Hit the time limit: valid answer, maybe not the best. Say so in the results.
        if problem.sol_status == pulp.LpSolutionIntegerFeasible:
            status = "Feasible (time limit)"

        # Solvers return 0.9999999, not 1, so don't test == 1.
        # Every built index is the selection, same as the authors' AMPL run.
        selected_ids = {i for i in indexes if x[i].value() > 0.5}

        return selected_ids, status

    def _solver(self):
        time_limit = self.parameters["solver_time_limit_s"]
        if self.parameters["solver"] == "highs":
            return pulp.HiGHS(msg=False, timeLimit=time_limit)
        if self.parameters["solver"] == "cbc":
            return pulp.PULP_CBC_CMD(msg=False, timeLimit=time_limit)
        if self.parameters["solver"] == "gurobi":
            return pulp.GUROBI(msg=False, timeLimit=time_limit)
        raise ValueError(f"Unknown solver: {self.parameters['solver']}")
