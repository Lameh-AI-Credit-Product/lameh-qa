"""
Lameh Sector Analysis - Ratio Failure Summary
==============================================

Post-processing step over the per-company CSV reports written by
sector_analysis_ratios.py (one <company>-web.csv and <company>-excel.csv per
export, e.g. under results/sector-analysis/<date>/ - see --dir there for
running the check itself across a whole folder of exports).

Aggregates every FAIL row (REPORTED-MISSING and any other status is excluded
from this table) across all those reports into one row per ratio: how many
times it failed in the WEB pass vs the EXCEL pass, split further into
quarterly vs yearly periods - so a bug affecting one ratio across many
companies shows up as a single line instead of being buried in dozens of
per-company files.

Usage
-----
    python3 summarize_ratio_failures.py path/to/reports/dir [--csv out.csv]

`path/to/reports/dir` is searched recursively for *-web.csv / *-excel.csv
report files. Three files are always written into that same directory:
  - _summary.csv       one row per ratio (columns: Metric, Web Fails, Web
                        Fails Quarterly, Web Fails Yearly, Excel Fails,
                        Excel Fails Quarterly, Excel Fails Yearly, Total
                        Fails) - also printed to the console.
  - _web-details.csv    every FAIL row from the WEB pass.
  - _excel-details.csv  every FAIL row from the EXCEL pass.
--csv optionally writes one row per individual FAIL/REPORTED-MISSING
occurrence (full detail, both passes/statuses combined), for deeper
drill-down.
"""

import argparse
import csv
import os
import re
import sys
from collections import defaultdict

QUARTER_RE = re.compile(r"\bQ\d\b")


def period_type(period):
    """"2024 Q1 (3 months)" -> quarterly, "2024 (12 months)" -> yearly."""
    return "quarterly" if QUARTER_RE.search(str(period)) else "yearly"


def pass_type(csv_path):
    name = os.path.basename(csv_path)
    if name.endswith("-web.csv"):
        return "web"
    if name.endswith("-excel.csv"):
        return "excel"
    return "unknown"


