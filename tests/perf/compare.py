"""
Compare up to three load-time perf runs
========================================
Reads the `results.json` each `poe perf` run writes and produces one HTML
page putting them side by side: per metric, every run's mean with its
min-max spread, and the change against the first run given.

Usage
-----
    poetry run poe perf-compare results/perf/DEV-2026-08-10-17-50-45 \
                                results/perf/UAT-2026-08-11-09-14-02

    poetry run poe perf-compare <dev-dir> <uat-dir> <core-dir> -o compare.html

Each argument is a run directory (or the `results.json` inside one). Two or
three are the useful cases; one is accepted and just renders that run on its
own. The first run given is the baseline every delta is measured against, so
put the "before" - or the reference environment - first.

The output defaults to `comparison.html` inside the first run's directory.

Reading the result
------------------
Deltas are on the **mean**, and a mean is only as trustworthy as its
spread: a 10% difference between runs whose min-max ranges overlap is
noise, not a regression. Both are on the page for that reason - the bar is
the mean, the whisker is min to max, and the table carries the standard
deviation. Anything inside +/- NOISE_FLOOR_PCT is reported as unchanged
rather than given a direction.

A metric that failed on every run of a given execution has no bar and no
delta; its failure count is still in the table, since "it stopped working
here" is usually the finding.
"""
import argparse
import json
import sys
from pathlib import Path

import reporting
# The palette, page CSS and number formatting are reporting.py's, so a
# comparison and the single-run reports it compares are one document.
from reporting import (BASELINE, CRITICAL, GOOD, GRIDLINE, INK_MUTED,  # noqa: F401
                       INK_PRIMARY, INK_SECONDARY, PAGE_CSS, RUN_COLORS,
                       SURFACE, fmt)

MAX_RUNS = len(RUN_COLORS)

# Below this, a difference in the mean is reported as unchanged: run-to-run
# scatter on these scenarios is comfortably this size.
NOISE_FLOOR_PCT = 5.0


def load_run(path: Path):
    """One run's {label, meta, records, stats} from its results.json."""
    json_path = path if path.suffix == ".json" else path / "results.json"
    if not json_path.is_file():
        raise SystemExit(f"no results.json at {json_path} - is that a perf run directory?")

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    records = payload.get("records")
    if not records:
        raise SystemExit(f"{json_path} has no records to compare")

    meta = payload.get("meta", {})
    # The folder name is the run's identity elsewhere in this repo
    # (<ENV>-<timestamp>), so it is the label unless the file is being read
    # from somewhere else entirely.
    label = json_path.parent.name or meta.get("env") or str(json_path)

    return {
        "label": label,
        "meta": meta,
        "records": records,
        # Recomputed rather than read from the file's own "stats": a run
        # written by an older version of the suite may not carry them, and
        # this keeps one implementation of the arithmetic.
        "stats": reporting.summarize_all(records),
        "dir": json_path.parent,
    }


def all_metrics(runs):
    """Every metric key across the runs, in first-seen order.

    Runs from different versions of the suite do not carry the same
    scenarios - announcements was dropped, for one - so the union is what
    keeps an older run comparable instead of silently dropping columns.
    """
    ordered = []
    for run in runs:
        for key in reporting.metric_keys(run["records"]):
            if key not in ordered:
                ordered.append(key)
    return ordered


def delta(baseline_mean, mean):
    """(percent, kind) for a mean against the baseline's.

    `kind` is one of "none", "flat", "faster", "slower" - rendered into
    words by the caller, so the HTML and the console can say the same
    thing in their own punctuation.
    """
    if baseline_mean in (None, 0) or mean is None:
        return None, "none"
    pct = (mean - baseline_mean) / baseline_mean * 100
    if abs(pct) < NOISE_FLOOR_PCT:
        return pct, "flat"
    return pct, "faster" if pct < 0 else "slower"


DELTA_HTML = {
    "none": ("&mdash;", INK_MUTED),
    "flat": ("~ {pct:+.1f}% unchanged", INK_SECONDARY),
    "faster": ("&darr; {abs_pct:.1f}% faster", GOOD),
    "slower": ("&uarr; {pct:.1f}% slower", CRITICAL),
}

