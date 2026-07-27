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


_STOPWORDS = {"of", "to", "the", "and", "a", "an", "for", "in", "on", "per", "by", "ratio", "ratios"}
_WORD_RE = re.compile(r"[a-z0-9]+")


def _significant_words(label):
    """Content words of a metric label, lowercased. Stopwords and the generic
    "ratio" suffix are dropped so "Debt to CapEx ratio" and "Debt/CapEx"
    reduce to the same {debt, capex}."""
    return {w for w in _WORD_RE.findall(label.lower()) if w not in _STOPWORDS}


_HEADING_RE = re.compile(r"^#{1,6}\s+(.+)$", re.M)
_TABLE_ROW_RE = re.compile(r"^\|(.+)\|\s*$", re.M)
_TABLE_DIVIDER_RE = re.compile(r"^[\s|:-]+$")


def coverage_anchors(response_text):
    """The places a metric can legitimately be *presented*: markdown headings
    and table column headers.

    Deliberately not all prose. Matching anywhere in the text counts a metric
    as covered when the response only name-drops it - a sector ranking that
    answered "Note on Market Cap: ... not practical to retrieve" scored full
    coverage for Market Cap while supplying none of it. A metric the prompt
    asked for should appear as a section or a column, not as an excuse."""
    anchors = list(_HEADING_RE.findall(response_text))
    for row in _TABLE_ROW_RE.findall(response_text):
        if not _TABLE_DIVIDER_RE.match(row):
            anchors.extend(cell.strip() for cell in row.split("|"))
    return anchors


def _metric_is_covered(metric, mentioned_metrics, anchors):
    """Whether the response covers `metric`: an exact `rid` match, or a
    word-subset match against one of the `anchors` (see coverage_anchors).

    The subset match exists because the agent names things its own way - a
    prompt asking for "Cost of Revenue Breakdown" gets answered under a
    "## Cost of Revenue Breakdown - Materials Sector (FY2025)" heading, and
    "Debt to CapEx" appears as a "Debt/CapEx" column. Matching tag attributes
    literally missed all of those."""
    if metric in mentioned_metrics:
        return True
    words = _significant_words(metric)
    if not words:
        return False
    return any(words <= _significant_words(anchor) for anchor in anchors)


def _coverage(expected, missing):
    """Fraction of one dimension covered, or None if nothing was expected in
    that dimension (so it's left out of the score rather than counted as a
    free pass)."""
    if not expected:
        return None
    return (len(expected) - len(missing)) / len(expected)


def completeness_check(facts, expected_companies=None, expected_metrics=None,
                        expected_fiscal_years=None, stream_completed=True, response_text="",
                        metrics_present=None):
    """Compares what the response actually covers (via extraction.py facts,
    plus the raw `response_text`) against what the prompt's dataset metadata
    expected. Any of expected_companies/expected_metrics/expected_fiscal_years
    can be omitted (None or empty) to skip that dimension - e.g. open-ended
    prompts (metrics_expected == [] - see prompt_set.json) have no fixed
    target to check against.

    `response_text` is the full answer. Metric coverage is checked against
    its headings and table columns as well as against the tags, because the
    agent frequently presents a requested metric under a plain markdown
    heading or column without ever wrapping it in a <data_table>/<calc> tag -
    tag-only matching reported those as missing. See coverage_anchors for why
    this stops short of matching prose.

    `stream_completed` should come from agent_client.ask_agent()'s result -
    False means the SSE stream ended without a message_complete event, i.e.
    a truncated response, which overrides everything else as incomplete.

    "score" averages the three dimensions (companies / metrics / fiscal
    years) that the prompt actually asked for, rather than pooling them into
    one item count. Pooling let whichever dimension had the most items
    dominate, and made a single missed metric on a one-metric prompt collapse
    the whole score to zero. None means nothing was expected at all - an
    unscored row, never a zero."""
    mentioned_companies = {f["company"] for f in facts if f.get("company")}
    mentioned_metrics = {f["metric"] for f in facts if f.get("metric")}
    mentioned_fiscal_years = {f["fiscal_year"] for f in facts if f.get("fiscal_year")}
    anchors = coverage_anchors(response_text)
    anchors.extend(f["table_title"] for f in facts if f.get("table_title"))

    missing_companies = [c for c in (expected_companies or []) if c not in mentioned_companies]

    if metrics_present is None:
        # Structural fallback, used when no judge verdict is available (unit
        # tests, or a judge that failed to return valid JSON). Weaker than the
        # judge: it can't tell a metric that's presented from one that's
        # merely named - see coverage_anchors.
        missing_metrics = [m for m in (expected_metrics or [])
                           if not _metric_is_covered(m, mentioned_metrics, anchors)]
    else:
        missing_metrics = [m for m in (expected_metrics or []) if not metrics_present.get(m)]

    normalized_expected_years = sorted(
        {y.replace("FY", "").strip() for y in (expected_fiscal_years or []) if "FY" in y.upper()}
    )
    missing_fiscal_years = [y for y in normalized_expected_years if y not in mentioned_fiscal_years]

    dimension_scores = {
        "companies": _coverage(expected_companies, missing_companies),
        "metrics": _coverage(expected_metrics, missing_metrics),
        "fiscal_years": _coverage(normalized_expected_years, missing_fiscal_years),
    }
    graded = [s for s in dimension_scores.values() if s is not None]

    if not stream_completed:
        # A cut-off response is a real failure regardless of what was
        # covered before it got cut off - don't let partial credit mask that.
        score = 0.0
    elif not graded:
        # Nothing was expected, so there's nothing to score. None (not 0.0,
        # not 1.0) so "not applicable" stays visibly distinct from "covered
        # nothing it should have" in the dashboard and in the report's
        # aggregates.
        score = None
    else:
        score = sum(graded) / len(graded)

    return {
        "missing_companies": missing_companies,
        "missing_metrics": missing_metrics,
        "missing_fiscal_years": missing_fiscal_years,
        "truncated": not stream_completed,
        "complete": not (missing_companies or missing_metrics or missing_fiscal_years) and stream_completed,
        "dimension_scores": dimension_scores,
        "score": score,
    }


