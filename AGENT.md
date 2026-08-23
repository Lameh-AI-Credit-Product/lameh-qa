# Sector Analysis Ratio Verification — Agent Context

> Scope note: this file covers `tests/integration/sector_analysis_ratios.py`
> only — one step of a five-step pipeline. The others document themselves in
> their module docstrings, which are the primary documentation for them:
>
> | | |
> |---|---|
> | `tests/sector_analysis_audit.py` | runs all five in order, passing each step's output to the next |
> | `tests/E2E/sector_analysis_download_company_ratios.py` | step 1, the Playwright download |
> | `tests/integration/summarize_ratio_failures.py` | step 3, one row per ratio across a run |
> | `tests/coverage/ratio_coverage.py` | step 4, which companies were offered each ratio |
> | `tests/coverage/ratio_emptiness.py` | step 5, which offered ratios ever held a number |
> | `tests/utils/` | shared helpers — see [`tests/utils/AGENT.md`](tests/utils/AGENT.md) |
>
> Read `ratio_coverage.py`'s docstring before changing how it finds the
> download script's ratio labels: it parses them out of the download script's
> source, so it depends on that file's path as well as its shape.
>
> The LangSmith eval suites for the Intelligence agent are unrelated to all of
> this and are documented in [`tests/langsmith/AGENT.md`](tests/langsmith/AGENT.md)
> (financial statements in `fs/`, board analysis in `research/`).

