"""
Lameh Intelligence - helpfulness evaluators
=============================================
Two checks, independent of correctness:
  - completeness_check: does the response cover every company/metric/fiscal
    year the prompt asked for, and did the stream actually finish (vs a
    truncated/cut-off response)?
  - relevance_usability_judge: LLM-as-judge (Claude via Bedrock, see
    judge_client.py) against a short rubric - stays on topic, usable/
    well-formatted, doesn't dodge the question.

Both take plain extraction.py fact dicts / raw text - no LangSmith or
dataset coupling, so both are independently unit-testable (the judge check
via a fake `ask_judge_fn`).
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from judge_client import ask_judge  # noqa: E402


def completeness_check(facts, expected_companies=None, expected_metrics=None,
                        expected_fiscal_years=None, stream_completed=True):
    """Compares what the response actually covers (via extraction.py facts)
    against what the prompt's dataset metadata expected. Any of
    expected_companies/expected_metrics/expected_fiscal_years can be omitted
    (None or empty) to skip that dimension - e.g. sector-wide prompts have no
    fixed company list in the dataset row, so the caller should resolve one
    via ground_truth.list_sector_companies() first, or skip the check for
    open-ended prompts (metrics_expected == [] - see prompt_set.json) where
    there's no fixed target to check against.

    `stream_completed` should come from agent_client.ask_agent()'s result -
    False means the SSE stream ended without a message_complete event, i.e.
    a truncated response, which overrides everything else as incomplete.

    "score" is a fraction of expected items actually covered, or None when
    nothing was expected at all - an unscored row, never a zero."""
    mentioned_companies = {f["company"] for f in facts if f.get("company")}
    mentioned_metrics = {f["metric"] for f in facts if f.get("metric")}
    mentioned_table_titles = {f["table_title"].lower() for f in facts if f.get("table_title")}
    mentioned_fiscal_years = {f["fiscal_year"] for f in facts if f.get("fiscal_year")}

    missing_companies = [c for c in (expected_companies or []) if c not in mentioned_companies]

    missing_metrics = []
    for metric in expected_metrics or []:
        found = metric in mentioned_metrics or any(metric.lower() in title for title in mentioned_table_titles)
        if not found:
            missing_metrics.append(metric)

    normalized_expected_years = {y.replace("FY", "").strip() for y in (expected_fiscal_years or []) if "FY" in y.upper()}
    missing_fiscal_years = sorted(normalized_expected_years - mentioned_fiscal_years)

    expected_item_count = len(expected_companies or []) + len(expected_metrics or []) + len(normalized_expected_years)
    missing_item_count = len(missing_companies) + len(missing_metrics) + len(missing_fiscal_years)
    if not stream_completed:
        # A cut-off response is a real failure regardless of what was
        # covered before it got cut off - don't let partial credit mask that.
        score = 0.0
    elif expected_item_count == 0:
        # Nothing was expected, so there's nothing to score. None (not 0.0,
        # not 1.0) so "not applicable" stays visibly distinct from "covered
        # nothing it should have" in the dashboard and in the report's
        # aggregates.
        score = None
    else:
        score = (expected_item_count - missing_item_count) / expected_item_count

    return {
        "missing_companies": missing_companies,
        "missing_metrics": missing_metrics,
        "missing_fiscal_years": missing_fiscal_years,
        "truncated": not stream_completed,
        "complete": not (missing_companies or missing_metrics or missing_fiscal_years) and stream_completed,
        "expected_item_count": expected_item_count,
        "score": score,
    }


JUDGE_RUBRIC = """You are grading a financial-analysis assistant's response for Lameh Intelligence, a tool for Saudi capital-markets (Tadawul/Nomu) analysis.

Score the RESPONSE against the ORIGINAL PROMPT on exactly three criteria:
1. on_topic: does it address what was actually asked, without drifting into unrelated content?
2. well_formatted: is it usable/readable (clear structure - tables, headings, etc. - not just a wall of run-on prose)?
3. answers_question: does it give a real, substantive answer rather than dodging, refusing, or answering a different question than the one asked?

Respond with ONLY a JSON object, no other text, no markdown fences:
{{"on_topic": true/false, "well_formatted": true/false, "answers_question": true/false, "reasoning": "one sentence"}}

ORIGINAL PROMPT:
{prompt}

RESPONSE:
{response}
"""

_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$")


def _parse_judge_json(raw_text):
    cleaned = _JSON_FENCE_RE.sub("", raw_text.strip())
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return None


def relevance_usability_judge(prompt_text, response_text, ask_judge_fn=ask_judge):
    """Returns {"on_topic", "well_formatted", "answers_question", "reasoning",
    "judge_error"}. judge_error=True (with a "raw_response" field instead of
    the parsed fields) means the judge's reply wasn't valid JSON - a real
    failure to surface, not something to silently paper over with a default
    verdict."""
    raw = ask_judge_fn(JUDGE_RUBRIC.format(prompt=prompt_text, response=response_text))
    parsed = _parse_judge_json(raw)
    if parsed is None:
        return {"judge_error": True, "raw_response": raw}
    parsed["judge_error"] = False
    return parsed
