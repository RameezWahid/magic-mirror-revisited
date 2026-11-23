"""Compare the budget-aware tuner (two-phase greedy, Wii, Esc) with the original algorithms.

Usage:
  python scripts/compare_algorithms.py RESULTS.txt OUT_DIR --runs out/rep1/runs.jsonl --esc out/esc3/runs.jsonl

RESULTS.txt is the output of scripts/analyze_results.py (original algorithms: relative
cost % | runtime s | calls per storage budget in MB, or per index count for AutoAdmin and
Drop). Baselines are taken at the 10000 MB budget and K=10. Our tuner is limited by index
count, not storage, so the rows are matched by storage only approximately (see the
storage column). Writes OUT_DIR/fig_compare.png and OUT_DIR/compare_table.tex.
"""
import argparse
import json
import os
import re

OURS = [
    ("Two-phase greedy", "unlimited_k20_none", "runs", "#2a78d6"),
    ("Wii ($\\alpha{=}0.9$)", "unlimited_k20_wii_0.9", "runs", "#eb6834"),
    ("Wii + coverage", "alpha_k20_b1000_wii_coverage_0.9", "runs", "#1baf7a"),
    ("Two-phase + Esc-B (5\\%)", "esc_k20_none_eps0.05", "esc", "#8b5cf6"),
]


def baselines(path):
    out = {}
    for line in open(path):
        m = re.match(r"\s{2}(\w+)\s+\(n=\d+\)\s+(.*)", line)
        if not m:
            continue
        pts = []
        for token in m.group(2).split():
            x, rest = token.split(":")
            rc, rt, calls = rest.split("|")
            pts.append((int(x), float(rc), float(rt), int(calls)))
        out[m.group(1)] = pts
    return out


def load(path):
    with open(path) as f:
        return {r["run_id"]: r for r in map(json.loads, filter(str.strip, f))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("results")
    ap.add_argument("out_dir")
    ap.add_argument("--runs", required=True, help="runs.jsonl with the unlimited/alpha experiments")
    ap.add_argument("--esc", help="runs.jsonl of the esc experiment")
    args = ap.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)
    b = baselines(args.results)
    base = []
    for name in ["Extend", "Relaxation", "DTA", "CoPhy", "DB2Advis"]:
        x, rc, rt, calls = next(p for p in b[name] if p[0] == 10000)
        base.append((name, "10 GB", rc, rt, calls))
    for name in ["AutoAdmin", "Drop"]:
        x, rc, rt, calls = next(p for p in b[name] if p[0] == 10)
        base.append((name, "K=10", rc, rt, calls))
    sources = {"runs": load(args.runs), "esc": load(args.esc) if args.esc else {}}
    mine = []
    for label, run_id, src, color in OURS:
        run = sources[src].get(run_id)
        if run is None:
            continue
        mine.append(
            (label, color, 100 * (1 - run["improvement"]), run["runtime_s"],
             run["statistics"]["whatif_calls"], run["storage_bytes"] / 2**30)
        )

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 2, figsize=(9.6, 3.9), dpi=200)
    for ax, use_calls, xlabel in ((axes[0], True, "what-if calls (log)"), (axes[1], False, "runtime in s (log)")):
        for name, _, rc, rt, calls in base:
            x = calls if use_calls else rt
            ax.scatter(x, rc, s=34, color="#8a8a85", zorder=3, edgecolor="white", linewidth=1)
            offset = {"Relaxation": (-44, -12), "AutoAdmin": (-6, 9)}.get(name, (5, 4))
            ax.annotate(name, (x, rc), xytext=offset, textcoords="offset points", fontsize=7.5, color="#52514e")
        for label, color, rc, rt, calls, _ in mine:
            ax.scatter(calls if use_calls else rt, rc, s=46, color=color, zorder=4,
                       edgecolor="white", linewidth=1, label=label.replace("\\%", "%").replace("$\\alpha{=}0.9$", "α=0.9"))
        ax.set_xscale("log")
        ax.set_xlabel(xlabel, fontsize=8.5)
        ax.grid(alpha=0.25, linewidth=0.6)
        ax.tick_params(labelsize=8)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
    axes[0].set_ylabel("relative workload cost (%), lower is better", fontsize=8.5)
    axes[0].legend(fontsize=7, frameon=False, loc="upper right")
    fig.suptitle("TPC-H SF10: quality against effort at about 10 GB / K=10 (grey: original algorithms; colour: ours)", fontsize=9)
    fig.tight_layout()
    fig.savefig(os.path.join(args.out_dir, "fig_compare.png"))

    lines = [r"\begin{tabular}{llrrr}", r"\toprule",
             r"Algorithm & Setting & Rel.\ cost \% & What-if calls & Runtime s\\", r"\midrule"]
    for name, setting, rc, rt, calls in base:
        lines.append(f"{name} & {setting} & {rc:.1f} & {calls} & {rt:.1f}\\\\")
    for label, _, rc, rt, calls, gb in mine:
        lines.append(f"{label} & K=20, {gb:.1f} GB & {rc:.1f} & {calls} & {rt:.1f}\\\\")
    lines += [r"\bottomrule", r"\end{tabular}"]
    with open(os.path.join(args.out_dir, "compare_table.tex"), "w") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
