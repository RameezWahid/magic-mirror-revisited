"""Turns all experiment outputs into figures (PNG) and RESULTS tables (text).

Usage: PYTHONPATH=. .venv/bin/python scripts/analyze_results.py OUT_DIR

Inputs (whatever exists is used):
  benchmark_results/<run>/rep_*/results_*.csv   platform runs (8 algorithms + ours)
  benchmark_results/wii_pg18/<name>/runs.jsonl   budget-aware experiments
  benchmark_results/wii_pg18/<name>/assumptions.json
  benchmark_results/execution/*.csv             real execution (Objective 3)

CoPhy's runtime is what_if_time_s + solve_time_s and its cost requests are
what_if_cost_requests (the platform columns only hold the solver part when the
input is reused). This is enforced in load_platform_run (see its test).
"""

import ast
import csv
import glob
import json
import os
import re
import statistics
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

# Validated categorical palette (dataviz skill reference palette, light mode), fixed
# order; colour follows the algorithm in every chart.
PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
ALGORITHMS = ["extend", "relaxation", "anytime", "cophy", "db2advis", "auto_admin", "drop", "dexter"]
LABELS = {
    "extend": "Extend", "relaxation": "Relaxation", "anytime": "DTA", "cophy": "CoPhy",
    "db2advis": "DB2Advis", "auto_admin": "AutoAdmin", "drop": "Drop", "dexter": "Dexter",
    "two_phase_greedy": "Two-phase greedy", "two_phase_greedy_wii": "Two-phase greedy + Wii",
    "two_phase_greedy_esc": "Two-phase greedy + Esc", "two_phase_greedy_wii_esc": "Two-phase greedy + Wii + Esc",
}
COLOR = dict(zip(ALGORITHMS, PALETTE))
INK, MUTED, GRID = "#0b0b0b", "#52514e", "#e4e3df"
MODES = {"none": ("Baseline", PALETTE[0]), "wii": ("Wii", PALETTE[1]),
         "wii_coverage": ("Wii-coverage", PALETTE[2])}

plt.rcParams.update({
    "font.size": 9, "axes.edgecolor": MUTED, "axes.labelcolor": INK, "text.color": INK,
    "xtick.color": MUTED, "ytick.color": MUTED, "axes.grid": True, "grid.color": GRID,
    "grid.linewidth": 0.6, "axes.spines.top": False, "axes.spines.right": False,
    "legend.frameon": False, "lines.linewidth": 2, "lines.markersize": 5,
    "figure.dpi": 150, "savefig.bbox": "tight",
})


def parse_parameters(text):
    text = text.replace("null", "None").replace("true", "True").replace("false", "False")
    return ast.literal_eval(text)


def total_cost(row):
    return sum(json.loads(row[c])["Cost"] for c in row if re.fullmatch(r"q\d+", c))


def load_platform_run(rep_dir):
    """{algorithm: [record]} for one repetition folder."""
    out = {}
    base = None
    for path in glob.glob(os.path.join(rep_dir, "results_*.csv")):
        name = re.search(r"results_(.*)_(tpch|tpcds)_", os.path.basename(path))[1]
        for row in csv.DictReader(open(path), delimiter=";"):
            p = parse_parameters(row["parameters"])
            runtime = float(row["algorithm runtime"])
            requests = int(row["cost requests"] or 0)
            hits = int(row["cache hits"] or 0)
            calls = requests - hits
            label = name
            if name == "cophy":
                runtime = p["what_if_time_s"] + p["solve_time_s"]
                calls = p["what_if_cost_requests"]
            if name == "two_phase_greedy":
                label = "two_phase_greedy_wii" if p.get("interception") == "wii" else name
                if p.get("esc", "none") != "none":
                    label += "_esc"
                calls = p["whatif_calls"]
            if name == "db2advis":
                calls = requests
            record = {
                "key": p.get("budget_MB", p.get("max_indexes", p.get("min_saving_percentage"))),
                "cost": total_cost(row),
                "memory_mb": float(row["memory consumption"]) / 1e6,
                "indexes": int(row["#indexes"]),
                "runtime": runtime,
                "calls": calls,
            }
            if name == "no_index":
                base = record["cost"]
            out.setdefault(label, []).append(record)
    for records in out.values():
        for r in records:
            r["relative"] = 100 * r["cost"] / base
    return out


