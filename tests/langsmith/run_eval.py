"""
Lameh Intelligence - eval run entrypoint
==========================================
Wires the dataset, the agent, and the evaluators built so far into one
LangSmith experiment run, visible in the LangSmith dashboard under project
config.LANGSMITH_PROJECT.

STATUS: first working version - correctness + helpfulness only.
safety.py and report/build_report.py don't exist yet, so this is a "see the
data in the dashboard" run, not the final production-readiness pipeline.

Known limitation carried over from extraction.py: <calc> tags don't carry
their own `company` attribute, so for a multi-company prompt (Q1 has 4
companies, Q4 has 5) we can't attribute a stated ratio value to a specific
company without guessing. This run only passes a single `default_company`
to extraction when the example's metadata lists exactly one company;
otherwise calc facts get company=None and are skipped by numeric_comparison
(they simply won't show up in the correctness score for those examples yet).

Usage
-----
    poetry run python tests/langsmith/run_eval.py
"""

import argparse
import json
import sys
from pathlib import Path

from langsmith import Client
from langsmith.evaluation import evaluate

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "evaluators"))
from agent_client import ask_agent  # noqa: E402
from config import DATASET_NAME, LANGSMITH_PROJECT  # noqa: E402
from correctness import grounding_check, numeric_comparison  # noqa: E402
from extraction import extract_all  # noqa: E402
from ground_truth import GroundTruthClient, list_sector_companies  # noqa: E402
from helpfulness import completeness_check, relevance_usability_judge  # noqa: E402

# One shared client per run, so repeated (company, metric, fiscal_year)
# lookups across examples are cached rather than re-fetched.
_ground_truth_client = GroundTruthClient()


def _single_company_or_none(metadata):
    companies = metadata.get("companies") or []
    return companies[0] if len(companies) == 1 else None


def target(inputs):
    """The system under test: the Intelligence agent itself."""
    result = ask_agent(inputs["prompt"])
    return {"answer": result["answer"], "conversation_id": result["conversation_id"], "completed": result["completed"]}


def correctness_evaluator(run, example):
    metadata = example.metadata or {}
    facts = extract_all(run.outputs["answer"], default_company=_single_company_or_none(metadata))
    comparisons = numeric_comparison(facts, _ground_truth_client)
    graded = [c for c in comparisons if not c["skipped"]]
    if not graded:
        return {"key": "correctness", "score": None, "comment": "no comparable facts extracted"}
    passed = sum(1 for c in graded if c["within_tolerance"])
    return {
        "key": "correctness",
        "score": passed / len(graded),
        "comment": json.dumps({"passed": passed, "total": len(graded)}, ensure_ascii=False),
    }


def grounding_evaluator(run, example):
    metadata = example.metadata or {}
    facts = extract_all(run.outputs["answer"], default_company=_single_company_or_none(metadata))
    sector = metadata.get("sector")
    try:
        valid_companies = list_sector_companies(sector) if sector else []
    except KeyError:
        valid_companies = []
    flagged = grounding_check(facts, valid_companies) if valid_companies else []
    return {
        "key": "grounding",
        "score": 0.0 if flagged else 1.0,
        "comment": json.dumps(flagged, ensure_ascii=False) if flagged else "no fabricated companies detected",
    }


def completeness_evaluator(run, example):
    metadata = example.metadata or {}
    facts = extract_all(run.outputs["answer"], default_company=_single_company_or_none(metadata))
    result = completeness_check(
        facts,
        expected_companies=metadata.get("companies") or [],
        expected_metrics=metadata.get("metrics_expected") or [],
        expected_fiscal_years=metadata.get("fiscal_years") or [],
        stream_completed=run.outputs.get("completed", True),
    )
    return {"key": "completeness", "score": 1.0 if result["complete"] else 0.0, "comment": json.dumps(result, ensure_ascii=False)}


def helpfulness_judge_evaluator(run, example):
    verdict = relevance_usability_judge(example.inputs["prompt"], run.outputs["answer"])
    if verdict.get("judge_error"):
        return {"key": "helpfulness_judge", "score": None, "comment": "judge did not return valid JSON"}
    score = 1.0 if (verdict["on_topic"] and verdict["well_formatted"] and verdict["answers_question"]) else 0.0
    return {"key": "helpfulness_judge", "score": score, "comment": verdict.get("reasoning")}


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
    args = ap.parse_args()

    evaluate(
        target,
        data=_select_examples(args.example_id),
        evaluators=[correctness_evaluator, grounding_evaluator, completeness_evaluator, helpfulness_judge_evaluator],
        experiment_prefix="materials-sector",
        metadata={"suite": "lameh-intelligence-eval"},
        max_concurrency=1,
    )
    print(f"Done - check the '{LANGSMITH_PROJECT}' project in the LangSmith dashboard.")


if __name__ == "__main__":
    main()
