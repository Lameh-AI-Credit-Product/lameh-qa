"""
Lameh Intelligence - eval run entrypoint
==========================================
Wires the dataset, the agent, and the evaluators built so far into one
LangSmith experiment run, visible in the LangSmith dashboard under project
config.LANGSMITH_PROJECT.

Eight evaluators in two families:

  deterministic (parse the response, query the live DB)
    numeric_accuracy       stated values vs ground truth - <calc> results,
                           <number> line items, and each <calc>'s own inputs
    tag_completeness       does every tag carry the attributes needed to
                           verify it at all
    company_coverage       is every company the prompt asked about present
    no_fabricated_companies is every company named real
    answer_coverage        companies + metrics + fiscal years asked for

  judged (one Bedrock call each, independent - see llm_judge.py)
    answer_quality         on-topic, usable, actually answers
    security               leaks, injection compliance, regulated advice
    all_values_tagged      figures stated with no provenance tag at all
    (answer_coverage also consumes a judged metric-coverage verdict)

The deterministic pair (tag_completeness) and judged pair (all_values_tagged)
are two halves of one question - "can a reader verify this figure?". The
first grades tags that exist but say too little; the second finds figures
with no tag at all. Neither sees the other's failure mode, which is why both
exist.

Known limitation carried over from extraction.py: <calc> tags don't carry
their own `company` attribute, so it's recovered from their `ops` entries.
Where ops entries omit `company` too (the ad-hoc ratios the agent computes
itself), a single `default_company` is passed to extraction when the
example's metadata lists exactly one company; otherwise those facts get
company=None and are skipped by numeric_comparison. They are no longer
invisible when that happens - tag_completeness charges for exactly this.

Once the experiment finishes, the markdown production-readiness report is
built automatically from it (results/langsmith/<experiment>.md) - pass
--skip-report to only run the experiment.

Usage
-----
    poetry run python tests/langsmith/run_eval.py
"""

import argparse
import json
import sys
import time
from pathlib import Path

from langsmith import Client
from langsmith.evaluation import evaluate

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "evaluators"))
sys.path.insert(0, str(Path(__file__).resolve().parent / "report"))
from agent_client import DEFAULT_DEADLINE_SECONDS, ask_agent  # noqa: E402
from build_report import write_report  # noqa: E402
from config import (ALL_VALUES_TAGGED, ANSWER_COVERAGE, ANSWER_QUALITY, COMPANY_COVERAGE,  # noqa: E402
                     DATASET_NAME, LANGSMITH_PROJECT, NO_FABRICATED_COMPANIES, NUMERIC_ACCURACY,
                     SECURITY, TAG_COMPLETENESS)
from correctness import (company_coverage, grounding_check, numeric_comparison,  # noqa: E402
                          ops_component_comparison)
from extraction import extract_all, extract_tags  # noqa: E402
from ground_truth import GroundTruthClient, list_sector_companies  # noqa: E402
from helpfulness import completeness_check  # noqa: E402
from llm_judge import (judge_metric_coverage, judge_quality, judge_security,  # noqa: E402
                        judge_untagged_values)
from tag_integrity import check_tags, unverifiable_facts  # noqa: E402

# One shared client per run, so repeated (company, metric, fiscal_year)
# lookups across examples are cached rather than re-fetched.
_ground_truth_client = GroundTruthClient()

REPORT_FETCH_ATTEMPTS = 3
REPORT_RETRY_DELAY_SECONDS = 10

# Each prompt is a 7-10 minute agent call that's almost entirely network wait,
# so examples are run in parallel by default - the dataset is 4 rows, hence 4.
# Raise via --max-concurrency if the dataset grows; lower it to 1 if the
# orchestrator starts rate-limiting or the parallel load skews response times.
DEFAULT_MAX_CONCURRENCY = 4

# Wall-clock budget per prompt, overridable with --agent-timeout. Module-level
# because LangSmith calls target() itself and gives us nowhere to pass it.
_agent_deadline_seconds = DEFAULT_DEADLINE_SECONDS


def _single_company_or_none(metadata):
    companies = metadata.get("companies") or []
    return companies[0] if len(companies) == 1 else None


# Long comments are truncated in the LangSmith UI, and a 60-tag response can
# produce a defect list longer than the answer itself. Every comment leads
# with its counts and aggregates so the useful part survives; the per-item
# lists are capped and marked as truncated. Full detail is always
# reconstructible from the run's own output text.
MAX_COMMENT_ITEMS = 15


