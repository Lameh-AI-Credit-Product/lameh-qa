"""
Lameh Intelligence (research) - eval run entrypoint
====================================================
Runs the board-analysis prompt set against the Intelligence agent as a
LangSmith experiment over the `Research-Intelligence` dataset, and writes the
production-readiness report from the result.

Seven evaluators in three families:

  deterministic (parse the response)
    source_attribution     every tagged figure carries document_id, page,
                           evidence_block_id and value
    answer_coverage        the fiscal years asked about are addressed

  judged (one Bedrock call each - see evaluators/llm_judge.py)
    trap_handling          did the answer deal with the defects this prompt
                           was built around
    no_hallucinated_data   anything asserted the sources don't support
    answer_quality         on topic, sourced, usable
    security               leaks, injection compliance, regulated advice,
                           inference about named individuals

Response time is deliberately NOT an evaluator: LangSmith already records
per-run latency (matching our own stream timing to 0.1s) and exposes
latency_p50/p99 on the experiment, which build_report reads. See the retired
note in config.py.

How this differs from the FS suite, in one line: that suite asks whether a
number is right; this one asks whether the agent noticed the number cannot be.
The prompts are built around real defects in the merged board reports, each
declared in the dataset row's `traps`, and trap_handling is the gate that
matters.

Usage
-----
    poetry run python tests/langsmith/research/run_eval.py
    poetry run python tests/langsmith/research/run_eval.py --ai-mode fast
"""

import argparse
import sys
import time
from pathlib import Path

from langsmith import Client
from langsmith.evaluation import evaluate

_HERE = Path(__file__).resolve().parent
_SHARED = _HERE.parent / "shared"
sys.path.insert(0, str(_SHARED / "report"))
sys.path.insert(0, str(_SHARED))
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE / "evaluators"))
sys.path.insert(0, str(_HERE / "dataset"))

import comment_format as fmt  # noqa: E402
from agent_client import ask_agent  # noqa: E402
from attribution import check_attribution  # noqa: E402
from build_report import write_report  # noqa: E402
from config import (ANSWER_COVERAGE, ANSWER_QUALITY, NO_HALLUCINATED_DATA,  # noqa: E402
                     REPORT_SPEC, SECURITY, SOURCE_ATTRIBUTION, TRAP_HANDLING, prefix)
from coverage import check_coverage  # noqa: E402
from env_config import (AI_MODES, DEFAULT_AI_MODE, LANGSMITH_PROJECT,  # noqa: E402
                         normalize_ai_mode)
from ground_truth import (FrozenGroundTruth, fact_terms, locator_terms,  # noqa: E402
                            render_rows, search_any)
from llm_judge import judge_hallucination, judge_quality, judge_security, judge_traps  # noqa: E402
from traps import score_traps  # noqa: E402

# The frozen ground truth (dataset/ground_truth.json), not the live API. The
# eval grades against a committed snapshot so a score change means the agent
# changed and nothing else - see dataset/freeze_ground_truth.py. Drift against
# the live merge run is checked once at startup and warned about loudly.
_ground_truth = FrozenGroundTruth()

REPORT_FETCH_ATTEMPTS = 3
REPORT_RETRY_DELAY_SECONDS = 10

# Six prompts, and the 2026-08-23 probe answered in 135 seconds - an order of
# magnitude faster than the FS suite's 7-10 minute sector prompts. The whole
# set therefore fits comfortably in flight at once.
DEFAULT_MAX_CONCURRENCY = 6

# Wall-clock budget per prompt. 300s rather than the FS suite's 900s: at a
# measured 135s, a 900s budget would let a hung prompt cost fifteen minutes
# before anyone learned anything. Module-level because LangSmith calls target()
# itself and gives us nowhere to pass it - same for the two below.
DEFAULT_DEADLINE_SECONDS = 300
_agent_deadline_seconds = DEFAULT_DEADLINE_SECONDS

_agent_mode = DEFAULT_AI_MODE
_max_concurrency = DEFAULT_MAX_CONCURRENCY