def load_platform(run_dir):
    """Mean over repetitions: {algorithm: [record with mean/min/max relative]}."""
    reps = [load_platform_run(d) for d in sorted(glob.glob(os.path.join(run_dir, "rep_*")))]
    reps = [r for r in reps if r]
    merged = {}
    for algorithm in reps[0] if reps else []:
        rows = []
        for i, record in enumerate(reps[0][algorithm]):
            same = [r[algorithm][i] for r in reps if algorithm in r and len(r[algorithm]) > i]
            row = dict(record)
            for field in ("relative", "memory_mb", "runtime", "calls"):
                values = [s[field] for s in same]
                row[field] = statistics.mean(values)
                row[field + "_min"], row[field + "_max"] = min(values), max(values)
            row["reps"] = len(same)
            rows.append(row)
        merged[algorithm] = rows
    return merged


def save(fig, out_dir, name):
    fig.savefig(os.path.join(out_dir, name))
    plt.close(fig)


def plot_reproduction(data, title, out_dir, prefix):
    if not data:
        return
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4))
    for algorithm in ALGORITHMS:
        rows = sorted(data.get(algorithm, []), key=lambda r: r["memory_mb"])
        if not rows:
            continue
        x = [r["memory_mb"] / 1000 for r in rows]
        kw = dict(color=COLOR[algorithm], marker="o", label=LABELS[algorithm])
        ax1.plot(x, [r["relative"] for r in rows], **kw)
        ax2.plot(x, [r["runtime"] for r in rows], **kw)
    ax1.set(xlabel="Index storage consumption (GB)", ylabel="Relative workload cost (%)",
            title=f"{title}: solution quality")
    ax2.set(xlabel="Index storage consumption (GB)", ylabel="Algorithm runtime (s, log)",
            yscale="log", title=f"{title}: runtime")
    ax2.legend(loc="center left", bbox_to_anchor=(1.0, 0.5))
    save(fig, out_dir, f"{prefix}_reproduction.png")


def plot_new_algorithms(data, title, out_dir, prefix):
    ours = [a for a in ("two_phase_greedy", "two_phase_greedy_wii") if a in data]
    if not ours:
        return
    fig, axes = plt.subplots(1, 3, figsize=(14, 4))
    for algorithm in ("extend", "anytime", "relaxation"):
        rows = sorted(data.get(algorithm, []), key=lambda r: r["memory_mb"])
        if rows:
            x = [r["memory_mb"] / 1000 for r in rows]
            kw = dict(color=COLOR[algorithm], marker="o", label=LABELS[algorithm], alpha=0.9)
            axes[0].plot(x, [r["relative"] for r in rows], **kw)
            axes[1].plot(x, [r["runtime"] for r in rows], **kw)
            axes[2].plot(x, [max(1, r["calls"]) for r in rows], **kw)
    for algorithm, style in zip(ours, ("-", "--")):
        rows = sorted(data[algorithm], key=lambda r: r["memory_mb"])
        x = [r["memory_mb"] / 1000 for r in rows]
        kw = dict(color=INK, linestyle=style, marker="s", label=LABELS[algorithm])
        axes[0].plot(x, [r["relative"] for r in rows], **kw)
        axes[1].plot(x, [r["runtime"] for r in rows], **kw)
        axes[2].plot(x, [max(1, r["calls"]) for r in rows], **kw)
    axes[0].set(xlabel="Index storage (GB)", ylabel="Relative workload cost (%)", title=f"{title}: quality")
    axes[1].set(xlabel="Index storage (GB)", ylabel="Runtime (s, log)", yscale="log", title="Runtime")
    axes[2].set(xlabel="Index storage (GB)", ylabel="What-if calls (log)", yscale="log", title="What-if calls")
    axes[2].legend(loc="center left", bbox_to_anchor=(1.0, 0.5))
    save(fig, out_dir, f"{prefix}_new_algorithms.png")


def plot_quality_vs_calls(data, title, out_dir, prefix):
    names = [a for a in ALGORITHMS + ["two_phase_greedy", "two_phase_greedy_wii"]
             if a in data and a != "dexter"]
    if not names:
        return
    cols = 5
    rows_n = (len(names) + cols - 1) // cols
    fig, axes = plt.subplots(rows_n, cols, figsize=(3.0 * cols, 2.6 * rows_n), sharex=True, sharey=True)
    axes = axes.flatten()
    every = [(max(1, r["calls"]), r["relative"]) for a in names for r in data[a]]
    for ax, algorithm in zip(axes, names):
        ax.scatter(*zip(*every), s=8, color=GRID)
        own = [(max(1, r["calls"]), r["relative"]) for r in data[algorithm]]
        ax.scatter(*zip(*own), s=16, color=COLOR.get(algorithm, INK))
        ax.set_title(LABELS[algorithm])
        ax.set_xscale("log")
    for ax in axes[len(names):]:
        ax.axis("off")
    fig.supxlabel("What-if calls per run (log; cache hits excluded)")
    fig.supylabel("Relative workload cost (%)")
    fig.suptitle(f"{title}: solution quality vs. what-if calls (each dot one budget)")
    save(fig, out_dir, f"{prefix}_quality_vs_calls.png")


