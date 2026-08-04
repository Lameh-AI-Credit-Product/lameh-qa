"""
Sector Analysis - ratio coverage across a download run
=======================================================

Post-processing step over a `poe download-ratios` run directory. Answers one
question: **of the ratios we ask for, how many companies actually got each
one?**

Every ratio starts at full coverage - the download script tries to click all
of them for every company - and each "ratio not available, skipping" line in
run.log takes one company off it. Sorted rarest-first, so the thin end of
the list is the answer.

    ratio                       companies  coverage  status
    CFO Interest Coverage         0 / 119        0%  MISSING
    Net Borrowing                 7 / 119        6%  PARTIAL
    Debt to Assets              102 / 119       86%  PARTIAL
    Return on Equity (ROE)      119 / 119      100%  FULL

0% is a different finding from 6%. `Net Borrowing` at 7 of 119 is a data
gap - most companies genuinely have no such figure. `CFO Interest Coverage`
at 0 of 119 means no company has it because the app has no ratio under that
name at all: it was renamed or removed, and we've been asking for a label
that stopped existing. Those two are indistinguishable in the raw log, which
is what this script is for.

Usage
-----
    poetry run poe ratio-coverage data/sector-analysis/<timestamp>
    poetry run poe ratio-coverage <dir> --resolve-names
    poetry run poe ratio-coverage <dir> --max-coverage 99   # only the gaps

Writes `_ratio-coverage.csv` into the run directory and prints the table.

--resolve-names
---------------
Off by default because it opens all ~120 exports; worth it when a ratio
reads 0%. The download script clicks by visible text, so several of its
labels are deliberate prefixes and the log echoes the prefix, not the name
the app used. Reading the exports back recovers the real name:

    NOPAT                            -> NOPAT (Tax Rate Assumed Zero)
    Days Payables Outstanding (      -> Days Payables Outstanding (DPO)
    Return on Invested Capital (     -> (nothing - genuinely gone)

That difference is the whole diagnosis when chasing a rename, and the log
alone cannot show it.

Scope
-----
This can only report on labels the download script asks for. A ratio the app
offers under a name we don't know is never clicked, so it is never skipped,
never logged, and invisible here - a rename *to* an unknown name looks
exactly like a deletion. Enumerating the app's own ratio picker would close
that, but it needs Playwright and a live session, not a finished run folder.
"""

import argparse
import ast
import csv
import re
import sys
from collections import Counter
from pathlib import Path

# Parsed, not imported (see selected_labels), so this is a file path rather
# than a module reference and does not survive the download script moving.
DOWNLOAD_SCRIPT = (Path(__file__).parent.parent
                   / "E2E" / "sector_analysis_download_company_ratios.py")
CLICK_FUNCTION = "click_ratio_if_present"

SKIP_RE = re.compile(r"ratio not available, skipping: '(.*)'\s*$")
START_RE = re.compile(r"=== (.+?): starting ===\s*$")

HEADER_ROW = 3  # Chart Data: row 3 is the header, row 4+ the metric rows


# --- the labels we ask for --------------------------------------------------

def _literal_string_lists(tree):
    """{name: [str, ...]} for every assignment of a plain list of strings,
    at any scope. Used to resolve `for label in ratio_labels:` back to the
    list itself."""
    found = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        try:
            value = ast.literal_eval(node.value)
        except (ValueError, TypeError, SyntaxError):
            continue
        if isinstance(value, list) and all(isinstance(v, str) for v in value):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    found[target.id] = value
    return found


