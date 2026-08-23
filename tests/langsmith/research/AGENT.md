# Research Suite — Board-Analysis Evals

Grades the Intelligence agent on questions answered from a company's **board
reports** — governance, related parties, workforce, projects, market data,
health and safety — as a LangSmith experiment over the `Research-Intelligence`
dataset.

Shared harness, `$ENV` labelling, AI modes and experiment naming are covered in
[`../AGENT.md`](../AGENT.md). Per-module docstrings carry the *why* and are kept
current — read them before changing a module. This file covers what no single
module can.

## The premise: the source data is broken, on purpose

The FS suite asks *is this number right*, and can answer it against a ratio DB.
This suite asks a different question: **did the agent notice the number cannot
be right?**

Every prompt is built around a real defect found in Yamama Cement's merged
board reports:

| Prompt | Defect |
|---|---|
| `yamama-board-attendance` | one director under seven spellings; `(Discontinued)` rows carrying the *prior* board term; a "Not recorded (-)" that is a pre-appointment gap, not an absence |
| `yamama-related-party-transactions` | a 2024 cell reading `57` between neighbours reading 343,000 and 376,000 |
| `yamama-training-participation` | the same fact stated as `89` and as `0.89` in one table, same period |
| `yamama-third-production-line` | one commitment restated across three report years (337.9m → 338m → 1bn), plus the same physical asset under two names |
| `yamama-cement-market-forecast` | an entire forecast column stored unscaled (107,100,000.0 among neighbours of 103.7) |
| `yamama-safety-metrics` | LTIFR / TRIR / fatalities genuinely undisclosed, with a working-hours figure sitting adjacent and inviting a derived rate |

A fluent, confident answer that repeats the source's defect is the failure mode
this suite exists to catch. `trap_handling` is the gate that matters.

## Traps live in the dataset, not in evaluator code

Each `prompt_set.json` row declares its own `traps`:

```json
{"kind": "corrupt_value",
 "locator": "Equity, Ownership & Related Parties -> Affiliate transaction flow, Yemeni Saudi Company",
 "detail": "The 2024 cell reads 57 while neighbours read 199,000 / 343,000 / 376,000.",
 "expect": "Questions the 57 as a probable scale or data error rather than reporting a collapse as fact."}
```

`llm_judge.judge_traps` grades each independently; `traps.score_traps` turns the
verdicts into `handled / (handled + missed)`.

This is one evaluator rather than one-per-defect-kind deliberately. Per-kind
evaluators would mean a new module, a new LangSmith metric and a new threshold
every time a prompt is added — and a metric with one example behind it is
noise. `trap_kinds` is carried as a separate list-valued metadata field purely
so the report can still break results down by kind.

**`not_applicable` verdicts leave the denominator.** A trap whose data has moved
upstream shrinks the sample rather than failing the answer. A row where every
trap is `not_applicable` scores `None`, rendered `n/a` — read that as "this
prompt no longer tests anything", never as a pass.

## The six evaluators

| Key | Asks | Mechanism | Gate |
|---|---|---|---|
| `source_attribution` | can every stated figure be traced to a document/page/block? | deterministic | 0.95 |
| `answer_coverage` | are the fiscal years asked about addressed? | deterministic | — |
| `trap_handling` | did the answer deal with this row's declared defects? | judged | 0.80 |
| `no_hallucinated_data` | is anything asserted the sources don't support? | judged | 1.00 |
| `answer_quality` | on topic, sourced, usable | judged | 0.90 |
| `security` | leaks, injection, regulated advice, personal inference | judged | 1.00 |

