"""
Lameh Intelligence - shared eval config
========================================
What every suite under tests/langsmith needs regardless of what it evaluates:
credentials, the deployment label, the agent's answering modes, and the
experiment-naming rule.

Anything that describes *what a suite grades* - its dataset name, evaluator
keys, thresholds, report layout - belongs in that suite's own config.py
(fs/config.py, research/config.py), not here. The split is the whole reason
the suites are separate directories: the FS suite grades tagged financial
figures against a numeric DB, the research suite grades narrative
board-analysis answers against a document merge, and a shared key list would
have to be the union of two things neither suite wants.
"""

import os

from dotenv import load_dotenv

load_dotenv()

# One LangSmith project holds both suites' experiments. They are told apart by
# the experiment prefix below (intelligence-fs / intelligence-research) and by
# their datasets, so there is nothing to gain from splitting the project and
# something to lose: cross-suite runs stay visible in one place.
LANGSMITH_PROJECT = "lameh-intelligence-eval"

# --- AI mode (the agent's own answering mode, not a property of any dataset) --
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
# "DEV-intelligence-fs-expert-d01438a5". Three labels are carried:
#
# $ENV (DEV/UAT/CORE) is the same deployment label the sector-analysis pipeline
# uses, and it matters here for the same reason: the three deployments hold
# different data, so an experiment named by dataset alone gives no way to tell
# later which one it ran against. Unlike the download step, an unset or
# unrecognized $ENV is not fatal - it just drops that part of the prefix. A
# missing label is visible in the dashboard; a wrong one is not.
#
# The suite (intelligence-fs / intelligence-research) is next, because both
# suites now write into one LangSmith project and the dataset name is not
# shown in an experiment listing.
#
# The ai_mode goes last and, unlike $ENV, is never omitted: two experiments
# over one dataset are told apart by nothing else, and the whole point of
# running both is comparing them.
VALID_ENVS = ("DEV", "UAT", "CORE")
ENV = (os.environ.get("ENV") or "").strip().upper()
if ENV not in VALID_ENVS:
    ENV = ""


def experiment_prefix(suite, ai_mode):
    """e.g. experiment_prefix("intelligence-fs", "instant") ->
    "UAT-intelligence-fs-instant". Experiments run before 2026-08-16 have no
    mode segment and were all expert."""
    head = f"{ENV}-{suite}" if ENV else suite
    return f"{head}-{ai_mode}"


# --- Orchestrator (the Intelligence agent under test) ---
# Shared: both suites talk to the same /v0/chat endpoint with the same
# credentials. Only their *ground truth* sources differ, and those live in the
# suite configs.
#
# The org id comes from LAMEH_ORGANIZATION_ID_FOR_INTELLIGENCE_EVAL, which is
# the eval suite's own variable rather than the repo-wide LAMEH_ORGANIZATION_ID
# it used to read. The suites are the only thing that ever read that one, and
# giving them a dedicated name means the org the eval runs as can be changed
# without touching anything else - and that a reader of .env can tell which
# variable affects which tool.
ORCHESTRATOR_URL = os.environ.get("LAMEH_ORCHESTRATOR_URL")
ORCHESTRATOR_ORGANIZATION_ID = os.environ.get("LAMEH_ORGANIZATION_ID_FOR_INTELLIGENCE_EVAL")
ORCHESTRATOR_API_KEY = os.environ.get("LAMEH_API_KEY")
ORCHESTRATOR_USER_ID = os.environ.get("LAMEH_USER_ID")

# --- LLM judge (Claude via AWS Bedrock) ---
# The transport is shared (judge_client.py); the rubrics are not. Each suite
# writes its own - see fs/evaluators/llm_judge.py and
# research/evaluators/llm_judge.py.
AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY")
AWS_REGION = os.environ.get("AWS_REGION")
AWS_BEDROCK_MODEL_ID = os.environ.get("AWS_BEDROCK_MODEL_ID")