DELTA_TEXT = {
    "none": "-",
    "flat": "~ {pct:+.1f}% unchanged",
    "faster": "-{abs_pct:.1f}% faster",
    "slower": "+{pct:.1f}% slower",
}


def render_delta(template_map, pct, kind):
    return template_map[kind].format(pct=pct or 0, abs_pct=abs(pct or 0))


def _chart_base64(runs, metric):
    """Grouped bars - one per run - with min-max whiskers, as a PNG.

    One chart per metric rather than all metrics on shared axes: a 0.3s
    page open and a 45s build on one scale would flatten the fast ones to
    nothing, and a second y-axis is never the answer.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return None

    import base64
    import io

    plotted = [(i, r) for i, r in enumerate(runs)
               if r["stats"].get(metric, {}).get("mean_s") is not None]
    if not plotted:
        return None
    # A one-bar chart in a comparison is not a comparison - it happens
    # when a metric exists in only one of the runs (a scenario added or
    # dropped between them). The summary table still carries its number.
    if len(plotted) < 2 and len(runs) > 1:
        return None

    fig, ax = plt.subplots(figsize=(5.6, 2.9))
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)

    for x, (run_index, run) in enumerate(plotted):
        s = run["stats"][metric]
        mean = s["mean_s"]
        ax.bar(x, mean, width=0.5, color=RUN_COLORS[run_index], zorder=3)
        # Whisker is the observed min-max, not a confidence interval - it
        # is what makes an overlapping "difference" visibly not one.
        ax.errorbar(x, mean,
                    yerr=[[mean - s["min_s"]], [s["max_s"] - mean]],
                    fmt="none", ecolor=INK_SECONDARY, elinewidth=1.2,
                    capsize=4, capthick=1.2, zorder=4)
        # Direct label on every bar: the relief the palette requires, and
        # with three bars there is no reason to make anyone read a value
        # off an axis.
        ax.annotate(f"{mean:.3f}s", (x, s["max_s"]),
                    textcoords="offset points", xytext=(0, 6),
                    ha="center", fontsize=8.5, color=INK_PRIMARY)

    ax.set_xticks(range(len(plotted)))
    ax.set_xticklabels([r["label"] for _, r in plotted], fontsize=8, color=INK_MUTED)
    ax.set_ylabel("seconds", fontsize=8.5, color=INK_MUTED)
    ax.tick_params(axis="y", labelsize=8, colors=INK_MUTED, length=0)
    ax.tick_params(axis="x", length=0)
    ax.set_title(metric, fontsize=10, color=INK_PRIMARY, pad=14, loc="left")

    ax.grid(axis="y", color=GRIDLINE, linewidth=0.8, zorder=0)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(BASELINE)
    ax.margins(y=0.22)

    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=140, facecolor=SURFACE)
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def build_html(runs, metrics):
    baseline = runs[0]

    run_cards = "\n".join(
        f"""<div class="run">
              <span class="chip" style="background:{RUN_COLORS[i]}"></span>
              <div>
                <div class="run-label">{run['label']}"""
        + (' <span class="tag">baseline</span>' if i == 0 else "")
        + f"""</div>
                <div class="run-meta">{run['meta'].get('url', '')}
                  &middot; {len(run['records'])} runs</div>
              </div>
            </div>"""
        for i, run in enumerate(runs)
    )

    head_cells = "".join(
        f'<th><span class="chip" style="background:{RUN_COLORS[i]}"></span>{run["label"]}</th>'
        + ("" if i == 0 else "<th>vs baseline</th>")
        for i, run in enumerate(runs)
    )

    body_rows = []
    for metric in metrics:
        base_stats = baseline["stats"].get(metric, {})
        cells = []
        for i, run in enumerate(runs):
            s = run["stats"].get(metric)
            if not s:
                cells.append('<td class="num">&mdash;</td>')
                if i:
                    cells.append('<td class="num">&mdash;</td>')
                continue

            failures = (f'<div class="fail">{s["failures"]} failed</div>'
                        if s["failures"] else "")
            spread = (f'<div class="sub">{fmt(s["min_s"])}&ndash;{fmt(s["max_s"])} '
                      f'&middot; sd {fmt(s["stdev_s"])}</div>'
                      if s["mean_s"] is not None else "")
            cells.append(f'<td class="num"><strong>{fmt(s["mean_s"])}</strong>'
                         f'{spread}{failures}</td>')

            if i:
                pct, kind = delta(base_stats.get("mean_s"), s["mean_s"])
                template, color = DELTA_HTML[kind]
                text = template.format(pct=pct or 0, abs_pct=abs(pct or 0))
                cells.append(f'<td class="num" style="color:{color}">{text}</td>')

        body_rows.append(f"<tr><td>{metric}</td>{''.join(cells)}</tr>")

    charts = [(m, _chart_base64(runs, m)) for m in metrics]
    charts_html = "\n".join(
        f'<figure><img src="data:image/png;base64,{png}" alt="{m} by run"></figure>'
        for m, png in charts if png
    ) or '<p class="sub">No charts &mdash; install matplotlib to include them.</p>'

    raw_blocks = []
    for i, run in enumerate(runs):
        keys = reporting.metric_keys(run["records"])
        header = "".join(f'<th class="num">{k}</th>' for k in keys)
        rows = "\n".join(
            f"<tr><td>{r['run']}</td>"
            + "".join(f'<td class="num">{fmt(r.get(k))}</td>' for k in keys)
            + "</tr>"
            for r in run["records"]
        )
        raw_blocks.append(
            f"""<details>
                  <summary><span class="chip" style="background:{RUN_COLORS[i]}"></span>
                    {run['label']} &mdash; every measurement</summary>
                  <table><tr><th>Run</th>{header}</tr>{rows}</table>
                </details>""")

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Perf comparison</title>
<style>{PAGE_CSS}</style>
</head>
<body>
  <h1>Load-time comparison</h1>
  <p class="sub">Deltas are on the mean, against the first run.
     Differences under {NOISE_FLOOR_PCT:.0f}% are reported as unchanged; check the
     min&ndash;max whiskers before believing a larger one.</p>

  <div class="runs">{run_cards}</div>

  <h2>Summary</h2>
  <table>
    <tr><th>Metric</th>{head_cells}</tr>
    {''.join(body_rows)}
  </table>

  <h2>By metric</h2>
  {charts_html}

  <h2>Raw measurements</h2>
  {''.join(raw_blocks)}
</body>
</html>
"""


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("runs", nargs="+", type=Path,
                        help=f"1 to {MAX_RUNS} perf run directories; the first is "
                             f"the baseline")
    parser.add_argument("-o", "--out", type=Path,
                        help="output HTML (default: comparison.html in the first run)")
    args = parser.parse_args()

    if len(args.runs) > MAX_RUNS:
        raise SystemExit(
            f"at most {MAX_RUNS} runs can be compared (given {len(args.runs)}) - "
            f"the palette carries {MAX_RUNS} validated colors, and a fourth would "
            f"have to reuse one")

    runs = [load_run(p) for p in args.runs]
    metrics = all_metrics(runs)
    out = args.out or (runs[0]["dir"] / "comparison.html")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build_html(runs, metrics), encoding="utf-8")

    def plain(value):
        return f"{value:.3f}s" if isinstance(value, (int, float)) else "-"

    baseline = runs[0]
    print(f"baseline: {baseline['label']}")
    for metric in metrics:
        base_mean = baseline["stats"].get(metric, {}).get("mean_s")
        parts = []
        for run in runs[1:]:
            mean = run["stats"].get(metric, {}).get("mean_s")
            pct, kind = delta(base_mean, mean)
            parts.append(f"{run['label']}: {plain(mean)} "
                         f"({render_delta(DELTA_TEXT, pct, kind)})")
        print(f"  {metric}: {plain(base_mean)}"
              + ("  |  " + "  |  ".join(parts) if parts else ""))

    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