def _capped(items, limit=MAX_COMMENT_ITEMS):
    if len(items) <= limit:
        return items
    return items[:limit] + [{"truncated": f"...and {len(items) - limit} more"}]


# Six of the eight evaluators need the same parsed facts, and LangSmith calls
# each evaluator separately - so parsing is cached per run rather than redone
# eight times over a 40 KB answer. Same pattern as the judge cache below;
# keyed by run id, and a race under LangSmith's evaluator threads costs one
# redundant parse at worst.
_extractions = {}


def _facts_and_tags(run, example):
    """(facts, tags) for a run: interpreted facts for value grading, raw tags
    for integrity grading. See extraction.extract_tags for why they're
    separate rather than one pass."""
    if run.id not in _extractions:
        answer = run.outputs["answer"]
        facts = extract_all(answer, default_company=_single_company_or_none(example.metadata or {}))
        _extractions[run.id] = (facts, extract_tags(answer))
    return _extractions[run.id]


# Each judged dimension is its own Bedrock call (see llm_judge.py for why they
# aren't bundled), cached per (run, dimension) so an evaluator that reads a
# verdict twice doesn't pay twice. Under LangSmith's evaluator threads a race
# costs one redundant call at worst.
_judge_verdicts = {}


def _judge(run, dimension, judge_fn, *args):
    key = (run.id, dimension)
    if key not in _judge_verdicts:
        _judge_verdicts[key] = judge_fn(*args)
    return _judge_verdicts[key]


def _judge_error_comment(verdict):
    """Says *how* the judge failed. "Truncated" means the reply was cut off
    mid-JSON - a token-ceiling problem with a specific fix - while anything
    else means it answered with something that wasn't JSON. Reporting both as
    "invalid JSON" sent the first full run's investigation down the wrong
    path once already."""
    if verdict.get("truncated"):
        return ("judge reply was cut off mid-JSON - raise judge_client.DEFAULT_MAX_TOKENS "
                "or lower llm_judge.MAX_FINDINGS")
    return f"judge did not return valid JSON: {str(verdict.get('raw_response'))[:200]!r}"


def _expected_companies(metadata):
    """What company_coverage should hold the response to.

    A company-scoped row names its companies outright. A sector-scoped row
    ("for all companies in the materials sector") carries an empty list, so
    the sector roster is the expectation instead - without it those prompts
    had no coverage requirement at all and an answer covering two of eleven
    companies scored full marks."""
    companies = metadata.get("companies") or []
    if companies:
        return companies, "dataset row"
    if metadata.get("scope") != "sector":
        return [], "nothing expected"
    try:
        return list_sector_companies(metadata.get("sector")), "sector roster"
    except KeyError:
        return [], f"no roster for sector {metadata.get('sector')!r}"


def target(inputs):
    """The system under test: the Intelligence agent itself.

    `timed_out`/`elapsed_seconds` ride along in the outputs so a prompt that
    blew the deadline is visible in the dashboard as its own thing, not just
    as a mysteriously short answer."""
    result = ask_agent(inputs["prompt"], deadline_seconds=_agent_deadline_seconds)
    if result["timed_out"]:
        print(f"  TIMED OUT after {result['elapsed_seconds']}s "
              f"({len(result['answer'])} chars received): {inputs['prompt'][:60]!r}")
    return {
        "answer": result["answer"],
        "conversation_id": result["conversation_id"],
        "completed": result["completed"],
        "timed_out": result["timed_out"],
        "elapsed_seconds": result["elapsed_seconds"],
    }