JUDGE_RUBRIC = """You are grading a financial-analysis assistant's response for Lameh Intelligence, a tool for Saudi capital-markets (Tadawul/Nomu) analysis.

Score the RESPONSE against the ORIGINAL PROMPT on exactly three criteria:
1. on_topic: does it address what was actually asked, without drifting into unrelated content?
2. well_formatted: is it usable/readable (clear structure - tables, headings, etc. - not just a wall of run-on prose)?
3. answers_question: does it give a real, substantive answer rather than dodging, refusing, or answering a different question than the one asked?
{coverage_task}
Respond with ONLY a JSON object, no other text, no markdown fences:
{{{{"on_topic": true/false, "well_formatted": true/false, "answers_question": true/false, "reasoning": "one sentence"{coverage_field}}}}}

ORIGINAL PROMPT:
{{prompt}}

RESPONSE:
{{response}}
"""

# Appended only when the dataset row lists expected metrics. Metric coverage
# is judged rather than string-matched because the agent renames freely -
# "Cash Flow from Operating Activities" comes back as an "Operating CF"
# column, "Cost of Revenue Breakdown" as a "COGS Composition" table - while
# also *mentioning* metrics it failed to supply ("Note on Market Cap: batch
# retrieval is not practical"). No string rule separated those two cases.
_COVERAGE_TASK = """
Then, for each metric in METRICS BELOW, decide whether the response actually
PRESENTS that metric - i.e. gives real figures/values for it. A metric only
mentioned by name, promised, or explained away as unavailable is NOT present.
Match on meaning, not wording: a differently-named or abbreviated column
("Operating CF" for "Cash Flow from Operating Activities") counts as present.

METRICS TO CHECK:
{metrics}
"""

_COVERAGE_FIELD = ', "metrics_present": {{"<metric name exactly as listed>": true/false, ...}}'

_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$")


def _parse_judge_json(raw_text):
    cleaned = _JSON_FENCE_RE.sub("", raw_text.strip())
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return None


def relevance_usability_judge(prompt_text, response_text, expected_metrics=(), ask_judge_fn=ask_judge):
    """One judge call covering both judged dimensions: the quality rubric
    (on_topic / well_formatted / answers_question) and, when
    `expected_metrics` is non-empty, which of those metrics the response
    actually presents.

    Returns {"on_topic", "well_formatted", "answers_question", "reasoning",
    "metrics_present": {metric: bool}, "judge_error"}. judge_error=True (with
    a "raw_response" field instead of the parsed fields) means the judge's
    reply wasn't valid JSON - a real failure to surface, not something to
    silently paper over with a default verdict.

    Both judged evaluators share this single call (see run_eval.py's cache),
    so adding metric coverage costs no extra Bedrock requests."""
    metrics = list(expected_metrics or [])
    rubric = JUDGE_RUBRIC.format(
        coverage_task=_COVERAGE_TASK.format(metrics="\n".join(f"- {m}" for m in metrics)) if metrics else "",
        coverage_field=_COVERAGE_FIELD if metrics else "",
    )
    raw = ask_judge_fn(rubric.format(prompt=prompt_text, response=response_text))
    parsed = _parse_judge_json(raw)
    if parsed is None:
        return {"judge_error": True, "raw_response": raw}
    parsed["judge_error"] = False
    parsed.setdefault("metrics_present", {})
    return parsed