# Per-run caches keyed by LangSmith run id. Four judges and two deterministic
# evaluators all want the same parsed response and the same source excerpt;
# without this each would re-derive them, and the judges would each pay for a
# second Bedrock call.
_judge_cache = {}
_source_cache = {}


def target(inputs):
    """The system under test, as LangSmith calls it per example."""
    result = ask_agent(inputs["prompt"], ai_mode=_agent_mode,
                       deadline_seconds=_agent_deadline_seconds)
    return {
        "answer": result["answer"],
        "conversation_id": result["conversation_id"],
        "completed": result["completed"],
        "timed_out": result["timed_out"],
        "elapsed_seconds": result["elapsed_seconds"],
        "ai_mode": _agent_mode,
        "max_concurrency": _max_concurrency,
    }


def _answer(run):
    return (run.outputs or {}).get("answer") or ""


def _prompt(example):
    return (example.inputs or {}).get("prompt") or ""


def _metadata(example):
    return example.metadata or {}


def _source_excerpt(example, limit_rows=200):
    """The slice of merge/tables relevant to this prompt, as text for the
    hallucination judge.

    Built from the row's trap locators *and* its expected facts rather than
    from the whole 6 MB payload: the judge needs enough source to catch a
    contradiction, and the full flattened set (1,779 rows) would swamp the
    context. Terms are searched independently and merged, so a term that
    matches nothing costs nothing.

    `limit_rows` is 200, not 60. The first research run used 60 and the
    governance example filled its whole budget with one director's attendance
    rows, leaving out the classification rows the answer was actually making
    claims about - and the judge then reported three findings for facts that
    were merely *absent* from the excerpt. The rubric now forbids
    absence-based findings outright, but a thin excerpt invites them, so both
    ends were fixed. Rows are interleaved across terms for the same reason:
    one broad term must not consume the budget before a narrower one is
    reached.
    """
    key = example.id
    if key in _source_cache:
        return _source_cache[key]

    metadata = _metadata(example)
    company = metadata.get("company_ar") or metadata.get("company")
    try:
        rows = _ground_truth.rows(company)
        source_meta = dict(_ground_truth.meta(company), frozen_at=_ground_truth.frozen_at)
    except Exception as exc:  # noqa: BLE001 - a snapshot problem must not fail the run
        _source_cache[key] = ("", {"error": str(exc)})
        return _source_cache[key]

    terms = []
    for trap in metadata.get("traps") or []:
        terms += locator_terms(trap.get("locator"))
    # Expected facts name the entities and metrics the answer will discuss
    # ("Cement Products Manufacturing Company", "Prince Nayef..."), which is
    # exactly the source a contradiction check needs and which trap locators
    # (table names) do not cover.
    terms += fact_terms(metadata.get("facts_expected"))
    picked = search_any(rows, terms, limit=limit_rows, interleave=True)

    _source_cache[key] = (render_rows(picked), source_meta)
    return _source_cache[key]


def _judge(run, dimension, judge_fn, *args):
    """Judge verdicts cached per (run, dimension). A judge failure returns the
    exception rather than raising: the dimension scores None and the run keeps
    going, because a Bedrock outage must shrink the sample, not fail the
    experiment."""
    key = (run.id, dimension)
    if key not in _judge_cache:
        try:
            _judge_cache[key] = judge_fn(*args)
        except Exception as exc:  # noqa: BLE001
            _judge_cache[key] = exc
    return _judge_cache[key]


# --- deterministic evaluators ------------------------------------------------

def source_attribution_evaluator(run, example):
    result = check_attribution(_answer(run))
    return {"key": SOURCE_ATTRIBUTION, "score": result["score"],
            "comment": fmt.attribution_comment(result)}


def answer_coverage_evaluator(run, example):
    metadata = _metadata(example)
    outputs = run.outputs or {}
    if outputs.get("timed_out"):
        return {"key": ANSWER_COVERAGE, "score": 0.0,
                "comment": "Answer was cut off at the deadline - scored as no coverage."}
    result = check_coverage(_answer(run), metadata.get("fiscal_years"),
                            metadata.get("facts_expected"))
    return {"key": ANSWER_COVERAGE, "score": result["score"],
            "comment": fmt.coverage_comment(result)}


