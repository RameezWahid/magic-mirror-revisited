# BEN_KNAP — Project Structure

This README describes only the BEN_KNAP implementation and its experimental files in the Index Selection Evaluation framework.

## 1. Directory structure

```text
index_selection_evaluation/
├── selection/
│   ├── algorithms/
│   │   └── ben_knap_algorithm.py       # BEN_KNAP implementation
│   ├── index_selection_evaluation.py  # Algorithm registration and runner
│   ├── selection_algorithm.py        # Base algorithm class
│   ├── cost_evaluation.py            # Cost estimation and caching
│   ├── database_connector.py         # Optimizer counters and timings
│   ├── dbms/postgres_dbms.py          # PostgreSQL interaction
│   ├── workload.py                   # Queries and candidate indexes
│   └── benchmark.py                  # Benchmark execution and result storage
├── example_configs/
│   ├── ben_knap_test.json            # Small initial test
│   ├── ben_knap_full.json            # 19-query test
│   ├── ben_knap_comparison.json      # SF1 comparison
│   ├── ben_knap_comparison_sf10.json # SF10 comparison
│   ├── actual_execution_sf1.json     # Physical execution, SF1
│   └── actual_execution_sf10.json    # Physical execution, SF10
├── benchmark_results/
│   └── results_ben_knap_tpch_19_queries.csv
├── optimization_comparison_sf1.log
├── optimization_comparison_sf10.log
├── exact_optimization_cost_sf1.csv
├── exact_optimization_cost_sf10.csv
├── comparison_sf1.csv
├── comparison_sf10.csv
├── actual_execution_sf1.log
├── actual_execution_sf10.log
├── actual_results_sf1.csv
└── actual_results_sf10.csv           # Extraction not yet confirmed
```

## 2. Main implementation file

`selection/algorithms/ben_knap_algorithm.py`

| Function | Responsibility |
|---|---|
| `assign_benefits()` | Assign individual index benefits using linear programming and search for the smallest feasible tolerance K. |
| `solve(k)` | Inner LP feasibility check for a fixed K, using SciPy `linprog`. |
| `BenKnapAlgorithm.__init__()` | Initialize the algorithm and convert the storage budget to bytes. |
| `_calculate_best_indexes()` | Generate candidates, estimate sizes and benefits, aggregate scores, perform greedy knapsack selection and return indexes. |

The algorithm is registered as `ben_knap` in `selection/index_selection_evaluation.py`.

## 3. Execution flow

```text
JSON configuration
    ↓
Evaluation runner
    ↓
BEN_KNAP
    ├── Generate single-column candidate indexes
    ├── Estimate index sizes using HypoPG
    ├── Remove candidates exceeding the budget
    ├── For each query:
    │   ├── Estimate baseline cost
    │   ├── Evaluate individual indexes and pairs
    │   └── Assign benefits using LP
    ├── Sum assigned benefits across queries
    ├── Rank indexes by benefit / size
    ├── Greedily select within budget
    └── Compare with best feasible singleton
    ↓
Benchmark selected indexes
    ↓
CSV results and logs
```

Pairs are two separate indexes, not one composite index. Recommendation uses hypothetical indexes; physical indexes are created only during the actual execution benchmark.

## 4. Configuration files

| Experiment | Configuration | Scale | Budget (MB) |
|---|---|---|---|
| Estimated comparison | `ben_knap_comparison.json` | SF1 | 100, 250, 500, 1000, 2000 |
| Estimated comparison | `ben_knap_comparison_sf10.json` | SF10 | 1000, 2500, 5000, 10000, 20000 |
| Actual execution | `actual_execution_sf1.json` | SF1 | 250 |
| Actual execution | `actual_execution_sf10.json` | SF10 | 2500 |

The comparison configurations also include other algorithms. BEN_KNAP uses `budget_MB`, `k_precision` (0.001) and `max_k` (default 1048576). The main workload contains 19 TPC-H queries.

## 5. Where to find BEN_KNAP results

| Result required | File |
|---|---|
| Selected indexes and raw per-query costs | `benchmark_results/results_ben_knap_tpch_19_queries.csv` |
| Estimated workload cost, storage and improvement | `comparison_sf1.csv`, `comparison_sf10.csv` |
| Optimizer calls, optimizer time, simulation overhead and recommendation runtime | `exact_optimization_cost_sf1.csv`, `exact_optimization_cost_sf10.csv` |
| Original optimization measurement logs | `optimization_comparison_sf1.log`, `optimization_comparison_sf10.log` |
| Actual query execution logs | `actual_execution_sf1.log`, `actual_execution_sf10.log` |
| Consolidated actual execution results | `actual_results_sf1.csv`, `actual_results_sf10.csv` (latter unconfirmed) |

Raw CSVs use semicolon separators and append multiple runs. Filter by scale factor, parameters, date and whether actual runtimes are present. Consolidated comparison files include other algorithms; filter the algorithm column for `ben_knap`.

## 6. How to inspect existing results

```bash
cd ~/Desktop/ABC/index_selection_evaluation

# Raw BEN_KNAP results
ls -lh benchmark_results/results_ben_knap_tpch_19_queries.csv

# BEN_KNAP optimization metrics
 grep 'OPTIMIZATION_METRICS algorithm=ben_knap' optimization_comparison_sf1.log
 grep 'OPTIMIZATION_METRICS algorithm=ben_knap' optimization_comparison_sf10.log

# BEN_KNAP comparison rows
 grep -i ben_knap comparison_sf1.csv comparison_sf10.csv

# Check actual execution summaries
ls -lh actual_results_sf*.csv
```

## 7. Implementation scope

This is a PostgreSQL/HypoPG adaptation of BEN_KNAP. It uses single-column candidates, singleton/pair observations, and greedy knapsack selection with a best-singleton comparison. An additive LP fallback handles infeasible multiplicative assignments. The full theoretical approximation guarantee is not established for this adaptation.

This structure is based on the recorded project workflow, not a live inspection of the local repository. Verify file presence before using it as a final repository inventory. SF1 comparison quality and later instrumentation may come from different runs; match timestamps when combining metrics.
