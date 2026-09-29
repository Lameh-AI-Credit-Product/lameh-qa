# Mohammed's Testing Cases

*From Mohammed. What must be tested and reliable by **6 October 2026**, grouped by engineer. Updated 2026-09-29.*

Words are as defined in [MOHAMMED-VOCAB.md](MOHAMMED-VOCAB.md). If a word here is unclear, look there first.

---

## The rule

- **Everything in this file is tested by Milad.** If he finds a regression, the engineer who owns the module fixes it.
- Each engineer's section lists **what they are working on first**, then what comes **after 6 October**, and **no regression last**.
- **No regression** means: everything that works today still works after every change. It applies to every engineer.
- Every test result names its environment: `DEV`, `UAT` or `CORE`.

**Legend.** `by 6 Oct` must exist and be reliable by the deadline · `after` comes after 6 October · `testing` finished in dev, now being tested · `to confirm` a case I proposed, Mohammed has not confirmed it yet.

---

## Ashraf · Intelligence

**Working on** · by 6 Oct
- **General agent capability**, one item: upload a document and question it; preference, memory that persists across sessions; create presentations; web search; PDF download.
- **Scapula reliable**: run a playbook on one company; the most important data are there, as defined by Mohammed's template. *Cases to confirm*: completes in one to two hours; output covers business model, main clients, main investors; numbers from our data carry a pointer; Intelligence keeps answering while a playbook runs; the same company run twice gives consistent conclusions.

**No regression**
- All existing evals green: `tests/langsmith/fs/` and `tests/langsmith/research/`.
- Fetches FS data.
- Fetches research data: fund documents, board reports, investor presentations, prospectuses.
- Fetches the third layer: commodity and GSTAT, through sector analysis.
- Pointer: every number is clickable and lands on the right source.

---

## Ben Abdullah · Research

**Working on** · by 6 Oct
- **Critical-metrics agent call.** After merge, an agent classifies the merged data critical, medium, not critical, and shows whether the critical metrics connected across documents and where they did not. This is the fix for the known problem under *no regression* below. Critical metrics: number of pharmacies and pharmacies by region, hospitals, number of patients, number of students, number of campuses. Medium is defined later by Mohammed.
- **Fund cost estimation.** Cost per fund upload, number of funds, total time. *Very important*: Mohammed sets the date to upload all funds from this number.
- **Input caching.** Finished, then a bug was found; back in development.

**After 6 October**
- Iterative, file-by-file import. Today only bulk import exists.
- Research reports: GSTAT reports, Ministry of Education reports, a prospectus used as sector data. Owner to confirm.
- Upload all funds, once the estimate is in and Mohammed decides.

**No regression** · company-specific documents: any document for a company that is not a financial statement
- Works today: about 4 board reports plus 1 prospectus per company.
- Same company, same document type, different years: mainly works. Cases: 5 board reports; 10 board reports.
- Same company, different document types: **known problem.** Above 4 board reports, or when investor presentations are included, critical metrics are missing about 30% of the time. Case: 3 board reports, 1 prospectus, 3 investor presentations; the check is that one variable such as *number of pharmacies* lands in one row with all its periods across all three types. The agent call above is the fix; until it lands, this case documents the failure rate.
- Bulk import works.
- Auto-merge triggers when processing of the third document completes, not on upload or click. *(The checklist says second; to be resolved by Ben Abdullah.)*

---

## Amari · FS

**Working on**
- Calculating and optimising the cost of the new workflow, with input caching in the number.
- Add-document · `testing`.
- Input caching, FS side · `testing`.
- Editing · done.

**No regression** · patching mode is finished, so it lives here and is the most critical item in this file
- **Patching mode: a new period changes nothing outside its coverage.** Upload Q2 2026; every period the document does not cover is unchanged, value for value.
- Patching mode: validation runs only on the new period.
- Patching mode: the new period connects to the existing template, for example trade receivables Q2 2026 to the existing trade receivables row; the template is not regenerated.
- Patching mode: cost of the upload is measured and lower than before.
- Existing company plus one new document: Q2 2026.
- Existing company plus several periods at once: Q2, Q3 and 12M.
- New company, multi-year set · *to confirm*: 3 years, each with Q1, Q2, Q3 and 12M, so 12 documents.
- Any case above with an Arabic document beside an English one.
- On every case, three checks: merging connected the variables; most or all variables have validation formulas; imbalances are flagged as accounting errors.

---

## Aziz · GSTAT and full stack

**Working on** · by 6 Oct
- **GSTAT workflow** finishes: the general research workflow reads Excel, GSTAT data lands in sector analysis, Intelligence fetches it.

**Outside the 6 October north star**
- CAGR and growth fixed metrics · sector analysis.
- Pre-done AI scraper, announcements · monitors.

**No regression**
- The data he feeds sector analysis, commodity and valuation, still present per environment · *to confirm*.

---

## Milad · Testing

**Working on**
- Test plans for the new things: 3M and P period handling, textual analysis, cost analysis. *(Wording to confirm.)*
- Tests every *working on* item above as it lands.

**No regression**
- Owns the rule for every engineer: he tests, the engineer fixes.
- Sector analysis: standardized metrics, custom standardized formulas, custom AI variables, pointer on child variables. Existing tooling: `poetry run poe sector-analysis-audit`.
- Data testing: source of truth against sector analysis. No net profit equal to zero, and the like.

---

## Mohammed

**Working on** · by 6 Oct
- Pattern: template and expected output, clients B and S. The infrastructure is Ashraf's and exists.
- Scapula: template and expected output.
- Funds overview: pick a company, see the funds holding it, each categorised. Plus the fund categories.
- Define medium critical.
- Cleanup · in progress.

**Decisions**
- General quick pointer, with Ashraf and Ben Abdullah.
- When to upload all funds, after the estimate.

---

## Not assigned

- **General quick pointer** · Intelligence · not by 6 October · Mohammed will discuss it with Ashraf and Ben Abdullah.

---

## Open questions

1. New-company FS set: 3 years, each with Q1, Q2, Q3 and 12M, so 12 documents? Or a different set?
2. Milad's current plan wording: "3M and P period handling", or something else?
3. Pattern: purely Mohammed's template on the existing infrastructure, or does Ashraf still have a piece by 6 October?
4. Auto-merge trigger: after the second or the third board report?

---

## How this maps to the existing tooling

| Here | In this repo today |
|---|---|
| Amari, no regression | `regression-test-checklist.md` § Data Sync covers propagation only; the four cases and three checks above are new |
| Ashraf, no regression | `tests/langsmith/` FS and research suites; checklist § Intelligence |
| Ben Abdullah, no regression | checklist § Research, import and merge; the cross-type case is new |
| Milad, sector analysis | `tests/sector_analysis_audit.py`, `tests/coverage/`, checklist § Sector Analysis |
| Milad, data testing | new |
