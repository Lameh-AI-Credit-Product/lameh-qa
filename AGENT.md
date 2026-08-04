# Sector Analysis Ratio Verification — Agent Context

> Scope note: this file covers `sector_analysis_ratios.py` only. The
> LangSmith eval suite for the Intelligence agent is documented separately in
> [`tests/langsmith/AGENT.md`](tests/langsmith/AGENT.md). The E2E scripts
> under `tests/E2E/` document themselves in their module docstrings — read
> `ratio_coverage.py`'s before changing how it finds the download script's
> ratio labels, since it parses them out of that file's source.

This file documents `sector_analysis_ratios.py` for future agent sessions. In
this repo it is a **standalone** file with no dependency on the rest of the
codebase — it can be dropped into any Python environment with `openpyxl` (and
optionally `pywin32` on Windows) and run on its own. It covers what the tool
does, why it's built the way it is, the non-obvious things discovered
empirically about Lameh's export format, and the conventions to follow when
extending it.

## Purpose

`sector_analysis_ratios.py` checks the internal consistency of every "Financial Ratios"
metric in an Excel export from Lameh's Sector Analysis chart-builder ("select
all ratios" export, one company per file). The export already includes, for
every ratio, a full decomposition down to its input components and raw
financial-statement line items. The script recomputes each ratio from its
**own reported input components** — not from a separate ground truth — and
flags any case where `reported_value != formula(reported_inputs)`.

This is a **correctness check**, not a performance/timing test. It catches:

- Aggregation/rollup bugs (e.g. a "Total X" that doesn't equal the sum of its
  parts).
- Ratio-calculation bugs (wrong formula, wrong sign, wrong denominator).
- Silent "N/A treated as 0" substitutions (flagged separately as
  `SILENT-ZERO-INPUT`, not as failures — see Conventions below).

## Usage

```
python sector_analysis_ratios.py path/to/export.xlsx [--tolerance 0.005] [--csv out.csv] [--fail-only] [--web-only]
```

- `--tolerance` — relative tolerance for a PASS (default 0.5%).
- `--csv` — base path for CSV reports (see "Two-pass WEB vs EXCEL" below).
  If omitted, defaults to `results/sector-analysis/<date>/<company name>.csv`
  (date format `YYYY-MM-DD-HH-MM-SS`, company name read from the export
  itself and sanitized for use as a filename). Under `--dir` the `<date>`
  segment is instead the name of the folder passed to `--dir`, so a batch's
  reports land under the same name as the download run they describe rather
  than under a second timestamp taken at verification time — and re-verifying
  a run overwrites its previous reports instead of adding another folder.
- `--fail-only` — when writing CSV, include only non-PASS rows.
- `--web-only` — skip the Excel-recalculation pass (see below); use this on a
  machine without a local Excel installation.

(In the sandbox this was developed in, there was also a separate
`extract_fails.py` utility that extracted non-PASS rows from a CSV after the
fact. Its logic was folded directly into this script as `--fail-only`, so
`sector_analysis_ratios.py` alone covers that need — `extract_fails.py`
itself is not part of the main repo.)

## Two-pass WEB vs EXCEL: the caching bug this tool exists to work around

This is the single most important thing to understand about this script, and
it came from a real investigation, not a hypothetical design choice.

**The problem:** `.xlsx` cells can hold both a formula (`<f>`) and a cached
last-calculated value (`<v>`). Lameh's export writes both, but the cached
`<v>` does not always match what the formula would currently evaluate to —
i.e. the export can ship a **stale cache**. Concretely, on one real export:
reading the file fresh (never opened in Excel) gave 219 (metric, period)
mismatches; opening the same file in Excel, editing, and saving it (forcing a
full recalculation) dropped this to 57. Every one of the ~160 mismatches that
disappeared traced to a formula cell whose stale cached value just hadn't
been refreshed — not a real product bug.

**The fix:** the script now runs two passes automatically per invocation:

1. **WEB** — reads whatever value is currently cached in the file via
   `openpyxl(data_only=True)`. This is `load_metric_values()`, unchanged.
