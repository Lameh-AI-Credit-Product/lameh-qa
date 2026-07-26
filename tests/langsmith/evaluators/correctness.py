"""
Lameh Intelligence - correctness evaluators
=============================================
Three checks over a set of extraction.py facts:
  - numeric_comparison: agent-stated value vs live ground truth (exact /
    tolerance / MAPE, via metrics.py).
  - grounding_check: every company the agent names must exist in the live
    sector data - flags fabricated companies.
  - cross_prompt_consistency: the same (company, metric, fiscal_year) must
    not diverge across different examples/prompts in one eval run.

All three take plain extraction fact dicts (see extraction.py) plus a
ground_truth.GroundTruthClient - no LangSmith or dataset coupling here, so
each is independently unit-testable with a fake client/company list.
"""

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from metrics import compare, tolerance_match, DEFAULT_TOLERANCE  # noqa: E402


def _fact_metric_label(fact):
    """A <calc> fact's comparable metric name: the canonical `rid` if the
    agent tagged one, else the fuzzy table_title fallback (see
    extraction.py's _enclosing_table_title) for ad-hoc ratios like
    "Debt to CapEx" that have no rid at all."""
    return fact.get("metric") or fact.get("table_title")


def _dedupe_by_id(facts):
    """The same tagged value is often cited multiple times (data table, key
    finding prose, summary dashboard) - all sharing one `id`. Keep only the
    first occurrence per id so a value is compared against ground truth once,
    not once per citation (each citation's fiscal-year attribution is a
    same-line text heuristic in extraction.py and can differ slightly across
    citations of the same tag). Facts without an id (ad-hoc ratios like
    "Debt to CapEx" - see extraction.py) aren't deduped, since there's
    nothing to correlate them by."""
    seen_ids = set()
    deduped = []
    for fact in facts:
        fact_id = fact.get("id")
        if fact_id is None:
            deduped.append(fact)
        elif fact_id not in seen_ids:
            seen_ids.add(fact_id)
            deduped.append(fact)
    return deduped


def _scale_ground_truth(expected, unit):
    """The chart-data/batch API returns percent-type ratios (e.g. ROE) as a
    raw fraction (0.0535), while extraction.py parses the agent's "5.35%" as
    5.35 (unit="percent"). Scale ground truth to match whenever the extracted
    fact is a percent - other units (multiple/days/amount) already match."""
    if expected is None:
        return None
    if unit == "percent":
        return expected * 100
    return expected


def numeric_comparison(facts, ground_truth_client, tolerance=DEFAULT_TOLERANCE):
    """Compares every comparable "calc" fact (has company + metric + fiscal
    year + a parsed value) against a live ground-truth lookup. Facts missing
    any of those are skipped entirely (not returned), since there's nothing
    to compare - callers should treat that as a helpfulness/completeness
    signal, not a correctness one.

    A returned result can still have expected=None and "skipped": True -
    that happens when the metric label isn't a real "Financial Ratios" name
    (e.g. the table_title fallback for an ad-hoc ratio like "Debt to CapEx
    Ratio — Inputs..." - see extraction.py), so ground truth genuinely
    couldn't be resolved. Callers must exclude skipped results from
    pass/fail rates - they are not failures, they're "couldn't grade this
    one yet"."""
    results = []
    for fact in _dedupe_by_id(facts):
        if fact.get("kind") != "calc" or fact.get("value") is None:
            continue
        metric = _fact_metric_label(fact)
        company = fact.get("company")
        fiscal_year = fact.get("fiscal_year")
        if not (metric and company and fiscal_year):
            continue
        expected = _scale_ground_truth(ground_truth_client.get(company, metric, fiscal_year), fact.get("unit"))
        result = compare(fact["value"], expected, tolerance)
        result.update({
            "company": company,
            "metric": metric,
            "fiscal_year": fiscal_year,
            "fact_id": fact.get("id"),
            "skipped": expected is None,
        })
        results.append(result)
    return results


def grounding_check(facts, valid_companies):
    """Flags every distinct company named in the facts (via <number> tags'
    explicit `company` attribute) that isn't in `valid_companies` - i.e. a
    company the agent referenced that doesn't actually exist in the live
    sector data. `valid_companies` should come from
    ground_truth.list_sector_companies() for the prompt's sector."""
    valid_set = set(valid_companies)
    seen = set()
    flagged = []
    for fact in facts:
        company = fact.get("company")
        if not company or company in seen:
            continue
        seen.add(company)
        if company not in valid_set:
            flagged.append({"company": company, "reason": "not in live sector company list"})
    return flagged


def cross_prompt_consistency(facts_by_example_id, tolerance=DEFAULT_TOLERANCE):
    """Groups all "calc" facts across every example in one eval run by
    (company, metric, fiscal_year) and flags any group whose values disagree
    beyond `tolerance` - the same company's metric stated differently in two
    different prompts within the same run.

    `facts_by_example_id`: {example_id: [facts...]} for every dataset example
    evaluated in this run."""
    groups = defaultdict(list)
    for example_id, facts in facts_by_example_id.items():
        for fact in _dedupe_by_id(facts):
            if fact.get("kind") != "calc" or fact.get("value") is None:
                continue
            metric = _fact_metric_label(fact)
            company = fact.get("company")
            fiscal_year = fact.get("fiscal_year")
            if not (metric and company and fiscal_year):
                continue
            groups[(company, metric, fiscal_year)].append({
                "example_id": example_id,
                "value": fact["value"],
                "fact_id": fact.get("id"),
            })

    inconsistencies = []
    for (company, metric, fiscal_year), occurrences in groups.items():
        if len(occurrences) < 2:
            continue
        base_value = occurrences[0]["value"]
        if not all(tolerance_match(o["value"], base_value, tolerance) for o in occurrences[1:]):
            inconsistencies.append({
                "company": company,
                "metric": metric,
                "fiscal_year": fiscal_year,
                "occurrences": occurrences,
            })
    return inconsistencies
