"""
Lameh Intelligence - tag integrity (deterministic)
====================================================
Grades the *provenance markup itself*, before any value is compared against
ground truth. A figure is only verifiable if its tag says which company,
which fiscal year, and which statement line it came from - so a tag missing
any of those is a defect in its own right, independent of whether the number
happens to be right.

This is the check that catches what numeric_comparison silently skips. In a
real multi-company response the agent emitted twelve "Debt to CapEx" tags
like:

    <calc v="0.0x" f="Total Debt / |CapEx|"
          ops='[{"id":"...","l":"Total Debt FY2023","v":0,"raw":"0"}]' />

- no `rid`, no `id`, and `ops` entries carrying only id/label/value. Nothing
about that tag is checkable: correctness.py can't resolve a metric name, and
can't attribute the value to a company or a year. It scored nothing at all,
so a whole section of unverifiable figures cost the run zero points. Here it
costs twelve.

What "complete" means
---------------------
REQUIRED_CALC_ATTRS / REQUIRED_NUMBER_ATTRS / REQUIRED_OPS_FIELDS below are
the contract. They're the intersection of "what the agent emits when it's
behaving" (every one is present on 100% of tags in the healthy fixture) and
"what an automated verifier needs to look the value up" - not an aspirational
wish list.

`id` is required on every ops component too, even though a derived component
like "Average Total Shareholders' Equity" has no single source row to point
at (it's the mean of two balance-sheet dates). Those are reported under their
own `derived_components` count so they stay distinguishable in the report -
but they still count against the score, deliberately: an averaged input the
response can't trace back to two dated rows is an input a reviewer can't
check by hand.

Scoring is per tag, not per field: a tag is complete or it isn't, matching
"if a tag is not complete that's an error". The field-level histogram rides
along in the result so a run that drops one attribute across the board reads
as one systemic bug rather than N unrelated ones.
"""

from collections import Counter

# --- The tag contract. Every one of these is present on 100% of tags in
# fixtures/sample_response_materials_q1_multicompany.json, so requiring them
# describes the agent's own good behaviour rather than imposing a new one.

# `rid` = canonical ratio name, the only thing that resolves to a ground-truth
# lookup; `ops` = the components the value was derived from, without which a
# wrong result can't be traced to a wrong input.
REQUIRED_CALC_ATTRS = ("v", "f", "rid", "id", "ops")

# <number> is the strongest tag: it names its own company/year/section/metric
# and carries `raw`, the unrounded source figure that grading compares against.
REQUIRED_NUMBER_ATTRS = ("id", "company", "year", "period", "section", "metric", "raw")

# One ops entry = one input to a calculation. `l` (label) + section + company
# + year + period is exactly the tuple ground_truth.get() needs; `v` is the
# input value; `id` traces it to a row in the source statement.
REQUIRED_OPS_FIELDS = ("l", "v", "id", "section", "company", "year", "period")

# Present in real responses and useful, but absent often enough on healthy
# output that requiring them would flag correct behaviour: <number>'s `type`
# and `multiplier`, ops' `subsection`, <calc>'s optional `subsection`.
OPTIONAL_ATTRS = ("type", "multiplier", "subsection", "raw")


def _missing(mapping, required):
    """Required keys that are absent, None, or blank. An attribute present
    but empty (company="") is as unusable as one that was never emitted, so
    both count as missing rather than only testing for the key."""
    missing = []
    for field in required:
        value = mapping.get(field)
        if value is None or (isinstance(value, str) and not value.strip()):
            missing.append(field)
    return missing


