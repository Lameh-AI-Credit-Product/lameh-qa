"""
Lameh Intelligence - production-readiness report
==================================================
Reads a completed LangSmith experiment (produced by run_eval.py), aggregates
evaluator pass rates per dimension (correctness / helpfulness / safety),
sliced by sector and prompt type, applies configurable threshold gates
(config.THRESHOLDS), and renders a markdown report. Every example links back
to its LangSmith run (one-click trace debugging) and, when available, the
orchestrator's own conversation_id (the agent-side thread).

Usage
-----
    poetry run python tests/langsmith/report/build_report.py --experiment materials-sector-3a94b70b
"""

import argparse
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from langsmith import Client

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import (ALL_VALUES_TAGGED, ANSWER_COVERAGE, ANSWER_QUALITY, COMPANY_COVERAGE,  # noqa: E402
                     DATASET_NAME, EVALUATOR_KEYS, NO_FABRICATED_COMPANIES, NUMERIC_ACCURACY,
                     SECURITY, TAG_COMPLETENESS, THRESHOLDS)

DIMENSION_KEYS = EVALUATOR_KEYS

# Column headers for the breakdown tables - the evaluator keys, title-cased.
DIMENSION_LABELS = {
    NUMERIC_ACCURACY: "Numeric Accuracy",
    TAG_COMPLETENESS: "Tag Completeness",
    COMPANY_COVERAGE: "Company Coverage",
    NO_FABRICATED_COMPANIES: "No Fabricated Companies",
    ANSWER_COVERAGE: "Answer Coverage",
    ANSWER_QUALITY: "Answer Quality",
    SECURITY: "Security",
    ALL_VALUES_TAGGED: "All Values Tagged",
}

# Which dimensions gate release, and how. Everything else is reported but
# doesn't block: answer_coverage and no_fabricated_companies are diagnostic
# (a missing metric is a prompt-design question as often as an agent bug),
# while these five are "the output is wrong or unverifiable".
GATED_KEYS = (NUMERIC_ACCURACY, TAG_COMPLETENESS, COMPANY_COVERAGE, ALL_VALUES_TAGGED,
              ANSWER_QUALITY, SECURITY)


def fetch_experiment_results(experiment_name, client=None):
    """One row per dataset example evaluated in `experiment_name`: its
    scores/comments per evaluator key, its sector/prompt_type (from the
    dataset example's metadata), and links for debugging a failure."""
    client = client or Client()
    runs = list(client.list_runs(project_name=experiment_name, is_root=True))
    feedback_by_run = defaultdict(list)
    for fb in client.list_feedback(run_ids=[r.id for r in runs]):
        feedback_by_run[fb.run_id].append(fb)
    examples_by_id = {e.id: e for e in client.list_examples(dataset_name=DATASET_NAME)}

    rows = []
    for run in runs:
        example = examples_by_id.get(run.reference_example_id)
        metadata = (example.metadata if example else None) or {}
        scores = {fb.key: fb.score for fb in feedback_by_run.get(run.id, [])}
        comments = {fb.key: fb.comment for fb in feedback_by_run.get(run.id, [])}
        outputs = run.outputs or {}
        rows.append({
            "example_id": metadata.get("id", str(run.reference_example_id)),
            "sector": metadata.get("sector"),
            "prompt_type": metadata.get("prompt_type"),
            "scores": scores,
            "comments": comments,
            "conversation_id": outputs.get("conversation_id"),
            "run_url": run.url,
        })
    return rows


def _pass_rate(values):
    graded = [v for v in values if v is not None]
    return sum(graded) / len(graded) if graded else None


def aggregate(rows):
    """Pass rate per dimension, overall and sliced by sector/prompt_type.
    A dimension with no graded values anywhere in the slice is None (not
    0.0) - "no data" and "everything failed" must never look the same."""
    overall = {key: _pass_rate([r["scores"].get(key) for r in rows]) for key in DIMENSION_KEYS}

    def _sliced(group_key):
        buckets = defaultdict(lambda: defaultdict(list))
        for r in rows:
            for key in DIMENSION_KEYS:
                buckets[r[group_key]][key].append(r["scores"].get(key))
        return {group: {key: _pass_rate(vals) for key, vals in dims.items()} for group, dims in buckets.items()}

    return {"overall": overall, "by_sector": _sliced("sector"), "by_prompt_type": _sliced("prompt_type")}


def _gate(value, threshold, comparison_ok):
    if value is None:
        return {"value": None, "threshold": threshold, "status": "undetermined"}
    return {"value": value, "threshold": threshold, "status": "ready" if comparison_ok(value, threshold) else "not_ready"}


def apply_thresholds(overall, thresholds=THRESHOLDS):
    """Each gated dimension against its configured threshold, plus a single
    overall verdict.

    A dimension with no graded data anywhere in the run is "undetermined",
    never "ready" - a gate that nothing exercised has not been passed. One
    not_ready gate is enough to fail the run overall, since these are the
    dimensions where a failure means the output is wrong or unverifiable
    rather than merely thin."""
    gates = {key: _gate(overall.get(key), thresholds[key], lambda v, t: v >= t) for key in GATED_KEYS}
    statuses = {gates[key]["status"] for key in GATED_KEYS}
    if "not_ready" in statuses:
        failing = sorted(DIMENSION_LABELS[k] for k in GATED_KEYS if gates[k]["status"] == "not_ready")
        overall_status = f"not_ready ({', '.join(failing)})"
    elif "undetermined" in statuses:
        undetermined = sorted(DIMENSION_LABELS[k] for k in GATED_KEYS if gates[k]["status"] == "undetermined")
        overall_status = f"undetermined (no data for: {', '.join(undetermined)})"
    else:
        overall_status = "ready"
    gates["overall"] = overall_status
    return gates


