import pandas as pd

df = pd.read_csv("comparison_sf1.csv")

df["uncached_requests"] = (
    df["cost_requests"] - df["cache_hits"]
)

df["cache_hit_pct"] = (
    df["cache_hits"] / df["cost_requests"] * 100
).round(2)

df["relative_workload_cost"] = (
    100 - df["improvement_pct"]
)

columns = [
    "algorithm",
    "budget_MB",
    "cost_requests",
    "cache_hits",
    "uncached_requests",
    "cache_hit_pct",
    "runtime_sec",
    "relative_workload_cost",
    "improvement_pct"
]

result = df[columns]

result.to_csv(
    "optimization_cost_sf1.csv",
    index=False
)

print("\n===== OPTIMIZATION COST =====\n")

print(result.to_string(index=False))

print("\n===== UNCACHED REQUESTS =====\n")

print(result.pivot(
    index="budget_MB",
    columns="algorithm",
    values="uncached_requests"
))

print("\n===== OPTIMIZATION RUNTIME =====\n")

print(result.pivot(
    index="budget_MB",
    columns="algorithm",
    values="runtime_sec"
))

print("\nResults saved successfully.")
