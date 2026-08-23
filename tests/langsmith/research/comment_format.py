"""
Lameh Intelligence (research) - feedback comments
==================================================
Renders each evaluator's findings as the plain-text comment attached to its
LangSmith feedback.

Plain text, not JSON, for the reason the FS suite learned the hard way:
LangSmith renders a comment as a string, so a json.dumps blob shows up escaped
and unreadable. The shape is one headline line followed by "- " detail lines;
build_report._detail_lines relies on that to nest them under the score.
Nothing parses these back, so they are free to change.
"""

MAX_COMMENT_ITEMS = 12


def _lines(headline, items):
    return "\n".join([headline] + [f"- {item}" for item in items[:MAX_COMMENT_ITEMS]]
                     + ([f"- ... and {len(items) - MAX_COMMENT_ITEMS} more"]
                        if len(items) > MAX_COMMENT_ITEMS else []))


def attribution_comment(result):
    if result["total"] == 0:
        return "No tagged figures in this answer - nothing to attribute."
    headline = f"{result['complete']}/{result['total']} tagged figures carry a resolvable citation."
    return _lines(headline, [f"{f['figure']} - missing {', '.join(f['missing'])}"
                             for f in result["findings"]])


def coverage_comment(result):
    if result["score"] is None:
        return "No fiscal years declared for this prompt."
    headline = (f"{len(result['years_covered'])}/"
                f"{len(result['years_covered']) + len(result['years_missing'])} "
                f"fiscal years mentioned.")
    items = []
    if result["years_missing"]:
        items.append(f"Not mentioned: {', '.join(result['years_missing'])}")
    items += [f"expected fact: {fact}" for fact in result["facts_expected"]]
    return _lines(headline, items)


def traps_comment(result, source_run=None):
    if result["score"] is None:
        return "No trap was gradeable on this answer - read as untested, not as a pass."
    handled, missed = result["handled"], result["missed"]
    headline = f"{len(handled)}/{len(handled) + len(missed)} planted defects handled."
    items = [f"MISSED [{m['kind']}]: {m['evidence']}" for m in missed]
    items += [f"handled [{h['kind']}]" for h in handled]
    items += [f"not applicable [{na['kind']}] - the data behind this trap has moved"
              for na in result["not_applicable"]]
    items += [f"no verdict returned for [{kind}]" for kind in result["unjudged"]]
    if source_run:
        # Which snapshot this verdict was reached against. The ground truth is
        # frozen, so this should be identical across a run and across runs -
        # if it ever isn't, that is the finding.
        items.append(f"graded against frozen merge_run_id={source_run.get('merge_run_id')} "
                     f"({source_run.get('table_count')} tables, "
                     f"frozen {source_run.get('frozen_at')})")
    return _lines(headline, items)


def hallucination_comment(findings):
    if not findings:
        return "No unsupported claims found."
    return _lines(f"{len(findings)} unsupported claim(s).",
                  [f"[{f.get('kind')}] {f.get('claim')} - {f.get('why')}" for f in findings])


def quality_comment(verdict):
    met = [k for k in ("answers_question", "sourced", "usable") if verdict.get(k) == 1]
    unmet = [k for k in ("answers_question", "sourced", "usable") if verdict.get(k) != 1]
    headline = f"{len(met)}/3 quality criteria met."
    items = [f"not met: {k}" for k in unmet]
    if verdict.get("notes"):
        items.append(str(verdict["notes"]))
    return _lines(headline, items)


def security_comment(findings):
    if not findings:
        return "No security findings."
    return _lines(f"{len(findings)} security finding(s).",
                  [f"[{f.get('kind')}] {f.get('quote')} - {f.get('why')}" for f in findings])


def judge_error_comment(dimension, exc):
    """A judge failure must shrink the sample, not fail the run - so the score
    is None and this says why. Truncation and non-JSON output are named
    separately: conflating them sent one FS investigation down the wrong path
    already."""
    detail = str(exc)
    if "Unterminated" in detail or "Expecting" in detail:
        reason = "judge reply was truncated or not valid JSON (token ceiling?)"
    else:
        reason = f"judge call failed: {type(exc).__name__}"
    return f"{dimension} not scored - {reason}. Detail: {detail[:300]}"
