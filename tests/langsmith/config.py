"""
Lameh Intelligence - LangSmith eval suite config
=================================================
Central place for names/thresholds shared across the dataset builder,
evaluators, and report generator. Nothing here should require code changes
elsewhere to tune.
"""

import os

from dotenv import load_dotenv

load_dotenv()

LANGSMITH_PROJECT = "lameh-intelligence-eval"

DATASET_NAME = "FS-Intelligence"

# --- AI mode (the agent's own answering mode, not a property of the dataset) --
# The agent answers in one of two modes, and both need evaluating. The mode is
# a property of the system under test - like chat_model and reasoning_effort
# beside it in agent_client - not of the questions, so both modes run against
# the *same* dataset as two separate experiments. That is what makes them
# comparable: LangSmith's comparison view is scoped to one dataset, so
# splitting the prompts per mode would throw away the side-by-side diff that
# is the whole reason for running fast at all.
#
# Two experiments rather than one for the same reason the gates exist: an
# experiment's aggregate is per-experiment, so a single mixed run would gate
# on an expert/fast blend and a fast-mode failure could average into "ready".
# The wire values, which are what /v0/chat validates against: send anything
# else and it 422s with "ai_mode must be one of: instant, expert". Note the
# product calls the second one **fast** - "instant" appears nowhere in the UI.
# `fast` is therefore accepted as a CLI alias (see AI_MODE_ALIASES) and
# normalized away immediately, so exactly one spelling reaches the experiment
# names and the run outputs.
AI_MODES = ("expert", "instant")
AI_MODE_ALIASES = {"fast": "instant"}
DEFAULT_AI_MODE = "expert"


def normalize_ai_mode(value):
    """CLI spelling -> wire value. Unknown values pass through unchanged for
    argparse's `choices` to reject with the valid list."""
    return AI_MODE_ALIASES.get(value, value)

# --- Experiment naming -------------------------------------------------------
# LangSmith appends its own 8-hex suffix, so the prefix below produces e.g.
# "DEV-intelligence-fs-expert-d01438a5". Two labels are carried:
#
# $ENV (DEV/UAT/CORE) is the same deployment label the sector-analysis pipeline
# uses, and it matters here for the same reason: the three deployments hold
# different data, so an experiment named by dataset alone gives no way to tell
# later which one it ran against. Unlike the download step, an unset or
# unrecognized $ENV is not fatal - it just drops that part of the prefix. A
# missing label is visible in the dashboard; a wrong one is not.
#
# The ai_mode goes in the name too, and unlike $ENV it is never omitted: two
# experiments over one dataset are only telling apart by it, and the whole
# point of running both is comparing them.
VALID_ENVS = ("DEV", "UAT", "CORE")
ENV = (os.environ.get("ENV") or "").strip().upper()
if ENV not in VALID_ENVS:
    ENV = ""

EXPERIMENT_SUITE = "intelligence-fs"
EXPERIMENT_PREFIX = f"{ENV}-{EXPERIMENT_SUITE}" if ENV else EXPERIMENT_SUITE


def experiment_prefix(ai_mode):
    """e.g. "UAT-intelligence-fs-instant". Experiments run before 2026-08-16
    have no mode segment and were all expert."""
    return f"{EXPERIMENT_PREFIX}-{ai_mode}"

# --- Orchestrator (the Intelligence agent under test) ---
ORCHESTRATOR_URL = os.environ.get("LAMEH_ORCHESTRATOR_URL")
ORCHESTRATOR_ORGANIZATION_ID = os.environ.get("LAMEH_ORGANIZATION_ID")
ORCHESTRATOR_API_KEY = os.environ.get("LAMEH_API_KEY")
ORCHESTRATOR_USER_ID = os.environ.get("LAMEH_USER_ID")

# --- Ground truth: chart-builder/chart-data/batch (same host+key as the
# orchestrator above, but a different, apparently shared/public
# organization-id - NOT the same org as the agent conversations). Returns
# both raw financial-statement line items and pre-computed ratios
# (section="Financial Ratios") straight from the same DB the agent's own
# tools read from - no separate ratio recomputation needed.
CHART_DATA_URL = f"{ORCHESTRATOR_URL}/v0/chart-builder/chart-data/batch" if ORCHESTRATOR_URL else None
CHART_DATA_ORGANIZATION_ID = os.environ.get("LAMEH_CHART_DATA_ORGANIZATION_ID", "00000000-0000-0000-0000-000000000000")

# Sector rosters. One GET returns every sector with its companies, so this is
# the enumeration chart-data/batch can't do (that endpoint takes companies as
# input and 500s on an empty list, whatever `sectors` it's given).
SECTORS_GROUPED_URL = (f"{ORCHESTRATOR_URL}/v0/chart-builder/sectors/grouped-by-companies"
                       if ORCHESTRATOR_URL else None)

# --- LLM judge (Claude via AWS Bedrock) - used by helpfulness.py's
# relevance/usability check. ---
AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY")
AWS_REGION = os.environ.get("AWS_REGION")
AWS_BEDROCK_MODEL_ID = os.environ.get("AWS_BEDROCK_MODEL_ID")

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

# Judged - decided by the LLM judge (one shared call, see llm_judge.py).
ANSWER_QUALITY = "answer_quality"                # on-topic, usable, actually answers
SECURITY = "security"                            # no leaks, injection compliance, or regulated advice
ALL_VALUES_TAGGED = "all_values_tagged"          # no financial figure stated without provenance

# Measured, not judged. Scored in *seconds*, not as a 0-1 rate, and lower is
# better - the only key in the suite for which either is true. It exists as
# feedback rather than being read off the dashboard's own latency column
# because only feedback is comparable: the comparison view diffs scores, and
# a mode comparison wants response time sitting in the same table as the eight
# quality columns. The dashboard latency also counts a deadline kill as an
# ordinary ~900s run, while this reads agent_client's own stream timing and
# says so in the comment. Ungated (see build_report.GATED_KEYS): what counts
# as too slow differs per mode and isn't settled yet.
RESPONSE_TIME = "response_time_seconds"

# Order matters: this drives the report's column order, so it reads
# deterministic-first, then judged, then measured.
EVALUATOR_KEYS = (NUMERIC_ACCURACY, TAG_COMPLETENESS, COMPANY_COVERAGE, NO_FABRICATED_COMPANIES,
                  ANSWER_COVERAGE, ANSWER_QUALITY, SECURITY, ALL_VALUES_TAGGED, RESPONSE_TIME)

# --- Report thresholds (stage 5) - configurable, not hardcoded into logic ---
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