This file documents `sector_analysis_ratios.py` for future agent sessions. It
is **standalone** — it imports nothing from the rest of the repo and can be
dropped into any Python environment with `openpyxl` (and optionally `pywin32`
on Windows) and run on its own. It covers what the tool does, why it's built
the way it is, the non-obvious things discovered empirically about Lameh's
export format, and the conventions to follow when extending it.

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
poetry run poe ratios path/to/export.xlsx [--tolerance 0.005] [--csv out.csv] [--fail-only] [--web-only]
poetry run poe ratios --dir data/sector-analysis/<ENV>-<timestamp>/ [--fail-only] [--web-only]
```

- `--dir` — verify every `.xlsx` directly under a directory instead of a
  single file. One unreadable file is reported and skipped rather than
  stopping the batch.
- `--tolerance` — relative tolerance for a PASS (default 0.5%).
- `--csv` — base path for CSV reports (see "Two-pass WEB vs EXCEL" below).
  If omitted, defaults to `results/sector-analysis/<ENV>-<date>/<company
  name>.csv` (date format `YYYY-MM-DD-HH-MM-SS`, company name read from the
  export itself and sanitized for use as a filename; `<ENV>-` is dropped when
  `$ENV` is unset — see "The ENV label" below). Under `--dir` the whole
  segment is instead the name of the folder passed to `--dir`, so a batch's
  reports land under the same name as the download run they describe rather
  than under a second timestamp taken at verification time — and re-verifying
  a run overwrites its previous reports instead of adding another folder.
- `--fail-only` — when writing CSV, include only non-PASS rows.
- `--web-only` — skip the Excel-recalculation pass (see below); use this on a
  machine without a local Excel installation.

## The ENV label

`$ENV` (`DEV`, `UAT` or `CORE`, read from `.env`) names the deployment a run
came from. The three export materially different ratio sets — the tables below
are split by environment for exactly that reason — and a folder named by
timestamp alone gives no way to tell them apart later.

It is applied in one place and inherited everywhere else:

- The download (step 1) writes to `data/sector-analysis/<ENV>-<timestamp>/`.
  It **requires** `$ENV` and refuses to start without a valid one. A run
  mislabelled as the wrong environment is worse than one that doesn't start,
  since every downstream report takes this folder's name.
- Steps 2–5 all take that directory as `--dir` and name their output after it,
  so they need no knowledge of `$ENV` at all. This is the same inheritance
  that already made a batch's reports land under the run's own name.
- `sector_analysis_ratios.py` reads `$ENV` **only** for the single-file case,
  which invents its own timestamp and would otherwise be the one unlabelled
  output. It does not require it: refusing to verify one workbook because an
  unrelated variable is unset costs more than the label is worth, so an unset
  or unrecognized `$ENV` just falls back to a bare timestamp. Values are
  upper-cased and stripped, so `uat` and ` Core ` work.

Nothing cross-checks `$ENV` against `BASE_URL` — they are two independent
strings in `.env`, and pointing `BASE_URL` at uat while `ENV=DEV` will happily
produce a folder full of uat exports labelled `DEV-`. Keep them in step by
hand.

Reading `.env` in `sector_analysis_ratios.py` is done behind a
`try: from dotenv import load_dotenv / except ImportError: pass`, deliberately:
this file is documented above as standalone and droppable into any environment
with `openpyxl`, and a hard `python-dotenv` import would end that.

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
`pyproject.toml` already carries the dependency behind a
`sys_platform == "win32"` marker, so it is not required on Linux/Mac CI.

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

## Ratios the registry knows but no run has exercised

Thirteen ratios were originally added to the registry without ever producing
a verdict: in the 2026-08-01 run they appeared as top-level rows in all 121
exports but were blank in every period, in the WEB pass *and* after a full
Excel recalculation. Eight of them ship a real Excel formula that falls
through to its own `IFERROR(...,"-")`, and their registry entries were
transcribed character-for-character from the `<f>` cell text rather than
inferred from an input tree — reading the formula text is the reliable move
when a ratio has no values to fit against. The other five had no formula at
all: static rows, and per the Excel-behavior convention above, a cell with no
formula is never computable.

**Since then the failure mode has changed and is now upstream of this
script.** Across the three 2026-08-04/05 runs (dev 121, uat 57, core 94
companies), ten of the thirteen were no longer *offered*: the download logged
"ratio not available, skipping" for every company, so they scored 0% in
`_ratio-coverage.csv` and never reached the export at all. They are gone from
the picker under the names we asked for, not blank within it:

`Enterprise Value (EV)`, `EV/EBITDA`, `EV/Revenue`, `Fixed Asset Turnover`,
`Return on Sales`, `Return on Invested Capital (ROIC)`, `Operating Income`,
`CFO Interest Coverage`, `Free Cash Flow to Firm (FCFF)`,
`Investing and Financing Coverage`.

**The download no longer asks for those ten.** All ten labels are gone from
`select_all_ratios`, along with the `Enterprise Value` section click that
only existed to expose two of them. Three consequences worth knowing before
reading a future report:

- They will **not** appear in `_ratio-coverage.csv` at all — not at 0%.
  `ratio_coverage.py` derives its rows by parsing the download script's own
  click labels, so a label we stopped asking for stops being a row. A 0% line
  is evidence a ratio just went missing; silence is the state after we've
  acknowledged it. Their last 0% readings are in the 2026-08-04/05 runs.
- Their formulas stay in the registry in `sector_analysis_ratios.py`. They
  cost nothing there, they are the transcriptions described above, and if the
  app brings a ratio back the only change needed is re-adding its click
  label.
- Re-adding a label is also the only way to find out whether it came back —
  nothing enumerates the app's picker, so a ratio we don't ask for is
  invisible to this pipeline whether it exists or not. That trade is
  deliberate: the ten were costing a click attempt per company per run
  (~270 across the three runs) to re-derive an answer already recorded here.

Three have started producing values, so the "unexercised" label no longer
applies to them:

| Ratio | Where | Result |
|---|---|---|
| `Debt Payment Ratio` | dev and uat, 100% coverage (0% in core) | 1 WEB fail in dev, none in uat — the transcription holds up |
| `ROA Adjusted` | core only, 94/94, POPULATED | 27 WEB / 122 EXCEL fails — the second-largest failure in core |
| `NOPAT` | core only, 91/94, POPULATED | 10 WEB fails |

The last two arrive under core's older labels, `ROA Adjusted (Tax Rate
Assumed Zero)` and `NOPAT (Tax Rate Assumed Zero)`; the registry carries both
spellings, and `NOPAT`'s formula was added when core started populating it.
The download clicks by visible-text prefix, which is why asking for `NOPAT`
matches the longer name.

`EV/EBITDA` and `EV/Revenue` remain unfixable from this side regardless:
both divide by `Enterprise Value (EV)`, which is itself in the missing ten.

### The duplicate-row false positive to watch for

`Return on Sales` was a confirmed false positive while it still exported, and
is the clearest illustration of the duplicate-row caveat above. It computed a
value where the app showed `"-"`, so under the blank-is-a-FAIL rule it FAILed
— 34 times across 4 recalculated exports. It was not a product bug. The app's
formula is `=IFERROR((E269/E270),"-")`, pointing at its own subtree rows,
which in a recalculated export read:

```
row  268  Return on Sales             sub=(top-level)       ['-', '-', '-', '-']
row  269  Net Profit for the Period   sub=Return on Sales   ['-', '-', '-', '-']
row  270  Total Revenue               sub=Return on Sales   ['-', '-', '-', '-']
```

The app has no inputs there and is right to show nothing. `load_metric_values`
resolves those same two names to rows 175 and 30 — different, populated
occurrences elsewhere in the sheet — so the check "computes" a ratio from
numbers the app never fed into it.

The ratio dropping out of the export retired that particular FAIL — and now
that it is no longer even requested, it cannot come back silently — but that
retires nothing about the limitation behind it, which belongs to the first-occurrence loader rather
than to the FAIL rule: **any** ratio whose subtree is empty while the same
metric names appear populated elsewhere will produce the same false FAIL.
Fixing it means resolving a formula's inputs within its own subtree (by row
proximity or by `Subsection`) instead of globally by name. Until then, check
`source_rows` before believing a blank-reported FAIL.

## Known bugs found via this tool

These survived a full Excel recalculation (i.e. they are NOT caching
artifacts — the formula itself disagrees with the correct, documented
calculation). Counts below are `WEB / EXCEL` fails from the three
2026-08-04/05 runs (dev 121 companies, uat 57, core 94), read off each run's
`_summary.csv`; `—` means the ratio isn't exported in that environment.

| Ratio | dev | uat | core |
|---|---|---|---|
| Free Cash Flow to Equity (FCFE) | 20 / 84 | 55 / 106 | 7 / 807 |
| Financial Leverage | 5 / 5 | 17 / 17 | 31 / 30 |
| ROE (DuPont 3-Factor) | 0 / 0 | 17 / 17 | 22 / 25 |
| ROE (DuPont 5-Factor) | 0 / 0 | 17 / 17 | 22 / 25 |
| Net Debt | 182 / 182 | — | 7 / 7 |
| Net Debt to EBITDA | 151 / 151 | — | 8 / 6 |
| Retention Ratio | 0 / 0 | — | 10 / 10 |
| Net Borrowing | 0 / 86 | 0 / 42 | 50 / 50 |

Two shapes in that table are worth reading deliberately:

- **`0 / N`** — every cached value passes and the recalculated value fails.
  The number the app displays is masking a formula that is wrong underneath.
  `Net Borrowing` in dev and uat is the clean example; `FCFE` in core is the
  severe one (7 cached fails against 807 recalculated).
- **`N / 0`** — fails in the cache, passes after recalculation. A stale cache
  in the export pipeline rather than a formula bug, though the stale number is
  still what a user sees. Most of core's margin family and the
  return-on-assets group sit here.

Plus the duplicate-row inconsistency for `Inventory Turnover` described
above (and potentially other metrics with the same "standalone vs
decomposition-input duplicate" pattern — not exhaustively audited).

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
  `Net Borrowing` is `CFF - Proceeds from Loans` + `CFF - Repayment of
  Loans`. Repayment is negative in all 972 periods of the 2026-08-01 run that
  report it, so the signed sum is the subtraction. Either side may be absent
  on its own and counts as 0; only a period with neither is left unverified.
  It passed cleanly on that run (1210 periods PASS, 0 FAIL) but has failed in
  all three runs since — see the table above, and note the `0 / N` shape in
  dev and uat.
  The eight formulas in the "transcribed from the export's own Excel
  formulas" block at the end are a different case — see "Ratios the registry
  knows but no run has exercised" above.
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