def load_jsonl(path):
    if not os.path.exists(path):
        return []
    return [json.loads(line) for line in open(path) if line.strip()]


def plot_wii(runs, title, out_dir, prefix, lines):
    main = [r for r in runs if r["run_id"].startswith("wii_")]
    if not main:
        return
    budgets = sorted({r["parameters"]["whatif_budget"] for r in main})
    best_budget = budgets[-1]
    budgets = budgets[:-1]
    fig, axes = plt.subplots(1, 2, figsize=(11, 3.8), sharey=True)
    lines.append(f"\n[{title}] Wii: improvement (%) of the final configuration, alpha=0.9")
    lines.append(f"{'K':>3} {'B':>6} " + " ".join(f"{MODES[m][0]:>13}" for m in MODES)
                 + "   skipped(Wii) calls(Wii)")
    for ax, k in zip(axes, (10, 20)):
        width = 0.8 / len(MODES)
        for i, (mode, (label, color)) in enumerate(MODES.items()):
            values = []
            for b in budgets:
                hit = [r for r in main if r["parameters"]["max_indexes"] == k
                       and r["parameters"]["whatif_budget"] == b
                       and r["parameters"]["interception"] == mode]
                values.append(100 * hit[0]["improvement"] if hit else 0)
            xs = [j + (i - 1) * width for j in range(len(budgets))]
            ax.bar(xs, values, width=width * 0.92, color=color, label=label)
        best = [100 * r["improvement"] for r in main if r["parameters"]["max_indexes"] == k
                and r["parameters"]["whatif_budget"] == best_budget]
        if best:
            ax.axhline(max(best), color=INK, linestyle=":", linewidth=1.2,
                       label=f"Best (B={best_budget})")
        ax.set_xticks(range(len(budgets)), [str(b) for b in budgets])
        ax.set(xlabel="Budget B (what-if calls)", title=f"{title}, K = {k}")
        for b in budgets:
            row = {}
            for mode in MODES:
                hit = [r for r in main if r["parameters"]["max_indexes"] == k
                       and r["parameters"]["whatif_budget"] == b
                       and r["parameters"]["interception"] == mode]
                row[mode] = hit[0] if hit else None
            text = " ".join(f"{100 * row[m]['improvement']:13.3f}" if row[m] else f"{'-':>13}" for m in MODES)
            wii = row["wii"]
            extra = (f"   {wii['statistics']['skipped_calls']:>7} {wii['statistics']['whatif_calls']:>10}"
                     if wii else "")
            lines.append(f"{k:>3} {b:>6} {text}{extra}")
        lines.append(f"{k:>3} best   " + (f"{max(best):.2f} (B={best_budget})" if best else "-"))
    axes[0].set_ylabel("Improvement of final configuration (%)")
    axes[1].legend(loc="center left", bbox_to_anchor=(1.0, 0.5))
    save(fig, out_dir, f"{prefix}_wii.png")


def report_alpha_random(runs, title, lines):
    alpha = [r for r in runs if r["run_id"].startswith("alpha_")]
    if alpha:
        lines.append(f"\n[{title}] Confidence threshold (K=20): improvement % / skipped calls")
        for mode in ("wii", "wii_coverage"):
            cells = []
            for a in (0.5, 0.8, 0.9, 0.95):
                hit = [r for r in alpha if r["parameters"]["interception"] == mode
                       and r["parameters"]["alpha"] == a]
                cells.append(f"a={a}: {100 * hit[0]['improvement']:.2f}% / {hit[0]['statistics']['skipped_calls']}"
                             if hit else f"a={a}: -")
            lines.append(f"  {MODES[mode][0]:<13} " + "   ".join(cells))
    rnd = [r for r in runs if r["run_id"].startswith("random_")]
    if rnd:
        values = [100 * r["improvement"] for r in rnd]
        lines.append(f"[{title}] Random skipping (p=0.9, 3 seeds): "
                     f"{', '.join(f'{v:.2f}' for v in values)} % (mean {statistics.mean(values):.2f})")


