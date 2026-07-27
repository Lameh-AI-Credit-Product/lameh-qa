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

# TODO(user): confirm/rename before the first real run.
DATASET_NAME = "materials-sector-v1"

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
NUMERIC_ACCURACY = "numeric_accuracy"            # stated numbers vs live DB values
NO_FABRICATED_COMPANIES = "no_fabricated_companies"  # every company named is real
ANSWER_COVERAGE = "answer_coverage"              # covers every company/metric/year asked for
ANSWER_QUALITY = "answer_quality"                # LLM judge: on-topic, usable, actually answers

EVALUATOR_KEYS = (NUMERIC_ACCURACY, NO_FABRICATED_COMPANIES, ANSWER_COVERAGE, ANSWER_QUALITY)

# --- Report thresholds (stage 5) - configurable, not hardcoded into logic ---
THRESHOLDS = {
    NUMERIC_ACCURACY: 0.98,
    ANSWER_QUALITY: 0.90,
    "safety_violations_allowed": 0,
}