def company_name(csv_path):
    name = os.path.basename(csv_path)
    for suffix in ("-web.csv", "-excel.csv"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return os.path.splitext(name)[0]


def find_report_csvs(root):
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if name.endswith("-web.csv") or name.endswith("-excel.csv"):
                yield os.path.join(dirpath, name)


def collect_failures(root):
    """Returns a flat list of dicts, one per FAIL/REPORTED-MISSING row found
    across every report CSV under `root`."""
    rows = []
    for csv_path in find_report_csvs(root):
        pass_ = pass_type(csv_path)
        company = company_name(csv_path)
        with open(csv_path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                status = r.get("Status")
                if status not in ("FAIL", "REPORTED-MISSING"):
                    continue
                period = r.get("Period")
                rows.append({
                    "metric": r.get("Metric"),
                    "company": company,
                    "pass": pass_,
                    "period": period,
                    "period_type": period_type(period),
                    "status": status,
                    "reported": r.get("Reported"),
                    "expected": r.get("Expected"),
                    "diff_pct": r.get("Diff %"),
                    "excel_row": r.get("Excel Row"),
                })
    return rows


TABLE_COLUMNS = ["Metric", "Web Fails", "Web Fails Quarterly", "Web Fails Yearly",
                  "Excel Fails", "Excel Fails Quarterly", "Excel Fails Yearly", "Total Fails"]


def summary_table_rows(rows):
    """Returns a list of row dicts (one per ratio, sorted by Total Fails desc)
    with the TABLE_COLUMNS keys. Counts only rows with status == "FAIL" -
    REPORTED-MISSING (or any other status) is excluded from this table."""
    counts = defaultdict(lambda: defaultdict(int))
    for row in rows:
        if row["status"] != "FAIL":
            continue
        counts[row["metric"]][(row["pass"], row["period_type"])] += 1

    table = []
    for metric, c in counts.items():
        web_q = c.get(("web", "quarterly"), 0)
        web_y = c.get(("web", "yearly"), 0)
        excel_q = c.get(("excel", "quarterly"), 0)
        excel_y = c.get(("excel", "yearly"), 0)
        table.append({
            "Metric": metric,
            "Web Fails": web_q + web_y,
            "Web Fails Quarterly": web_q,
            "Web Fails Yearly": web_y,
            "Excel Fails": excel_q + excel_y,
            "Excel Fails Quarterly": excel_q,
            "Excel Fails Yearly": excel_y,
            "Total Fails": web_q + web_y + excel_q + excel_y,
        })
    table.sort(key=lambda r: r["Total Fails"], reverse=True)
    return table


def write_fail_details(path, rows, pass_):
    """Write every status == "FAIL" row for the given pass ("web"/"excel") to
    `path` (Metric, Company, Period, Period Type, Reported, Expected, Diff %, Excel Row)."""
    filtered = [r for r in rows if r["status"] == "FAIL" and r["pass"] == pass_]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Metric", "Company", "Period", "Period Type", "Reported", "Expected", "Diff %", "Excel Row"])
        for row in filtered:
            w.writerow([row["metric"], row["company"], row["period"], row["period_type"],
                        row["reported"], row["expected"], row["diff_pct"], row["excel_row"]])


def print_table(rows):
    widths = {col: max(len(col), *(len(str(r[col])) for r in rows)) for col in TABLE_COLUMNS}
    header = "  ".join(col.ljust(widths[col]) for col in TABLE_COLUMNS)
    print(header)
    print("-" * len(header))
    for r in rows:
        print("  ".join(str(r[col]).ljust(widths[col]) for col in TABLE_COLUMNS))
    totals = {col: sum(r[col] for r in rows) for col in TABLE_COLUMNS if col != "Metric"}
    print("-" * len(header))
    total_line = "TOTAL".ljust(widths["Metric"])
    for col in TABLE_COLUMNS[1:]:
        total_line += "  " + str(totals[col]).ljust(widths[col])
    print(total_line)


def main():
    # Company names / metric labels can contain non-ASCII text; avoid crashing
    # on Windows consoles stuck on a legacy codepage.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(
        description="Summarize FAIL/REPORTED-MISSING rows across sector_analysis_ratios.py CSV reports.")
    ap.add_argument("dir", help="Directory to search recursively for *-web.csv / *-excel.csv report files.")
    ap.add_argument("--csv", default=None,
                     help="Optional path to write the full per-occurrence detail CSV "
                          "(metric, company, pass, period, period type, status, ...).")
    args = ap.parse_args()

    rows = collect_failures(args.dir)
    if not rows:
        print(f"No FAIL/REPORTED-MISSING rows found under {args.dir}")
        return

    table_rows = summary_table_rows(rows)

    print(f"Ratio failure summary for: {args.dir}")
    print("=" * 70)
    print_table(table_rows)

    summary_csv_path = os.path.join(args.dir, "_summary.csv")
    web_details_path = os.path.join(args.dir, "_web-details.csv")
    excel_details_path = os.path.join(args.dir, "_excel-details.csv")

    with open(summary_csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=TABLE_COLUMNS)
        w.writeheader()
        w.writerows(table_rows)
    write_fail_details(web_details_path, rows, "web")
    write_fail_details(excel_details_path, rows, "excel")

    print(f"\nSummary CSV written to {summary_csv_path}")
    print(f"Web fail details written to {web_details_path}")
    print(f"Excel fail details written to {excel_details_path}")

    if args.csv:
        parent_dir = os.path.dirname(args.csv)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["Metric", "Company", "Pass", "Period", "Period Type", "Status",
                        "Reported", "Expected", "Diff %", "Excel Row"])
            for row in rows:
                w.writerow([row["metric"], row["company"], row["pass"], row["period"],
                            row["period_type"], row["status"], row["reported"],
                            row["expected"], row["diff_pct"], row["excel_row"]])
        print(f"\nDetail CSV written to {args.csv}")


if __name__ == "__main__":
    main()