def plot_gaps(runs, title, out_dir, prefix, lines):
    gap_runs = [r for r in runs if "gaps" in r]
    if not gap_runs:
        return
    gaps = [100 * (d - c) / d for d, c in gap_runs[0]["gaps"] if d > 0]
    gaps.sort()
    fig, ax = plt.subplots(figsize=(5, 3.4))
    ax.plot(gaps, [(i + 1) / len(gaps) for i in range(len(gaps))], color=PALETTE[0])
    ax.axvline(5, color=MUTED, linestyle=":", linewidth=1)
    ax.set(xlabel="Relative gap (derived - what-if) / derived (%)", ylabel="Cumulative share of calls",
           title=f"{title}: derived vs what-if cost (baseline, K=20)", xlim=(-5, 100))
    save(fig, out_dir, f"{prefix}_gaps.png")
    below = sum(g < 5 for g in gaps) / len(gaps)
    negative = sum(g < -1e-6 for g in gaps) / len(gaps)
    lines.append(f"[{title}] Gap derived vs what-if over {len(gaps)} calls: {100 * below:.1f}% below 5%; "
                 f"{100 * negative:.2f}% negative (what-if > derived, monotonicity violated)")


def report_unlimited(runs, title, lines):
    rows = [r for r in runs if r["run_id"].startswith("unlimited_")]
    if rows:
        lines.append(f"\n[{title}] No budget (K=20): improvement / what-if calls / tuning time")
        for r in rows:
            lines.append(f"  {r['run_id']:<22} {100 * r['improvement']:6.2f}%  "
                         f"{r['statistics']['whatif_calls']:>7} calls  {r['runtime_s']:8.1f}s")


def report_assumptions(path, title, lines):
    if not os.path.exists(path):
        return
    data = json.load(open(path))
    tol = 1e-6
    mono_total = mono_yes = 0
    for _, empty, cz, cxz, cx in data["monotonicity_points"]:
        for ok in (empty + tol >= cz, empty + tol >= cxz, cz + tol >= cxz):
            mono_total += 1
            mono_yes += ok
    sub = data["submodularity_points"]
    violations = []
    for _, empty, cz, cx, cxz in sub:
        delta = (cx - cxz) - (empty - cz)  # how much more z saves next to x than alone
        if delta > tol * max(1.0, empty):
            violations.append(100 * delta / empty)
    sub_yes = len(sub) - len(violations)
    lines.append(f"\n[{title}] Cost-function assumptions on PostgreSQL ({data['queries']} queries, "
                 f"{data['seconds']:.0f}s):")
    lines.append(f"  Monotonicity: {mono_yes}/{mono_total} checks hold = {100 * mono_yes / max(1, mono_total):.1f}%")
    lines.append(f"  Submodularity: {sub_yes}/{len(sub)} hold = {100 * sub_yes / max(1, len(sub)):.1f}%")
    if violations:
        violations.sort()
        lines.append(f"  Violation size (% of c(q,empty)): median {statistics.median(violations):.2f}, "
                     f"p90 {violations[int(0.9 * len(violations)) - 1]:.2f}, max {violations[-1]:.2f}")


def report_execution(path, title, out_dir, prefix, lines):
    if not os.path.exists(path):
        return
    rows = list(csv.DictReader(open(path)))
    base = {r["query"]: r for r in rows if r["algorithm"] == "no_index"}
    if not base:
        return
    configs = {}
    for r in rows:
        configs.setdefault((r["algorithm"], r["setting"]), []).append(r)
    base_est = sum(float(r["estimated_cost"]) for r in base.values())
    base_time = sum(float(r["runtime_ms"]) for r in base.values())
    lines.append(f"\n[{title}] Real execution (median of 3 runs) vs. estimate, no-index runtime "
                 f"{base_time / 1000:.1f}s")
    lines.append(f"  {'algorithm':<22} {'setting':>7} {'#idx':>4} {'est. impr.':>10} {'real impr.':>10} "
                 f"{'slower q (>10%)':>15}")
    points = []
    for (algorithm, setting), rs in sorted(configs.items()):
        if algorithm == "no_index":
            continue
        est = sum(float(r["estimated_cost"]) for r in rs)
        real = sum(float(r["runtime_ms"]) for r in rs)
        slower = [r["query"] for r in rs if float(r["runtime_ms"]) > 1.1 * float(base[r["query"]]["runtime_ms"])]
        e, a = 100 * (1 - est / base_est), 100 * (1 - real / base_time)
        points.append((algorithm, e, a))
        lines.append(f"  {LABELS.get(algorithm, algorithm):<22} {setting:>7} {rs[0]['indexes']:>4} "
                     f"{e:9.1f}% {a:9.1f}% {len(slower):>6} {','.join('q' + s for s in slower)}")
    fig, ax = plt.subplots(figsize=(5, 4))
    for algorithm, e, a in points:
        ax.scatter(e, a, color=COLOR.get(algorithm, INK), s=30,
                   label=LABELS.get(algorithm, algorithm))
    lim = [min(0, *[p[2] for p in points]) - 5, 100]
    ax.plot(lim, lim, color=MUTED, linestyle="--", linewidth=1)
    handles, labels = ax.get_legend_handles_labels()
    unique = dict(zip(labels, handles))
    ax.legend(unique.values(), unique.keys(), fontsize=7, loc="center left", bbox_to_anchor=(1, 0.5))
    ax.set(xlabel="Estimated improvement (%)", ylabel="Measured improvement (%)",
           title=f"{title}: estimate vs. real execution")
    save(fig, out_dir, f"{prefix}_execution.png")