# --- judged evaluators -------------------------------------------------------

def trap_handling_evaluator(run, example):
    metadata = _metadata(example)
    declared = metadata.get("traps") or []
    verdict = _judge(run, TRAP_HANDLING, judge_traps, _prompt(example), _answer(run), declared)
    if isinstance(verdict, Exception):
        return {"key": TRAP_HANDLING, "score": None,
                "comment": fmt.judge_error_comment("trap_handling", verdict)}
    result = score_traps(verdict.get("verdicts"), declared)
    _, source_run = _source_excerpt(example)
    return {"key": TRAP_HANDLING, "score": result["score"],
            "comment": fmt.traps_comment(result, source_run if isinstance(source_run, dict) else None)}


def no_hallucinated_data_evaluator(run, example):
    excerpt, _ = _source_excerpt(example)
    verdict = _judge(run, NO_HALLUCINATED_DATA, judge_hallucination,
                     _prompt(example), _answer(run), excerpt)
    if isinstance(verdict, Exception):
        return {"key": NO_HALLUCINATED_DATA, "score": None,
                "comment": fmt.judge_error_comment("no_hallucinated_data", verdict)}
    findings = verdict.get("findings") or []
    return {"key": NO_HALLUCINATED_DATA, "score": 0.0 if findings else 1.0,
            "comment": fmt.hallucination_comment(findings)}


def answer_quality_evaluator(run, example):
    verdict = _judge(run, ANSWER_QUALITY, judge_quality, _prompt(example), _answer(run))
    if isinstance(verdict, Exception):
        return {"key": ANSWER_QUALITY, "score": None,
                "comment": fmt.judge_error_comment("answer_quality", verdict)}
    criteria = ("answers_question", "sourced", "usable")
    met = sum(1 for k in criteria if verdict.get(k) == 1)
    return {"key": ANSWER_QUALITY, "score": met / len(criteria),
            "comment": fmt.quality_comment(verdict)}


def security_evaluator(run, example):
    verdict = _judge(run, SECURITY, judge_security, _prompt(example), _answer(run))
    if isinstance(verdict, Exception):
        return {"key": SECURITY, "score": None,
                "comment": fmt.judge_error_comment("security", verdict)}
    findings = verdict.get("findings") or []
    return {"key": SECURITY, "score": 0.0 if findings else 1.0,
            "comment": fmt.security_comment(findings)}


EVALUATORS = [source_attribution_evaluator, answer_coverage_evaluator,
              trap_handling_evaluator, no_hallucinated_data_evaluator,
              answer_quality_evaluator, security_evaluator]


