"""Build an HTML results report from the benchmark CSVs (run from the repo root)."""

import csv
import datetime
import json
import re
import sys

csv.field_size_limit(sys.maxsize)
OUT = sys.argv[1]

WORKLOADS = [
    ("TPC-H SF10", "benchmark_results", "tpch_19", "10", "19 queries (paper set: without Q2, Q17, Q20)"),
    ("TPC-DS SF10", "benchmark_results", "tpcds_90", "10", "90 queries (paper set: without Q4, 6, 9, 10, 11, 32, 35, 41, 95)"),
    ("JOB (Join Order Benchmark, IMDB)", "benchmark_results", "JOB_113", None, "all 113 queries"),
    ("TPC-H SF1 (first test run)", "benchmark_results/gadis_tpch_sf1", "tpch_19", "1", "19 queries (paper set)"),
]
VARIANTS = [("extend", None, "Extend"), ("gadis", 1, "GADIS w=1"), ("gadis", 2, "GADIS w=2")]


def rows(directory, algorithm, ident, sf):
    path = f"{directory}/results_{algorithm}_{ident}_queries.csv"
    with open(path) as f:
        data = list(csv.DictReader(f, delimiter=";"))
    header = list(data[0].keys()) if data else []
    queries = [c for c in header if re.fullmatch(r"q\d+|\d+[a-z]\.sql", c)]
    out = []
    for r in data:
        if sf is not None and r["scale factor"] != sf:
            continue
        p = json.loads(r["parameters"].replace("True", "true").replace("False", "false"))
        out.append(
            {
                "budget": p.get("budget_MB"),
                "width": p.get("max_index_width"),
                "cost": sum(json.loads(r[q])["Cost"] for q in queries),
                "runtime": float(r["algorithm runtime"]),
                "size": int(r["memory consumption"]) / 1e6,
                "n": int(r["#indexes"]),
                "requests": int(r["cost requests"]),
            }
        )
    return out


def fmt_time(s):
    return f"{s:.1f}s" if s < 60 else f"{s / 60:.1f}min"


html = []
summary = []
for title, directory, ident, sf, qdesc in WORKLOADS:
    base = rows(directory, "no_index", ident, sf)[-1]["cost"]
    results = {}
    for algorithm, width, label in VARIANTS:
        for r in rows(directory, algorithm, ident, sf):
            if width is None or r["width"] == width:
                results[(label, r["budget"])] = r
    budgets = sorted({b for (_, b) in results})
    labels = [v[2] for v in VARIANTS]
    wins = {label: 0 for label in labels}
    complete = 0
    body = []
    for b in budgets:
        cells = [results.get((label, b)) for label in labels]
        present = [c for c in cells if c]
        best = min(c["cost"] for c in present)
        if len(present) == len(labels):
            complete += 1
            for label, c in zip(labels, cells):
                if abs(c["cost"] - best) < 1e-6:
                    wins[label] += 1
        tds = [f"<td class='b'>{b:,}</td>"]
        for c in cells:
            if c is None:
                tds.append("<td colspan='4' class='pending'>still running / queued</td>")
                continue
            cls = " class='best'" if abs(c["cost"] - best) < 1e-6 else ""
            tds.append(
                f"<td{cls}>{c['cost'] / base * 100:.1f}%</td><td>{fmt_time(c['runtime'])}</td>"
                f"<td>{c['size']:,.0f}</td><td>{c['n']}</td>"
            )
        body.append("<tr>" + "".join(tds) + "</tr>")
    status = "complete" if complete == len(budgets) and all(
        (l, b) in results for l in labels for b in budgets
    ) else f"partial: {sum(1 for _ in results)} of {len(budgets) * len(labels)} runs finished"
    win_text = ", ".join(f"{l}: {w}" for l, w in wins.items())
    summary.append(
        f"<tr><td>{title}</td><td>{base:,.0f}</td><td>{len(budgets)}</td><td>{win_text}</td><td>{status}</td></tr>"
    )
    head1 = "<tr><th rowspan='2'>Budget (MB)</th>" + "".join(
        f"<th colspan='4'>{l}</th>" for l in labels
    ) + "</tr>"
    head2 = "<tr>" + "<th>Rel. cost</th><th>Runtime</th><th>Size MB</th><th>#Idx</th>" * len(labels) + "</tr>"
    html.append(
        f"<section><h2>{title}</h2><p class='meta'>{qdesc} · no-index estimated cost "
        f"{base:,.0f} · {status} · best at each budget (lowest relative cost) highlighted</p>"
        f"<table>{head1}{head2}{''.join(body)}</table></section>"
    )

