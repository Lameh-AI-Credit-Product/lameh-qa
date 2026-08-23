"""
Lameh Intelligence (research) - source attribution
===================================================
Grades whether the figures in an answer can be traced back to a source
document. Deterministic: it reads the `<number>` tags' attributes and nothing
else.

This is the research suite's analogue of the FS suite's `tag_completeness`,
and it is one evaluator rather than two on purpose. The FS suite splits the
question in half - `tag_completeness` grades tags that exist but say too
little, `all_values_tagged` finds figures with no tag at all - because its
judged half needs a judge to sweep a 1400-tag answer for omissions. Board
analysis answers carry a handful of figures amid prose dense with numbers
that are *not* figures (meeting counts, ISO standard numbers, dates, article
numbers), so the same sweep would be mostly false positives. The untagged half
is folded into the hallucination judge, which has the source excerpt in front
of it and can tell a claimed figure from a sentence containing a digit.

Score = tagged figures carrying every required attribute / all tagged figures.
An answer with no figures at all scores None (not applicable), not 1.0 - a
prompt whose correct answer is "this is not disclosed" has nothing to
attribute, and crediting it for perfect attribution would flatter the run.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extraction import REQUIRED_NUMBER_ATTRS, missing_attrs, number_tags  # noqa: E402


def check_attribution(response_text, required=REQUIRED_NUMBER_ATTRS):
    """Returns
    {"total": int, "complete": int, "score": float|None, "findings": [...]}.

    A finding names the figure as displayed and what it lacks, so a failure in
    the report reads as "17.5 million - missing page" rather than as a count.
    """
    tags = number_tags(response_text)
    if not tags:
        return {"total": 0, "complete": 0, "score": None, "findings": []}

    findings = []
    complete = 0
    for tag in tags:
        gaps = missing_attrs(tag, required)
        if gaps:
            findings.append({
                "figure": tag["text"] or tag["attrs"].get("value") or "(empty)",
                "missing": gaps,
                "section": tag["attrs"].get("section"),
            })
        else:
            complete += 1

    return {
        "total": len(tags),
        "complete": complete,
        "score": complete / len(tags),
        "findings": findings,
    }