def check_tag(tag):
    """One raw tag (see extraction.extract_tags) -> a defect record, or None
    if the tag is complete. `missing_attrs` are the tag's own absent
    attributes; `missing_ops_fields` are absences inside its `ops` components,
    reported as "ops.<field>" so the two levels stay distinguishable in the
    aggregate histogram."""
    kind = tag["kind"]
    required = REQUIRED_CALC_ATTRS if kind == "calc" else REQUIRED_NUMBER_ATTRS
    missing_attrs = _missing(tag["attrs"], required)

    missing_ops_fields = Counter()
    derived_components = 0
    if kind == "calc":
        if tag["ops_error"]:
            missing_attrs.append("ops (invalid JSON)")
        elif not tag["ops"] and "ops" not in missing_attrs:
            # An `ops=''` or `ops='[]'` attribute is present but says nothing
            # about where the value came from - no better than omitting it.
            missing_attrs.append("ops (empty)")
        for entry in tag["ops"]:
            entry_missing = _missing(entry, REQUIRED_OPS_FIELDS)
            if entry_missing == ["id"]:
                derived_components += 1
            for field in entry_missing:
                missing_ops_fields[f"ops.{field}"] += 1

    if not missing_attrs and not missing_ops_fields:
        return None
    return {
        "kind": kind,
        "line": tag["line"],
        "label": tag["attrs"].get("rid") or tag["attrs"].get("metric") or tag["attrs"].get("f"),
        "value": tag["attrs"].get("v") or tag["attrs"].get("raw"),
        "missing_attrs": missing_attrs,
        "missing_ops_fields": dict(missing_ops_fields),
        "derived_components": derived_components,
    }


def check_tags(tags):
    """Grades every tag in one response.

    Returns {"score", "total_tags", "complete_tags", "by_kind",
    "missing_field_counts", "derived_components", "incomplete"}.

    "score" is complete_tags / total_tags, or None when the response contains
    no tags at all. None is not a zero: a response with no tags has a
    different problem (see llm_judge.py's untagged-value check), and scoring
    it here as 0.0 would double-penalise the same defect while making
    "emitted nothing" indistinguishable from "emitted 40 broken tags".

    "incomplete" is capped by the caller if it needs to fit in a LangSmith
    comment - the histogram is the part that survives truncation usefully."""
    defects = [d for d in (check_tag(t) for t in tags) if d is not None]

    missing_field_counts = Counter()
    for defect in defects:
        missing_field_counts.update(defect["missing_attrs"])
        missing_field_counts.update(defect["missing_ops_fields"])

    by_kind = Counter(t["kind"] for t in tags)
    incomplete_by_kind = Counter(d["kind"] for d in defects)

    return {
        "score": (len(tags) - len(defects)) / len(tags) if tags else None,
        "total_tags": len(tags),
        "complete_tags": len(tags) - len(defects),
        "by_kind": {kind: {"total": count, "incomplete": incomplete_by_kind[kind]}
                    for kind, count in by_kind.items()},
        "missing_field_counts": dict(missing_field_counts.most_common()),
        "derived_components": sum(d["derived_components"] for d in defects),
        "incomplete": defects,
    }


def unverifiable_facts(facts):
    """The interpreted facts (see extraction.extract_all) that grading will
    end up skipping, and why.

    Complements check_tags: that one asks "did the agent emit the attributes",
    this one asks "after extraction.py's best-effort recovery, is there still
    not enough to look this value up". The two disagree usefully - a <calc>
    with no `rid` whose table title happens to name the ratio is an incomplete
    tag that is nonetheless gradeable, and a tag with every attribute present
    can still be ungradeable if its value string wouldn't parse."""
    unverifiable = []
    for fact in facts:
        reasons = []
        if fact.get("value") is None:
            reasons.append("value did not parse")
        if not fact.get("company"):
            reasons.append("no company")
        if not fact.get("fiscal_year"):
            reasons.append("no fiscal year")
        if not (fact.get("metric") or fact.get("table_title")):
            reasons.append("no metric name")
        if reasons:
            unverifiable.append({
                "kind": fact.get("kind"),
                "label": fact.get("metric") or fact.get("table_title") or fact.get("formula"),
                "display_value": fact.get("display_value"),
                "reasons": reasons,
            })
    return unverifiable