def _select_examples(example_id):
    client = Client()
    examples = list(client.list_examples(dataset_name=REPORT_SPEC.dataset_name))
    if not example_id:
        return examples
    picked = [e for e in examples if (e.metadata or {}).get("id") == example_id]
    if not picked:
        raise ValueError(f"No example with id={example_id!r} found in dataset "
                         f"{REPORT_SPEC.dataset_name!r}")
    return picked


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(description="Run the Lameh Intelligence board-analysis eval suite.")
    ap.add_argument("--example-id", default=None,
                     help="Run one prompt only, by its prompt_set id (e.g. yamama-safety-metrics).")
    ap.add_argument("--ai-mode", type=normalize_ai_mode, choices=AI_MODES, default=DEFAULT_AI_MODE,
                     help="Which mode the agent answers in (default expert). 'fast' is accepted "
                          "for 'instant', which is what the product calls fast mode and what the "
                          "API requires. One run is one mode; compare by running twice.")
    ap.add_argument("--max-concurrency", type=int, default=DEFAULT_MAX_CONCURRENCY,
                     help=f"Examples in flight at once (default {DEFAULT_MAX_CONCURRENCY}). "
                          "Recorded on every run, because the report's latency percentiles only "
                          "compare fairly against another run at the same concurrency.")
    ap.add_argument("--agent-timeout", type=int, default=DEFAULT_DEADLINE_SECONDS, metavar="SECONDS",
                     help=f"Wall-clock budget per prompt (default {DEFAULT_DEADLINE_SECONDS}s). "
                          "A prompt past it is cut off and scored on the text that arrived, with "
                          "answer_coverage failed. Pass 0 to wait indefinitely.")
    ap.add_argument("--report-out", default=None,
                     help="Where to write the markdown report. Defaults to "
                          "results/langsmith/research/<experiment>.md")
    ap.add_argument("--skip-report", action="store_true",
                     help="Only run the experiment; don't build the markdown report afterwards.")
    ap.add_argument("--skip-drift-check", action="store_true",
                     help="Don't compare the frozen ground truth against the live merge run "
                          "before starting. The check costs one request and never blocks the "
                          "run; skip it only when offline.")
    args = ap.parse_args()

    global _agent_mode, _agent_deadline_seconds, _max_concurrency
    _agent_mode = args.ai_mode
    _agent_deadline_seconds = args.agent_timeout or None
    _max_concurrency = args.max_concurrency

    _warn_on_ground_truth_drift(skip=args.skip_drift_check)

    examples = _select_examples(args.example_id)
    print(f"Running {len(examples)} example(s) in {args.ai_mode} mode "
          f"at concurrency {args.max_concurrency} ...")

    results = evaluate(
        target,
        data=examples,
        evaluators=EVALUATORS,
        experiment_prefix=prefix(args.ai_mode),
        metadata={"suite": REPORT_SPEC.experiment_suite, "ai_mode": args.ai_mode,
                  "max_concurrency": args.max_concurrency},
        max_concurrency=args.max_concurrency,
    )
    print(f"Done - check the '{LANGSMITH_PROJECT}' project in the LangSmith dashboard.")

    if args.skip_report:
        print("Report skipped. Build it later with: poetry run poe langsmith-research-report "
              f"--experiment {results.experiment_name}")
        return

    _build_report_for(results.experiment_name, args.report_out, expected_example_count=len(examples))


def _warn_on_ground_truth_drift(skip=False):
    """Says, before any grading happens, whether the frozen ground truth still
    matches the live merge run.

    A warning, never a failure. The whole point of freezing is that a run does
    not depend on the API being up or unchanged; making this fatal would hand
    that dependency straight back. What it prevents is the silent case - an
    agent graded for weeks against a source that moved.
    """
    if skip:
        print("Ground-truth drift check skipped (--skip-drift-check).")
        return
    try:
        from freeze_ground_truth import check_drift  # noqa: PLC0415 - optional path
        drift = check_drift()
    except Exception as exc:  # noqa: BLE001
        print(f"Could not check ground-truth drift ({type(exc).__name__}: {exc}) - continuing.")
        return
    if not drift:
        print(f"Ground truth current (frozen {_ground_truth.frozen_at}).")
        return
    print("=" * 72)
    print("WARNING: the frozen ground truth no longer matches the live merge run.")
    for line in drift:
        print(f"  {line}")
    print("Scores below are graded against the frozen snapshot, which is the point -")
    print("but they describe an older source than the agent is now reading.")
    print("=" * 72)


def _build_report_for(experiment_name, report_out, expected_example_count):
    """One report per run, covering every example in it. Feedback is uploaded
    asynchronously and lands per-example, so write_report waits until every
    example has all its scores rather than rendering whichever finished first.
    A report failure must never lose the run itself, hence the catch."""
    print(f"Building report for {experiment_name} ({expected_example_count} examples) ...")
    try:
        out_path = write_report(
            REPORT_SPEC,
            experiment_name,
            out=report_out,
            expected_example_count=expected_example_count,
            wait_attempts=REPORT_FETCH_ATTEMPTS,
            wait_delay_seconds=REPORT_RETRY_DELAY_SECONDS,
        )
        print(f"Report written to {out_path}")
    except Exception as exc:  # noqa: BLE001 - the experiment itself already succeeded
        print(f"Experiment finished but the report failed to build: {exc}")
        print(f"Retry with: poetry run poe langsmith-research-report --experiment {experiment_name}")


if __name__ == "__main__":
    main()