def report_platform_table(data, title, lines):
    if not data:
        return
    lines.append(f"\n[{title}] Platform runs (mean over repetitions): relative cost % | runtime s | calls")
    for algorithm in ALGORITHMS + ["two_phase_greedy", "two_phase_greedy_wii",
                                   "two_phase_greedy_esc", "two_phase_greedy_wii_esc"]:
        rows = data.get(algorithm)
        if not rows:
            continue
        reps = rows[0]["reps"]
        cells = "  ".join(f"{r['key']}:{r['relative']:.1f}|{r['runtime']:.1f}|{int(r['calls'])}" for r in rows)
        lines.append(f"  {LABELS[algorithm]:<24}(n={reps}) {cells}")
        spread = max(r["relative_max"] - r["relative_min"] for r in rows)
        if reps > 1:
            lines.append(f"  {'':<24}max spread over repetitions: {spread:.2f} pts")


def main():
    out_dir = sys.argv[1]
    os.makedirs(out_dir, exist_ok=True)
    lines = ["RESULTS (generated by scripts/analyze_results.py)", "=" * 78]
    runs_dir = "benchmark_results"
    platform = {
        ("TPC-H SF10", "tpch_sf10"): "tpch_sf10_paper_pg18.6",
        ("TPC-DS SF10", "tpcds_sf10"): "tpcds_sf10_paper_pg18.6",
        ("TPC-H SF1", "tpch_sf1"): "tpch_sf1_paper_tpg_pg18.6",
    }
    for (title, prefix), run in platform.items():
        path = os.path.join(runs_dir, run)
        if not os.path.isdir(path):
            continue
        data = load_platform(path)
        # Greedy, Wii and Esc runs (configs/tpch_sf10_new_algos.json), if present
        extra = os.path.join(runs_dir, run.replace("_paper_", "_new_algos_"))
        if os.path.isdir(extra):
            for algorithm, rows in load_platform(extra).items():
                if algorithm != "no_index":
                    data[algorithm] = rows
        report_platform_table(data, title, lines)
        plot_reproduction(data, title, out_dir, prefix)
        plot_new_algorithms(data, title, out_dir, prefix)
        plot_quality_vs_calls(data, title, out_dir, prefix)
    for version, folder_name in (("PG 18.6", "wii_pg18"),):
        tag = folder_name.split("_")[-1]
        for name, title, prefix in (("tpch_sf10", "TPC-H SF10 (19 q.)", "tpch_sf10"),
                                    ("tpch22_sf10", "TPC-H SF10 (22 q.)", "tpch22_sf10"),
                                    ("tpcds_sf10", "TPC-DS SF10", "tpcds_sf10")):
            folder = os.path.join(runs_dir, folder_name, name)
            if not os.path.isdir(folder):
                continue
            runs = load_jsonl(os.path.join(folder, "runs.jsonl"))
            title_v, prefix_v = f"{title}, {version}", f"wii_{prefix}_{tag}"
            report_assumptions(os.path.join(folder, "assumptions.json"), title_v, lines)
            plot_wii(runs, title_v, out_dir, prefix_v, lines)
            report_alpha_random(runs, title_v, lines)
            plot_gaps(runs, title_v, out_dir, prefix_v, lines)
            report_unlimited(runs, title_v, lines)
    report_execution(os.path.join(runs_dir, "execution", "tpch_sf1_pg18.6.csv"),
                     "TPC-H SF1", out_dir, "tpch_sf1", lines)
    report_execution(os.path.join(runs_dir, "execution", "tpch_sf10_pg18.6.csv"),
                     "TPC-H SF10", out_dir, "tpch_sf10", lines)
    with open(os.path.join(out_dir, "RESULTS.txt"), "w") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
