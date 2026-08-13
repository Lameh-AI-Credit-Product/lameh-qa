"""Stats and report generation for the load-time perf test.

Driven purely by whichever metric keys appear in the records, so a new
scenario needs no changes here. A record looks like:

    {"run": 1, "dashboard_load_s": 1.23, "analysis_load_s": "F", ...}

Any key other than "run" is treated as a timed metric and gets its own
summary row and chart. A value of "F" marks a scenario that failed on that
run: excluded from the stats and charts, still shown as-is in the CSV, JSON
and HTML raw data.

This module touches no Playwright and no browser, which is deliberate - it
is the part of the suite that can be exercised without a session.

It also owns the look of both HTML pages the suite produces: the palette,
`PAGE_CSS` and `fmt` below are imported by compare.py, so a single run's
report and a comparison of three of them read as the same document rather
than two tools that happen to live in one folder.
"""
import csv
import json
import statistics
from datetime import datetime
from pathlib import Path

FAILURE_MARK = "F"

# --- Shared page design ------------------------------------------------------
# Categorical slots 1-3, light mode, from the data-viz reference palette.
# Validated as a set for all pairs: worst CVD dE 9.2, worst normal-vision
# dE 24.0. Aqua sits below 3:1 on this surface, so every bar carries a
# visible value label and the full table is on the page - the palette's
# documented relief for exactly that case. A single-run report uses slot 1
# only; comparison.py assigns the rest, one per run.
RUN_COLORS = ["#2a78d6", "#eb6834", "#1baf7a"]

SURFACE = "#fcfcfb"
INK_PRIMARY = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
GRIDLINE = "#e1e0d9"
BASELINE = "#c3c2b7"
GOOD = "#006300"      # faster
CRITICAL = "#d03b3b"  # slower, and failures

PAGE_CSS = f"""
  body {{ font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
         margin: 2rem auto; max-width: 62rem; padding: 0 1.5rem;
         background: {SURFACE}; color: {INK_PRIMARY}; }}
  h1 {{ font-size: 1.4rem; margin-bottom: 0.25rem; }}
  h2 {{ font-size: 1.05rem; margin: 2.5rem 0 0.75rem; }}
  .sub {{ color: {INK_MUTED}; font-size: 0.82rem; }}
  .runs {{ display: flex; flex-wrap: wrap; gap: 1.5rem; margin: 1.25rem 0 0.5rem; }}
  .run {{ display: flex; gap: 0.6rem; align-items: flex-start; }}
  .run-label {{ font-weight: 600; font-size: 0.9rem; }}
  .run-meta {{ color: {INK_MUTED}; font-size: 0.78rem; }}
  .tag {{ font-weight: 400; font-size: 0.7rem; color: {INK_SECONDARY};
          border: 1px solid {BASELINE}; border-radius: 3px; padding: 0 4px; }}
  .chip {{ display: inline-block; width: 10px; height: 10px; border-radius: 2px;
           margin-right: 6px; vertical-align: baseline; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 0.86rem;
           margin-bottom: 1rem; }}
  th, td {{ border-bottom: 1px solid {GRIDLINE}; padding: 8px 10px;
            text-align: left; vertical-align: top; }}
  th {{ color: {INK_SECONDARY}; font-weight: 600; white-space: nowrap; }}
  td.num, th.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  td.num .sub, td .sub {{ font-weight: 400; }}
  .fail {{ color: {CRITICAL}; font-size: 0.78rem; }}
  figure {{ margin: 0 0 1.25rem; }}
  img {{ max-width: 100%; }}
  details {{ margin-bottom: 0.75rem; }}
  summary {{ cursor: pointer; font-size: 0.86rem; padding: 4px 0; }}
"""


def fmt(value, suffix="s"):
    """A measurement as text: seconds to 3dp, an em dash for nothing.

    Anything else - notably FAILURE_MARK - passes through as itself, since
    the raw tables show what was actually recorded.
    """
    if isinstance(value, (int, float)):
        return f"{value:.3f}{suffix}"
    return "&mdash;" if value is None else str(value)


def metric_keys(records):
    return [k for k in records[0].keys() if k != "run"]


def _numeric_values(records, key):
    return [r[key] for r in records if isinstance(r[key], (int, float))]


def summarize(values):
    if not values:
        return {"runs": 0, "mean_s": None, "min_s": None, "max_s": None, "stdev_s": None}

    return {
        "runs": len(values),
        "mean_s": round(statistics.mean(values), 3),
        "min_s": round(min(values), 3),
        "max_s": round(max(values), 3),
        "stdev_s": round(statistics.stdev(values), 3) if len(values) > 1 else 0.0,
    }


def summarize_all(records):
    stats = {}
    for key in metric_keys(records):
        values = _numeric_values(records, key)
        failures = len(records) - len(values)
        stats[key] = {**summarize(values), "failures": failures}
    return stats


def write_csv(path: Path, records):
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["run"] + metric_keys(records))
        writer.writeheader()
        writer.writerows(records)