2. **EXCEL** — `recalculate_with_excel()` opens the file in a real,
   invisible Excel instance via `pywin32` (`win32com.client.DispatchEx`),
   forces `Application.CalculateFullRebuild()`, saves the result to a
   throwaway temp file, and closes the **original** file with
   `SaveChanges=False` (never mutated). The temp file is then read the same
   way as pass 1, and deleted afterward.

Both passes go through the same `check_values()` function and produce two
separate CSVs: `<name>-web.csv` and `<name>-excel.csv`. Comparing them tells
you whether a FAIL is a genuine formula/product bug (persists in both,
identical values) or a caching artifact of the export pipeline (present only
in WEB, and the values differ between the two files).

**A FAIL that survives into the EXCEL pass, with unchanged values, is a real
bug.** A FAIL that's WEB-only is almost certainly a stale-cache artifact, not
something to report to the dev team as-is.

The EXCEL pass requires `pywin32` and a local Excel installation — it will
not work in CI or on non-Windows machines. Use `--web-only` to skip it there.
When adding this to the main repo with Poetry: `poetry add pywin32 --platform
win32` (the platform marker keeps it from being required on Linux/Mac CI).

## Duplicate metric rows — do not assume "first occurrence" is authoritative

The same metric name can appear **multiple times** in the sheet, once per
position in the ratio-decomposition tree (e.g. "Inventory Turnover" as its
own standalone ratio, and again nested inside "Days Inventory on Hand (DIO)"
as an input). These duplicates are **not guaranteed to hold the same value**.

Confirmed example: `Inventory Turnover`, 2022 (12 months):

| row | Subsection | value | type |
|---|---|---|---|
| 6 | Days Inventory on Hand (DIO) | 1.901 | static (no formula) |
| 33 | Days Inventory on Hand (DIO) | 1.901 | static (no formula) |
| 59 | *(empty — standalone ratio)* | 0.728 (correct) | formula |

The live web app was confirmed (by manually checking) to display **1.901** —
i.e. the wrong, stale duplicate — even though the correct value (0.728) is
computed correctly elsewhere in the same export. `load_metric_values()`
takes the **first occurrence** of each metric name (`if metric in values:
continue`), which happens to match rows 6/33 here — i.e. it matches what the
web app actually shows, which is usually what you want to verify, but this is
NOT a safe assumption in general. If a future check seems wrong, check
`source_rows[metric]` (now included as the "Excel Row" column in every CSV
report) and manually inspect whether other occurrences of that same metric
name disagree.

## Input format expected

An `.xlsx` with a `Chart Data` sheet laid out as:

```
Row 3   : header row -> Metric, Entity, Section, Subsection, <period columns...>
Row 4+  : one row per (metric, position-in-tree) instance, with period values
          in the columns following "Subsection".
```

Some export layouts append **metadata columns** (`__lamehCompanyId`,
`__lamehCompanyName`) directly after the real period columns in the header
row, with no blank column in between. The period-detection loop must stop at
these, not just at the first blank cell — this was a real bug (inflated
period counts from 12 to 14, silently absorbing two garbage "periods"),
fixed by checking `val.startswith("__lameh")` in addition to `val is None`.
If you ever touch the period-scanning loop, preserve this check.

Company name extraction (`get_company_name()`, used for the default output
path) has two fallbacks, because export layouts differ:
1. A `__lamehCompanyName` row inside `Chart Data` (some exports).
2. The `Companies` table inside a separate `_lameh_context` sheet, structured
   as a `companyId | companyName` header row followed by one data row (other
   exports — this is the "chart-builder" workbook type). If neither exists,
   the output path falls back to `unknown-company`.

## Conventions discovered empirically from Lameh's export data

- **Expense/outflow line items** (Finance costs, Zakat, COGS, CapEx
  additions, Dividends paid, lease/loan repayments) are stored as **negative**
  numbers. Ratios measuring "cost coverage" or "turnover" typically take the
  absolute value; cash-flow SUM formulas (FCF, FCFE, FCFF, Total Debt
  Service) use the raw signed values directly (adding a negative value
  achieves the subtraction).
