# Mohammed's Vocab

*From Mohammed. These are the words we use, with one meaning each. Updated 2026-09-29.*

When a word here conflicts with wording elsewhere in this repo, in the roadmap, in the task list or in Linear, **this file wins**. The roadmap, the task list, the testing cases and the Linear tickets are all written in these words, so that when I say a word every engineer hears the same thing.

Companion: [MOHAMMED-TESTING-CASES.md](MOHAMMED-TESTING-CASES.md).

---

## 1. Primitives

| Word | Meaning | Example |
|---|---|---|
| **Extraction** | A raw document becomes raw JSON. Machine-readable, nothing more. PDF, HTML or Excel in; JSON out. | A Jarir Marketing Co. FS PDF becomes JSON with the tables exactly as found. |
| **Processing** | Raw JSON becomes **our schema**: a table title, a unit or multiplier, and columns that are periods. | The revenue table becomes `title: Revenue`, `unit: thousands SAR`, columns `2024-12-31_12M`, `2025-12-31_12M`. |
| **Merging** | The same variable connected across documents and across periods, **within one company**. | Jarir revenue from the FY2024 FS and the FY2025 FS become one row with two periods. |
| **Validation** | A formula exists that checks a variable against others. | Gross profit equals revenue minus COGS. Balance sheet balances. |
| **To-be-fixed** (accounting errors) | Validation found an imbalance. It is flagged for the analyst or data annotator to fix. | Balance sheet is off by 1,200 SAR; the row is flagged. |
| **Template** | The company's consolidated schema JSON after merging: groups, sub-groups, variable names, values per period. The output of the FS data workflow. | One JSON per company. |
| **Patching mode** | FS merge of a **new document only**. It connects the new periods to the existing template and validates only those periods. It never regenerates the whole template. | Upload Q2 2026: trade receivables Q2 2026 connects to the existing trade receivables row. FY2023, FY2024, FY2025 are untouched, byte for byte. |
| **Pointer** | Click a number, land on the exact place in the source document it came from. | Click net profit in View Analysis, the PDF opens on the income statement with the cell highlighted. |
| **General quick pointer** | Pointer for a document we never analysed: hand it a document, it cleans the data very quickly and links every data point to a bounding box so Intelligence can use it. Fast, not deep. **Not built. Not assigned.** | |
| **General pointer** | Pointer for document types beyond PDF: Excel, Word. Follows the general quick pointer. | |

**Before and after, in one line.** Extraction is *reading*. Processing is *shaping*. Merging is *connecting*. Validation is *checking*. Patching is *adding without rebuilding*.

---

## 2. Periods

The period model is defined in the ontology and we do not redefine it here:
`ontology_lameh/ontology/conventions/conventions.md`, section 13.

- Format: `YYYY-MM-DD_suffix`, where the date is the period **end** date.
- **`P`** is a point-in-time snapshot: balance sheet items such as trade receivables, total assets.
- **`3M` `6M` `9M` `12M`** are covered durations: flow items such as revenue, COGS, cash flows.
- We also use **`18M` `24M` `36M`** in the pipeline for multi-year flows. Revenue 2023 to 2025 is `2025-12-31_24M`. These are not yet in the ontology table; to be added.

---

## 3. Data workflows

Data workflows are engines. The client never sees them.

### FS data workflow
Financial statements only. Steps: extraction, processing, merging, validation, to-be-fixed, template. Runs in **patching mode** for new documents. Feeds Sector analysis, View Analysis and Intelligence.

### Research data workflow
Everything that is **not** a financial statement.

- **Company-specific documents**: any document for a company that is not an FS. Board reports, investor presentations, prospectuses.
- **Funds**: fund documents. Same workflow, more focused. The ontology holds how funds relate to companies.
- **Critical-metrics agent call**: after merge, an agent classifies the merged data **critical / medium / not critical** and shows whether the critical metrics connected across documents, and where they did not. See section 6.
- **Research reports** *(after 6 October)*: documents not tied to one company. GSTAT reports, Ministry of Education reports, and a prospectus used as **sector** data. A Nahdi Medical Co. prospectus describes every pharmacy chain, so analysts use it to read the whole market.

GSTAT and SAMA are variations of the research data workflow.

**Merging is always within one company.** The workflow never merges Jarir's documents with another company's. Cross-company comparison is a *question*, answered by Intelligence, or a *table*, shown by Sector analysis on standardized FS metrics. Neither one merges documents.

---

## 4. Modules the client sees

