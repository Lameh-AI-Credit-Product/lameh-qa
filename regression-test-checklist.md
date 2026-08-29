# Regression Test Checklist

Lightweight checklist for quick regression passes (e.g. Dev → UAT merges), derived from the [QA Scope Map](QA-SCOPE.md). Check element + sub-element, not full known-risk-area depth — use the scope map for the "why" behind each item.

---

## Data Sync (FS upload → downstream)

*(Cross-module — one pass, verified in three places. Run early: later
sections read better against freshly ingested data.)*
- [ ] Upload a new financial statement file to a company
- [ ] Processing completes (wait for the finish signal, not the upload
      returning — see the merge-trigger race in the scope map)
- [ ] New values appear in **View Analysis**
- [ ] New values appear in **Sector Analysis** (ratios recalculated from them)
- [ ] New values appear in the **exported Excel**

## View Analysis

- [ ] **Manual value update**
  - [ ] Normal value (replace existing value)
  - [ ] Null value, no file uploaded
  - [ ] Null value, file uploaded
  - [ ] Validation value (sub-value of a parent) — parent reconciles after update
  - [ ] Update propagates to Sector Analysis, Intelligence, Excel export
  - [ ] Private org account cannot update a public-org company's values
  - [ ] Public-org update is visible to private org accounts
- [ ] New-upload propagation — see [Data Sync](#data-sync-fs-upload--downstream)
- [ ] **Export to Excel**
  - [ ] Export works across company list (not just a sample)
  - [ ] No company fails, times out, or produces empty/partial workbook
- [ ] **Pointer (View Analysis instance)**
  - [ ] Source location shown matches displayed value
  - [ ] Coverage — check companies for Pointer presence (not just "works when present")

## Research

- [ ] **Import**
  - [ ] Bulk import
  - [ ] File-by-file import
- [ ] **Merge**
  - [ ] Auto-triggers after 2nd board report processed (not just uploaded/clicked)
  - [ ] Auto-updates on 3+ files
  - [ ] Field completeness post-merge (e.g. Annual Capacity, Daily Capacity, sector-specific fees)
- [ ] **Intelligence use of Research tools**
  - [ ] Agent correctly pulls merged board report data into Research-page prompts

## Sector Analysis

- [ ] **Ratios** — pre-calculated values match recomputation (script: `poetry run poe sector-analysis-audit --web-only`)
- [ ] **Statement data** — displayed line items match FS source and match View Analysis for same company
- [ ] **Macro sector aggregates** — avg / min / max / sum
  - [ ] Computed over correct company/period set
  - [ ] Missing-value handling (skipped vs counted as 0)
- [ ] **Custom formulas** — evaluates correctly, persists, recalculates after underlying value changes
- [ ] **AI grouping / AI variables** — correct grouping, correct value picked
- [ ] **Market data** — present (check per environment: Dev/UAT/Core), valuation figures current
- [ ] **Commodity data** — present / selectable (no known risk areas defined yet — flag anything found)
- [ ] **Pointer (Sector Analysis instance)** — source match, per-module behavior may differ from View Analysis

## Intelligence

- [ ] **Expert mode** — response correctness on standard prompts
- [ ] **Fast mode**
  - [ ] Company coverage on broad prompts (full sector / many companies)
  - [ ] Response time (baseline: ~90s avg)
- [ ] **Tako graphs** — render correctly
- [ ] **Consistency**
  - [ ] Same prompt run multiple times → same result
  - [ ] Same value consistent across sessions
- [ ] **Grounding** — answers match underlying FS/board report values (no hallucination/misquote)
- [ ] **Pointer (Intelligence instance)** — source match
- [ ] Automated: LangSmith Intelligence suite

## RBAC / Access Control

*(Distributed check — verify per-module rather than as a standalone pass)*
- [ ] Private org cannot update public-org company values (View Analysis)
- [ ] Public-org updates visible to private org accounts
- [ ] Private org data stays scoped to that org (import/delete)
- [ ] Public org data stays globally visible (import/delete)

## Performance

Run once per environment (set `ENV` and `BASE_URL` in `.env` first — the run
folder is named after `ENV`), then compare the three run directories:

```
poetry run poe perf --runs 5
poetry run poe perf-compare results/perf/DEV-<timestamp> results/perf/UAT-<timestamp> results/perf/CORE-<timestamp>
```

The first directory given is the baseline every delta is measured against.
Deltas under 5% are reported as unchanged; check the min–max whiskers before
believing a larger one.

- [ ] Page load time (Dev vs UAT vs Core) — `poe perf --runs 5` per env, then `poe perf-compare`
- [ ] AI response time (Dev vs UAT vs Core)
- [ ] Note environment factors (e.g. unstable connection) that could skew results

## LamehAI Extraction (FS) — *not covered by module-level regression, check separately if time allows*

- [ ] Arabic numeral translation accuracy
- [ ] Year classification
- [ ] Data extraction accuracy vs source PDF