def numeric_accuracy_evaluator(run, example):
    """Are the numbers the agent states actually right, vs live DB values?

    Pools three levels of check into one score: stated ratio results
    (<calc>), stated statement line items (<number>), and the inputs each
    <calc> claims it used (`ops`). They're pooled rather than averaged
    per-level because they're the same question asked of different figures -
    but the comment breaks the counts down by level, since *which* level
    fails says what went wrong: bad ops with a good result means the agent
    got lucky, and good ops with a bad result is a formula error."""
    facts, _ = _facts_and_tags(run, example)
    comparisons = (numeric_comparison(facts, _ground_truth_client)
                   + ops_component_comparison(facts, _ground_truth_client))
    graded = [c for c in comparisons if not c["skipped"]]
    if not graded:
        return {"key": NUMERIC_ACCURACY, "score": None,
                "comment": f"no comparable facts extracted ({len(comparisons)} found, all unresolvable "
                           f"against ground truth)"}
    failures = [c for c in graded if not c["within_tolerance"]]
    by_kind = {}
    for comparison in graded:
        bucket = by_kind.setdefault(comparison["kind"], {"passed": 0, "total": 0})
        bucket["total"] += 1
        bucket["passed"] += bool(comparison["within_tolerance"])
    return {
        "key": NUMERIC_ACCURACY,
        "score": (len(graded) - len(failures)) / len(graded),
        "comment": json.dumps({
            "passed": len(graded) - len(failures),
            "total": len(graded),
            "skipped_unresolvable": len(comparisons) - len(graded),
            "by_kind": by_kind,
            "failures": _capped([{k: f[k] for k in
                                  ("kind", "company", "metric", "fiscal_year", "actual", "expected", "mape")}
                                 for f in failures]),
        }, ensure_ascii=False),
    }


def tag_completeness_evaluator(run, example):
    """Does every <calc>/<number> carry the attributes needed to verify it?

    A figure whose tag omits its company, fiscal year or metric name can't be
    looked up by anything - so it never reaches numeric_accuracy, and before
    this evaluator existed it cost the run nothing at all. This is where an
    untraceable figure is charged for."""
    facts, tags = _facts_and_tags(run, example)
    result = check_tags(tags)
    if result["score"] is None:
        return {"key": TAG_COMPLETENESS, "score": None,
                "comment": "the response contains no <calc>/<number> tags at all - "
                           "see all_values_tagged for whether it should have"}
    return {
        "key": TAG_COMPLETENESS,
        "score": result["score"],
        "comment": json.dumps({
            "complete_tags": result["complete_tags"],
            "total_tags": result["total_tags"],
            "by_kind": result["by_kind"],
            "missing_field_counts": result["missing_field_counts"],
            "derived_components_missing_id": result["derived_components"],
            "ungradeable_after_recovery": len(unverifiable_facts(facts)),
            "incomplete": _capped([{k: d[k] for k in ("kind", "line", "label", "value", "missing_attrs",
                                                       "missing_ops_fields")}
                                   for d in result["incomplete"]]),
        }, ensure_ascii=False),
    }


def company_coverage_evaluator(run, example):
    """Is every company the prompt asked about actually in the response?"""
    metadata = example.metadata or {}
    facts, _ = _facts_and_tags(run, example)
    expected, source = _expected_companies(metadata)
    result = company_coverage(facts, expected)
    if result["score"] is None:
        return {"key": COMPANY_COVERAGE, "score": None,
                "comment": f"no companies expected for this row ({source})"}
    return {
        "key": COMPANY_COVERAGE,
        "score": result["score"],
        "comment": json.dumps({
            "covered": len(result["covered"]),
            "expected": len(result["expected"]),
            "expectation_source": source,
            "missing": result["missing"],
        }, ensure_ascii=False),
    }


def no_fabricated_companies_evaluator(run, example):
    """Does every company the agent names actually exist in the sector?"""
    metadata = example.metadata or {}
    facts, _ = _facts_and_tags(run, example)
    sector = metadata.get("sector")
    try:
        valid_companies = list_sector_companies(sector) if sector else []
    except KeyError:
        valid_companies = []
    if not valid_companies:
        return {"key": NO_FABRICATED_COMPANIES, "score": None,
                "comment": f"no company roster for sector {sector!r} - nothing to validate against"}
    named_companies = {f["company"] for f in facts if f.get("company")}
    if not named_companies:
        return {"key": NO_FABRICATED_COMPANIES, "score": None,
                "comment": "the response names no companies - nothing to validate"}
    flagged = grounding_check(facts, valid_companies)
    return {
        "key": NO_FABRICATED_COMPANIES,
        "score": 0.0 if flagged else 1.0,
        "comment": json.dumps(flagged, ensure_ascii=False) if flagged
                   else f"all {len(named_companies)} companies named are real",
    }


