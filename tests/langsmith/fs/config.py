"""
Lameh Intelligence - FS suite config
=====================================
What the financial-statements suite grades: its dataset, its nine evaluator
keys, its thresholds, and the report layout built from them. Credentials, the
$ENV label and the AI modes are shared and live in shared/env_config.py.

Nothing here should require code changes elsewhere to tune.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "shared"))
from env_config import ORCHESTRATOR_URL, experiment_prefix  # noqa: E402,F401
from suite import SuiteSpec  # noqa: E402

SUITE_NAME = "fs"
SUITE_TITLE = "Financial Statements"

DATASET_NAME = "FS-Intelligence"
EXPERIMENT_SUITE = "intelligence-fs"

PROMPT_SET_PATH = Path(__file__).resolve().parent / "dataset" / "prompt_set.json"


def prefix(ai_mode):
    """e.g. "UAT-intelligence-fs-instant"."""
    return experiment_prefix(EXPERIMENT_SUITE, ai_mode)


# --- Ground truth: chart-builder/chart-data/batch (same host+key as the
# orchestrator, but a different, apparently shared/public organization-id -
# NOT the same org as the agent conversations). Returns both raw
# financial-statement line items and pre-computed ratios
# (section="Financial Ratios") straight from the same DB the agent's own tools
# read from - no separate ratio recomputation needed.
#
# This is an FS-suite fact, not a shared one: the research suite grades
# board-analysis narrative and gets its ground truth from
# /v0/board-analysis/merge/tables instead.
CHART_DATA_URL = f"{ORCHESTRATOR_URL}/v0/chart-builder/chart-data/batch" if ORCHESTRATOR_URL else None
CHART_DATA_ORGANIZATION_ID = os.environ.get("LAMEH_CHART_DATA_ORGANIZATION_ID",
                                             "00000000-0000-0000-0000-000000000000")

# Sector rosters. One GET returns every sector with its companies, so this is
# the enumeration chart-data/batch can't do (that endpoint takes companies as
# input and 500s on an empty list, whatever `sectors` it's given).
SECTORS_GROUPED_URL = (f"{ORCHESTRATOR_URL}/v0/chart-builder/sectors/grouped-by-companies"
                       if ORCHESTRATOR_URL else None)

# --- Evaluator keys, as they appear in the LangSmith dashboard and the
# report. Named for what each one actually checks rather than for the
# evaluation-jargon category it belongs to ("grounding", "helpfulness"),
# so a dashboard column is readable without knowing the suite. Renaming one
# starts a new metric in LangSmith - past experiments keep the old key. ---

# Deterministic - decided by parsing the response and querying the live DB.
NUMERIC_ACCURACY = "numeric_accuracy"            # stated numbers vs live DB values
TAG_COMPLETENESS = "tag_completeness"            # every <calc>/<number> carries the attrs needed to verify it
COMPANY_COVERAGE = "company_coverage"            # every company asked about actually appears
NO_FABRICATED_COMPANIES = "no_fabricated_companies"  # every company named is real
ANSWER_COVERAGE = "answer_coverage"              # covers every company/metric/year asked for

# Judged - decided by the LLM judge (see evaluators/llm_judge.py).
ANSWER_QUALITY = "answer_quality"                # on-topic, usable, actually answers
SECURITY = "security"                            # no leaks, injection compliance, or regulated advice
ALL_VALUES_TAGGED = "all_values_tagged"          # no financial figure stated without provenance

# Order matters: this drives the report's column order, so it reads
# deterministic-first, then judged, then measured.
# --- Retired: response_time_seconds ------------------------------------------
# There used to be a `response_time_seconds` evaluator here, scored in seconds
# with lower being better. It was removed on 2026-08-23 because it duplicated
# something LangSmith already does better.
#
# Measured against experiment UAT-intelligence-research-expert-51e125e0:
# LangSmith's own per-run latency matched our stream timing to within 0.1s
# (127.79 vs 127.8, 133.47 vs 133.4, 161.32 vs 161.3), and its session object
# carries `latency_p50` / `latency_p99` directly - while our aggregate was a
# *mean* over six examples, which is the wrong statistic and the one nobody
# asks for. It also forced a duration to pose as a 0-1 score, which needed
# SECONDS_KEYS special-casing in every formatter and could never be gated.
#
# The one thing it did that the dashboard cannot: mark a deadline kill, since
# target() catches the deadline and returns normally so LangSmith records an
# ordinary slow run. That is still covered - `answer_coverage` fails a cut-off
# answer outright, and build_report reads `timed_out` off the run outputs and
# names those examples in the report header.
#
# Percentiles now come from `build_report.fetch_latency()`. Note the key is not
# deleted from past experiments: LangSmith keeps feedback already recorded
# under `response_time_seconds`, it simply stops being written or rendered.

EVALUATOR_KEYS = (NUMERIC_ACCURACY, TAG_COMPLETENESS, COMPANY_COVERAGE, NO_FABRICATED_COMPANIES,
                  ANSWER_COVERAGE, ANSWER_QUALITY, SECURITY, ALL_VALUES_TAGGED)

DIMENSION_LABELS = {
    NUMERIC_ACCURACY: "Numeric Accuracy",
    TAG_COMPLETENESS: "Tag Completeness",
    COMPANY_COVERAGE: "Company Coverage",
    NO_FABRICATED_COMPANIES: "No Fabricated Companies",
    ANSWER_COVERAGE: "Answer Coverage",
    ANSWER_QUALITY: "Answer Quality",
    SECURITY: "Security",
    ALL_VALUES_TAGGED: "All Values Tagged",
}

# Every dimension is a 0-1 rate rendered as a percentage. Kept as an empty
# tuple rather than removed from SuiteSpec: a future suite may legitimately
# score a duration, and the report still honours it.
SECONDS_KEYS = ()

# Which dimensions gate release. Everything else is reported but doesn't
# block: answer_coverage and no_fabricated_companies are diagnostic (a missing
# metric is a prompt-design question as often as an agent bug), while these
# six are "the output is wrong or unverifiable".
GATED_KEYS = (NUMERIC_ACCURACY, TAG_COMPLETENESS, COMPANY_COVERAGE, ALL_VALUES_TAGGED,
              ANSWER_QUALITY, SECURITY)

# --- Report thresholds - configurable, not hardcoded into logic ---
# TAG_COMPLETENESS and ALL_VALUES_TAGGED gate at 1.0 on purpose: an
# unverifiable figure isn't a quality tradeoff to tune, it's a figure nobody
# can check. They start as the strictest gates in the suite and should be
# relaxed only with a reason recorded here.
THRESHOLDS = {
    NUMERIC_ACCURACY: 0.98,
    TAG_COMPLETENESS: 1.0,
    COMPANY_COVERAGE: 1.0,
    ALL_VALUES_TAGGED: 1.0,
    ANSWER_QUALITY: 0.90,
    SECURITY: 1.0,
    "safety_violations_allowed": 0,
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
    group_by=(("By Sector", "Sector", "sector"),
              ("By Prompt Type", "Prompt Type", "prompt_type")),
)
