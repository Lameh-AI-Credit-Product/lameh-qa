"""
Lameh Intelligence - evaluator comment formatting
==================================================
Turns each evaluator's findings into the `comment` string attached to its
LangSmith feedback.

These were `json.dumps(...)` blobs. LangSmith stores a comment as a plain
string and renders it as one, so the dashboard showed an escaped one-line
object - `{\"tagged\": 206, \"untagged\": 40, ...}` - and the markdown report
inlined that same blob into a bullet. Both were technically complete and
practically unreadable, which defeats the point of a comment: it exists to be
read at a glance next to the score.

Nothing parses these back. `build_report` reads the score and displays the
comment verbatim; no code path consumes the structure. So the format is
optimised purely for reading, and the full detail is always reconstructible
from the run's own output text.

Shape, uniformly:

    <headline: the counts, one line>
    - <detail>
    - <detail>
    - ... and N more

Line 0 is the summary and every later line starts with "- ", which is what
lets build_report indent them into nested markdown bullets under the score
while the raw string still reads fine in the dashboard.
"""

# Long comments are truncated by the LangSmith UI, and a 60-tag response can
# produce a defect list longer than the answer itself. Every comment leads
# with its counts and aggregates so the useful part survives the cut.
MAX_COMMENT_ITEMS = 15


def _num(value):
    """A figure a human can compare at a glance. Thousands separators matter
    here: the difference between -24724966000 and -345652000 is invisible
    without them, and telling those apart is the entire point of the line."""
    if value is None:
        return "n/a"
    if not isinstance(value, (int, float)):
        return str(value)
    magnitude = abs(value)
    if magnitude >= 1000:
        return f"{value:,.0f}"
    if magnitude >= 1:
        return f"{value:,.2f}"
    return f"{value:.4g}"


def _pct(fraction, decimals=1):
    return "n/a" if fraction is None else f"{fraction * 100:.{decimals}f}%"


def _listing(items, render, limit=MAX_COMMENT_ITEMS):
    lines = [f"- {render(item)}" for item in items[:limit]]
    if len(items) > limit:
        lines.append(f"- ... and {len(items) - limit} more")
    return lines


def _counts(mapping, limit=None):
    """{"ops.id": 12, "rid": 9} -> "ops.id x12, rid x9"."""
    items = list(mapping.items())
    shown = items if limit is None else items[:limit]
    text = ", ".join(f"{name} x{count}" for name, count in shown)
    if limit is not None and len(items) > limit:
        text += f", ... and {len(items) - limit} more"
    return text


def _plural(count, singular, plural=None):
    return singular if count == 1 else (plural or singular + "s")


def _joined(lines):
    return "\n".join(line for line in lines if line)


# --- numeric_accuracy -------------------------------------------------------

def _metric_label(comparison):
    """A skipped comparison's metric, qualified by section when it isn't the
    default - "Revenue" means something different in each statement."""
    metric = comparison.get("metric") or "(unnamed)"
    section = comparison.get("section")
    return metric if section in (None, "Financial Ratios") else f"{metric} [{section}]"


def _tally(comparisons):
    tally = {}
    for comparison in comparisons:
        label = _metric_label(comparison)
        tally[label] = tally.get(label, 0) + 1
    return dict(sorted(tally.items(), key=lambda kv: (-kv[1], kv[0])))


def _unresolved_lines(graded, skipped):
    """Splits the skipped comparisons into the two cases that need different
    responses.

    A metric that resolved for *some* company in this response and not others
    is ordinary missing data - that company genuinely has no value for it.

    A metric that resolved for *nobody* is a name chart-data doesn't know, and
    that is worth surfacing by name, because the endpoint gives no other
    signal: it slugifies and echoes back any attribute name you send it, so a
    renamed ratio and an absent value are identical from here - both just
    return None and drop out of the score. Ratios really have been renamed
    app-side while chart-data kept the old names (CFO Finance Cost Coverage ->
    CFO Interest Coverage, and others), and the only symptom was this count
    going up while the score went up with it."""
    resolved = {_metric_label(c) for c in graded}
    never = _tally([c for c in skipped if _metric_label(c) not in resolved])
    partial = _tally([c for c in skipped if _metric_label(c) in resolved])

    lines = []
    if never:
        lines.append(f"- never resolved for any company here - suspect a renamed or "
                      f"unknown attribute: {_counts(never, limit=MAX_COMMENT_ITEMS)}")
    if partial:
        lines.append(f"- resolved for other companies, absent for these: "
                      f"{_counts(partial, limit=MAX_COMMENT_ITEMS)}")
    return lines


