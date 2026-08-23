# Lameh QA

QA tooling and scope documentation for the Lameh platform.

## Contents

- **[QA-SCOPE.md](QA-SCOPE.md)** — one-page inventory of what needs testing across the platform (modules, testing types, known risk areas).
- **[AGENT.md](AGENT.md)** — detailed developer/agent notes for `tests/integration/sector_analysis_ratios.py`.
- **`tests/sector_analysis_audit.py`** — runs the whole Sector Analysis pipeline in order (download → verify → summarize → coverage → emptiness), passing each step's output directory to the next instead of you copying paths between five commands. `--run-dir` starts from the verification step against a download you already have, which is the common case since the download is slow and needs a human at the OTP prompt.
- **`tests/integration/sector_analysis_ratios.py`** — standalone script that verifies internal consistency of every "Financial Ratios" metric in a Sector Analysis chart-builder export, by recomputing each ratio from its own reported input components. Supports a single `.xlsx` file or a whole `--dir` of them (one bad file doesn't stop the batch).
- **`tests/integration/summarize_ratio_failures.py`** — post-processing step that aggregates the FAIL rows across all the CSV reports written by `sector_analysis_ratios.py` into one row per ratio (Web/Excel fail counts, split into quarterly/yearly), so a bug affecting one ratio across many companies is visible as a single line instead of buried in dozens of per-company files.
- **`tests/E2E/sector_analysis_download_company_ratios.py`** — Playwright script that logs in once (manual OTP), then loops through a list of companies building a "select all ratios" Sector Analysis and downloading each Excel export under `data/`.
- **`tests/coverage/ratio_coverage.py`** — post-processing step over one download run: of the ratios the download script asks for, how many companies actually got each one. Every ratio starts at full coverage and each "ratio not available, skipping" line in `run.log` takes one company off it, sorted rarest-first. Separates a genuine per-company data gap from a ratio the app no longer offers under that name at all.
- **`tests/coverage/ratio_emptiness.py`** — the next question after coverage: when a ratio *was* offered, did it ever hold a number? Reports per-ratio blank and zero rates across a run, kept as separate columns because a blank means the app had nothing to show while a `0` is often a silent substitution for a missing input. A ratio blank in every period of every company still reads as 100% coverage, which is how twelve of them went unnoticed.
- **`tests/langsmith/`** — two LangSmith eval suites for the Lameh Intelligence module (the LLM agent that answers analysis prompts), over one shared harness. **`fs/`** grades financial-statement answers against the live financial database; **`research/`** grades board-analysis answers, where the question is less "is this figure right" than "did the agent notice this figure can't be right" — its prompts are built around real defects in the merged board reports. Both run against the live agent in either of its answering modes and produce a markdown production-readiness report. See [tests/langsmith/AGENT.md](tests/langsmith/AGENT.md) for how they fit together, and each suite's own `AGENT.md` and `config.py` for its evaluators and thresholds.
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

The E2E script, and the LangSmith eval suite, read their configuration (`BASE_URL`, `ENV`, LangSmith credentials, orchestrator/chart-data credentials, AWS Bedrock credentials for the LLM judge) from a `.env` file (gitignored, not committed). Copy `.env.example` to `.env` and fill in the real values:

```
cp .env.example .env
```

`ENV` must be `DEV`, `UAT` or `CORE`, matching the deployment `BASE_URL` points at. It labels the Sector Analysis run folder (`data/sector-analysis/<ENV>-<timestamp>/`) and, through it, every report derived from that run — the three deployments export different ratio sets, so a run you can't attribute to one of them is hard to read later. The download refuses to start without it. It also names the LangSmith experiment (`<ENV>-intelligence-fs-<mode>-<hex>`), which tolerates it being unset and just drops that segment. Nothing checks it against `BASE_URL` or `LAMEH_ORCHESTRATOR_URL`, so keep them in step by hand.

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

### LangSmith eval suites (Lameh Intelligence)

Two suites over one harness. Every command below exists in an `fs` and a `research` form; they take the same flags and differ only in what they grade.

| Suite | Dataset | Grades |
|---|---|---|
| `fs` | `FS-Intelligence` | financial-statement answers — are the stated numbers right, and can each be traced back to the database? |
| `research` | `Research-Intelligence` | board-analysis answers — governance, related parties, workforce, projects, market data, HSE |

Push a suite's prompt set to its LangSmith dataset (this also picks up edits to existing prompts, not just new ones):

```
poetry run poe langsmith-fs-build-dataset         # tests/langsmith/fs/dataset/prompt_set.json
poetry run poe langsmith-research-build-dataset   # tests/langsmith/research/dataset/prompt_set.json
```

The research suite grades against a **frozen** snapshot of the board-report data rather than fetching it per run, so a score change means the agent changed and not the source. The snapshot is committed, and it rides along on each LangSmith example as its reference output — so the dashboard shows which source rows back each planted defect. Refresh it, or check whether it has gone stale, with:

```
poetry run poe langsmith-research-freeze           # refresh
poetry run poe langsmith-research-freeze --check   # report drift only
```

A run warns loudly if the frozen snapshot no longer matches the live merge run, but never fails on it.

Run the eval — calls the live agent for every dataset example (or just one via `--example-id`), scores each response, and uploads the results to LangSmith.

**FS evaluators:**

| Evaluator | What it checks |
|---|---|
| `numeric_accuracy` | Every number the agent states, against the live database within 1% tolerance |
| `tag_completeness` | Every `<calc>`/`<number>` tag carries the fields needed to look its figure up at all |
| `company_coverage` | Every company the prompt asked about is actually present |
| `no_fabricated_companies` | Every company the agent names actually exists in the sector |
| `answer_coverage` | The answer covers every company, metric and fiscal year the prompt asked for |
| `answer_quality` | LLM judge: on topic, well formatted, and actually answers the question |
| `security` | LLM judge: no leaked internals, no obeying injected instructions, no regulated investment advice |
| `all_values_tagged` | No financial figure stated with no provenance tag at all |

**Research evaluators** — a different set, because the failure being hunted is different. Each prompt in this suite is built around a real defect in the source data (a related-party cell reading `57` between neighbours reading 343,000; a forecast column stored unscaled; one director spelled seven ways; a safety metric the company never disclosed). A fluent answer that repeats the source's defect is the failure mode:

| Evaluator | What it checks |
|---|---|
| `source_attribution` | Every tagged figure carries the document, page and evidence block needed to find it |
| `answer_coverage` | The fiscal years the prompt asked about are addressed — a floor, not a pass |
| `trap_handling` | Did the answer deal with the specific data defects this prompt declares? |
| `no_hallucinated_data` | LLM judge: nothing asserted that the sources don't support |
| `answer_quality` | LLM judge: on topic, sourced, actually answers the question |
| `security` | LLM judge: no leaked internals, no obeying injected instructions, no regulated advice, no inference about named individuals beyond what a board report discloses |

Response time is not an evaluator. LangSmith already records per-run latency (matching our own measurement to 0.1s) and exposes p50/p99 per experiment, so the report reads those and prints them in its header rather than re-measuring them as a fake "score".

A blank score means "not applicable to this example" (no numbers to compare, no companies named, nothing specific expected) — distinct from a failing `0`. A judge failure also scores blank, so an outage shrinks the sample rather than failing the run; check the per-example detail before trusting a headline number.

```
poetry run poe langsmith-fs-run       [--ai-mode expert|fast] [--example-id materials-q1-cash-quality] [--max-concurrency 8] [--report-out path/to/report.md] [--skip-report]
poetry run poe langsmith-research-run [--ai-mode expert|fast] [--example-id yamama-safety-metrics]     [--max-concurrency 6] [--report-out path/to/report.md] [--skip-report]
```

Examples run in parallel, one per dataset row by default, so a whole dataset takes about as long as its slowest single prompt. Pass `--max-concurrency 1` to serialize. The two suites' defaults differ because their workloads do: an FS sector prompt is a ~7–10 minute agent call (8 at a time, 900s deadline), a research prompt answered in 135s when measured (6 at a time, 300s deadline).

**Testing both AI modes.** The agent answers in expert mode or fast mode, and `--ai-mode` picks which (default `expert`). A run is one mode, so comparing them is two runs against the same dataset — which is what makes them comparable, since LangSmith's side-by-side comparison view only works within one dataset:

```
poetry run poe langsmith-fs-run                 # expert
poetry run poe langsmith-fs-run --ai-mode fast  # fast
```

The API's own name for fast mode is `instant`, and that is the spelling that appears in experiment names and reports; `fast` is accepted here as an alias for it. Note the report's p50/p99 response times are measured with the whole dataset in flight at once, so they compare fairly between two runs at the same `--max-concurrency` and are not absolute per-prompt latencies — the report header states the concurrency for that reason.

Each run prints an experiment name (e.g. `UAT-intelligence-fs-expert-3a94b70b` or `UAT-intelligence-research-instant-d01438a5` — the deployment from `ENV`, then the suite, then the AI mode, then a suffix LangSmith generates) and a LangSmith dashboard URL, then automatically builds the markdown production-readiness report for that experiment once it finishes. Pass `--skip-report` to only run the experiment.

To (re)build the report for an experiment on its own — e.g. an older run, or one where report generation failed:

```
poetry run poe langsmith-fs-report       --experiment UAT-intelligence-fs-expert-3a94b70b [--out path/to/report.md]
poetry run poe langsmith-research-report --experiment UAT-intelligence-research-instant-d01438a5
```

Written by default to `results/langsmith/fs/<experiment>.md` or `results/langsmith/research/<experiment>.md` (gitignored) — aggregates each dimension against that suite's thresholds, with every example linking to its LangSmith trace and orchestrator conversation thread. The header records the AI mode and concurrency the run used. The breakdown tables differ per suite: FS slices by sector and prompt type, research by prompt type and by the *kind* of data defect each prompt plants.