def selected_labels():
    """The click selectors from the download script, as [(label, exact)].

    Read out of the source with `ast` rather than imported: importing that
    module reads BASE_URL from the environment at module scope. This script
    runs against a finished run folder, with no .env and no Playwright.

    Rather than looking for particular variable names, this finds every
    `for <var> in <list of strings>:` loop whose body calls
    click_ratio_if_present, and takes `exact` from that call. So the
    download script needs no cooperating structure and no edits - it keeps
    working exactly as it does today, which is the point: it is expensive
    to re-run and shouldn't be disturbed by a reporting script.
    """
    if not DOWNLOAD_SCRIPT.exists():
        raise FileNotFoundError(
            f"Cannot find the download script at {DOWNLOAD_SCRIPT}. The ratio labels are "
            f"read out of its source, so this path has to track wherever it lives.")
    tree = ast.parse(DOWNLOAD_SCRIPT.read_text(encoding="utf-8"))
    known_lists = _literal_string_lists(tree)

    labels = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.For):
            continue
        call = next((n for n in ast.walk(node)
                     if isinstance(n, ast.Call)
                     and isinstance(n.func, ast.Name)
                     and n.func.id == CLICK_FUNCTION), None)
        if call is None:
            continue

        if isinstance(node.iter, ast.Name):
            values = known_lists.get(node.iter.id)
        else:
            try:
                values = ast.literal_eval(node.iter)
            except (ValueError, TypeError, SyntaxError):
                values = None
        if not values or not all(isinstance(v, str) for v in values):
            continue

        exact = any(kw.arg == "exact" and getattr(kw.value, "value", False) is True
                    for kw in call.keywords)
        labels.extend((value, exact) for value in values)

    if not labels:
        raise RuntimeError(
            f"Found no ratio labels in {DOWNLOAD_SCRIPT.name}. This script looks for "
            f"`for <var> in [<strings>]:` loops calling {CLICK_FUNCTION}() - if that "
            f"shape changed, this parser needs to change with it.")
    return labels


def matches_label(metric, label, exact):
    """Match a metric name the way the click that produced it matched.

    The substring list clicks `get_by_text(label)`, so several entries are
    deliberate prefixes ("Days Payables Outstanding ("). The exact list
    clicks with exact=True precisely because prefix matching would grab the
    wrong element - "Net Debt" would swallow "Net Debt to EBITDA".
    """
    return metric == label if exact else metric.startswith(label)


# --- the run ----------------------------------------------------------------

def read_log(run_dir):
    """(companies started, {selector: how many companies skipped it})."""
    log_path = run_dir / "run.log"
    if not log_path.exists():
        raise FileNotFoundError(
            f"No run.log in {run_dir} - coverage is counted from its skip lines.")
    started, skips = 0, Counter()
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if START_RE.search(line):
            started += 1
            continue
        skipped = SKIP_RE.search(line)
        if skipped:
            skips[skipped.group(1)] += 1
    return started, skips


def exported_metrics(paths):
    """Every distinct metric name across the run's exports.

    Imported lazily so the default path doesn't pay for openpyxl at all.
    """
    from openpyxl import load_workbook

    names = set()
    for path in paths:
        try:
            workbook = load_workbook(path, read_only=True, data_only=True)
        except Exception as exc:  # one unreadable export shouldn't lose the run
            print(f"WARNING: skipping {path.name}: {exc}", file=sys.stderr)
            continue
        try:
            rows = workbook["Chart Data"].iter_rows(min_row=HEADER_ROW, values_only=True)
            header = next(rows, None)
            if not header or "Metric" not in header:
                continue
            metric_at = header.index("Metric")
            names.update(str(row[metric_at]).strip() for row in rows if row[metric_at])
        finally:
            workbook.close()
    return names


# --- the report -------------------------------------------------------------

COLUMNS = ["Ratio", "Companies", "Total", "Coverage", "Status", "Skipped", "Exported As"]


def build_rows(labels, skips, companies, metric_names=None):
    rows = []
    for label, exact in labels:
        # Every ratio is attempted for every company, so it starts at full
        # coverage and each logged skip takes one company off it.
        skipped = skips.get(label, 0)
        covered = companies - skipped
        exported = ""
        if metric_names is not None:
            matched = sorted(n for n in metric_names if matches_label(n, label, exact))
            exported = "; ".join(matched)
        rows.append({
            "Ratio": label,
            "Companies": covered,
            "Total": companies,
            "Coverage": f"{covered / companies:.0%}" if companies else "n/a",
            "Status": "MISSING" if covered <= 0 else ("FULL" if covered == companies else "PARTIAL"),
            "Skipped": skipped,
            "Exported As": exported,
        })
    rows.sort(key=lambda r: (r["Companies"], r["Ratio"]))
    return rows


