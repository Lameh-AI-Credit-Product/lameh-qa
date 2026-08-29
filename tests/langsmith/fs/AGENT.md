# FS Suite — Financial-Statement Evals

Evaluates the Intelligence agent (the orchestrator's `/v0/chat` endpoint) on
financial-statement questions, as a LangSmith experiment over the
`FS-Intelligence` dataset, and writes a production-readiness report from the
result.

This is one of two suites under `tests/langsmith`. The shared harness, `$ENV`
labelling, AI modes and experiment naming are documented once in
[`../AGENT.md`](../AGENT.md); the board-analysis suite is in
[`../research/AGENT.md`](../research/AGENT.md).

**The per-module docstrings are the primary documentation and are kept
current — read them before changing a module.** They carry the *why*:
`llm_judge.py` explains why there are four judge calls instead of one,
`ground_truth.py` why lookups are batched, `correctness.py` why unresolvable
facts are skipped rather than failed. This file covers only what no single
module can: the shape of the whole thing, and the state of the world.

## Running it

```
poetry run poe langsmith-fs-build-dataset     # sync prompt_set.json -> LangSmith dataset
poetry run poe langsmith-fs-run               # run the experiment + build the report
poetry run poe langsmith-fs-run --ai-mode fast           # the same set, fast mode
poetry run poe langsmith-fs-report --experiment <name>   # rebuild a report only
```

A full run is ~10 minutes: the prompts run in parallel at 7–10 min each, plus
under a minute of evaluator overhead. The set is 6 prompt types × 2 sectors
(Materials, Health Care Equipment & Svc) = 12 rows, so a run is also 48 judge
calls. Four of the six are the analysis-heavy prompts (q1–q4); q5 and q6 are
deliberately simple single-metric lookups — one or two companies, one metric,
one or two fiscal years — so a failure there is unambiguous rather than
tangled up in a multi-part question. Useful flags on `langsmith-fs-run`:

- `--ai-mode expert|instant` — default `expert`; `fast` is an alias for
  `instant`, which is what the API actually calls fast mode. See "The two AI
  modes" below
- `--example-id materials-q1-cash-quality` — one prompt only, for a fast loop
- `--max-concurrency N` — default 12 (= the dataset size); use 1 to serialize
- `--agent-timeout SECONDS` — default 900; a prompt past it is cut off, scored
  on the text that arrived, and fails `answer_coverage` as truncated
- `--skip-report`

## The two AI modes

The agent answers in one of two modes, sent as `context.ai_mode` in the
`/v0/chat` payload.

**The product calls them "expert" and "fast"; the API calls them `expert` and
`instant`.** Sending `"fast"` is a 422 — `ai_mode must be one of: instant,
expert` — so `instant` is the value everywhere in this suite: in `AI_MODES`,
in the experiment names, and in the run outputs. `fast` is accepted as a CLI
alias (`config.AI_MODE_ALIASES`) and normalized before it reaches anything, so
you can type the name you'd say out loud without two spellings ending up in
the dashboard. Confirmed live against uat on 2026-08-16; both values stream
and complete normally.

Both modes are evaluated, as **two experiments over the one dataset** — never
as two datasets and never as one mixed experiment:

- **One dataset**, because LangSmith's comparison view is scoped to a single
  dataset. Two datasets can't be diffed against each other at all, which would
  destroy the only reason to run fast mode. A mode is a property of the system
  under test — like `chat_model` and `reasoning_effort` sitting beside it in
  `agent_client`, neither of which is in the dataset either — not a property
  of the questions.
- **Two experiments**, because an experiment's aggregate is per-experiment. A
  single run holding both modes would have `build_report.apply_thresholds`
  gating on an expert/fast blend, where a fast-mode security failure can
  average into "ready".

So comparing them is two invocations, and `--ai-mode` is not a list. The mode
lands in three places: the experiment name
(`UAT-intelligence-fs-instant-<hex>`), the experiment metadata, and every
run's outputs — the last of which is what lets the report state the conditions
a timing was measured under.

Only expert mode has been run as a full experiment; everything under "Known
state" below predates fast mode entirely, and no claim there has been checked
against it.

Reports land in `results/langsmith/fs/<experiment>.md`, which is gitignored.

## Pipeline

```
prompt_set.json ──build_dataset──> LangSmith dataset ("FS-Intelligence")
                                         │
                          run_eval.target │ ask_agent (SSE stream)
                                         ▼
                                    answer text
                        ┌────────────────┴────────────────┐
              extract_all / extract_tags            llm_judge (Bedrock)
                        │                                 │
                  evaluators ─────────────────────────────┘
                        ▼
                  LangSmith feedback ──build_report──> results/langsmith/*.md
```

Files marked *(shared)* live in `../shared/` and are used by both suites.

| File | Role |
|---|---|
| `run_eval.py` | entrypoint; the 9 evaluator functions; `--ai-mode`; per-run parse + judge caches |
| `agent_client.py` *(shared)* | the system under test — SSE client, `ai_mode`, deadline handling |
| `config.py` | this suite's evaluator keys, thresholds, ground-truth URLs, `REPORT_SPEC` |
| `../shared/env_config.py` *(shared)* | credentials, `$ENV`, AI modes, experiment naming |
| `comment_format.py` | renders each evaluator's findings as the feedback comment |
| `judge_client.py` *(shared)* | Bedrock transport only (swappable/mockable) |
| `evaluators/extraction.py` | parses `<calc>`/`<number>` into facts *and* raw tags |
| `evaluators/tag_integrity.py` | grades the markup itself |
| `evaluators/correctness.py` | value comparison vs ground truth; company coverage |
| `evaluators/ground_truth.py` | batched `chart-data/batch` client + sector roster |
| `evaluators/llm_judge.py` | the four rubrics |
| `evaluators/helpfulness.py` | structural coverage (no judge — it moved out) |
| `../shared/metrics.py` *(shared)* | `compare`, `tolerance_match`, `mape` |
| `../shared/report/build_report.py` *(shared)* | threshold gates, markdown output; takes a `SuiteSpec` |

## The eight evaluators

Order below matches `EVALUATOR_KEYS`, which drives report column order.

| Key | Asks | Mechanism | Gate |
|---|---|---|---|
| `numeric_accuracy` | are the stated numbers right? | deterministic | 0.98 |
| `tag_completeness` | do the tags that exist carry every field needed to verify them? | deterministic | 1.00 |
| `company_coverage` | is every company asked about present? | deterministic | 1.00 |
| `no_fabricated_companies` | is every company named real? | deterministic | — |
| `answer_coverage` | companies + metrics + fiscal years asked for | judged, deterministic fallback | — |
| `answer_quality` | on topic, usable, actually answers | judged | 0.90 |
| `security` | leaks, injection compliance, regulated advice | judged | 1.00 |
| `all_values_tagged` | figures stated with **no** tag at all | **hybrid** | 1.00 |

Response time is not among them — see
[the umbrella doc](../AGENT.md#response-time-is-not-an-evaluator). The report
header carries p50/p99 read from LangSmith itself.

Four things about that table are easy to get wrong:

**`tag_completeness` and `all_values_tagged` are two halves of one question**
("can a reader verify this figure?") and neither sees the other's failure
mode. The first grades tags that exist but say too little; the second finds
figures with no tag at all. Don't merge them.

**`all_values_tagged` is the only hybrid.** Score is
`len(tags) / (len(tags) + untagged_total)` — numerator counted by regex,
denominator's second term counted by the judge. The split is deliberate: the
judge is good at spotting what's missing and bad at counting what isn't. It
follows that this score is *not* reproducible run-to-run; treat a couple of
points of movement as noise.

**`answer_coverage` asks the judge only for metric presence**, and only when
the row has `metrics_expected`. The comment records which path ran as
`"metric_source": "judge"` or `"structural-fallback"`. q4 has no expected
metrics, so it never calls the judge.

## Scoring semantics

**`score: None` means "not applicable" and is deliberately not `0.0`.** It is
what an evaluator returns when there was nothing to grade — no tags, no
expected companies, no resolvable facts — *and also when the judge failed*.

The consequence is worth internalizing: **a Bedrock outage does not fail the
run, it shrinks the sample.** The report renders `n/a` and computes the
dimension's average over whichever examples did score, so
`Answer Quality 100%` may be 2 of 4 examples rather than 4. Check the
per-example detail before trusting a gate. `_judge_error_comment` distinguishes
a truncated reply (token-ceiling problem) from non-JSON output, because
conflating them sent one investigation down the wrong path already.

Thresholds live in `config.THRESHOLDS`; `build_report.GATED_KEYS` decides
which ones block. `tag_completeness` and `all_values_tagged` gate at 1.0 on
purpose — an unverifiable figure isn't a quality tradeoff to tune. Relax only
with a reason recorded in `config.py`.

## Ground truth

`chart-builder/chart-data/batch`, the same DB the agent's own tools read, so
ratios come back pre-computed (`section: "Financial Ratios"`) alongside raw
line items (`"Income Statement"` / `"Balance Sheet"` / `"Cash Flow Statement"`).
Note it uses its own organization-id (`LAMEH_CHART_DATA_ORGANIZATION_ID`), separate from the one the agent is called as — see [the umbrella doc](../AGENT.md#the-organization-ids).

- It is **live**, not a fixture. A restatement in the DB moves the score
  without the agent changing. A drop isn't automatically a regression.
- Lookups are batched via `GroundTruthClient.prefetch`. Before this existed a
  sector prompt made ~693 serial requests and the run took 37 minutes. If you
  add a comparison path, prefetch it — don't reintroduce per-cell `get()`.
- Percent ratios come back as fractions (`0.0535`) while tags display
  `"5.35%"`.
- Sector rosters come from `/v0/chart-builder/sectors/grouped-by-companies`
  (one GET, all sectors), filtered to **`uploaded: true`** — the companies
  whose financials are actually loaded. The rest are listed on the exchange
  with nothing for the agent to read, so counting them would fail coverage
  for companies no answer could ever cover. Health Care is 10 uploaded of 27
  listed; Materials is 68 of 68, which is why the distinction stayed
  invisible while there was only one sector. `chart-data/batch` cannot
  enumerate — it 500s on an empty company list whatever `sectors` you pass.
- Tolerance is 1% relative (`metrics.DEFAULT_TOLERANCE`). `<calc>` additionally
  gets a half-ULP allowance on its *printed* precision via `display_precision`,
  because `"1.4%"` vs a true `1.4267%` is rounding, not error. `<number>` does
  **not** get it — it carries an unrounded `raw`.

## The judge

Claude via Bedrock (`AWS_*` in `.env`). Four independent calls per example,
each re-sending the response text. `judge_client.ask_judge` currently has **no
retry beyond the SDK default**, so a brief DNS failure takes out every judged
dimension for the examples in flight — this has happened.

`MAX_FINDINGS = 20` caps findings lists; `untagged_total` carries the real
count separately. Both exist because a 1402-tag response once overran the
token ceiling, truncated the JSON, and left all three judged dimensions
unscored.

## Known state — as of 2026-07-29

Verify before acting on these; they are snapshots, not invariants.

- **`شركة هضاب الخليج التجارية` (materials q2/q3) is a genuine fabrication.**
  It was previously written off as roster drift; that was wrong. A scan of all
  23 groups — 404 companies — finds no such company anywhere, and the old
  static roster turned out to match the live uploaded Materials set exactly
  (68/68). `no_fabricated_companies` was right both times it fired.
- **chart-data lags the app on ratio names.** The app renamed four ratios
  (commit `d4dd0a6`); `chart-data/batch` still answers only to the *old* name
  for three of them — `CFO Finance Cost Coverage`, `NOPAT (Tax Rate Assumed
  Zero)`, `ROA Adjusted (Tax Rate Assumed Zero)`. The eight newly-added ratios
  (`Fixed Asset Turnover`, `Return on Sales`, `Return on Invested Capital`,
  `EV/EBITDA`, `EV/Revenue`, `Enterprise Value (EV)`, `Operating Income`,
  `Investing and Financing`) don't exist there at all, and no section holds
  market data — `Market Cap`, `P/E` and `Share Price` resolve under none of
  `Financial Ratios`, `Market Data`, `Market Ratios`, `Valuation` or
  `Enterprise Value`. So q4's "EV/EBITDA if data available" and every market
  figure are unverifiable by design, not by fault.

  This fails *silently and upward*: an unresolvable metric leaves the
  denominator instead of failing, so the score rises as coverage falls. The
  `numeric_accuracy` comment now names the metrics that resolved for nobody —
  that list is the rename detector. Treat a new name appearing in it as an
  API/app divergence until proven otherwise.
- **q2's numeric accuracy is `n/a`.** 693 facts extracted, none resolvable —
  cost-of-revenue component names aren't attributes in `chart-data`. Its 1402
  tags are consequently unverified by any value check, so its
  `tag_completeness: 1.00` says nothing about correctness. Needs a different
  ground-truth path.
- **Real agent defects, reproduced across runs:** SABIC FY2025 Net Profit
  reported ~71x the DB value; Saudi Paper FY2025 Net Profit ~45% off; q1/q4
  ad-hoc `<calc>` tags omit `rid`/`id`/`ops` fields (tag completeness 0.59–0.74);
  q2 omits all 10 cement companies from a sector-wide answer.
- **Al-Modawat (`شركة المداواة التخصصية الطبية`, healthcare-q1) has almost no
  data**: FY2025 only, and only Total Assets and ROE (0.0) — no revenue, no
  net profit, no CFO. The name is correct and it is an uploaded company; the
  figures aren't there. q1 asks for DSO/ROE/CapEx across FY2023–25, so "not
  available" is the correct answer for it and `company_coverage` will sit
  near 0.75 on that row.
- **Flake:** q4 has returned a completely empty answer on one run and a full
  one the next.
- The Health Care rows have never been run. Everything above about them is
  from the dataset and the API, not from an observed run.

## Conventions

- Dataset rows are `prompt_set.json`; metadata keys (`scope`, `companies`,
  `metrics_expected`, `fiscal_years`, `sector`) drive what each evaluator
  expects. A `scope: "sector"` row carries `companies: []` and falls back to
  the roster — see `_expected_companies`. Adding a sector is adding rows;
  `build_dataset.py` never needs to change, and `sector` must equal the API's
  `name_en` exactly ("Health Care Equipment & Svc", not "... & Services").
- `companies` metadata must be the **Arabic** name exactly as the DB spells
  it, even where the prompt text names the company in English (both q4 rows
  do this) — matching is exact string equality. Don't transliterate by hand;
  take `name_ar` from the roster endpoint. Canadian Medical Center Co. is
  `شركة مجمع المركز الكندي الطبي العام`, which is not what you'd guess.
- Verify a new company name resolves before trusting it, **and verify with a
  metric that has data**: a wrong name and an empty metric both return `None`.
  `Revenue` is empty for every company checked so far; `Net Profit for the
  Period` works.
- `DATASET_NAME` is `FS-Intelligence` (was `materials-sector-v1` until
  2026-08-16; it had long since stopped being one sector). It lives in
  `fs/config.py`. **Editing it does not rename anything** — see the rename
  procedure in [`../AGENT.md`](../AGENT.md), which also records that the
  2026-08-16 rename kept UUID `8db5b0cb-b845-4bc0-90a3-8b2cd4e39085`. The name
  is read in three places — `build_dataset` (via `REPORT_SPEC`),
  `run_eval._select_examples`, and `build_report.fetch_experiment_results`; the
  last degrades quietly if it's stale (no examples resolve, so every row loses
  its `sector` and `prompt_type` and the breakdown tables collapse into one
  `None` bucket) rather than failing loudly.
- **Experiment names are `<ENV>-intelligence-fs-<mode>-<hex>`**, e.g.
  `UAT-intelligence-fs-instant-d01438a5`. LangSmith appends the hex suffix; the
  rest is `config.prefix(ai_mode)`, which delegates to
  `env_config.experiment_prefix(EXPERIMENT_SUITE, ai_mode)`. The `$ENV` label
  matters because the three deployments hold different data — see the roster
  differences under "Known state" — and an experiment named by dataset alone
  can't be traced back to one. An unset or unrecognized `$ENV` drops that
  segment rather than failing the run, giving `intelligence-fs-<mode>-<hex>`:
  a missing label is visible in the dashboard, a wrong one isn't. The mode
  segment is never dropped — two experiments over one dataset are told apart
  by nothing else. Nothing cross-checks `$ENV` against
  `LAMEH_ORCHESTRATOR_URL`, so keep them in step by hand.
  Experiments run before 2026-08-16 have no mode segment and were all expert;
  those before 2026-08-13 are named `materials-sector-<hex>`.
- Renaming an evaluator key starts a *new* metric in LangSmith; past
  experiments keep the old one. Rename deliberately.
- Feedback comments are built by `comment_format.py`, not by the evaluators,
  and are plain text rather than JSON — LangSmith renders a comment as a
  string, so a `json.dumps` blob showed up escaped and unreadable. The shape
  is a headline line plus `- ` detail lines; `build_report._detail_lines`
  relies on that to nest them under the score. Nothing parses comments back,
  so they're free to change. Lists are capped at `MAX_COMMENT_ITEMS`.
- Six evaluators need the same parsed facts; parsing and judge verdicts are
  cached per run id in `run_eval`. Don't call `extract_all` directly from an
  evaluator — go through `_facts_and_tags`.
- Console output is Arabic-heavy. Reconfigure stdout to UTF-8 in any script
  you add; Windows defaults to cp1252 and will crash on a company name.
