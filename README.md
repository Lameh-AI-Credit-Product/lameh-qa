# Lameh QA

QA tooling and scope documentation for the Lameh platform.

## Contents

- **[QA-SCOPE.md](QA-SCOPE.md)** — one-page inventory of what needs testing across the platform (modules, testing types, known risk areas).
- **[AGENT.md](AGENT.md)** — detailed developer/agent notes for `tests/integration/sector_analysis_ratios.py`.
- **`tests/integration/sector_analysis_ratios.py`** — standalone script that verifies internal consistency of every "Financial Ratios" metric in a Sector Analysis chart-builder export, by recomputing each ratio from its own reported input components. Supports a single `.xlsx` file or a whole `--dir` of them (one bad file doesn't stop the batch).
- **`tests/integration/summarize_ratio_failures.py`** — post-processing step that aggregates the FAIL rows across all the CSV reports written by `sector_analysis_ratios.py` into one row per ratio (Web/Excel fail counts, split into quarterly/yearly), so a bug affecting one ratio across many companies is visible as a single line instead of buried in dozens of per-company files.
- **`tests/E2E/sector_analysis_download_company_ratios.py`** — Playwright script that logs in once (manual OTP), then loops through a list of companies building a "select all ratios" Sector Analysis and downloading each Excel export under `data/`.
- **`tests/coverage/ratio_coverage.py`** — post-processing step over one download run: of the ratios the download script asks for, how many companies actually got each one. Every ratio starts at full coverage and each "ratio not available, skipping" line in `run.log` takes one company off it, sorted rarest-first. Separates a genuine per-company data gap from a ratio the app no longer offers under that name at all.
- **`tests/coverage/ratio_emptiness.py`** — the next question after coverage: when a ratio *was* offered, did it ever hold a number? Reports per-ratio blank and zero rates across a run, kept as separate columns because a blank means the app had nothing to show while a `0` is often a silent substitution for a missing input. A ratio blank in every period of every company still reads as 100% coverage, which is how twelve of them went unnoticed.
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

Then check how much of the ratio list that run actually came back with:

```
poetry run poe ratio-coverage data/sector-analysis/<timestamp> [--resolve-names] [--max-coverage 0] [--csv out.csv]
```

Writes `_ratio-coverage.csv` into the run directory and prints the table. A ratio at 0% is a different finding from one at 6%: 6% is a data gap (most companies genuinely have no such figure), while 0% means the app has no ratio under that name at all — it was renamed or removed and the download script has been asking for a label that stopped existing. `--resolve-names` opens the exports to recover the name each label actually matched, which is what tells a rename apart from a removal; it's off by default because it reads every file in the run. `--max-coverage 0` prints only the ones that are gone entirely.

This reads the ratio labels straight out of the download script's source, so that script needs no edits to stay in sync. It can only report on labels the download script asks for — a ratio the app offers under a name we don't know is never clicked, never skipped, and so invisible here.

### LangSmith eval suite (Lameh Intelligence)

Push the prompt set (`tests/langsmith/dataset/prompt_set.json`) to the LangSmith dataset (also picks up any edits to existing prompts, not just new ones):

```
poetry run poe langsmith-build-dataset
```

Run the eval — calls the live agent for every dataset example (or just one via `--example-id`), scores each response, and uploads the results to LangSmith:

| Evaluator | What it checks |
|---|---|
| `numeric_accuracy` | Every number the agent states, against the live database within 1% tolerance |
| `no_fabricated_companies` | Every company the agent names actually exists in the sector |
| `answer_coverage` | The answer covers every company, metric and fiscal year the prompt asked for |
| `answer_quality` | LLM judge: on topic, well formatted, and actually answers the question |

A blank score means "not applicable to this example" (no numbers to compare, no companies named, nothing specific expected) — distinct from a failing `0`.


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