# Resolved names include the odd long one; one of them shouldn't set the
# width of the whole table. The CSV keeps them whole.
MAX_CONSOLE_WIDTH = 55


def _cell(value):
    text = str(value)
    return text if len(text) <= MAX_CONSOLE_WIDTH else text[:MAX_CONSOLE_WIDTH - 1] + "…"


def print_table(rows):
    widths = {col: max(len(col), *(len(_cell(r[col])) for r in rows)) for col in COLUMNS}
    header = "  ".join(col.ljust(widths[col]) for col in COLUMNS)
    print(header)
    print("-" * len(header))
    for row in rows:
        print("  ".join(_cell(row[col]).ljust(widths[col]) for col in COLUMNS))


def main():
    # Resolved metric names can contain non-ASCII text; Windows consoles
    # default to a legacy codepage that raises on those.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="Per-ratio company coverage across one download-ratios run directory.")
    parser.add_argument("dir", help="A data/sector-analysis/<timestamp>/ run directory.")
    parser.add_argument("--csv", default=None,
                        help="Where to write the CSV (default: <dir>/_ratio-coverage.csv).")
    parser.add_argument("--resolve-names", action="store_true",
                        help="Open the exports to recover the metric name each label actually "
                             "matched. Slower; the way to tell a rename from a removal.")
    parser.add_argument("--max-coverage", type=int, default=None,
                        help="Print only ratios at or below this coverage percentage "
                             "(--max-coverage 0 shows just the ones that are gone entirely).")
    args = parser.parse_args()

    run_dir = Path(args.dir)
    if not run_dir.is_dir():
        print(f"Not a directory: {run_dir}", file=sys.stderr)
        return 1
    exports = sorted(p for p in run_dir.glob("*.xlsx") if not p.name.startswith("~$"))
    if not exports:
        print(f"No .xlsx exports found in {run_dir}", file=sys.stderr)
        return 1

    labels = selected_labels()
    started, skips = read_log(run_dir)
    companies = len(exports)

    metric_names = exported_metrics(exports) if args.resolve_names else None
    rows = build_rows(labels, skips, companies, metric_names)

    print(f"Ratio coverage for: {run_dir}")
    print(f"{companies} companies exported, {started} started in run.log, "
          f"{len(labels)} ratios asked for")
    print("=" * 70)

    shown = rows if args.max_coverage is None else [
        r for r in rows if r["Companies"] * 100 <= args.max_coverage * companies]
    if shown:
        print_table(shown)
    else:
        print(f"No ratio is at or below {args.max_coverage}% coverage.")

    # The denominator is the export count, but skips are logged by any
    # company that reached the ratio step - including one that then died
    # before producing a file. When that happens the subtraction
    # under-reports, so say so rather than printing a quiet negative.
    over_skipped = [r for r in rows if r["Skipped"] > companies]
    if over_skipped:
        print(f"\nWARNING: {len(over_skipped)} ratios were skipped by more companies than "
              f"there are exports ({companies}). {started - companies} companies started "
              f"without producing a file; any that got as far as ratio selection logged "
              f"skips counted here. Coverage for those rows is a lower bound.")

    missing = [r for r in rows if r["Status"] == "MISSING"]
    if missing:
        print(f"\n{len(missing)} ratios reached no company at all. Not a data gap - the app "
              f"has no ratio under these names, so they were renamed or removed:")
        for row in missing:
            print(f"  - {row['Ratio']}"
                  + (f"  (exports call it: {row['Exported As']})" if row["Exported As"] else ""))
        if metric_names is None:
            print("  Re-run with --resolve-names to see what the exports call them, if anything.")

    csv_path = Path(args.csv) if args.csv else run_dir / "_ratio-coverage.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with open(csv_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nCoverage CSV written to {csv_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
