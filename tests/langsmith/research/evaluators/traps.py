"""
Lameh Intelligence (research) - trap handling
==============================================
Scores the judge's per-trap verdicts (llm_judge.judge_traps) into one number.

Why traps live in the dataset rather than in evaluator code
------------------------------------------------------------
Every prompt in this suite is built around a specific defect in the source
data - a related-party cell reading 57 among neighbours reading 343,000, a
forecast column stored unscaled, one director spelled seven ways, a metric the
company never disclosed. Those defects are properties of *the question and its
data*, not of the grading logic, and they change when the underlying merge run
changes.

Encoding them as one evaluator per defect kind would mean a new evaluator
module, a new LangSmith metric and a new threshold every time a prompt is
added - and a metric with one example behind it is noise. So each prompt_set
row declares its own `traps`, this module scores whatever it declared, and
adding prompt 7 with a defect nobody has seen before is a dataset edit.

Scoring
-------
score = handled / (handled + missed)

`not_applicable` verdicts leave the denominator, so a trap whose data has
moved upstream shrinks the sample rather than failing the answer. A row where
every trap is not_applicable scores None, which the report renders as `n/a` -
and which should be read as "this prompt no longer tests anything", not as a
pass.
"""

HANDLED = "handled"
MISSED = "missed"
NOT_APPLICABLE = "not_applicable"


def score_traps(verdicts, declared_traps=None):
    """Returns
    {"score": float|None, "handled": [...], "missed": [...],
     "not_applicable": [...], "unjudged": [...]}.

    `declared_traps` is the row's own trap list. It is passed in so a trap the
    judge silently failed to return a verdict for is reported as `unjudged`
    rather than vanishing - a missing verdict and a handled one must never
    look the same.
    """
    by_kind = {}
    for verdict in verdicts or []:
        kind = verdict.get("kind") or "(unnamed)"
        by_kind[kind] = verdict

    buckets = {HANDLED: [], MISSED: [], NOT_APPLICABLE: []}
    for verdict in by_kind.values():
        bucket = verdict.get("verdict")
        if bucket in buckets:
            buckets[bucket].append({
                "kind": verdict.get("kind"),
                "evidence": verdict.get("evidence"),
            })

    declared_kinds = [t.get("kind") for t in (declared_traps or [])]
    unjudged = [k for k in declared_kinds if k not in by_kind]

    graded = len(buckets[HANDLED]) + len(buckets[MISSED])
    score = len(buckets[HANDLED]) / graded if graded else None

    return {
        "score": score,
        "handled": buckets[HANDLED],
        "missed": buckets[MISSED],
        "not_applicable": buckets[NOT_APPLICABLE],
        "unjudged": unjudged,
    }