def _fmt_pct(value):
    return f"{value * 100:.1f}%" if value is not None else "n/a"


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


def render_markdown(experiment_name, rows, aggregates, gates):
    lines = []
    lines.append(f"# Lameh Intelligence - Production Readiness Report")
    lines.append(f"\nExperiment: `{experiment_name}`  ")
    lines.append(f"Generated: {datetime.now(timezone.utc).isoformat(timespec='seconds')}  ")
    lines.append(f"Examples evaluated: {len(rows)}")

    lines.append("\n## Overall Readiness Gates\n")
    lines.append("| Dimension | Score | Threshold | Status |")
    lines.append("|---|---|---|---|")
    for key in GATED_KEYS:
        lines.append(_fmt_gate_row(DIMENSION_LABELS[key], gates[key]))
    lines.append(f"\n**Overall: {gates['overall']}**")

    header = " | ".join(DIMENSION_LABELS[k] for k in DIMENSION_KEYS)
    divider = "|---" * (len(DIMENSION_KEYS) + 1) + "|"

    for title, group_label, grouped in (
        ("By Sector", "Sector", aggregates["by_sector"]),
        ("By Prompt Type", "Prompt Type", aggregates["by_prompt_type"]),
    ):
        lines.append(f"\n## {title}\n")
        lines.append(f"| {group_label} | {header} |")
        lines.append(divider)
        for group, dims in sorted(grouped.items(), key=lambda kv: str(kv[0])):
            cells = " | ".join(_fmt_pct(dims.get(k)) for k in DIMENSION_KEYS)
            lines.append(f"| {group} | {cells} |")

    lines.append("\n## Per-Example Detail\n")
    for row in rows:
        lines.append(f"### {row['example_id']}")
        lines.append(f"- Sector: {row['sector']} | Prompt type: {row['prompt_type']}")
        for key in DIMENSION_KEYS:
            score = row["scores"].get(key)
            comment = row["comments"].get(key)
            marker = "PASS" if score == 1.0 else ("FAIL" if score == 0.0 else "n/a" if score is None else f"{score:.2f}")
            lines.extend(_detail_lines(DIMENSION_LABELS[key], marker, comment))
        lines.append(f"- LangSmith trace: {row['run_url']}")
        if row["conversation_id"]:
            lines.append(f"- Orchestrator conversation_id (thread): `{row['conversation_id']}`")
        lines.append("")

    return "\n".join(lines)


def build_report(experiment_name, client=None):
    rows = fetch_experiment_results(experiment_name, client=client)
    aggregates = aggregate(rows)
    gates = apply_thresholds(aggregates["overall"])
    return render_markdown(experiment_name, rows, aggregates, gates)


def default_out_path(experiment_name):
    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    return repo_root / "results" / "langsmith" / f"{experiment_name}.md"


def missing_feedback(rows, expected_example_count=None):
    """Which examples don't yet have every evaluator score recorded.

    Feedback is uploaded asynchronously and lands per-example, so an
    experiment queried too early looks like a *smaller* run rather than an
    unfinished one - the slowest examples are simply absent. The report must
    cover the whole run, so callers wait on this instead of silently
    rendering a partial one."""
    pending = [r["example_id"] for r in rows if not set(DIMENSION_KEYS) <= set(r["scores"])]
    if expected_example_count is not None and len(rows) < expected_example_count:
        pending.append(f"<{expected_example_count - len(rows)} example(s) not yet reported>")
    return pending


def write_report(experiment_name, out=None, client=None, expected_example_count=None,
                  wait_attempts=1, wait_delay_seconds=10):
    """Builds the report and writes it to disk, returning the output path.
    Shared by this script's CLI and by run_eval.py, which calls it directly
    once an experiment finishes so a run always leaves a report behind.

    With wait_attempts > 1, re-queries until every example in the run has all
    its evaluator scores, so the report always covers the whole run rather
    than whichever examples happened to finish uploading first."""
    client = client or Client()
    for attempt in range(wait_attempts):
        rows = fetch_experiment_results(experiment_name, client=client)
        pending = missing_feedback(rows, expected_example_count)
        if not pending or attempt == wait_attempts - 1:
            if pending:
                print(f"Warning: still waiting on scores for {pending} - reporting what's available.")
            break
        print(f"Waiting for evaluator scores on {pending} ...")
        time.sleep(wait_delay_seconds)

    aggregates = aggregate(rows)
    report_md = render_markdown(experiment_name, rows, aggregates, apply_thresholds(aggregates["overall"]))
    out_path = Path(out) if out else default_out_path(experiment_name)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report_md, encoding="utf-8")
    return out_path


def main():
    ap = argparse.ArgumentParser(description="Build the production-readiness report for a completed LangSmith experiment.")
    ap.add_argument("--experiment", required=True, help="LangSmith experiment name, e.g. materials-sector-3a94b70b")
    ap.add_argument("--out", default=None, help="Output markdown file path. Defaults to reports/<experiment>.md")
    args = ap.parse_args()

    print(f"Report written to {write_report(args.experiment, out=args.out)}")


if __name__ == "__main__":
    main()