- **Silent zero substitution**: when a debt-related input is unavailable
  (`'-'` in the export), several ratios (Debt to Assets/Capital/EBITDA/Equity,
  Net Debt, Net Debt to EBITDA) appear to silently treat it as `0` rather
  than propagating "N/A". This is flagged as `SILENT-ZERO-INPUT` (a PASS with
  a note), not a FAIL — it's not a math error, but it is a product concern: a
  company with "Total Debt: not retrieved" currently renders identically to
  a company with genuinely zero debt (0.0x).
  `Net Borrowing` shows the same pattern: in the 2026-08-01 run, 8 companies
  report `0` for 61 periods in which neither `CFF - Proceeds from Loans` nor
  `CFF - Repayment of Loans` exists at all. The formula deliberately does
  *not* assume 0 there — it reports those periods as unverifiable, because
  the other 228 input-less periods report nothing rather than 0, and treating
  the absence as zero would turn all of them into false REPORTED-MISSING
  rows.
- **A blank reported value is a FAIL unless the computable value is 0.**
  When the export shows nothing but its own reported inputs do support a
  value, that is the app hiding a number it has the data to display, and it
  is treated like any other mismatch. The exception is a computable value of
  `0`: blank and `0` render the same to a user, so those stay as
  `REPORTED-MISSING` (reported separately, not counted as failures). Note
  that a FAIL of this kind has `reported = None` and therefore no `Diff %` —
  anything consuming the results tuple must tolerate that.
- **Excel's own behavior on a formula cell**: opening a file and forcing
  recalculation *always* evaluates the formula — it never leaves a formula
  cell blank. If the formula's own logic can compute a number from available
  inputs, that's what shows, even overriding a stale/blank cache (this is the
  caching bug above). If the formula genuinely can't compute (e.g. wrapped in
  `IFERROR(..., "-")` with a missing input), it displays that literal `"-"`
  fallback — not empty. A cell is only ever *truly*, permanently empty if it
  has no formula at all to begin with.

## Ratios that exist in the export but produce nothing (2026-08-01 run)

Thirteen ratios appear as top-level (directly selected) rows in all 121
exports of the 2026-08-01 run and had no formula in the registry. They split
in two, and **neither group can currently be exercised** — every one of them
is blank in every period of all 121 exports, in the WEB pass *and* after a
full Excel recalculation:

- **Eight ship a real Excel formula** that evaluates to its `IFERROR(...,"-")`
  fallback, because the inputs it references are themselves blank. Formulas
  for these are now in the registry, transcribed character-for-character from
  the `<f>` cell text rather than inferred from the input tree — reading the
  formula text is the reliable move when a ratio has no values to fit
  against. They are correct by transcription but **unexercised**: no PASS and
  no FAIL, so treat them as untested code until an export actually populates
  them. `Fixed Asset Turnover`, `Debt Payment Ratio`, `EV/EBITDA`,
  `EV/Revenue`, `Return on Sales`, `Return on Invested Capital (ROIC)`,
  `Operating Income`, `ROA Adjusted`.
- **Five have no formula at all** — static, permanently empty rows (see the
  Excel-behavior convention above: no formula means never computable).
  Nothing to verify, so nothing was added. `Enterprise Value (EV)`,
  `NOPAT`, `CFO Interest Coverage`, `Free Cash Flow to Firm (FCFF)`,
  `Investing and Financing Coverage`.

The second group poisons part of the first: `EV/EBITDA` and `EV/Revenue`
divide by `Enterprise Value (EV)`, which is one of the permanently empty
rows, so those two can never compute no matter what else is fixed.

`Return on Sales` is a **confirmed false positive**, and a good illustration
of the duplicate-row caveat above. It computes a value where the app shows
`"-"`, so under the blank-is-a-FAIL rule below it now FAILs — 34 times across
4 recalculated exports, the only thing that rule currently surfaces. It is
not a product bug. The app's formula is `=IFERROR((E269/E270),"-")`, pointing
at its own subtree rows, and in a recalculated export those read:

```
row  268  Return on Sales             sub=(top-level)       ['-', '-', '-', '-']
row  269  Net Profit for the Period   sub=Return on Sales   ['-', '-', '-', '-']
row  270  Total Revenue               sub=Return on Sales   ['-', '-', '-', '-']
```