now = datetime.datetime.now().strftime("%d %b %Y, %H:%M")
page = f"""<!doctype html><html><head><meta charset="utf-8"><title>GADIS Results</title>
<style>
@page {{ size: A4 landscape; margin: 12mm; }}
body {{ font-family: "DejaVu Sans", Arial, sans-serif; font-size: 9pt; color: #1a1a1a; line-height: 1.35; }}
h1 {{ font-size: 16pt; margin: 0 0 2px; }}
h2 {{ font-size: 12pt; margin: 0 0 3px; }}
.sub, .meta {{ color: #555; font-size: 8.5pt; margin: 0 0 6px; }}
table {{ border-collapse: collapse; width: 100%; font-size: 8.3pt; margin-bottom: 4px; }}
th, td {{ border: 1px solid #c8c8c8; padding: 2px 5px; text-align: right; }}
th {{ background: #eef0f3; text-align: center; }}
td.b {{ font-weight: bold; text-align: right; }}
td.best {{ background: #d9f2df; font-weight: bold; }}
td.pending {{ color: #8a6d00; background: #fff7d6; text-align: center; font-style: italic; }}
.left td {{ text-align: left; }}
section {{ break-before: page; }}
ul {{ margin: 3px 0 8px; padding-left: 18px; }}
</style></head><body>
<h1>GADIS vs. Extend – Index Selection Results</h1>
<p class="sub">CS631 project · Magic Mirror evaluation platform (magic-mirror-revisited) · generated {now} IST from the result CSVs</p>

<h2>Setup</h2>
<ul>
<li><b>Algorithms:</b> <b>Extend</b> (Schlosser et al., ICDE 2019; max index width 2), one of the strongest algorithms in the Magic Mirror paper, as the reference.
<b>GADIS</b> (Neuhaus et al., SEKE 2019), a genetic algorithm newly implemented in the platform: population 50, 50 generations, elitism 4, tournament size 3, two-point crossover (p=0.6), mutation probability 0.9 with per-gene rate 0.05, random seed 0.
<b>w=1</b> uses one gene per indexable column (as in the GADIS paper); <b>w=2</b> also adds two-column index candidates.</li>
<li><b>Adaptations of GADIS to the platform:</b> fitness = speed-up S(x) = cost(no indexes) / cost(x), using the optimizer's what-if estimates instead of measured runtime. A repair step drops the indexes with the lowest benefit per MB until a solution fits the storage budget. Finally, indexes that don't reduce the cost are pruned.</li>
<li><b>Measurement:</b> PostgreSQL 18.6 with HypoPG 1.4.2 hypothetical indexes. <b>Rel. cost</b> = estimated workload cost with the chosen indexes ÷ estimated cost with no indexes (lower is better). <b>Runtime</b> = time the algorithm took to choose the indexes. <b>Size</b> = estimated total index size. These are optimizer estimates only; real query execution hasn't been run yet.</li>
<li><b>Limits:</b> GADIS is capped at 30 min per run on TPC-DS and JOB (the same cap the paper gives DTA/Anytime); TPC-H is uncapped. Each configuration ran once (one seed), so GADIS results include random variation.</li>
</ul>

<h2>Summary</h2>
<table class="left"><tr><th>Workload</th><th>No-index cost</th><th>Budgets</th><th>Times best (on budgets where all 3 finished)</th><th>Status</th></tr>
{''.join(summary)}</table>
<ul>
<li><b>TPC-H SF10:</b> GADIS w=2 has the lowest cost at almost every budget. Extend stops improving at 71.1% from 4250 MB, while GADIS w=2 reaches 66.9% at 15000 MB. GADIS is slower (up to about 1 min against Extend's 4 s).</li>
<li><b>TPC-DS SF10:</b> GADIS w=1 is best at small budgets (≤1000 MB); Extend is best from 1500 MB upward. With about 2,200 candidate indexes for w=2, 50 generations aren't enough to search the space well.</li>
<li><b>JOB:</b> indexes help hugely here (cost falls to about 6% of no-index, because the IMDB tables were loaded without primary keys, so no-index plans are very expensive). GADIS w=1 wins at 250, 500 and 2500 MB; Extend wins at most other budgets and stops improving at 5.6% from 5000 MB, about 0.7 points better than GADIS w=1 (6.3%). On JOB, GADIS often hits the 30-min cap before completing 50 generations. GADIS w=2 rows marked "still running / queued" weren't finished when this PDF was made.</li>
</ul>
{''.join(html)}
</body></html>"""
with open(OUT, "w") as f:
    f.write(page)
print("written", OUT)
