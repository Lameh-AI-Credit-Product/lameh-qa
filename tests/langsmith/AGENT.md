# Lameh Intelligence — LangSmith Eval Suite

Evaluates the Intelligence agent (the orchestrator's `/v0/chat` endpoint) as a
LangSmith experiment, and writes a production-readiness report from the result.

**The per-module docstrings are the primary documentation and are kept
current — read them before changing a module.** They carry the *why*:
`llm_judge.py` explains why there are four judge calls instead of one,
`ground_truth.py` why lookups are batched, `correctness.py` why unresolvable
facts are skipped rather than failed. This file covers only what no single
module can: the shape of the whole thing, and the state of the world.

## Running it

```
poetry run poe langsmith-build-dataset     # sync prompt_set.json -> LangSmith dataset
poetry run poe langsmith-run               # run the experiment + build the report
poetry run poe langsmith-report --experiment <name>   # rebuild a report only
```

A full run is ~9 minutes: four prompts in parallel at 7–10 min each, plus
under a minute of evaluator overhead. Useful flags on `langsmith-run`:

- `--example-id materials-q1-cash-quality` — one prompt only, for a fast loop
- `--max-concurrency N` — default 4 (= the dataset size); use 1 to serialize
- `--agent-timeout SECONDS` — default 600; a prompt past it is cut off, scored
  on the text that arrived, and fails `answer_coverage` as truncated
- `--skip-report`

Reports land in `results/langsmith/<experiment>.md`, which is gitignored.

## Pipeline

```
prompt_set.json ──build_dataset──> LangSmith dataset ("materials-sector-v1")
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

| File | Role |
|---|---|
| `run_eval.py` | entrypoint; the 8 evaluator functions; per-run parse + judge caches |
| `agent_client.py` | the system under test — SSE client, deadline handling |
| `config.py` | evaluator keys, thresholds, env/credentials |
| `comment_format.py` | renders each evaluator's findings as the feedback comment |
| `judge_client.py` | Bedrock transport only (swappable/mockable) |
| `evaluators/extraction.py` | parses `<calc>`/`<number>` into facts *and* raw tags |
| `evaluators/tag_integrity.py` | grades the markup itself |
| `evaluators/correctness.py` | value comparison vs ground truth; company coverage |
| `evaluators/ground_truth.py` | batched `chart-data/batch` client + sector roster |
| `evaluators/llm_judge.py` | the four rubrics |
| `evaluators/helpfulness.py` | structural coverage (no judge — it moved out) |
| `evaluators/metrics.py` | `compare`, `tolerance_match`, `mape` |
| `report/build_report.py` | threshold gates, markdown output |

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

Three things about that table are easy to get wrong:

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
Note it uses a *different* organization-id from the agent conversations.

- It is **live**, not a fixture. A restatement in the DB moves the score
  without the agent changing. A drop isn't automatically a regression.
- Lookups are batched via `GroundTruthClient.prefetch`. Before this existed a
  sector prompt made ~693 serial requests and the run took 37 minutes. If you
  add a comparison path, prefetch it — don't reintroduce per-cell `get()`.
- Percent ratios come back as fractions (`0.0535`) while tags display
  `"5.35%"`.
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

- **`dataset/sector_companies.json` is stale.** It holds 68 Materials
  companies; the live roster has 69. `no_fabricated_companies` therefore
  reports a false FAIL on q2 and q3 for `شركة هضاب الخليج التجارية`. Re-derive
  from a fresh batch call.
- **q2's numeric accuracy is `n/a`.** 693 facts extracted, none resolvable —
  cost-of-revenue component names aren't attributes in `chart-data`. Its 1402
  tags are consequently unverified by any value check, so its
  `tag_completeness: 1.00` says nothing about correctness. Needs a different
  ground-truth path.
- **Real agent defects, reproduced across runs:** SABIC FY2025 Net Profit
  reported ~71x the DB value; Saudi Paper FY2025 Net Profit ~45% off; q1/q4
  ad-hoc `<calc>` tags omit `rid`/`id`/`ops` fields (tag completeness 0.59–0.74);
  q2 omits all 10 cement companies from a sector-wide answer.
- **Flake:** q4 has returned a completely empty answer on one run and a full
  one the next.

## Conventions

- Dataset rows are `prompt_set.json`; metadata keys (`scope`, `companies`,
  `metrics_expected`, `fiscal_years`, `sector`) drive what each evaluator
  expects. A `scope: "sector"` row carries `companies: []` and falls back to
  the roster — see `_expected_companies`.
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