The app has no inputs there and is right to show nothing. `load_metric_values`
resolves those same two names to rows 175 and 30 — different, populated
occurrences elsewhere in the sheet — so the check "computes" a ratio from
numbers the app never fed into it.

This is a real limitation of the first-occurrence loader, not of the new FAIL
rule: **any** ratio whose subtree is empty while the same metric names appear
populated elsewhere will produce the same false FAIL. Fixing it means
resolving a formula's inputs within its own subtree (by row proximity or by
`Subsection`) instead of globally by name. Until then, check `source_rows`
before believing a blank-reported FAIL.

## Known bugs found via this tool (as of last investigation)

These survived a full Excel recalculation (i.e. they are NOT caching
artifacts — the formula itself disagrees with the correct, documented
calculation):

- Free Cash Flow to Equity (FCFE)
- Retention Ratio
- ROE (DuPont 3-Factor) and ROE (DuPont 5-Factor)
- Net Debt to EBITDA

Plus the duplicate-row inconsistency for `Inventory Turnover` described
above (and potentially other metrics with the same "standalone vs
decomposition-input duplicate" pattern — not exhaustively audited).

See `BUG_REPORT_ratio_export.md` (in the sandbox this file was written
alongside) for the full writeup intended for the dev team, if it was copied
over too.

## Code structure (for extending the script)

- `is_num`, `g` — small value-access helpers. `g()` centralizes the
  silent-zero convention via `default_zero_if_missing`.
- `get_company_name`, `sanitize_filename` — used only for the default output
  path.
- `load_metric_values` — reads the sheet into `{metric: [values per
  period]}`, `periods`, and `{metric: source_row}`. First-occurrence-wins per
  metric name (see caveat above).
- `recalculate_with_excel` — the Excel-COM automation for the EXCEL pass.
  Always closes the original file with `SaveChanges=False` and cleans up its
  own temp file; never mutates the input.
- `formulas()` — the formula registry, `metric_name -> fn(values, i) ->
  (expected_value, note)`. This is where to add new ratios or fix formula
  bugs. Each formula returns `(None, None)` when it can't compute (missing
  inputs) rather than raising — `check_values()` treats that as "insufficient
  data to verify", not a failure.
  `Net Borrowing` is the newest verified entry: `CFF - Proceeds from Loans` +
  `CFF - Repayment of Loans`, checked against 121 exports (1210 periods
  PASS, 0 FAIL, plus 31 PASS in the EXCEL pass). Repayment is negative in all
  972 periods that report it, so the signed sum is the subtraction. Either
  side may be absent on its own and counts as 0; only a period with neither
  is left unverified.
  The eight formulas in the "transcribed from the export's own Excel
  formulas" block at the end are a different case — see below.
- `check_values` — the actual compare-and-report loop, parameterized so it
  can run against either the WEB or EXCEL dataset. Also contains the
  "average vs current-balance" diagnostic heuristic (`RATIO_AVG_INPUT`) that
  tries to guess whether a FAIL is because the app used a period-end balance
  instead of a properly averaged one — informational only, doesn't change
  PASS/FAIL.
- `main()` — CLI wiring, default path construction, and orchestrating the
  two passes.

## Working conventions from this project

- When editing `check_values`, remember every result tuple has a fixed
  7-then-8 element shape (`metric, period, status, reported, expected,
  diff_pct, note, source_row`) unpacked in multiple places — keep them in
  sync if the shape changes.
- Console output must go through UTF-8 (`sys.stdout.reconfigure(encoding=
  "utf-8", errors="replace")` at the top of `main()`), since company names
  can be Arabic and Windows consoles default to a legacy codepage (cp1252)
  that crashes on non-ASCII prints otherwise.
- Prefer verifying any change against a real exported `.xlsx` end-to-end
  (both `--web-only` and the full Excel-recalculation pass) rather than
  trusting a syntax check alone — several real bugs in this script (the
  metadata-column period leak, the stdout encoding crash) were only caught by
  actually running it against real data.
