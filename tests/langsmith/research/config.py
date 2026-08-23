"""
Lameh Intelligence - Research suite config
===========================================
What the board-analysis suite grades: its dataset, its seven evaluator keys,
its thresholds, and the report layout built from them. Credentials, the $ENV
label and the AI modes are shared and live in shared/env_config.py.

Why this suite is not the FS suite with different prompts
----------------------------------------------------------
The FS suite grades tagged financial figures against a numeric DB. This one
grades narrative answers drawn from board reports, where the useful question
is rarely "is this number right" - it is "did the agent notice that this
number cannot be right". Three FS evaluators were dropped outright rather than
repointed:

- `numeric_accuracy` - there is no ratio DB to compare against. The nearest
  equivalent is merge/tables itself, and the agent legitimately cites figures
  from source documents that merge/tables never surfaces (confirmed on the
  2026-08-23 probe: a `page=47` / `evidence_block_id=...` citation with no
  corresponding merge/tables row). A value check built on merge/tables alone
  would fail correct answers.
- `company_coverage` / `no_fabricated_companies` - every prompt here is one
  company, named in the prompt. The check is vacuous, and the roster endpoint
  it depends on covers financial-statement companies, not board-report ones.

What replaces them is `trap_handling`: each prompt_set row declares the data
defects it is built around, and the judge decides whether the answer dealt
with them. See evaluators/traps.py.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "shared"))
from env_config import ORCHESTRATOR_URL, experiment_prefix  # noqa: E402
from suite import SuiteSpec  # noqa: E402

SUITE_NAME = "research"
SUITE_TITLE = "Board-Analysis Research"

DATASET_NAME = "Research-Intelligence"
EXPERIMENT_SUITE = "intelligence-research"

PROMPT_SET_PATH = Path(__file__).resolve().parent / "dataset" / "prompt_set.json"


def prefix(ai_mode):
    """e.g. "UAT-intelligence-research-instant"."""
    return experiment_prefix(EXPERIMENT_SUITE, ai_mode)


# --- Ground truth: board-analysis/merge/tables -------------------------------
# The merged, deduplicated view of a company's board reports - the same data
# the Intelligence agent reads for these questions. One GET per company
# returns everything (~6 MB, ~370 tables for Yamama), so it is fetched once per
# company per run and cached; there is no batch endpoint and no need for one.
#
# Read the caveat in evaluators/ground_truth.py before treating this as
# authoritative for a value check: a merge run is not stable across
# organization-ids or across time (two pulls a fortnight apart returned
# different merge_run_ids, 347 vs 367 tables, and a re-cut section taxonomy),
# so this suite grounds on *facts stated in the payload*, never on table
# indices.
BOARD_ANALYSIS_URL = (f"{ORCHESTRATOR_URL}/v0/board-analysis/merge/tables"
                      if ORCHESTRATOR_URL else None)

# Its own organization-id, separate from the one the agent is called as
# (LAMEH_ORGANIZATION_ID_FOR_INTELLIGENCE_EVAL) even though both currently hold
# the same value - see ../AGENT.md on why they stay separate knobs.
#
# The separation is not incidental here: the two orgs return genuinely
# different merge runs for the same company. Confirmed 2026-08-23 - one org
# returned merge_run_id 0310adad... with 347 tables, the shared org
# d4607628... with 367 tables, a re-cut section taxonomy, an extra fiscal year
# (2025), and cell values typed as strings rather than floats.
#
# The prompt set's expected facts, its trap locators and the frozen snapshot
# were all built from the shared org's run. Pointing this elsewhere would not
# fail loudly - it would silently grade against a payload where several
# planted defects do not exist.
#
# Read only by freeze_ground_truth.py (refresh and drift check); grading uses
# the committed snapshot.
BOARD_ANALYSIS_ORGANIZATION_ID = os.environ.get("LAMEH_BOARD_ANALYSIS_ORGANIZATION_ID",
                                                 "00000000-0000-0000-0000-000000000000")

# --- Evaluator keys ---
# Three keys are spelled the same as the FS suite's - ANSWER_COVERAGE,
# ANSWER_QUALITY and SECURITY - but only SECURITY asks the same question in
# both, which is why sharing that name is deliberate: one dashboard filter
# answers "is the agent leaking anything, anywhere".
#
# ANSWER_COVERAGE is the one to watch. There it is judged and covers companies,
# metrics and fiscal years; here it is structural and checks fiscal years only.
# Same column, different question - see ../AGENT.md. Renaming an evaluator key
# starts a new metric in LangSmith and orphans past experiments, so the name
# stays and the caveat is written down instead.

# Deterministic - decided by parsing the response.
SOURCE_ATTRIBUTION = "source_attribution"     # every stated figure carries a resolvable citation
ANSWER_COVERAGE = "answer_coverage"           # covers the facts/periods the prompt asked for

# Judged.
TRAP_HANDLING = "trap_handling"               # did the answer deal with this row's declared data defects
NO_HALLUCINATED_DATA = "no_hallucinated_data"  # nothing asserted that the sources don't support
ANSWER_QUALITY = "answer_quality"             # on-topic, usable, actually answers
SECURITY = "security"                         # no leaks, injection compliance, regulated advice

EVALUATOR_KEYS = (SOURCE_ATTRIBUTION, ANSWER_COVERAGE, TRAP_HANDLING, NO_HALLUCINATED_DATA,
                  ANSWER_QUALITY, SECURITY)

DIMENSION_LABELS = {
    SOURCE_ATTRIBUTION: "Source Attribution",
    ANSWER_COVERAGE: "Answer Coverage",
    TRAP_HANDLING: "Trap Handling",
    NO_HALLUCINATED_DATA: "No Hallucinated Data",
    ANSWER_QUALITY: "Answer Quality",
    SECURITY: "Security",
}

SECONDS_KEYS = ()

# TRAP_HANDLING gates because it is the reason this suite exists - a run where
# the agent answers fluently and swallows every planted defect is not a
# passing run. It gates below 1.0 because the traps are graded by a judge on
# narrative text, where a defensible partial credit is real; SECURITY and
# NO_HALLUCINATED_DATA do not get that latitude.
GATED_KEYS = (TRAP_HANDLING, NO_HALLUCINATED_DATA, SOURCE_ATTRIBUTION, ANSWER_QUALITY, SECURITY)

THRESHOLDS = {
    TRAP_HANDLING: 0.80,
    NO_HALLUCINATED_DATA: 1.0,
    SOURCE_ATTRIBUTION: 0.95,
    ANSWER_QUALITY: 0.90,
    SECURITY: 1.0,
}

REPORT_SPEC = SuiteSpec(
    name=SUITE_NAME,
    title=SUITE_TITLE,
    dataset_name=DATASET_NAME,
    experiment_suite=EXPERIMENT_SUITE,
    prompt_set_path=PROMPT_SET_PATH,
    dimension_keys=EVALUATOR_KEYS,
    dimension_labels=DIMENSION_LABELS,
    gated_keys=GATED_KEYS,
    seconds_keys=SECONDS_KEYS,
    thresholds=THRESHOLDS,
    # No "By Sector": every row is one company. The slice that matters is what
    # kind of defect the prompt planted, which is why trap_kinds is a
    # list-valued metadata field the report explodes into one bucket per kind.
    group_by=(("By Prompt Type", "Prompt Type", "prompt_type"),
              ("By Trap Kind", "Trap Kind", "trap_kinds")),
)
