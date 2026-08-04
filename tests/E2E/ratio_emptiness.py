"""
Sector Analysis - which ratios never actually show a number
===========================================================

Post-processing step over a `poe download-ratios` run directory, and the
companion to `ratio_coverage.py`. That one answers "did the app *offer* this
ratio?"; this one answers the next question: **when it did, did it ever put
a number in the cell?**

A ratio can be present in every export and still be worthless - every period
of every company blank, or every period a flat 0. Both look like full
coverage in `ratio_coverage.py`, because the label was clicked and the row
was exported. Here they separate:

    ratio                       cells   blank  zero  status
    Enterprise Value (EV)        1500    100%    0%  EMPTY
    CFO Interest Coverage        1500    100%    0%  EMPTY
    Net Borrowing                1272     19%   61%  MOSTLY-BLANK-OR-ZERO
    Debt to Assets               1500      3%   14%  POPULATED

Blank and zero are counted in separate columns on purpose. A blank means the
app had nothing to show. A 0 usually means it *did* show something - and in
this export format a 0 is frequently a silent substitution for a missing
input rather than a real zero (see the SILENT-ZERO-INPUT convention in
AGENT.md), so a ratio sitting at a high zero rate is a finding of its own,
not a healthy one. Collapsing the two into one "no useful value" number
would hide that difference, so the two rates are reported side by side and
the combined figure is offered as a third column.

Usage
-----
    poetry run poe ratio-emptiness data/sector-analysis/<timestamp>
    poetry run poe ratio-emptiness <dir> --min-blank 50   # only the bad ones
    poetry run poe ratio-emptiness <dir> --include-nested

Writes `_ratio-emptiness.csv` into the run directory and prints the table.

Scope
-----
By default only **top-level** rows are counted - the ones whose `Subsection`
is empty, i.e. the ratios that were selected directly and that the app
displays as ratios. The same metric name also appears further down as a
nested input inside other ratios' decompositions, where being blank means
something different (that parent didn't need it / couldn't get it), and
those occurrences would otherwise dilute the rate. `--include-nested` counts
every occurrence instead, which is what you want when asking about a raw
line item rather than a ratio.

Reads the cached values the export ships, exactly as the web app renders
them. That is the point - a stale cache is what the user sees. If you need
the recalculated truth, that is `sector_analysis_ratios.py`'s EXCEL pass.
"""

import argparse
import csv
import sys
from pathlib import Path

HEADER_ROW = 3  # Chart Data: row 3 is the header, row 4+ the metric rows
FIRST_PERIOD_COL = 5  # Metric, Entity, Section, Subsection, then the periods

# Everything a blank renders as. The export writes a literal "-" wherever a
# formula hit its IFERROR fallback, and truly formula-less rows come back as
# an empty cell; both are "no number" to a reader.
BLANK_STRINGS = {"", "-", "--", "n/a", "N/A", "NA", "null", "None"}


def period_count(sheet):
    """How many period columns the sheet has.

    Some layouts append `__lamehCompanyId` / `__lamehCompanyName` straight
    after the last period with no blank column between, so stopping at the
    first empty header alone counts them as two extra periods.
    """
    count = 0
    for col in range(FIRST_PERIOD_COL, sheet.max_column + 1):
        header = sheet.cell(row=HEADER_ROW, column=col).value
        if header is None or (isinstance(header, str) and header.startswith("__lameh")):
            break
        count += 1
    return count


def classify(value):
    """'blank' | 'zero' | 'value' for one period cell."""
    if value is None:
        return "blank"
    if isinstance(value, bool):  # bool is an int; never a ratio value
        return "blank"
    if isinstance(value, (int, float)):
        return "zero" if value == 0 else "value"
    if isinstance(value, str):
        return "blank" if value.strip() in BLANK_STRINGS else "value"
    return "value"


class Tally:
    """Per-ratio counts across the whole run."""

    def __init__(self):
        self.blank = 0
        self.zero = 0
        self.value = 0
        self.companies = 0          # exports the ratio appears in
        self.empty_companies = 0    # ...in which it is blank in every period
        self.flat_companies = 0     # ...in which it is never a non-zero number

    @property
    def cells(self):
        return self.blank + self.zero + self.value

    def add_company(self, cells):
        counts = {"blank": 0, "zero": 0, "value": 0}
        for cell in cells:
            counts[classify(cell)] += 1
        self.blank += counts["blank"]
        self.zero += counts["zero"]
        self.value += counts["value"]
        self.companies += 1
        if counts["blank"] == len(cells) and cells:
            self.empty_companies += 1
        if counts["value"] == 0 and cells:
            self.flat_companies += 1


def read_export(path, include_nested):
    """({metric: [period values]}, period count) for one export.

    A metric can legitimately occupy several rows (once per position in the
    decomposition tree); with --include-nested every one of them counts, so
    the cells are concatenated rather than the first winning. Without it,
    only Subsection-less rows are read at all.

    A period count of 0 is a real export shape, not a read failure: the
    header runs Subsection straight into __lamehCompanyId, so the file has
    every metric row and no data whatsoever. It is returned rather than
    swallowed so the caller can say so.
    """
    from openpyxl import load_workbook

    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = workbook["Chart Data"]
        n_periods = period_count(sheet)
        if not n_periods:
            return {}, 0

        found = {}
        for row in sheet.iter_rows(min_row=HEADER_ROW + 1,
                                   max_col=FIRST_PERIOD_COL + n_periods - 1,
                                   values_only=True):
            metric = row[0]
            if not metric:
                continue
            subsection = row[3]
            if not include_nested and subsection:
                continue
            found.setdefault(str(metric).strip(), []).extend(
                row[FIRST_PERIOD_COL - 1:FIRST_PERIOD_COL - 1 + n_periods])
        return found, n_periods
    finally:
        workbook.close()