def write_json(path: Path, records, stats, meta=None):
    payload = {"records": records, "stats": stats}
    if meta:
        payload = {"meta": meta, **payload}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _chart_base64(records, key, mean=None):
    """One metric's per-run bars as a base64 PNG.

    One chart per metric rather than all metrics on shared axes, for the
    same reason the comparison does it: a 0.3s page open and a 45s build on
    one scale would flatten the fast ones to nothing.

    matplotlib is optional: without it the report is written without
    charts rather than failing.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return None

    import base64
    import io

    numeric = [r for r in records if isinstance(r.get(key), (int, float))]
    if not numeric:
        return None

    fig, ax = plt.subplots(figsize=(5.6, 2.9))
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)

    xs = [r["run"] for r in numeric]
    ys = [r[key] for r in numeric]
    ax.bar(xs, ys, width=0.5, color=RUN_COLORS[0], zorder=3)
    # Direct label on every bar: the relief the palette requires for its
    # weaker slots, and with a handful of runs nobody should have to read a
    # value off an axis.
    for x, y in zip(xs, ys):
        ax.annotate(f"{y:.3f}s", (x, y), textcoords="offset points",
                    xytext=(0, 5), ha="center", fontsize=8.5, color=INK_PRIMARY)

    # The mean is what the summary table and any later comparison quote, so
    # it is drawn on the run-by-run view rather than left to be inferred.
    runs = [r["run"] for r in records]
    if mean is not None:
        # Spans the bars only, not the full width: the label lives in the
        # right-hand gutter and a full-width rule would strike through it.
        ax.plot([min(runs) - 0.4, max(runs) + 0.4], [mean, mean],
                color=INK_SECONDARY, linewidth=1, linestyle="--", zorder=4)
        # The gutter itself. The bars' own value labels occupy the space
        # above the plot, and one landing on the other is how the first
        # version of this chart read.
        ax.set_xlim(min(runs) - 0.6, max(runs) + 1.4)
        ax.annotate(f"mean {mean:.3f}s", (max(runs) + 0.55, mean),
                    ha="left", va="center", fontsize=8, color=INK_SECONDARY)

    # A failed run is a gap in the bars; mark it so the gap reads as
    # "this scenario broke here" rather than as a missing measurement.
    for r in records:
        if not isinstance(r.get(key), (int, float)):
            ax.annotate(FAILURE_MARK, (r["run"], 0), textcoords="offset points",
                        xytext=(0, 4), ha="center", fontsize=9, weight="bold",
                        color=CRITICAL)

    ax.set_xticks(runs)
    ax.set_xticklabels([f"run {n}" for n in runs], fontsize=8, color=INK_MUTED)
    ax.set_ylabel("seconds", fontsize=8.5, color=INK_MUTED)
    ax.tick_params(axis="y", labelsize=8, colors=INK_MUTED, length=0)
    ax.tick_params(axis="x", length=0)
    ax.set_title(key, fontsize=10, color=INK_PRIMARY, pad=14, loc="left")

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


def write_html_report(path: Path, records, stats, meta=None):
    """One run's report, styled to match the comparison page.

    Deliberately the same document as compare.py's output - same palette,
    same summary shape, same per-metric charts - so reading one run and
    reading three side by side is the same act.
    """
    keys = metric_keys(records)
    meta = meta or {}

    summary_rows = []
    for key in keys:
        s = stats.get(key, {})
        spread = (f'<div class="sub">{fmt(s.get("min_s"))}&ndash;{fmt(s.get("max_s"))} '
                  f'&middot; sd {fmt(s.get("stdev_s"))}</div>'
                  if s.get("mean_s") is not None else "")
        failures = (f'<div class="fail">{s["failures"]} failed</div>'
                    if s.get("failures") else "")
        summary_rows.append(
            f'<tr><td>{key}</td>'
            f'<td class="num"><strong>{fmt(s.get("mean_s"))}</strong>{spread}{failures}</td>'
            f'<td class="num">{s.get("runs", 0)}</td></tr>')

    charts = [(key, _chart_base64(records, key, mean=stats.get(key, {}).get("mean_s")))
              for key in keys]
    charts_html = "\n".join(
        f'<figure><img src="data:image/png;base64,{png}" alt="{key} by run"></figure>'
        for key, png in charts if png
    ) or '<p class="sub">No charts &mdash; install matplotlib to include them.</p>'

    header_cells = "".join(f'<th class="num">{key}</th>' for key in keys)
    raw_rows = "\n".join(
        f"<tr><td>{r['run']}</td>"
        + "".join(f'<td class="num">{fmt(r.get(key))}</td>' for key in keys)
        + "</tr>"
        for r in records
    )

    label = meta.get("env") or path.parent.name
    url = meta.get("url", "")

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Load-time report</title>
<style>{PAGE_CSS}</style>
</head>
<body>
  <h1>Load-time report</h1>
  <p class="sub">Each metric's mean over {len(records)} runs, with the
     min&ndash;max spread it was drawn from. Compare this run against another
     with <code>poe perf-compare</code>.</p>

  <div class="runs">
    <div class="run">
      <span class="chip" style="background:{RUN_COLORS[0]}"></span>
      <div>
        <div class="run-label">{label}</div>
        <div class="run-meta">{url} &middot; {len(records)} runs
          &middot; {datetime.now().isoformat(timespec="seconds")}</div>
      </div>
    </div>
  </div>

  <h2>Summary</h2>
  <table>
    <tr><th>Metric</th><th class="num">Mean</th><th class="num">Runs measured</th></tr>
    {''.join(summary_rows)}
  </table>

  <h2>By metric</h2>
  {charts_html}

  <h2>Raw measurements</h2>
  <table>
    <tr><th>Run</th>{header_cells}</tr>
    {raw_rows}
  </table>
</body>
</html>
"""
    path.write_text(html, encoding="utf-8")
