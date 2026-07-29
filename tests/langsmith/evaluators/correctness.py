"""
Lameh Intelligence - correctness evaluators
=============================================
Checks over a set of extraction.py facts:
  - numeric_comparison: agent-stated value vs live ground truth (exact /
    tolerance / MAPE, via metrics.py), for both <calc> ratios and <number>
    statement line items.
  - ops_component_comparison: the same, one level down - each *input* a
    <calc> says it used, checked against the source statement.
  - grounding_check: every company the agent names must exist in the live
    sector data - flags fabricated companies.
  - company_coverage: every company the prompt asked about must actually
    appear in the response.
  - cross_prompt_consistency: the same (company, metric, fiscal_year) must
    not diverge across different examples/prompts in one eval run.

All of them take plain extraction fact dicts (see extraction.py) plus a
ground_truth.GroundTruthClient - no LangSmith or dataset coupling here, so
each is independently unit-testable with a fake client/company list.

Why both levels of numeric check: a <calc> can land on the right answer from
the wrong inputs, and more often the reverse - a formula applied correctly to
a misread line item. Grading only the stated result hides which of the two
happened, and a ratio that ground truth has no pre-computed entry for (the
ad-hoc ones the agent derives itself) can't be graded at the result level at
all, while its components almost always can.
"""

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extraction import display_precision  # noqa: E402
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


def _prefetch(ground_truth_client, keys):
    """Ask the client to resolve `keys` in bulk if it can.

    Optional by design: this module's contract is that every function works
    with any object exposing .get(), so unit tests can pass a trivial fake.
    A client without prefetch simply pays per-key, exactly as before."""
    prefetch = getattr(ground_truth_client, "prefetch", None)
    if prefetch and keys:
        prefetch(keys)


def _fact_section(fact):
    """Which chart-data section a fact's ground truth lives in. <number> tags
    name their own ("Income Statement", "Cash Flow Statement", ...); <calc>
    tags are ratios, which are always looked up under "Financial Ratios"."""
    if fact.get("kind") == "number":
        return fact.get("section") or "Financial Ratios"
    return "Financial Ratios"


def numeric_comparison(facts, ground_truth_client, tolerance=DEFAULT_TOLERANCE):
    """Compares every comparable fact - <calc> ratios *and* <number>
    statement line items - against a live ground-truth lookup. A fact needs a
    company, a metric name, a fiscal year and a parsed value; facts missing
    any of those are skipped entirely (not returned), since there's nothing
    to compare. Callers should treat that as a tag-integrity signal rather
    than a correctness one - tag_integrity.unverifiable_facts() reports the
    same facts with the reason each one couldn't be graded.

    <number> facts are graded against their own `section`, so a Cash Flow
    Statement line is looked up in the cash flow statement rather than
    mis-resolved against a same-named ratio. They're also the cheapest thing
    in the response to get right - the value is copied, not derived - which
    makes a failure here a much stronger signal than a failed ratio.

    A returned result can still have expected=None and "skipped": True -
    that happens when the metric label isn't a real name in its section
    (e.g. the table_title fallback for an ad-hoc ratio like "Debt to CapEx
    Ratio — Inputs..." - see extraction.py), so ground truth genuinely
    couldn't be resolved. Callers must exclude skipped results from
    pass/fail rates - they are not failures, they're "couldn't grade this
    one yet"."""
    pending = []
    for fact in _dedupe_by_id(facts):
        if fact.get("kind") not in ("calc", "number") or fact.get("value") is None:
            continue
        metric = _fact_metric_label(fact)
        company = fact.get("company")
        fiscal_year = fact.get("fiscal_year")
        if not (metric and company and fiscal_year):
            continue
        pending.append((fact, company, metric, fiscal_year, _fact_section(fact)))

    _prefetch(ground_truth_client, [(c, m, y, s) for _, c, m, y, s in pending])

    results = []
    for fact, company, metric, fiscal_year, section in pending:
        expected = _scale_ground_truth(
            ground_truth_client.get(company, metric, fiscal_year, section), fact.get("unit"))
        # <number> facts carry `raw`, the unrounded source figure, so they're
        # held to the relative tolerance alone. Only <calc> values, which are
        # parsed from the rounded string the agent displayed, get the
        # display-precision allowance.
        result = compare(fact["value"], expected, tolerance,
                          display_epsilon=display_precision(fact.get("display_value"))
                          if fact.get("kind") == "calc" else None)
        result.update({
            "kind": fact.get("kind"),
            "company": company,
            "metric": metric,
            "section": section,
            "fiscal_year": fiscal_year,
            "fact_id": fact.get("id"),
            "skipped": expected is None,
        })
        results.append(result)
    return results