| Module | What it is | Fed by |
|---|---|---|
| **Dashboard** | Companies, which quarters they cover, view analysis, upload. | FS data workflow |
| **View Analysis** | One company's FS data. Manual value updates, accounting-error fixes, Excel export. | FS data workflow |
| **Sector analysis** | Companies side by side on standardized metrics, custom formulas and custom AI variables. Pointer on the leaf variables. | FS data workflow; custom AI variables also read research data |
| **Intelligence** | **The general agent.** Our data with pointer; other data without pointer until the general quick pointer exists. Medium depth, under 10 minutes. Reads FS, research including funds, and the third layer: commodity and GSTAT through sector analysis. | Both workflows |
| **Research chat** | Its own module in the research repo. Behaves 90 to 95 percent like Intelligence, scoped to the company and page the user is on. | Both workflows |
| **Fund intelligence** | Upload any fund document and question it. | Research data workflow, funds |
| **Funds overview** | Pick a company, see the funds holding it, each with a category. Categories are defined by Mohammed. | Research data workflow, funds |

---

## 5. Inside Intelligence

| Word | Meaning | Client |
|---|---|---|
| **General agent capability** | One item: upload a document and question it; **preference**; create presentations; web search; PDF download. Mohammed also calls this **"ChatGPT capability"**; same thing. It includes the workspace working reliably, and a **model selector** for frontier, open-source and flash models (K3, Kimi), not usable until credit covers it, implemented on Sonnet or Terra. | B and S |
| **Preference** | User memory, outside the chat and **global per user**. Less output; things the user likes; things to remember automatically, for example he was analysing the price of wheat and now asks about an F&B company. | |
| **Depth** | The one variable that separates pattern from playbook. Make it a variable, not two products. | |
| **Pattern** | A **medium template**. A fixed question set run through Intelligence in about 10 minutes. Company overview: P/E, valuation, profit margins, growth, main segments and their profit, EBITDA. The infrastructure exists; the template and expected output come from Mohammed. | B |
| **Playbook** | A **long** analysis, one to two hours. Business model, main clients, main investors. This is the pre-done analysis. Runs in Scapula. | S |
| **Scapula** | The name in the front end and the orchestrator for the engine that runs a playbook and keeps asking itself questions. Making it reliable is Ashraf's; the template and expected output are Mohammed's. | S |

**Reliable**, for a playbook or a pattern, means: *the most important data are there*, as defined by Mohammed's template for it.

---

## 6. Variables, in Sector analysis

```
Standardized metrics
├── Extracted variables            found in the statements
│   ├── leaf                       revenue, COGS, general & admin
│   └── calculated                 gross profit = revenue − COGS; net profit
└── Ratios                         one extracted variable over another

Custom standardized formulas       your formula over extracted variables: G&A / revenue
Custom AI variables                any variable by query: rent expenses, executive bonuses
                                   reads FS data and research data
```

**Critical metrics** are the operational metrics of a company and their breakdowns. They must always merge, with higher priority than anything else in a research document.

| Sector | Critical metrics |
|---|---|
| Pharmacies | number of pharmacies, pharmacies by region |
| Healthcare | hospitals, number of patients |
| Education | number of students, number of campuses |

The agent call that classifies them also covers **future statements** and **important data that is not connected**.

**Medium critical** is defined later by Mohammed. **Not critical** is everything else.

---

## 7. Clients and environments

- **Client B**: pattern, funds overview, upload all funds, general agent capability.
- **Client S**: playbook in Scapula, general agent capability.
- **Environments**: `DEV`, `UAT`, `CORE`. Every test result names its environment.
- **Linear labels that are a rule**: `Eval1` is Mohammed's marker for things he will put into evals or wants to keep so he does not miss them; `one` stays as it is. Never retire, rename or remove either.

---

## 8. Wording this file replaces

| Where | Old wording | Use instead |
|---|---|---|
| `QA-SCOPE.md` | "LamehAI extraction (FS)" for the whole pipeline | **FS data workflow**. *Extraction* is only the first step. |
| `QA-SCOPE.md` | "LamehAI extraction FS pipeline" steps 1 to 4 | extraction, processing, merging, validation, template |
| `QA-SCOPE.md`, checklist | "Research: import + merge" | **Research data workflow**, company-specific documents |
| roadmap | "pre-done analysis" | **playbook**, run in **Scapula** |
| roadmap | "on-demand analysis" | **Intelligence**, general agent capability |
| roadmap | "other document types" | **general pointer** |
| roadmap, earlier drafts | "Lameh general agent" | **Intelligence** |
| checklist | auto-merge "after 2nd board report" vs scope "after 3rd" | to be resolved by Ben Abdullah; the doc that wins is this one once he answers |

`QA-SCOPE.md` and `regression-test-checklist.md` still carry the old words. They will be aligned in a follow-up; until then this file is the reference.
