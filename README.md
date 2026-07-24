# Lameh QA

QA tooling and scope documentation for the Lameh platform.

## Contents

- **[QA-SCOPE.md](QA-SCOPE.md)** — one-page inventory of what needs testing across the platform (modules, testing types, known risk areas).
- **[AGENT.md](AGENT.md)** — detailed developer/agent notes for `tests/integration/sector_analysis_ratios.py`.
- **`tests/integration/sector_analysis_ratios.py`** — standalone script that verifies internal consistency of every "Financial Ratios" metric in a Sector Analysis chart-builder export, by recomputing each ratio from its own reported input components. Supports a single `.xlsx` file or a whole `--dir` of them (one bad file doesn't stop the batch).
- **`tests/integration/summarize_ratio_failures.py`** — post-processing step that aggregates the FAIL rows across all the CSV reports written by `sector_analysis_ratios.py` into one row per ratio (Web/Excel fail counts, split into quarterly/yearly), so a bug affecting one ratio across many companies is visible as a single line instead of buried in dozens of per-company files.
- **`tests/E2E/sector_analysis_download_company_ratios.py`** — Playwright script that logs in once (manual OTP), then loops through a list of companies building a "select all ratios" Sector Analysis and downloading each Excel export under `data/`.
- **`results/`** — generated CSV reports from running the ratio-verification script (gitignored).
- **`data/`** — downloaded `.xlsx` exports from the E2E script (gitignored).

## Setup

Requires Python >= 3.11 and [Poetry](https://python-poetry.org/).

```
poetry install
```

For the E2E script, Playwright also needs its browser binaries installed once:

```
poetry run playwright install chromium
```

## Usage

Run the Sector Analysis ratio check against a single exported `.xlsx`:

```
poetry run poe ratios path/to/export.xlsx [--tolerance 0.005] [--csv out.csv] [--fail-only] [--web-only]
```

Or against every `.xlsx` directly under a directory:

```
poetry run poe ratios --dir path/to/exports/ [--tolerance 0.005] [--fail-only] [--web-only]
```

Then summarize the FAIL rows across all the reports just written (into the same directory, e.g. `results/sector-analysis/<date>/`):

```
poetry run poe ratios-summary results/sector-analysis/<date>
```

This always writes `_summary.csv` (one row per ratio), `_web-details.csv`, and `_excel-details.csv` into that directory, and prints the summary table to the console.

See [AGENT.md](AGENT.md) for full details on the ratio-verification script's behavior, conventions, and known bugs it has found.

Run the E2E download script (opens a browser window, waits for you to complete OTP once, then loops over the company list hardcoded at the top of the file):

```
poetry run poe download-ratios
```
