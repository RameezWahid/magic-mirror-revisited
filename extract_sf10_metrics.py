import pandas as pd

records = []

with open("optimization_comparison_sf10.log") as f:
    for line in f:
        if "OPTIMIZATION_METRICS " not in line:
            continue

        data = line.split(
            "OPTIMIZATION_METRICS ", 1
        )[1].strip()

        record = {}

        for item in data.split():
            key, value = item.split("=", 1)

            record[key] = (
                value if key == "algorithm"
                else float(value)
            )

        records.append(record)

df = pd.DataFrame(records)

assert len(df) == 26, "Expected 26 runs"

df = df[df["algorithm"] != "no_index"].copy()

df["optimizer_time_pct"] = (
    df["optimizer_time"]
    / df["optimization_runtime"] * 100
).round(2)

df["other_time"] = (
    df["optimization_runtime"]
    - df["optimizer_time"]
    - df["simulation_time"]
)

df.to_csv(
    "exact_optimization_cost_sf10.csv",
    index=False
)

print(df.to_string(index=False))

print("\nSF10 optimization metrics saved successfully")