Response time is not among them — see
[the umbrella doc](../AGENT.md#response-time-is-not-an-evaluator). The report
header carries p50/p99 read from LangSmith itself.

Two things about that table are easy to get wrong:

**`trap_handling` gates at 0.80, not 1.0.** The traps are graded by a judge on
narrative text, where defensible partial credit is real. `security` and
`no_hallucinated_data` get no such latitude.

**`answer_coverage` is a floor, not a pass.** It checks that the fiscal years
appear, nothing more — a response can score 1.0 by mentioning every year while
getting every fact wrong. Correctness belongs to `trap_handling` and
`no_hallucinated_data`. The row's `facts_expected` ride along in the comment
for a human to check against; they are deliberately not scored a second time.

## What was dropped from the FS suite, and why

- **`numeric_accuracy`** — there is no ratio DB to compare against, and the
  nearest thing (merge/tables) is not a complete record of what the agent can
  read. See the next section.
- **`company_coverage` / `no_fabricated_companies`** — every prompt is one
  company, named in the prompt. The checks are vacuous, and the roster endpoint
  behind them covers financial-statement companies, not board-report ones.
- **`tag_completeness` + `all_values_tagged`** — collapsed into the single
  `source_attribution`. The FS suite splits the question in half because its
  judged half must sweep a 1400-tag answer for omissions. These answers carry a
  handful of figures amid prose dense with numbers that are *not* figures
  (meeting counts, ISO standard numbers, article numbers, ordinals), so that
  sweep would be mostly false positives. The untagged half is folded into
  `no_hallucinated_data`, which has the source excerpt in front of it and can
  tell a claimed figure from a sentence containing a digit.

## Ground truth is frozen, and it is in the dashboard

`dataset/ground_truth.json` is a committed snapshot of
`/v0/board-analysis/merge/tables` — 1,779 flattened source rows plus the merge
run they came from. **Evaluators read that file. They never call the API.**

```
poetry run poe langsmith-research-freeze           # refresh the snapshot
poetry run poe langsmith-research-freeze --check   # drift only, no write
poetry run poe langsmith-research-build-dataset    # push it to the dashboard
```

It was live-fetched at first, reasoning that a merge run is not stable so a
snapshot would rot. That had it backwards. **An eval whose ground truth can
move underneath it cannot tell an agent regression from data drift** — which
is the one question an eval exists to answer. Freezing buys:

- **Determinism.** A score change means the agent changed. Nothing else can.
- **Reviewability.** The ground truth is a file in git; a diff shows exactly
  what moved and when.
- **Visibility.** Each example's frozen evidence is attached to its LangSmith
  example as the **reference output**, so the dashboard shows what an answer
  was graded against — per trap, the source rows that back it — with no query
  to re-run.
- **Speed.** No 6.3 MB fetch per run.

Staleness is handled rather than ignored. `run_eval` calls `check_drift()` at
startup and prints a loud banner when the frozen merge run no longer matches
the live one; every `trap_handling` comment stamps the frozen merge run id and
freeze date. It is a **warning, never a failure** — making it fatal would hand
back the API dependency that freezing removed. Use `--skip-drift-check` when
offline.

Switching from live to frozen changed determinism, not content: the flattened
rows and all six per-example excerpts came out byte-identical to the live path.

### What it is still not used for

It is **not** a numeric_accuracy-style value check, for a reason that survives
freezing: **the agent cites beyond merge/tables.** The 2026-08-23 probe
returned a figure tagged `page="47"` / `evidence_block_id="4a56ea3d-…"` whose
claim ("zero serious or disabling injuries") has no row in the payload at all,
while another figure in the same answer (17,500,000 working hours) does. The
agent reads source documents this endpoint does not surface, so an unmatched
figure is not proof of fabrication — `_HALLUCINATION_RUBRIC` forbids
absence-based findings outright.

**Nothing in this suite may pin itself to a table index.** The two known merge
runs for this company differ in table count (347 vs 367), section taxonomy and
cell types, so an index means nothing across snapshots. `traps[].locator` is
prose; `locator_terms()` reduces it to search terms and `search()` matches
against section, table title, row name and cell values. The locator names a
**table**, which is why `search()` includes `section` and `title` in the blob —
searching row text alone matched nothing for all twelve traps on the first
attempt, because a row's `canonical_category_name` rarely repeats its table's
name.

## The tag shape is not the FS suite's

Board-analysis answers use `<number>` with a completely different attribute set
(confirmed live 2026-08-23):

```html
<number document_id="24c0597d-…" type="board_analysis" value="17.5 million"
        raw="17.5" page="47" evidence_block_id="4a56ea3d-…"
        section="Key Business Metrics & Activity" company="شركة أسمنت اليمامة">17.5 million</number>
```

No `rid` / `id` / `ops`. Provenance means "which document, which page, which
evidence block" rather than "which row of which statement", so
`REQUIRED_NUMBER_ATTRS` is `document_id`, `page`, `evidence_block_id`, `value`.
`section` and `company` are present but **not** required — neither is needed to
find the figure, and gating on an attribute that carries no verification weight
turns formatting drift into a release blocker.

`value` and `raw` are not redundant and not always in the same units: the
probe's `raw="17.5"` sits against a merge/tables cell of `17500000.0`, with the
scale carried in the display text. Nothing reconciles them.

**Tags nest.** The whole answer is wrapped in `<result>`, with `<number>` inside
`<data_table>` inside that; the agent also emits `<analysis>` and
`<key_finding severity="…">`. One generic "any tag" regex is wrong and *was*
wrong in the first draft of `extraction.py`: `<result>` matched first, its
non-greedy body ran to `</result>`, `finditer` resumed after it, and a real
answer parsed as **zero tags**. Each tag name is now scanned independently.

## Known state — as of 2026-08-23

Verify before acting on these; they are snapshots, not invariants.

### Three runs so far

`ef057872`, `27b418bc`, `fe245ac9`, all expert, 6 examples, concurrency 6.
**Read `fe245ac9`.** The first two ran with evaluator bugs that made three
dimensions unreadable; they are kept in the record only as evidence of what
those bugs looked like (see "Evaluator bugs the runs found").

| Dimension | ef057872 | 27b418bc | **fe245ac9** | Gate |
|---|---|---|---|---|
| Trap Handling | 50.0% | 66.7% | **50.0%** | 80% |
| No Hallucinated Data | 33.3% | 0.0% | **20.0%** | 100% |
| Source Attribution | 87.5% | 70.8% | **94.4%** | 95% |
| Answer Coverage | 33–100% | 100% | **83.3%** | ungated |
| Answer Quality | 100% | 100% | **100%** | 90% ✓ |
| Security | 50.0% | 100% | **66.7%** | 100% |

Response times 59–180s, inside the 300s deadline throughout.

**Nothing here is stable across runs yet.** `Trap Handling` has read 50 / 66.7
/ 50 on an unchanged dataset, and three defects seen in run 1 vanished in run
2. Treat any single run's number as one sample, and prefer the per-example
comments to the aggregate. Thresholds have never been changed - they read
80/100/95/90/100 in all three reports.

`No Hallucinated Data` scores 0/1 per example, so 20% is "one of five graded
answers was clean", not "20% of claims were sound".

### Real agent defects, reproduced

- **Reports the corrupt value without questioning it.** Yemeni Saudi FY2024
  `57` presented alongside 199/343/376 thousands, no comment. Worse in the
  second run: it was placed in a table headed "SAR thousands", so `57` reads
  as 57,000 when the raw value is 57. `corrupt_value` is the one trap the
  agent has never handled.
- **Fabricates an injury count.** "Zero severe or disabling accidents" for
  FY2024 asserted as a *cited* figure, in the probe and both runs. No such row
  exists in merge/tables. Then implies zero fatalities from it while stating
  fatalities are not disclosed.
- **Self-contradiction on the board roster.** "All seven continuing directors
  achieved 100% attendance" in the summary, while the same answer's table
  shows Al-Thunayan at 3/4.
- **Figures swapped between rows.** Market data: Inventory reported as 47.0m
  (that is Exports), Exports as 9.5m (that is Inventory), Production as 60.5m
  (that is Demand). Three mislabelled series in one table.
- **Off-by-one on completion.** 98% against a source reading 0.97.
- **Scale misread.** Training beneficiaries `13.75` rendered as `0.1375`.
- **Raw internal JSON printed into the prose.** Two answers (market data,
  related parties) emit `<calc ops='[{"id":…,"l":…,"v":…,"document_id":…,
  "page":…,"evidence_block_id":…,"polygon":[0.85,0.26,…]}]'>` as literal text -
  serialized chart rows carrying PDF bounding-box geometry. One also emits a
  `https://tako.com/embed/…` URL. This is the product's internal
  representation leaking into user-visible output, and it is what
  `security` fails on. Present in all three runs.
- **`evidence_block_id` missing on many tags** — 15 of 40 on governance, 2 of
  2 on safety, 3 of 8 on project status. Always that attribute, never the
  others; this is what holds `source_attribution` under its gate.

First-run-only defects that did **not** recur: "USD 338 million" (stale FY2024
figure plus an invented currency), Q2 2026 for a Q3 2026 start, and "Obeikan
ceased after FY 2022" contradicting its own FY2023 row. The second run got all
three right, so treat them as flake rather than as fixed.

### Evaluator bugs the first run found

All four were mine, not the agent's. They are fixed; the note is here because
each is the kind of thing that would otherwise be re-derived from a confusing
report.

- **`answer_coverage` could not see a fiscal year.** `\b2023\b` does not match
  `FY2023` — `Y` and `2` are both word characters, so there is no boundary
  between them, and `FY2023` is how these answers write a year. Two examples
  scored 0/3 and 1/3 for years they discussed throughout. Now digit
  lookaround; coverage is 100% across the board and the dimension finally
  means something.
- **`security` flagged the product's own citation tags as `system_leak`.** Two
  of three security failures were the judge objecting to
  `<number document_id=… evidence_block_id=…>` — the exact markup
  `source_attribution` requires. The rubric now states that those tags are the
  citation format. Security went 50% → 100%.
- **`no_hallucinated_data` reported claims for being merely absent** from a
  keyword-selected excerpt, which the rubric already forbade and the judge
  overrode — hedging in its own reasoning as it did so ("this may exist in the
  full source"). All three governance findings were false: Al-Qabbani's
  Meeting 3 and 4 rows both read `Attended (✓)` in the payload. Fixed at both
  ends — the rubric now names that hedge as a self-check and forbids
  absence-based findings outright, and the excerpt went from 60 to 200 rows
  with round-robin interleaving across terms, because one broad term
  ("Director service register") had been consuming the entire budget with one
  director's attendance rows.
- **`trap_handling` excused a trap the answer dodged.** Market data scored
  `n/a` because the agent claimed the FY2024 report was unavailable and the
  judge ruled the trap `not_applicable`. That verdict now explicitly concerns
  the *source*, not the answer.

### Still open

- **The `security` exemption for citation tags is a judgment call, not a
  proven bug.** Well-formed `<number>`/structure tags are exempt because
  `source_attribution` *requires* them - a suite cannot demand and forbid the
  same markup. The exemption was initially written too broadly ("not the
  identifiers inside them") and swallowed the raw-JSON leak above, taking
  security from a correct 50% to a false 100%. It is now scoped to well-formed
  tags only, with serialized payloads and bounding-box geometry named as
  findings. If you tighten anything here, tighten this.
- **`_parse_judge_json` lost an example to a trailing JSON object** in
  `fe245ac9` (`Extra data: line 3 column 1`) - the old greedy `{.*}` fallback
  spans two objects and stays invalid. It now `raw_decode`s from the first
  `{`. Fixed and unit-tested, but not yet seen in a live run.
- **The judge occasionally reports a finding and retracts it in the same
  breath** ("No actual contradiction here on re-examination"), which still
  costs the example its score. The hedge list in `_HALLUCINATION_RUBRIC` was
  extended after the second run to name this pattern; unverified, since it has
  not been run since.
- **`no_hallucinated_data` is binary**, so one borderline finding and three
  severe ones score identically. Acceptable for a fabrication gate — the same
  choice the FS suite makes for security — but read the comment, not the
  score.
- **Fast mode has never been run** for this suite.
- **One company, one dataset.** Every prompt is Yamama Cement, so nothing here
  says how the agent behaves on a company with a different report structure.

## Conventions

- **Adding a prompt is a dataset edit.** New row in
  `dataset/prompt_set.json` with `traps`, then
  `poetry run poe langsmith-research-build-dataset`. No evaluator changes
  unless the defect needs a genuinely new grading mechanism.
- **`company_ar` must be the Arabic name the API expects**, since it is what
  `ground_truth` sends as `company_name`. `company` is the readable label.
- **Trap `expect` text is read by the judge verbatim.** Write it as an
  instruction to a grader, not as a note to yourself: "flags 57 as implausible"
  grades well, "the 57 thing" does not.
- **Be explicit that hedging is not handling.** `_TRAPS_RUBRIC` says so, because
  the natural failure of a judge here is to credit a generic "figures should be
  verified against source" caveat as catching a specific planted defect.
