import csv
import json
import re
import pandas as pd

algorithms = [
    "extend",
    "relaxation",
    "db2advis",
    "anytime",
    "ben_knap"
]

budgets = [1000, 2500, 5000, 10000, 20000]

def read_results(name):
    path = (
        f"benchmark_results/"
        f"results_{name}_tpch_19_queries.csv"
    )

    with open(path) as f:
        return list(csv.DictReader(f, delimiter=";"))

def workload_cost(row):
    return sum(
        json.loads(value)["Cost"]
        for key, value in row.items()
        if key and re.fullmatch(r"q\d+", key)
    )

baseline = [
    r for r in read_results("no_index")
    if float(r["scale factor"]) == 10
][-1]

base_cost = workload_cost(baseline)

print("SF10 NoIndex workload cost:", base_cost)

results = []

for name in algorithms:
    rows = read_results(name)

    for budget in budgets:

        matches = [
            r for r in rows
            if float(r["scale factor"]) == 10
            and json.loads(r["parameters"]).get(
                "budget_MB"
            ) == budget
            and r["date"] >= baseline["date"]
        ]

        if not matches:
            print("Missing:", name, budget)
            continue

        r = matches[-1]

        cost = workload_cost(r)
        storage = float(r["memory consumption"]) / 1e6

        results.append({
            "algorithm": name,
            "budget_MB": budget,
            "workload_cost": round(cost, 2),
            "improvement_pct": round(
                (base_cost - cost) / base_cost * 100, 4
            ),
            "storage_MB": round(storage, 4),
            "indexes": int(r["#indexes"]),
            "budget_satisfied": storage <= budget
        })

df = pd.DataFrame(results)

assert len(df) == 25, "Expected 25 configurations"

metrics = pd.read_csv(
    "exact_optimization_cost_sf10.csv"
)

df = df.merge(
    metrics,
    on=["algorithm", "budget_MB"],
    validate="one_to_one"
)

assert len(df) == 25, "Missing optimization metrics"

df.to_csv("comparison_sf10.csv", index=False)

print("\nCOMPLETE SF10 RESULTS\n")

print(df.to_string(index=False))

print("\nWORKLOAD IMPROVEMENT COMPARISON (%)\n")

print(df.pivot(
    index="budget_MB",
    columns="algorithm",
    values="improvement_pct"
))

print("\nWORKLOAD COST COMPARISON\n")

print(df.pivot(
    index="budget_MB",
    columns="algorithm",
    values="workload_cost"
))

print("\nResults saved to comparison_sf10.csv")
