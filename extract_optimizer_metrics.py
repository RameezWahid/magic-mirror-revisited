import pandas as pd

records = []

with open("optimization_comparison_sf1.log") as f:

    for line in f:

        if "OPTIMIZATION_METRICS " not in line:
            continue

        data = line.split(
            "OPTIMIZATION_METRICS ", 1
        )[1].strip()

        record = {}

        for item in data.split():

            key, value = item.split("=", 1)

            if key == "algorithm":
                record[key] = value
            else:
                record[key] = float(value)

        records.append(record)

df = pd.DataFrame(records)

if df.empty:
    raise RuntimeError("No optimization metrics found")

df = df[df["algorithm"] != "no_index"].copy()

df["optimizer_time_pct"] = (
    df["optimizer_time"]
    / df["optimization_runtime"]
    * 100
).round(2)

df["simulation_time_pct"] = (
    df["simulation_time"]
    / df["optimization_runtime"]
    * 100
).round(2)

df["other_time"] = (
    df["optimization_runtime"]
    - df["optimizer_time"]
    - df["simulation_time"]
)

df["avg_optimizer_time_ms"] = (
    df["optimizer_time"]
    / df["optimizer_calls"].replace(0, float("nan"))
    * 1000
).round(4)

df.to_csv(
    "exact_optimization_cost_sf1.csv",
    index=False
)

print("\n===== OPTIMIZER METRICS =====\n")

print(df.to_string(index=False))

print("\n===== OPTIMIZER CALL COMPARISON =====\n")

print(df.pivot(
    index="budget_MB",
    columns="algorithm",
    values="optimizer_calls"
))

print("\n===== OPTIMIZER TIME COMPARISON =====\n")

print(df.pivot(
    index="budget_MB",
    columns="algorithm",
    values="optimizer_time"
))

print("\nResults saved to exact_optimization_cost_sf1.csv")
