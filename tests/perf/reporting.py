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
"""
import csv
import json
import statistics
from datetime import datetime
from pathlib import Path

FAILURE_MARK = "F"


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


def _chart_base64(records, key, title):
    """One bar chart per metric as a base64 PNG.

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

    numeric_records = [r for r in records if isinstance(r[key], (int, float))]
    if not numeric_records:
        return None

    fig, ax = plt.subplots(figsize=(6, 3))
    xs = [r["run"] for r in numeric_records]
    ys = [r[key] for r in numeric_records]
    ax.bar(xs, ys, color="#4C72B0")
    ax.set_xlabel("Run")
    ax.set_ylabel("Seconds")
    ax.set_title(title)
    ax.set_xticks(xs)
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def write_html_report(path: Path, records, stats, meta=None):
    keys = metric_keys(records)
    meta = meta or {}

    def fmt(value):
        if isinstance(value, (int, float)):
            return f"{value:.3f}"
        return "&mdash;" if value is None else str(value)

    summary_rows = "\n".join(
        f"<tr><td>{key}</td><td>{s['runs']}</td><td>{s['failures']}</td>"
        f"<td>{fmt(s['mean_s'])}</td><td>{fmt(s['min_s'])}</td>"
        f"<td>{fmt(s['max_s'])}</td><td>{fmt(s['stdev_s'])}</td></tr>"
        for key, s in stats.items()
    )

    charts = [(key, _chart_base64(records, key, key)) for key in keys]
    charts_html = "\n".join(
        f'<img src="data:image/png;base64,{png}" alt="{key} chart">'
        for key, png in charts if png
    ) or "<p>No charts &mdash; install matplotlib to include them.</p>"

    header_cells = "".join(f"<th>{key}</th>" for key in keys)
    raw_rows = "\n".join(
        f"<tr><td>{r['run']}</td>"
        + "".join(f"<td>{fmt(r[key])}</td>" for key in keys)
        + "</tr>"
        for r in records
    )

    target = meta.get("url", "")
    if meta.get("env"):
        target += f" ({meta['env']})"

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Load time report</title>
<style>
  body {{ font-family: system-ui, sans-serif; margin: 2rem; color: #222; }}
  h1, h2 {{ color: #111; }}
  table {{ border-collapse: collapse; margin-bottom: 2rem; }}
  th, td {{ border: 1px solid #ccc; padding: 6px 12px; text-align: right; }}
  th:first-child, td:first-child {{ text-align: left; }}
  img {{ max-width: 100%; margin-bottom: 2rem; }}
  .meta {{ color: #555; font-size: 0.9rem; }}
</style>
</head>
<body>
  <h1>Load time report</h1>
  <p class="meta">Generated: {datetime.now().isoformat(timespec="seconds")}
     {"<br>Target: " + target if target else ""}</p>

  <h2>Summary</h2>
  <table>
    <tr><th>Metric</th><th>Runs</th><th>Failures</th><th>Mean (s)</th>
        <th>Min (s)</th><th>Max (s)</th><th>Stdev (s)</th></tr>
    {summary_rows}
  </table>

  <h2>Charts</h2>
  {charts_html}

  <h2>Raw data</h2>
  <table>
    <tr><th>Run</th>{header_cells}</tr>
    {raw_rows}
  </table>
</body>
</html>
"""
    path.write_text(html, encoding="utf-8")
