# Lameh QA

QA tooling and scope documentation for the Lameh platform.

## Contents

- **[QA-SCOPE.md](QA-SCOPE.md)** — one-page inventory of what needs testing across the platform (modules, testing types, known risk areas).
- **[AGENT.md](AGENT.md)** — detailed developer/agent notes for `tests/integration/sector_analysis_ratios.py`.
- **`tests/integration/sector_analysis_ratios.py`** — standalone script that verifies internal consistency of every "Financial Ratios" metric in a Sector Analysis chart-builder export, by recomputing each ratio from its own reported input components. Supports a single `.xlsx` file or a whole `--dir` of them (one bad file doesn't stop the batch).
- **`tests/integration/summarize_ratio_failures.py`** — post-processing step that aggregates the FAIL rows across all the CSV reports written by `sector_analysis_ratios.py` into one row per ratio (Web/Excel fail counts, split into quarterly/yearly), so a bug affecting one ratio across many companies is visible as a single line instead of buried in dozens of per-company files.
- **`tests/E2E/sector_analysis_download_company_ratios.py`** — Playwright script that logs in once (manual OTP), then loops through a list of companies building a "select all ratios" Sector Analysis and downloading each Excel export under `data/`.
- **`tests/langsmith/`** — LangSmith eval suite for the Lameh Intelligence module (the LLM agent that answers financial-analysis prompts). Builds a prompt-set dataset, runs it against the live agent, grades each response for correctness (live ground-truth comparison), helpfulness (completeness + LLM-as-judge), and safety (not yet built), then produces a markdown production-readiness report. See [tests/langsmith/config.py](tests/langsmith/config.py) for thresholds/settings.
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

The E2E script, and the LangSmith eval suite, read their configuration (`BASE_URL`, LangSmith credentials, orchestrator/chart-data credentials, AWS Bedrock credentials for the LLM judge) from a `.env` file (gitignored, not committed). Copy `.env.example` to `.env` and fill in the real values:

```
cp .env.example .env
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

### LangSmith eval suite (Lameh Intelligence)

Push the prompt set (`tests/langsmith/dataset/prompt_set.json`) to the LangSmith dataset (also picks up any edits to existing prompts, not just new ones):

```
poetry run poe langsmith-build-dataset
```

Run the eval — calls the live agent for every dataset example (or just one via `--example-id`) and scores each response with the correctness/grounding/completeness/helpfulness evaluators, uploading results to LangSmith:

```
poetry run poe langsmith-run [--example-id materials-q1-cash-quality] [--max-concurrency 4] [--report-out path/to/report.md] [--skip-report]
```

Examples run in parallel (4 at a time by default) since each prompt is a ~7–10 minute agent call — the whole dataset takes about as long as its slowest single prompt. Pass `--max-concurrency 1` to serialize.

This prints an experiment name (e.g. `materials-sector-3a94b70b`) and a LangSmith dashboard URL, then automatically builds the markdown production-readiness report for that experiment once it finishes. Pass `--skip-report` to only run the experiment.

To (re)build the report for an experiment on its own — e.g. an older run, or one where report generation failed:

```
poetry run poe langsmith-report --experiment materials-sector-3a94b70b [--out path/to/report.md]
```

Written by default to `results/langsmith/<experiment>.md` (gitignored) — aggregates pass rates per dimension, sliced by sector/prompt type, against the thresholds in `tests/langsmith/config.py`, with every example linking to its LangSmith trace and orchestrator conversation thread.