def numeric_accuracy(graded, failures, skipped, by_kind):
    passed = len(graded) - len(failures)
    headline = f"{passed}/{len(graded)} values match ground truth"
    if failures:
        headline += f" - {len(failures)} wrong"

    lines = [headline]
    if by_kind:
        lines.append("- by kind: " + ", ".join(
            f"{kind} {counts['passed']}/{counts['total']}" for kind, counts in sorted(by_kind.items())))
    if skipped:
        lines.append(f"- {len(skipped)} skipped as unresolvable - excluded from the score "
                      f"rather than counted wrong, so this number rising means less was checked")
        lines.extend(_unresolved_lines(graded, skipped))

    def _failure(comparison):
        return (f"{comparison.get('kind')} | {comparison.get('company')} | {comparison.get('metric')}"
                f" FY{comparison.get('fiscal_year')}: said {_num(comparison.get('actual'))}, "
                f"expected {_num(comparison.get('expected'))} (off by {_pct(comparison.get('mape'))})")

    return _joined(lines + _listing(failures, _failure))


def numeric_accuracy_unresolvable(comparisons):
    """Nothing in the response could be looked up. The metric names are the
    whole diagnosis here, so they lead - "693 facts, none resolvable" said
    only that something was wrong, never what."""
    lines = [f"not graded: {len(comparisons)} facts extracted, none resolvable against "
             f"ground truth - chart-data has no attribute by these names",
             f"- {_counts(_tally(comparisons), limit=MAX_COMMENT_ITEMS)}"]
    return _joined(lines)


# --- tag_completeness -------------------------------------------------------

def tag_completeness(result, ungradeable_count):
    incomplete = result["incomplete"]
    lines = [f"{result['complete_tags']}/{result['total_tags']} tags carry every field needed to "
             f"verify them - {len(incomplete)} incomplete"]

    if result["by_kind"]:
        lines.append("- by kind: " + ", ".join(
            f"{kind} {counts['total'] - counts['incomplete']}/{counts['total']}"
            for kind, counts in sorted(result["by_kind"].items())))
    if result["missing_field_counts"]:
        lines.append("- missing fields: " + _counts(result["missing_field_counts"]))
    if result["derived_components"]:
        lines.append(f"- {result['derived_components']} ops components have no id "
                      f"(derived values the agent computed itself)")
    if ungradeable_count:
        lines.append(f"- {ungradeable_count} facts stay ungradeable even after back-filling "
                      f"- nothing can look these up")

    def _defect(defect):
        # The ops field names already carry an "ops." prefix, so they don't
        # need a second label - "missing ops: ops.id x1" said it twice.
        missing = list(defect["missing_attrs"])
        if defect["missing_ops_fields"]:
            missing.append(_counts(defect["missing_ops_fields"]))
        return (f"line {defect['line']} | {defect['kind']} | {defect['label']} = {defect['value']}"
                f" | missing {', '.join(missing)}")

    return _joined(lines + _listing(incomplete, _defect))


def tag_completeness_untagged():
    return ("no <calc>/<number> tags at all in this response "
            "- see all_values_tagged for whether it should have had any")


# --- company_coverage -------------------------------------------------------

def company_coverage(result, source):
    covered, expected = len(result["covered"]), len(result["expected"])
    lines = [f"{covered}/{expected} companies covered (expected from: {source})"]
    if result["missing"]:
        lines.append(f"- {len(result['missing'])} missing:")
        lines.extend(_listing(result["missing"], str))
    if result["extra"]:
        lines.append(f"- {len(result['extra'])} companies present that weren't asked for")
    return _joined(lines)


def company_coverage_not_applicable(source):
    return f"not graded: no companies expected for this row ({source})"


# --- no_fabricated_companies ------------------------------------------------

def no_fabricated_companies(flagged, named_count):
    if not flagged:
        return f"all {named_count} companies named are in the sector roster"
    lines = [f"{len(flagged)} of {named_count} companies named "
             f"{_plural(len(flagged), 'is', 'are')} not in the sector roster "
             f"- check the roster is current before treating this as fabrication"]
    return _joined(lines + _listing(flagged, lambda f: f["company"]))


# --- answer_coverage --------------------------------------------------------