# --- the report -------------------------------------------------------------

COLUMNS = ["Ratio", "Companies", "Cells", "Blank", "Blank %", "Zero", "Zero %",
           "Blank or Zero %", "Empty Companies", "Flat Companies", "Status"]


def status_for(blank_pct, no_value_pct):
    if blank_pct >= 100:
        return "EMPTY"            # never a number anywhere, in any period
    if no_value_pct >= 100:
        return "ALWAYS-ZERO"      # always rendered, never anything but 0
    if no_value_pct >= 50:
        return "MOSTLY-BLANK-OR-ZERO"
    return "POPULATED"


def build_rows(tallies):
    rows = []
    for ratio, tally in tallies.items():
        cells = tally.cells
        blank_pct = 100 * tally.blank / cells if cells else 0.0
        zero_pct = 100 * tally.zero / cells if cells else 0.0
        no_value_pct = blank_pct + zero_pct
        rows.append({
            "Ratio": ratio,
            "Companies": tally.companies,
            "Cells": cells,
            "Blank": tally.blank,
            "Blank %": f"{blank_pct:.1f}",
            "Zero": tally.zero,
            "Zero %": f"{zero_pct:.1f}",
            "Blank or Zero %": f"{no_value_pct:.1f}",
            "Empty Companies": tally.empty_companies,
            "Flat Companies": tally.flat_companies,
            "Status": status_for(blank_pct, no_value_pct),
            "_sort": (-no_value_pct, -blank_pct, ratio),
        })
    rows.sort(key=lambda r: r.pop("_sort"))
    return rows


MAX_CONSOLE_WIDTH = 46


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
    # Metric names can contain Arabic; Windows consoles default to a legacy
    # codepage that raises on those.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="Per-ratio blank/zero rates across one download-ratios run directory.")
    parser.add_argument("dir", help="A data/sector-analysis/<timestamp>/ run directory.")
    parser.add_argument("--csv", default=None,
                        help="Where to write the CSV (default: <dir>/_ratio-emptiness.csv).")
    parser.add_argument("--min-blank", type=float, default=None,
                        help="Print only ratios blank in at least this %% of cells "
                             "(--min-blank 100 shows just the ones that never have a value).")
    parser.add_argument("--include-nested", action="store_true",
                        help="Also count rows nested inside another ratio's decomposition. "
                             "Off by default: only directly-selected ratios are counted.")
    args = parser.parse_args()

    run_dir = Path(args.dir)
    if not run_dir.is_dir():
        print(f"Not a directory: {run_dir}", file=sys.stderr)
        return 1
    exports = sorted(p for p in run_dir.glob("*.xlsx") if not p.name.startswith("~$"))
    if not exports:
        print(f"No .xlsx exports found in {run_dir}", file=sys.stderr)
        return 1

    tallies = {}
    periodless = []
    for path in exports:
        try:
            found, n_periods = read_export(path, args.include_nested)
        except Exception as exc:  # one unreadable export shouldn't lose the run
            print(f"WARNING: skipping {path.name}: {exc}", file=sys.stderr)
            continue
        if not n_periods:
            periodless.append(path.name)
            continue
        for metric, cells in found.items():
            tallies.setdefault(metric, Tally()).add_company(cells)

    if not tallies:
        print(f"No metric rows read from {len(exports)} exports in {run_dir}", file=sys.stderr)
        return 1

    rows = build_rows(tallies)

    print(f"Ratio emptiness for: {run_dir}")
    if periodless:
        # Not the same as a ratio being blank: these files have no period
        # columns at all, so every ratio in them is equally absent and
        # counting them would drag every rate toward 100% uniformly.
        print(f"{len(periodless)} of {len(exports)} exports carry no period columns at all "
              f"(every metric row present, no data). Excluded from the rates below:")
        for name in periodless:
            print(f"  - {name}")
    print(f"{len(exports) - len(periodless)} exports counted, {len(rows)} distinct "
          f"{'metrics' if args.include_nested else 'top-level ratios'}, "
          f"{sum(r['Cells'] for r in rows)} cells")
    print("=" * 70)

    shown = rows if args.min_blank is None else [
        r for r in rows if float(r["Blank %"]) >= args.min_blank]
    if shown:
        print_table(shown)
    else:
        print(f"No ratio is blank in {args.min_blank}% or more of its cells.")

    empty = [r for r in rows if r["Status"] == "EMPTY"]
    if empty:
        print(f"\n{len(empty)} ratios never showed a number in any period of any company:")
        for row in empty:
            print(f"  - {row['Ratio']}  ({row['Cells']} cells across {row['Companies']} companies)")

    always_zero = [r for r in rows if r["Status"] == "ALWAYS-ZERO"]
    if always_zero:
        print(f"\n{len(always_zero)} ratios rendered only ever as 0. Worth checking against the "
              f"SILENT-ZERO-INPUT convention - a 0 here may be a missing input, not a real zero:")
        for row in always_zero:
            print(f"  - {row['Ratio']}  ({row['Zero']} zero / {row['Blank']} blank)")

    csv_path = Path(args.csv) if args.csv else run_dir / "_ratio-emptiness.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with open(csv_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nEmptiness CSV written to {csv_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