def answer_coverage_evaluator(run, example):
    """Does the answer cover every company/metric/fiscal year asked for?"""
    metadata = example.metadata or {}
    answer = run.outputs["answer"]
    facts, _ = _facts_and_tags(run, example)
    expected_metrics = metadata.get("metrics_expected") or []

    # Metric presence comes from the judge, which reads meaning rather than
    # strings - the agent renames metrics freely and also names ones it never
    # supplied. Falls back to structural matching if the judge misbehaves.
    metrics_present = None
    if expected_metrics:
        verdict = _judge(run, "metric_coverage", judge_metric_coverage,
                          example.inputs["prompt"], answer, expected_metrics)
        if not verdict.get("judge_error"):
            metrics_present = verdict.get("metrics_present") or {}

    result = completeness_check(
        facts,
        expected_companies=metadata.get("companies") or [],
        expected_metrics=expected_metrics,
        expected_fiscal_years=metadata.get("fiscal_years") or [],
        stream_completed=run.outputs.get("completed", True) and not run.outputs.get("timed_out"),
        response_text=answer,
        metrics_present=metrics_present,
    )
    result["metric_source"] = "judge" if metrics_present is not None else "structural-fallback"
    if run.outputs.get("timed_out"):
        # A deadline kill is a genuine coverage failure - the answer really is
        # cut off - but it stays scoped to coverage. The other evaluators still
        # grade whatever text did arrive, so a slow prompt doesn't wipe out
        # every dimension at once.
        result["score"] = 0.0
        result["timed_out_after_seconds"] = run.outputs.get("elapsed_seconds")
    elif result["score"] is None:
        return {"key": ANSWER_COVERAGE, "score": None,
                "comment": "not applicable - the dataset row expects no specific companies/metrics/years"}
    return {"key": ANSWER_COVERAGE, "score": result["score"], "comment": json.dumps(result, ensure_ascii=False)}


def answer_quality_evaluator(run, example):
    """LLM judge: on topic, well formatted, and actually answers the question."""
    verdict = _judge(run, "quality", judge_quality, example.inputs["prompt"], run.outputs["answer"])
    if verdict.get("judge_error"):
        return {"key": ANSWER_QUALITY, "score": None, "comment": _judge_error_comment(verdict)}
    score = 1.0 if (verdict["on_topic"] and verdict["well_formatted"] and verdict["answers_question"]) else 0.0
    return {"key": ANSWER_QUALITY, "score": score, "comment": verdict.get("reasoning")}


def security_evaluator(run, example):
    """Did the response leak internals, obey injected instructions, or give
    regulated investment advice?

    Binary, not proportional: one leaked credential is not half as bad as
    two. A violation makes the run not-ready regardless of how well it scored
    everywhere else, which is why config.THRESHOLDS gates this at 1.0."""
    verdict = _judge(run, "security", judge_security, example.inputs["prompt"], run.outputs["answer"])
    if verdict.get("judge_error"):
        return {"key": SECURITY, "score": None, "comment": _judge_error_comment(verdict)}
    violations = verdict.get("security_violations") or []
    return {
        "key": SECURITY,
        "score": 0.0 if violations else 1.0,
        "comment": json.dumps(_capped(violations), ensure_ascii=False) if violations
                   else "no security findings",
    }


def all_values_tagged_evaluator(run, example):
    """Are there financial figures stated with no provenance tag at all?

    Scored as the share of financial figures that are tagged - tagged tags
    counted deterministically, untagged ones found by the judge - rather than
    pass/fail. A response citing 60 figures correctly and missing one is
    genuinely better than one that tags nothing, and a binary score would
    call them identical. The count of tags comes from parsing rather than
    from the judge, because the judge is reliable at spotting what's missing
    and poor at counting what isn't."""
    verdict = _judge(run, "untagged_values", judge_untagged_values,
                      example.inputs["prompt"], run.outputs["answer"])
    if verdict.get("judge_error"):
        return {"key": ALL_VALUES_TAGGED, "score": None, "comment": _judge_error_comment(verdict)}
    _, tags = _facts_and_tags(run, example)
    # `untagged_total` is the judge's full count; `untagged_values` is a
    # capped sample of it (see llm_judge.MAX_FINDINGS). Scoring on the sample
    # would flatter a response bad enough to hit the cap.
    untagged = verdict.get("untagged_values") or []
    untagged_total = verdict.get("untagged_total", len(untagged))
    total = len(tags) + untagged_total
    if not total:
        return {"key": ALL_VALUES_TAGGED, "score": None,
                "comment": "the response states no financial figures at all"}
    return {
        "key": ALL_VALUES_TAGGED,
        "score": len(tags) / total,
        "comment": json.dumps({
            "tagged": len(tags),
            "untagged": untagged_total,
            "findings_shown": len(untagged),
            "findings": _capped(untagged),
        }, ensure_ascii=False),
    }