def answer_coverage(result, metric_source):
    scores = result["dimension_scores"]
    graded = {name: score for name, score in scores.items() if score is not None}
    headline = ("covers everything the prompt asked for" if result["complete"]
                else "does not cover everything the prompt asked for")
    lines = [headline]
    if graded:
        lines.append("- " + ", ".join(f"{name.replace('_', ' ')} {_pct(score)}"
                                       for name, score in sorted(graded.items())))
    for label, missing in (("companies", result["missing_companies"]),
                            ("metrics", result["missing_metrics"]),
                            ("fiscal years", result["missing_fiscal_years"])):
        if missing:
            lines.append(f"- missing {label}: " + ", ".join(str(m) for m in missing[:MAX_COMMENT_ITEMS])
                          + (f" ... and {len(missing) - MAX_COMMENT_ITEMS} more"
                             if len(missing) > MAX_COMMENT_ITEMS else ""))
    if result.get("truncated"):
        lines.append(f"- the response was cut off"
                      + (f" after {result['timed_out_after_seconds']}s"
                         if result.get("timed_out_after_seconds") else ""))
    lines.append(f"- metric presence checked by: {metric_source}")
    return _joined(lines)


# --- security ---------------------------------------------------------------

def security(violations):
    if not violations:
        return "no security findings"
    lines = [f"{len(violations)} security {_plural(len(violations), 'finding')}"]
    return _joined(lines + _listing(
        violations, lambda v: f"{v.get('type')}: {v.get('evidence')}"))


# --- all_values_tagged ------------------------------------------------------

def all_values_tagged(tagged, untagged_total, findings):
    total = tagged + untagged_total
    lines = [f"{tagged}/{total} figures carry a provenance tag - {untagged_total} stated with no tag"]

    by_statement = {}
    for finding in findings:
        statement = finding.get("statement") or "unspecified"
        by_statement[statement] = by_statement.get(statement, 0) + 1
    if by_statement:
        lines.append(f"- sample of {len(findings)} untagged, by statement: "
                      + _counts(dict(sorted(by_statement.items(), key=lambda kv: -kv[1]))))

    def _finding(finding):
        # Context is usually a markdown table row, so it already starts with
        # "|" - hence "in:" rather than another pipe separator.
        return f"{finding.get('value')} ({finding.get('statement')}) in: {finding.get('context')}"

    return _joined(lines + _listing(findings, _finding))


def all_values_tagged_no_figures():
    return "not graded: the response states no financial figures at all"


# --- response_time_seconds --------------------------------------------------

def _duration(seconds):
    """Seconds, plus minutes once they stop being countable at a glance -
    "487.2s (8m 07s)". Prompts here run 7-10 minutes, and nobody reads 487
    as eight minutes without doing the division."""
    if seconds < 90:
        return f"{seconds:.1f}s"
    return f"{seconds:.1f}s ({int(seconds) // 60}m {int(seconds) % 60:02d}s)"


def response_time(elapsed, ai_mode, max_concurrency, timed_out=False, completed=True):
    lines = [_duration(elapsed) + (f" in {ai_mode} mode" if ai_mode else "")]
    if timed_out:
        lines.append("- cut off at the deadline: this is a lower bound on the real "
                      "response time, not a measurement of it")
    elif not completed:
        lines.append("- the stream ended without a message_complete event, so the answer "
                      "is truncated and this is the time to the cut-off")
    if max_concurrency and max_concurrency > 1:
        # Without this the number reads as a per-prompt latency, which it is
        # not: at the default concurrency the whole dataset is in flight at
        # once and every timing includes the queueing that causes.
        lines.append(f"- measured with {max_concurrency} prompts running at once - comparable "
                      f"to another run at the same concurrency, not an absolute latency")
    return _joined(lines)


def response_time_unmeasured():
    return "not graded: the run recorded no elapsed_seconds"


# --- judge failures ---------------------------------------------------------

def judge_error(verdict):
    """Says *how* the judge failed. "Truncated" means the reply was cut off
    mid-JSON - a token-ceiling problem with a specific fix - while anything
    else means it answered with something that wasn't JSON. Reporting both as
    "invalid JSON" sent the first full run's investigation down the wrong
    path once already."""
    if verdict.get("truncated"):
        return ("not graded: the judge's reply was cut off mid-JSON "
                "- raise judge_client.DEFAULT_MAX_TOKENS or lower llm_judge.MAX_FINDINGS")
    return (f"not graded: the judge did not return valid JSON "
            f"- {str(verdict.get('raw_response'))[:200]!r}")
