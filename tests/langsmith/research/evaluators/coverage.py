"""
Lameh Intelligence (research) - answer coverage
================================================
Did the response cover the facts and periods the prompt asked for?

Structural, not judged. The FS suite's answer_coverage asks a judge whether
each expected *metric* is present, because "Days Sales Outstanding" can be
presented under half a dozen phrasings. This suite's `facts_expected` entries
are statements, not metric names ("Fahd bin Thunayan Al-Thunayan missed
Meeting 4"), and a judge asked "is this statement present" is doing the same
work judge_traps already does with better context. So coverage here stays
cheap and structural: it checks the fiscal years asked about actually appear,
and reports the expected facts for a human to read against the answer rather
than scoring them a second time.

Score = fiscal years present / fiscal years asked about. A row with no
`fiscal_years` scores None.

The consequence worth knowing: a response can score 1.0 here by mentioning
every year while getting every fact wrong. That is intentional - this is the
"did it engage with the question asked" check, and correctness belongs to
trap_handling and no_hallucinated_data. Read it as a floor, never as a pass.
"""

import re


def _year_present(text, year):
    """A year counts as covered however the answer spells it: bare (`2023`),
    prefixed (`FY2023`, `FY 2023`), or inside an ISO date (`2023-12-31`).

    The boundary is digit-based, not `\\b`. This was a real bug found on the
    first research run: `\\b2023\\b` does **not** match `FY2023`, because `Y`
    and `2` are both word characters so there is no word boundary between
    them - and `FY2023` is exactly how these answers write a fiscal year. Two
    examples scored 0/3 and 1/3 for years they had discussed throughout.

    Digit lookaround still rejects a year embedded in a longer number
    (`120234`), which is the only false positive worth guarding against.
    """
    return bool(re.search(rf"(?<!\d){re.escape(str(year))}(?!\d)", text or ""))


def check_coverage(response_text, fiscal_years=None, facts_expected=None):
    """Returns
    {"score": float|None, "years_covered": [...], "years_missing": [...],
     "facts_expected": [...]}."""
    years = [str(y) for y in (fiscal_years or [])]
    if not years:
        return {"score": None, "years_covered": [], "years_missing": [],
                "facts_expected": list(facts_expected or [])}

    covered = [y for y in years if _year_present(response_text, y)]
    missing = [y for y in years if y not in covered]
    return {
        "score": len(covered) / len(years),
        "years_covered": covered,
        "years_missing": missing,
        # Carried through to the feedback comment rather than scored: the
        # report is where a reviewer checks these off, and scoring them here
        # would duplicate judge_traps with less context.
        "facts_expected": list(facts_expected or []),
    }