def _select_examples(example_id):
    """All examples in the dataset, or just the one whose prompt_set.json
    `id` matches --example-id (e.g. "materials-q1-cash-quality") - useful for
    a quick single-prompt dashboard run before committing to the full,
    slower sector-wide prompts."""
    client = Client()
    examples = list(client.list_examples(dataset_name=DATASET_NAME))
    if example_id is None:
        return examples
    matching = [e for e in examples if (e.metadata or {}).get("id") == example_id]
    if not matching:
        raise ValueError(f"No example with id={example_id!r} found in dataset {DATASET_NAME!r}")
    return matching


def main():
    ap = argparse.ArgumentParser(description="Run the Lameh Intelligence LangSmith eval suite.")
    ap.add_argument("--example-id", default=None,
                     help="Only run the single dataset example with this prompt_set.json id "
                          "(e.g. materials-q1-cash-quality). Omit to run the whole dataset.")
    ap.add_argument("--max-concurrency", type=int, default=DEFAULT_MAX_CONCURRENCY,
                     help=f"How many dataset examples to run against the agent at once "
                          f"(default {DEFAULT_MAX_CONCURRENCY}). Each prompt takes ~7-10 min, so running "
                          f"them in parallel is roughly the difference between one prompt's "
                          f"wall time and the whole dataset's. Use 1 to serialize.")
    ap.add_argument("--agent-timeout", type=int, default=DEFAULT_DEADLINE_SECONDS, metavar="SECONDS",
                     help=f"Wall-clock budget per prompt (default {DEFAULT_DEADLINE_SECONDS}s = "
                          f"{DEFAULT_DEADLINE_SECONDS // 60} min). A prompt still running past this is "
                          f"cut off and scored on whatever text arrived, with answer_coverage failed as "
                          f"truncated. Pass 0 to wait indefinitely.")
    ap.add_argument("--report-out", default=None,
                     help="Where to write the markdown report. Defaults to results/langsmith/<experiment>.md")
    ap.add_argument("--skip-report", action="store_true",
                     help="Only run the experiment; don't build the markdown report afterwards.")
    args = ap.parse_args()

    global _agent_deadline_seconds
    _agent_deadline_seconds = args.agent_timeout or None

    examples = _select_examples(args.example_id)
    results = evaluate(
        target,
        data=examples,
        evaluators=[numeric_accuracy_evaluator, tag_completeness_evaluator,
                    company_coverage_evaluator, no_fabricated_companies_evaluator,
                    answer_coverage_evaluator, answer_quality_evaluator,
                    security_evaluator, all_values_tagged_evaluator],
        experiment_prefix="materials-sector",
        metadata={"suite": "lameh-intelligence-eval"},
        max_concurrency=args.max_concurrency,
    )
    print(f"Done - check the '{LANGSMITH_PROJECT}' project in the LangSmith dashboard.")

    if args.skip_report:
        print(f"Report skipped. Build it later with: poetry run poe langsmith-report --experiment {results.experiment_name}")
        return

    _build_report_for(results.experiment_name, args.report_out, expected_example_count=len(examples))


def _build_report_for(experiment_name, report_out, expected_example_count):
    """One report per run, covering every example in it. Feedback is uploaded
    asynchronously and lands per-example - the slowest prompts can trail the
    fastest by minutes - so write_report waits until all `expected_example_count`
    examples have all their scores rather than rendering whichever finished
    first. A report failure must never lose the run itself, hence the catch."""
    print(f"Building report for {experiment_name} ({expected_example_count} examples) ...")
    try:
        out_path = write_report(
            experiment_name,
            out=report_out,
            expected_example_count=expected_example_count,
            wait_attempts=REPORT_FETCH_ATTEMPTS,
            wait_delay_seconds=REPORT_RETRY_DELAY_SECONDS,
        )
        print(f"Report written to {out_path}")
    except Exception as exc:  # noqa: BLE001 - the experiment itself already succeeded
        print(f"Report generation failed ({exc}).")
        print(f"The experiment itself is fine - retry with: "
              f"poetry run poe langsmith-report --experiment {experiment_name}")


if __name__ == "__main__":
    main()
