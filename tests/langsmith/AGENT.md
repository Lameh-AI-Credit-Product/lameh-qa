# Lameh Intelligence — LangSmith Eval Suites

Two eval suites over one shared harness, both grading the Intelligence agent
(the orchestrator's `/v0/chat` endpoint) as LangSmith experiments and writing a
production-readiness report from the result.

| | grades | dataset | ground truth |
|---|---|---|---|
| [`fs/`](fs/AGENT.md) | financial-statement answers — are the numbers right? | `FS-Intelligence` | `chart-builder/chart-data/batch` |
| [`research/`](research/AGENT.md) | board-analysis answers — did the agent notice the number *can't* be right? | `Research-Intelligence` | frozen snapshot of `board-analysis/merge/tables` |

**Each suite has its own AGENT.md, and those are the primary documentation.**
This file covers only what neither can: the split between them, and what lives
in `shared/`.

## Running them

```
poetry run poe langsmith-fs-build-dataset        # sync fs/dataset/prompt_set.json
poetry run poe langsmith-fs-run                  # run + report
poetry run poe langsmith-fs-report --experiment <name>

poetry run poe langsmith-research-freeze          # refresh the frozen ground truth
poetry run poe langsmith-research-build-dataset
poetry run poe langsmith-research-run
poetry run poe langsmith-research-report --experiment <name>
```

Both `run` tasks take `--ai-mode expert|instant` (`fast` is an alias for
`instant`), `--example-id`, `--max-concurrency`, `--agent-timeout`,
`--report-out` and `--skip-report`. Their defaults differ because the workloads
do: FS sector prompts take 7–10 minutes each, research prompts answered in
135s on the 2026-08-23 probe, so the deadlines are 900s and 300s respectively.

Reports land in `results/langsmith/<suite>/<experiment>.md` (`fs/` or `research/`), which is gitignored.

## Layout

```
tests/langsmith/
  shared/            everything neither suite owns
    env_config.py      credentials, $ENV, AI modes, experiment_prefix()
    agent_client.py    the system under test — SSE client, ai_mode, deadlines
    judge_client.py    Bedrock transport only (swappable/mockable)
    metrics.py         compare / tolerance_match / mape
    suite.py           SuiteSpec — what a suite tells the shared tools
    suites.py          slug -> SuiteSpec registry
    build_dataset.py   prompt_set.json -> LangSmith dataset, --suite
    report/build_report.py  threshold gates + markdown, --suite
  fs/                config.py, run_eval.py, dataset/, evaluators/, AGENT.md
  research/          config.py, run_eval.py, dataset/, evaluators/,
                     comment_format.py, AGENT.md
```

### What is shared, and what deliberately is not

Shared: the agent client, the judge *transport*, the dataset builder, the
report renderer, and `metrics.py`. All five are indifferent to what is being
graded.

Not shared, on purpose:

- **The evaluators.** Three keys appear in both suites — `answer_coverage`,
  `answer_quality` and `security` — and they are shared *names*, not shared
  code, thresholds, or in one case even meaning:

  | key | FS | research |
  |---|---|---|
  | `security` | judged; regulated-advice surface is buy/sell recommendations | judged; surface is inference about named individuals |
  | `answer_quality` | judged, 3 criteria | judged, 3 different criteria |
  | `answer_coverage` | **judged** — companies, metrics and fiscal years | **structural** — fiscal years only, a floor rather than a pass |

  `answer_coverage` is the trap: same column name, different question, so a
  cross-suite dashboard filter on it compares unlike things. It was named
  before the second suite existed. Renaming it now would start a fresh metric
  and orphan every past FS experiment, which costs more than the ambiguity —
  but do not read the two side by side.
- **The judge rubrics.** FS asks "is this figure right and is it tagged";
  research asks "did the answer handle the planted defect". Even security
  diverges: FS's regulated-advice surface is buy/sell recommendations, while
  research's is inference about the named individuals these answers are full
  of.
- **`config.py`.** Each suite has one, and `config` means "this suite's
  config" everywhere in the tree. The shared one is `env_config` precisely so
  the name never collides — `shared/suites.py` loads each suite's config from
  an explicit file path under a unique module name for the same reason.
- **`comment_format.py`.** The FS suite's lives at its root and the research
  suite has its own. They render different findings; the only thing they share
  is the shape `build_report._detail_lines` expects (a headline line, then
  `- ` detail lines).

The rule when adding something: if it would need to know which suite called
it, it does not belong in `shared/`.

## SuiteSpec

`shared/suite.py` defines the object a suite hands the shared tools:
dataset name, experiment-suite segment, prompt-set path, evaluator keys and
labels in report-column order, which keys gate, which are durations rather than
rates, the thresholds, and the breakdown tables to render.

`build_report.py` takes a spec on every entry point rather than importing
evaluator keys at module scope. That is not decoration — before it, the report
hardcoded the nine FS keys and a "By Sector" breakdown, both of which are
meaningless for a suite whose every row is one company. The research spec
groups by prompt type and by **trap kind** instead, using a list-valued
metadata field the report explodes into one bucket per kind.

Adding a suite is: a directory, a `config.py` exporting `REPORT_SPEC`, a
`run_eval.py`, and a slug in `shared/suites.py:SUITE_NAMES`.

## The two AI modes

The agent answers in one of two modes, sent as `context.ai_mode` in the
`/v0/chat` payload. **The product calls them "expert" and "fast"; the API calls
them `expert` and `instant`.** Sending `"fast"` is a 422 — `ai_mode must be one
of: instant, expert` — so `instant` is the value everywhere: in `AI_MODES`, in
experiment names, in run outputs. `fast` is a CLI alias
(`env_config.AI_MODE_ALIASES`), normalized before it reaches anything.

Both modes are evaluated as **two experiments over the one dataset** — never as
two datasets and never as one mixed experiment:

- **One dataset**, because LangSmith's comparison view is scoped to a single
  dataset. Two datasets can't be diffed at all, which would destroy the only
  reason to run fast mode. A mode is a property of the system under test — like
  `chat_model` and `reasoning_effort` beside it in `agent_client` — not of the
  questions.
- **Two experiments**, because an experiment's aggregate is per-experiment. A
  single run holding both modes would gate on an expert/fast blend, where a
  fast-mode security failure can average into "ready".

## Response time is not an evaluator

It was one, until 2026-08-23. `response_time_seconds` carried a duration as
its "score" — the only key in either suite that was not a 0–1 rate and for
which lower was better. It is gone from both suites.

It was removed because it duplicated LangSmith, worse. Measured against
`UAT-intelligence-research-expert-51e125e0`:

- LangSmith's own per-run latency matched our stream timing **to within 0.1s**
  (127.79 vs 127.8, 133.47 vs 133.4, 161.32 vs 161.3), so we were re-measuring
  a number we already had.
- The session object exposes `latency_p50` and `latency_p99` directly, while
  our aggregate was a **mean** over a handful of examples — the wrong
  statistic. On the FS suite the difference is not cosmetic: p50 316s / p99
  462s, against a mean of ~360s that hid the tail.
- Being a duration in a table of rates forced `SECONDS_KEYS` special-casing
  through every formatter, and it could never be gated because a per-mode
  budget was never defensible.

`build_report.fetch_latency()` now reads the percentiles off the experiment
and prints them in the report header alongside the concurrency they were
measured under.

**The one thing the dashboard cannot do is still covered.** `target()` catches
a deadline kill and returns normally, so LangSmith records an ordinary slow
run rather than a timeout. `answer_coverage` fails a cut-off answer outright,
and the report reads `timed_out` off the run outputs and names those examples
in its header.

`SECONDS_KEYS` stays on `SuiteSpec` as an empty tuple — a future suite may
legitimately score a duration, and the report still honours it. Past
experiments keep whatever `response_time_seconds` feedback they already
recorded; the key simply stops being written.

## Experiment naming

`<ENV>-<suite>-<mode>-<hex>`, e.g. `UAT-intelligence-research-instant-d01438a5`.
LangSmith appends the hex; the rest is `env_config.experiment_prefix(suite,
mode)`.

`$ENV` (`DEV`/`UAT`/`CORE`) matters because the three deployments hold different
data. An unset or unrecognized value drops that segment rather than failing —
a missing label is visible in the dashboard, a wrong one isn't. The suite
segment matters because both suites write into one LangSmith project
(`lameh-intelligence-eval`) and a dataset name isn't shown in an experiment
listing. The mode segment is never dropped: two experiments over one dataset
are told apart by nothing else.

Nothing cross-checks `$ENV` against `LAMEH_ORCHESTRATOR_URL`. Keep them in step
by hand.

## The two suites get ground truth differently

The FS suite fetches live from `chart-data/batch` at eval time; the research
suite grades against a committed snapshot (`research/dataset/ground_truth.json`)
and ships it to LangSmith as each example's reference output.

That is not an inconsistency. `chart-data/batch` is the system of record for
financial figures — the same DB the agent's own tools read — so "live" and
"correct" are the same thing there, and a restatement moving a score is
information rather than noise. Board-report merges are a derived artifact that
gets recut wholesale (347 vs 367 tables between two observed runs, with a
different section taxonomy), so live-fetching there means a score can move for
reasons that have nothing to do with the agent.

`shared/build_dataset.py` handles both with one rule: if `ground_truth.json`
sits beside a suite's `prompt_set.json`, its per-example entries become the
LangSmith examples' reference outputs. No suite branching, and the FS suite
simply has no such file.

## The organization-ids

Three variables, all read only by these suites:

| variable | used for | default |
|---|---|---|
| `LAMEH_ORGANIZATION_ID_FOR_INTELLIGENCE_EVAL` | the org the agent is called as, on `/v0/chat` | none |
| `LAMEH_CHART_DATA_ORGANIZATION_ID` | FS ground truth (`chart-data/batch`) and the sector roster | `00000000-…-000000000000` |
| `LAMEH_BOARD_ANALYSIS_ORGANIZATION_ID` | board-analysis ground truth — only when freezing the snapshot or checking drift | `00000000-…-000000000000` |

Each is named for what reads it. The first replaced a generic repo-wide
variable that nothing outside these suites turned out to consult, and which
has since been deleted: naming a variable for its consumer means the eval's
org can move without touching anything else, and a reader of `.env` can tell
which variable affects which tool.

**They are separate knobs even when they hold the same value**, which they
currently do. "Which org the agent runs as" and "which org holds the data we
grade against" are different questions, and collapsing them would mean
repointing the agent silently repoints the ground truth too.

The distinction is load-bearing for the research suite: the two orgs return
**genuinely different data**. Confirmed 2026-08-23 on Yamama Cement — one org
returned `merge_run_id 0310adad…` with 347 tables, the shared org
`d4607628…` with 367 tables, a re-cut section taxonomy, an extra fiscal year,
and cell values typed as strings rather than floats. The prompt set and the
frozen snapshot were both built from the shared org's run. Pointing the
board-analysis variable elsewhere fails silently, grading against a payload
where several planted defects do not exist.

## Conventions

- **Dataset rows are `prompt_set.json`.** Metadata keys drive what evaluators
  expect. Adding a prompt is a dataset edit; `build_dataset.py` never changes.
  **Removing a row deletes its LangSmith example**, so the file is the single
  source of truth in both directions. It has to be: `run_eval` takes its
  examples from LangSmith and never opens the prompt set, so before the sync
  pruned, a deleted row kept running in every experiment while the sync
  reported `0 created, 0 updated` — which is exactly how it was found. The
  deleted ids are named in the output, not just counted, because deleting an
  example orphans the runs past experiments point at. `--keep-orphans` lists
  them and deletes nothing. Only examples carrying an `id` in their metadata
  are ever considered, so anything hand-added in the dashboard is untouched.
- **`DATASET_NAME` is a pointer, not a name.** Editing it renames nothing —
  `get_or_create_dataset` will happily create an empty dataset under the new
  name and sync rows into it, leaving every past experiment attached to the
  old one. Rename in place (which keeps the UUID and the history) and change
  the constant in the same breath. `langsmith` 0.10.10 has no
  `Client.update_dataset`, so the rename goes through the REST endpoint on the
  client's own authenticated transport:
  `client.request_with_retries("PATCH", f"/datasets/{id}", request_kwargs={"json": {"name": ...}})`.
- **LangSmith writes its own metadata onto examples.** `dataset_split` is
  server-managed and absent from `prompt_set.json`, so a naive dict comparison
  never matches and every sync rewrites every row — which is what happened
  until 2026-08-23 (`0 created, 8 updated`, every time, on an untouched
  dataset). `build_dataset.SERVER_MANAGED_METADATA_KEYS` is the exclusion list;
  extend it if LangSmith adds another.
- **Renaming an evaluator key starts a new metric** in LangSmith; past
  experiments keep the old one. Rename deliberately.
- **`score: None` means "not applicable" and is deliberately not `0.0`.** It is
  what an evaluator returns when there was nothing to grade *and* when the
  judge failed. The consequence: a Bedrock outage does not fail a run, it
  shrinks the sample. The report renders `n/a` and averages over whichever
  examples scored, so `Answer Quality 100%` may be 2 of 6. Check the
  per-example detail before trusting a gate.
- **Feedback comments are plain text, not JSON.** LangSmith renders a comment
  as a string, so a `json.dumps` blob shows up escaped and unreadable. The
  shape is a headline line plus `- ` detail lines;
  `build_report._detail_lines` relies on it. Nothing parses comments back.
- **Console output is Arabic-heavy.** Reconfigure stdout to UTF-8 in any script
  you add; Windows defaults to cp1252 and will crash on a company name.
