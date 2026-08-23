"""
Lameh Intelligence - production-readiness report
==================================================
Reads a completed LangSmith experiment (produced by a suite's run_eval.py),
aggregates evaluator pass rates per dimension, slices them by whatever
metadata that suite groups on, applies its threshold gates, and renders a
markdown report. Every example links back to its LangSmith run (one-click
trace debugging) and, when available, the orchestrator's own conversation_id
(the agent-side thread).

**Every entry point takes a `SuiteSpec`** (shared/suite.py) rather than
importing evaluator keys at module scope. That indirection is not decoration:
the two suites share exactly two evaluator keys (`security`,
`response_time_seconds`), and the FS suite's "By Sector" breakdown is
meaningless for a research suite whose every row is one company. Teaching one
report to branch on which suite it was called for would have meant a
conditional in a dozen formatters; it renders what the spec hands it instead.

Usage
-----
    poetry run python tests/langsmith/shared/report/build_report.py --suite fs \
        --experiment UAT-intelligence-fs-expert-3a94b70b
"""

import argparse
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from langsmith import Client

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from suites import SUITE_NAMES, load_suite  # noqa: E402


def fetch_latency(experiment_name, client=None):
    """The experiment's latency percentiles, straight from LangSmith.

    This replaces a `response_time_seconds` *evaluator* that used to measure
    the same thing itself. That was redundant and worse: LangSmith's per-run
    latency matched our own stream timing to within 0.1s, while our aggregate
    was a **mean** over a handful of examples where p50/p99 is the statistic
    anyone actually wants. It also forced a duration to masquerade as a 0-1
    score, which needed special-casing in every formatter and could not be
    gated on.

    Returns None if the session carries no stats, so a report over an
    experiment LangSmith has not finished summarising still renders.
    """
    client = client or Client()
    try:
        session = client.read_project(project_name=experiment_name, include_stats=True)
    except Exception:  # noqa: BLE001 - timing must never fail a report
        return None
    p50, p99 = getattr(session, "latency_p50", None), getattr(session, "latency_p99", None)
    if p50 is None and p99 is None:
        return None
    return {"p50_seconds": p50.total_seconds() if p50 else None,
            "p99_seconds": p99.total_seconds() if p99 else None,
            "run_count": getattr(session, "run_count", None),
            "error_rate": getattr(session, "error_rate", None)}


def fetch_experiment_results(spec, experiment_name, client=None):
    """One row per dataset example evaluated in `experiment_name`: its
    scores/comments per evaluator key, its sector/prompt_type (from the
    dataset example's metadata), and links for debugging a failure."""
    client = client or Client()
    runs = list(client.list_runs(project_name=experiment_name, is_root=True))
    feedback_by_run = defaultdict(list)
    for fb in client.list_feedback(run_ids=[r.id for r in runs]):
        feedback_by_run[fb.run_id].append(fb)
    examples_by_id = {e.id: e for e in client.list_examples(dataset_name=spec.dataset_name)}

    rows = []
    for run in runs:
        example = examples_by_id.get(run.reference_example_id)
        metadata = (example.metadata if example else None) or {}
        scores = {fb.key: fb.score for fb in feedback_by_run.get(run.id, [])}
        comments = {fb.key: fb.comment for fb in feedback_by_run.get(run.id, [])}
        outputs = run.outputs or {}
        rows.append({
            "example_id": metadata.get("id", str(run.reference_example_id)),
            # Kept whole rather than picked apart into named fields, so
            # spec.group_by can slice on any metadata a suite chooses without
            # this function knowing the field names. The FS suite groups on
            # sector and prompt_type; the research suite groups on prompt_type
            # and trap kind.
            "metadata": metadata,
            "scores": scores,
            "comments": comments,
            "conversation_id": outputs.get("conversation_id"),
            # Read from the run rather than from a feedback key: a deadline
            # kill returns normally from target(), so LangSmith's own latency
            # counts it as an ordinary slow answer. This is the flag that says
            # otherwise.
            "timed_out": bool(outputs.get("timed_out")),
            "completed": outputs.get("completed"),
            "ai_mode": outputs.get("ai_mode"),
            "max_concurrency": outputs.get("max_concurrency"),
            "run_url": run.url,
        })
    return rows


def run_conditions(rows):
    """The mode and concurrency the run was executed under, read off the runs
    themselves rather than assumed.

    Both exist for Response Time's sake: the score is only meaningful next to
    the mode it measured and the load it was measured under. Read as a set,
    so a run that somehow mixed either shows both values rather than silently
    reporting the first. Runs from before these were recorded have neither,
    and were all expert."""
    def _distinct(field):
        return sorted({str(r[field]) for r in rows if r.get(field) is not None})
    return {"ai_mode": _distinct("ai_mode"), "max_concurrency": _distinct("max_concurrency")}


def _pass_rate(values):
    graded = [v for v in values if v is not None]
    return sum(graded) / len(graded) if graded else None


