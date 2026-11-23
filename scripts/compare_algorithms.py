"""Compare the budget-aware tuner (two-phase greedy, Wii, Esc) with the original algorithms.

Usage:  python scripts/compare_algorithms.py out/analysis/RESULTS.txt OUT_DIR

Reads the "Platform runs" block of RESULTS.txt (scripts/analyze_results.py), which has, per
algorithm and storage budget (MB) or index count, the mean over repetitions of
relative cost % | runtime s | what-if calls. Writes OUT_DIR/fig_compare.png (quality and
calls against the storage budget) and OUT_DIR/compare_table.tex (5, 10 and 15 GB).
"""
import os
import re
import sys

ORDER = [
    ("Extend", "Extend"), ("Relaxation", "Relaxation"), ("DTA", "DTA"), ("CoPhy", "CoPhy"),
    ("DB2Advis", "DB2Advis"), ("Two-phase greedy", "Two-phase greedy"),
    ("Two-phase greedy + Wii", "+ Wii"), ("Two-phase greedy + Esc", "+ Esc"),
    ("Two-phase greedy + Wii + Esc", "+ Wii + Esc"),
]
KEYED = [("AutoAdmin", 10, "K=10"), ("Drop", 10, "K=10"), ("Dexter", 10, "10\\%")]
COLORS = {"Extend": "#2a78d6", "Relaxation": "#eb6834", "DTA": "#1baf7a", "CoPhy": "#eda100",
          "DB2Advis": "#e87ba4", "Two-phase greedy": "#0b0b0b", "+ Wii": "#6250d6", "+ Esc": "#e34948"}


def parse(path):
    data = {}
    for line in open(path):
        m = re.match(r"\s{2}(.+?)\s*\(n=\d+\)\s+(.*)", line)
        if not m:
            continue
        cells = {}
        for token in re.findall(r"(\d+):([\d.]+)\|([\d.]+)\|(\d+)", m.group(2)):
            cells[int(token[0])] = (float(token[1]), float(token[2]), int(token[3]))
        data[m.group(1).strip()] = cells
    return data


def main(results, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    data = parse(results)
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 2, figsize=(9.6, 3.9), dpi=200)
    for key, label in ORDER:
        if key not in data or label == "+ Wii + Esc":
            continue
        xs = sorted(data[key])
        style = dict(color=COLORS[label], marker="o", markersize=3.5, linewidth=1.6,
                     linestyle="--" if label.startswith("+") else "-")
        axes[0].plot([x / 1000 for x in xs if x >= 1500], [data[key][x][0] for x in xs if x >= 1500],
                     label=label, **style)
        axes[1].plot([x / 1000 for x in xs], [data[key][x][2] for x in xs], label=label, **style)
    axes[0].set(xlabel="storage budget (GB)", ylabel="relative workload cost (%), lower is better")
    axes[1].set(xlabel="storage budget (GB)", ylabel="what-if calls per run (log)", yscale="log")
    axes[0].legend(fontsize=7, frameon=False, ncol=2, loc="upper right")
    for ax in axes:
        ax.grid(alpha=0.25, linewidth=0.6)
        ax.tick_params(labelsize=8)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
    fig.suptitle("TPC-H SF10, three repetitions: original algorithms and the budget-aware tuner", fontsize=9)
    fig.tight_layout()
    fig.savefig(os.path.join(out_dir, "fig_compare.png"))

    lines = [r"\begin{tabular}{lrrrrr}", r"\toprule",
             r"Algorithm & 5\,GB & 10\,GB & 15\,GB & Calls (10\,GB) & Runtime s (10\,GB)\\",
             r"\midrule"]
    def row(name, rel, calls, runtime):
        cells = " & ".join("--" if v is None else f"{v:.1f}" for v in rel)
        lines.append(f"{name} & {cells} & {calls} & {runtime:.1f}\\\\")
    for key, label in ORDER:
        if key in data:
            d = data[key]
            name = label if not label.startswith("+") else "\\quad " + label
            row(name, [d.get(b, (None,))[0] for b in (5000, 10000, 15000)], d[10000][2], d[10000][1])
    lines.append(r"\midrule")
    for key, k, setting in KEYED:
        if key in data and k in data[key]:
            rel, rt, calls = data[key][k]
            lines.append(f"{key} ({setting}) & \\multicolumn{{3}}{{c}}{{{rel:.1f}}} & {calls} & {rt:.1f}\\\\")
    lines += [r"\bottomrule", r"\end{tabular}"]
    with open(os.path.join(out_dir, "compare_table.tex"), "w") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main(*sys.argv[1:3])