def ops_component_comparison(facts, ground_truth_client, tolerance=DEFAULT_TOLERANCE):
    """Checks each *input* a <calc> claims it used, not the result.

    An ops entry is self-describing - {"l": "Net Profit for the Period",
    "v": 5127534.0, "section": "Income Statement", "company": "...",
    "year": "2023", "period": "yearly"} - which is exactly the tuple
    ground_truth.get() takes, so the claimed input can be looked up directly
    in the statement it names. A mismatch here means the arithmetic was
    performed on a figure that isn't in the data, which is a different and
    more serious defect than a formula error.

    Entries are deduped by their own `id`: a component like Net Profit feeds
    ROE, Cash Flow Quality and the summary table, and should be fetched and
    graded once. Entries with no id (derived averages) are deduped by their
    (company, label, year) tuple instead, since there's nothing else to
    correlate them by - tag_integrity.py is what penalises the missing id.

    Skipped results (expected=None) follow the same rule as
    numeric_comparison: excluded from pass/fail rates, not counted as
    failures."""
    pending = []
    seen = set()
    for fact in facts:
        if fact.get("kind") != "calc":
            continue
        for entry in fact.get("ops") or []:
            label, company = entry.get("l"), entry.get("company")
            year, section = entry.get("year"), entry.get("section")
            value = entry.get("v")
            if not (label and company and year and section) or not isinstance(value, (int, float)):
                continue
            key = entry.get("id") or (company, label, year)
            if key in seen:
                continue
            seen.add(key)
            pending.append((fact, entry, company, label, str(year), section, float(value)))

    _prefetch(ground_truth_client, [(c, m, y, s) for _, _, c, m, y, s, _ in pending])

    results = []
    for fact, entry, company, label, year, section, value in pending:
        expected = ground_truth_client.get(company, label, year, section)
        result = compare(value, expected, tolerance)
        result.update({
            "kind": "ops",
            "company": company,
            "metric": label,
            "section": section,
            "fiscal_year": year,
            "fact_id": entry.get("id"),
            "used_by": fact.get("metric") or fact.get("table_title"),
            "skipped": expected is None,
        })
        results.append(result)
    return results


def company_coverage(facts, expected_companies):
    """Which of the companies the prompt asked about actually appear in the
    response's tags.

    Distinct from grounding_check, which asks the opposite question (is every
    company the agent *named* real?). Both can pass while the other fails: an
    answer covering three of four requested companies invents nobody, and an
    answer covering all four can still invent a fifth.

    `expected_companies` should be the dataset row's `companies` for a
    company-scoped prompt, or the full sector roster
    (ground_truth.list_sector_companies) for a sector-wide one - those rows
    carry an empty `companies` list, so without the roster there is nothing
    to check and a sector prompt that silently covered two of eleven
    companies scored full marks.

    Matching is exact on the Arabic company name, which is how both the
    dataset and the tags spell it. Returns score=None when nothing was
    expected, never 0.0 - "not applicable" and "covered none" must stay
    distinguishable."""
    expected = list(expected_companies or [])
    mentioned = {f["company"] for f in facts if f.get("company")}
    missing = [c for c in expected if c not in mentioned]
    return {
        "expected": expected,
        "covered": [c for c in expected if c in mentioned],
        "missing": missing,
        "extra": sorted(mentioned - set(expected)),
        "score": (len(expected) - len(missing)) / len(expected) if expected else None,
    }


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