def aggregate(spec, rows):
    """Pass rate per dimension, overall and sliced by each field in
    spec.group_by. A dimension with no graded values anywhere in the slice is
    None (not 0.0) - "no data" and "everything failed" must never look the
    same."""
    keys = spec.dimension_keys
    overall = {key: _pass_rate([r["scores"].get(key) for r in rows]) for key in keys}

    def _sliced(field):
        buckets = defaultdict(lambda: defaultdict(list))
        for r in rows:
            value = r["metadata"].get(field)
            # A list-valued field (research rows carry a list of trap kinds)
            # puts the example in every bucket it belongs to, so a prompt
            # setting two traps is counted under both rather than under the
            # stringified list.
            for bucket in (value if isinstance(value, list) else [value]):
                for key in keys:
                    buckets[bucket][key].append(r["scores"].get(key))
        return {group: {key: _pass_rate(vals) for key, vals in dims.items()}
                for group, dims in buckets.items()}

    return {"overall": overall,
            "grouped": {field: _sliced(field) for _, _, field in spec.group_by}}


def _gate(value, threshold, comparison_ok):
    if value is None:
        return {"value": None, "threshold": threshold, "status": "undetermined"}
    return {"value": value, "threshold": threshold, "status": "ready" if comparison_ok(value, threshold) else "not_ready"}


def apply_thresholds(spec, overall, thresholds=None):
    """Each gated dimension against its configured threshold, plus a single
    overall verdict.

    A dimension with no graded data anywhere in the run is "undetermined",
    never "ready" - a gate that nothing exercised has not been passed. One
    not_ready gate is enough to fail the run overall, since these are the
    dimensions where a failure means the output is wrong or unverifiable
    rather than merely thin."""
    thresholds = spec.thresholds if thresholds is None else thresholds
    gated = spec.gated_keys
    gates = {key: _gate(overall.get(key), thresholds[key], lambda v, t: v >= t) for key in gated}
    statuses = {gates[key]["status"] for key in gated}
    if "not_ready" in statuses:
        failing = sorted(spec.dimension_labels[k] for k in gated if gates[k]["status"] == "not_ready")
        overall_status = f"not_ready ({', '.join(failing)})"
    elif "undetermined" in statuses:
        undetermined = sorted(spec.dimension_labels[k] for k in gated if gates[k]["status"] == "undetermined")
        overall_status = f"undetermined (no data for: {', '.join(undetermined)})"
    else:
        overall_status = "ready"
    gates["overall"] = overall_status
    return gates


def _fmt_pct(value):
    return f"{value * 100:.1f}%" if value is not None else "n/a"


def _fmt_seconds(value):
    return f"{value:.0f}s" if value is not None else "n/a"


def _fmt_dimension(spec, key, value):
    """A dimension's value in its own units - percent for the rates, seconds
    for the durations."""
    return _fmt_seconds(value) if key in spec.seconds_keys else _fmt_pct(value)


def _fmt_gate_row(name, gate):
    threshold_str = f"{gate['threshold'] * 100:.0f}%" if isinstance(gate["threshold"], float) else str(gate["threshold"])
    return f"| {name} | {_fmt_pct(gate['value'])} | {threshold_str} | {gate['status']} |"


def _detail_lines(label, marker, comment):
    """One evaluator's line in the per-example section.

    Comments are formatted by comment_format.py as a headline plus "- "
    detail lines (see that module for why they aren't JSON). The headline
    goes inline after the score; the rest are indented two spaces so they
    render as nested bullets rather than collapsing into one paragraph."""
    if not comment:
        return [f"- **{label}**: {marker}"]
    headline, *details = comment.splitlines()
    return [f"- **{label}**: {marker} — {headline}"] + [f"  {line}" for line in details]


def render_markdown(spec, experiment_name, rows, aggregates, gates, latency=None):
    keys = spec.dimension_keys
    labels = spec.dimension_labels
    lines = []
    lines.append(f"# Lameh Intelligence ({spec.title}) - Production Readiness Report")
    lines.append(f"\nExperiment: `{experiment_name}`  ")
    lines.append(f"Generated: {datetime.now(timezone.utc).isoformat(timespec='seconds')}  ")
    lines.append(f"Examples evaluated: {len(rows)}  ")

    conditions = run_conditions(rows)
    if conditions["ai_mode"]:
        lines.append(f"AI mode: `{', '.join(conditions['ai_mode'])}`  ")
    if conditions["max_concurrency"]:
        lines.append(f"Concurrency: {', '.join(conditions['max_concurrency'])} prompts at once  ")
    if latency:
        p50 = _fmt_seconds(latency.get("p50_seconds"))
        p99 = _fmt_seconds(latency.get("p99_seconds"))
        lines.append(f"Response time: p50 {p50} / p99 {p99} "
                      f"- measured with the whole set in flight, so it compares against "
                      f"another run at the same concurrency, not as an absolute latency  ")
    cut_off = [r["example_id"] for r in rows if r.get("timed_out")]
    if cut_off:
        lines.append(f"**Cut off at the deadline: {', '.join(cut_off)}** - their latency is a "
                      f"lower bound, and answer_coverage is failed for them  ")

    lines.append("\n## Overall Readiness Gates\n")
    lines.append("| Dimension | Score | Threshold | Status |")
    lines.append("|---|---|---|---|")
    for key in spec.gated_keys:
        lines.append(_fmt_gate_row(labels[key], gates[key]))
    lines.append(f"\n**Overall: {gates['overall']}**")

    header = " | ".join(labels[k] for k in keys)
    divider = "|---" * (len(keys) + 1) + "|"

    for title, group_label, field in spec.group_by:
        lines.append(f"\n## {title}\n")
        lines.append(f"| {group_label} | {header} |")
        lines.append(divider)
        for group, dims in sorted(aggregates["grouped"][field].items(), key=lambda kv: str(kv[0])):
            cells = " | ".join(_fmt_dimension(spec, k, dims.get(k)) for k in keys)
            lines.append(f"| {group} | {cells} |")

    lines.append("\n## Per-Example Detail\n")
    for row in rows:
        lines.append(f"### {row['example_id']}")
        context = " | ".join(f"{label}: {row['metadata'].get(field)}"
                             for _, label, field in spec.group_by)
        if context:
            lines.append(f"- {context}")
        for key in keys:
            score = row["scores"].get(key)
            comment = row["comments"].get(key)
            if key in spec.seconds_keys:
                # A duration has no pass mark, and 1.0 second must not print
                # as "PASS".
                marker = _fmt_seconds(score)
            else:
                marker = ("PASS" if score == 1.0 else "FAIL" if score == 0.0
                          else "n/a" if score is None else f"{score:.2f}")
            lines.extend(_detail_lines(labels[key], marker, comment))
        lines.append(f"- LangSmith trace: {row['run_url']}")
        if row["conversation_id"]:
            lines.append(f"- Orchestrator conversation_id (thread): `{row['conversation_id']}`")
        lines.append("")

    return "\n".join(lines)


def build_report(spec, experiment_name, client=None):
    client = client or Client()
    rows = fetch_experiment_results(spec, experiment_name, client=client)
    aggregates = aggregate(spec, rows)
    gates = apply_thresholds(spec, aggregates["overall"])
    return render_markdown(spec, experiment_name, rows, aggregates, gates,
                            fetch_latency(experiment_name, client=client))


def default_out_path(experiment_name):
    # .../tests/langsmith/shared/report/build_report.py -> repo root is five up.
    repo_root = Path(__file__).resolve().parents[4]
    return repo_root / "results" / "langsmith" / f"{experiment_name}.md"


def missing_feedback(spec, rows, expected_example_count=None):
    """Which examples don't yet have every evaluator score recorded.

    Feedback is uploaded asynchronously and lands per-example, so an
    experiment queried too early looks like a *smaller* run rather than an
    unfinished one - the slowest examples are simply absent. The report must
    cover the whole run, so callers wait on this instead of silently
    rendering a partial one."""
    pending = [r["example_id"] for r in rows if not set(spec.dimension_keys) <= set(r["scores"])]
    if expected_example_count is not None and len(rows) < expected_example_count:
        pending.append(f"<{expected_example_count - len(rows)} example(s) not yet reported>")
    return pending


def write_report(spec, experiment_name, out=None, client=None, expected_example_count=None,
                  wait_attempts=1, wait_delay_seconds=10):
    """Builds the report and writes it to disk, returning the output path.
    Shared by this script's CLI and by run_eval.py, which calls it directly
    once an experiment finishes so a run always leaves a report behind.

    With wait_attempts > 1, re-queries until every example in the run has all
    its evaluator scores, so the report always covers the whole run rather
    than whichever examples happened to finish uploading first."""
    client = client or Client()
    for attempt in range(wait_attempts):
        rows = fetch_experiment_results(spec, experiment_name, client=client)
        pending = missing_feedback(spec, rows, expected_example_count)
        if not pending or attempt == wait_attempts - 1:
            if pending:
                print(f"Warning: still waiting on scores for {pending} - reporting what's available.")
            break
        print(f"Waiting for evaluator scores on {pending} ...")
        time.sleep(wait_delay_seconds)

    aggregates = aggregate(spec, rows)
    report_md = render_markdown(spec, experiment_name, rows, aggregates,
                                 apply_thresholds(spec, aggregates["overall"]),
                                 fetch_latency(experiment_name, client=client))
    out_path = Path(out) if out else default_out_path(experiment_name)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report_md, encoding="utf-8")
    return out_path


def main():
    ap = argparse.ArgumentParser(description="Build the production-readiness report for a completed LangSmith experiment.")
    ap.add_argument("--suite", required=True, choices=SUITE_NAMES,
                     help="Which eval suite the experiment belongs to. Decides the dataset the "
                          "examples are looked up in and the evaluator columns rendered.")
    ap.add_argument("--experiment", required=True,
                     help="LangSmith experiment name, e.g. UAT-intelligence-fs-expert-3a94b70b")
    ap.add_argument("--out", default=None, help="Output markdown file path. Defaults to reports/<experiment>.md")
    args = ap.parse_args()

    print(f"Report written to {write_report(load_suite(args.suite), args.experiment, out=args.out)}")


if __name__ == "__main__":
    main()
