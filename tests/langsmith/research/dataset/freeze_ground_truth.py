"""
Lameh Intelligence (research) - freeze the board-analysis ground truth
=======================================================================
Pulls `/v0/board-analysis/merge/tables` once and writes `ground_truth.json`
beside this script: the flattened source rows the evaluators grade against,
plus a compact per-example evidence block that ships to LangSmith as the
dataset's reference outputs.

Why the ground truth is frozen rather than fetched per run
-----------------------------------------------------------
It was live-fetched at first, on the reasoning that a merge run is not stable
so a snapshot would rot. That had it backwards. **An eval whose ground truth
can move underneath it cannot tell an agent regression from data drift** - a
score that dropped between two runs gives you no way to know which changed,
which is exactly the question an eval exists to answer.

Freezing buys four things:

- **Determinism.** A score change means the agent changed. Nothing else can.
- **Reviewability.** The ground truth is a committed file; a diff shows
  precisely what moved and when.
- **Visibility.** The frozen evidence is attached to each LangSmith example as
  its reference output, so the dashboard shows what an answer was graded
  against without anyone re-running a query.
- **Speed.** No 6.3 MB fetch per run.

The cost is staleness, and it is handled rather than ignored: `check_drift()`
compares the frozen merge run against the live one, `run_eval` calls it at
startup and prints a loud warning on mismatch, and re-freezing is this one
command. Staleness becomes a visible, dated event instead of a silent
divergence.

Usage
-----
    poetry run poe langsmith-research-freeze          # refresh the snapshot
    poetry run poe langsmith-research-freeze --check  # drift check only, no write
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_RESEARCH = _HERE.parent
sys.path.insert(0, str(_RESEARCH.parent / "shared"))
sys.path.insert(0, str(_RESEARCH))
sys.path.insert(0, str(_RESEARCH / "evaluators"))

from ground_truth import (BoardAnalysisClient, fact_terms, flatten,  # noqa: E402
                           locator_terms, run_metadata, search_any)

GROUND_TRUTH_PATH = _HERE / "ground_truth.json"
PROMPT_SET_PATH = _HERE / "prompt_set.json"

# How many rows back one trap in the dashboard evidence block. Small on
# purpose: this is the "show your working" view a human reads next to a failed
# example, not the full excerpt the judge gets. Ten rows fits on a screen.
EVIDENCE_ROWS_PER_TRAP = 10


def load_prompt_set(path=PROMPT_SET_PATH):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def build_snapshot(client=None, prompt_set=None):
    """Fetches once and returns the whole frozen structure."""
    client = client or BoardAnalysisClient()
    prompt_set = prompt_set or load_prompt_set()

    companies = {}
    for row in prompt_set:
        name = row.get("company_ar") or row.get("company")
        if name not in companies:
            payload = client.fetch(name)
            companies[name] = {"meta": run_metadata(payload), "rows": flatten(payload)}

    examples = {}
    for row in prompt_set:
        name = row.get("company_ar") or row.get("company")
        rows = companies[name]["rows"]
        # Per-trap evidence: the rows that trap's own locator resolves to.
        # Stored per trap rather than as one blob so a dashboard reader can see
        # which source backs which planted defect.
        evidence = {}
        for trap in row.get("traps") or []:
            kind = trap.get("kind")
            hits = search_any(rows, locator_terms(trap.get("locator")),
                              limit=EVIDENCE_ROWS_PER_TRAP, interleave=True)
            evidence[kind] = [
                {"section": h["section"], "table": h["title"], "row": h["row"],
                 "cells": [[p, v] for p, v in h["cells"]]}
                for h in hits
            ]
        examples[row["id"]] = {
            "company": name,
            "merge_run_id": companies[name]["meta"]["merge_run_id"],
            "facts_expected": row.get("facts_expected") or [],
            "trap_evidence": evidence,
        }

    return {
        "frozen_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "companies": {name: data["meta"] for name, data in companies.items()},
        "rows": {name: data["rows"] for name, data in companies.items()},
        "examples": examples,
    }


def load_snapshot(path=GROUND_TRUTH_PATH):
    if not path.exists():
        raise FileNotFoundError(
            f"No frozen ground truth at {path}. Run: poetry run poe langsmith-research-freeze")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def check_drift(snapshot=None, client=None):
    """Frozen merge run vs live, per company.

    Returns a list of human-readable drift lines - empty when the snapshot
    still matches the source. Never raises on a network failure: a ground
    truth that is frozen precisely so runs don't depend on the API must not
    then fail the run when the API is down. An unreachable endpoint is
    reported as unknown, not as drift."""
    snapshot = snapshot or load_snapshot()
    client = client or BoardAnalysisClient()
    drift = []
    for name, frozen in (snapshot.get("companies") or {}).items():
        try:
            live = run_metadata(client.fetch(name))
        except Exception as exc:  # noqa: BLE001
            drift.append(f"{name}: could not check drift ({type(exc).__name__}: {exc})")
            continue
        if live.get("merge_run_id") != frozen.get("merge_run_id"):
            drift.append(
                f"{name}: frozen merge_run_id={frozen.get('merge_run_id')} "
                f"({frozen.get('table_count')} tables) but live is "
                f"{live.get('merge_run_id')} ({live.get('table_count')} tables). "
                f"Re-freeze with: poetry run poe langsmith-research-freeze")
        elif live.get("table_count") != frozen.get("table_count"):
            drift.append(
                f"{name}: same merge run but table count moved "
                f"{frozen.get('table_count')} -> {live.get('table_count')}")
    return drift


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Freeze the board-analysis ground truth.")
    ap.add_argument("--check", action="store_true",
                     help="Report drift between the frozen snapshot and the live merge run, "
                          "then exit without writing.")
    args = ap.parse_args()

    if args.check:
        drift = check_drift()
        if not drift:
            snapshot = load_snapshot()
            print(f"Ground truth is current (frozen {snapshot['frozen_at']}).")
            for name, meta in snapshot["companies"].items():
                print(f"  {name}: {meta['merge_run_id']} ({meta['table_count']} tables)")
            return
        print("GROUND TRUTH DRIFT:")
        for line in drift:
            print(f"  {line}")
        sys.exit(1)

    snapshot = build_snapshot()
    GROUND_TRUTH_PATH.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=1), encoding="utf-8")
    total_rows = sum(len(r) for r in snapshot["rows"].values())
    size_mb = GROUND_TRUTH_PATH.stat().st_size / 1e6
    print(f"Wrote {GROUND_TRUTH_PATH} ({size_mb:.2f} MB, {total_rows} rows, "
          f"{len(snapshot['examples'])} examples)")
    for name, meta in snapshot["companies"].items():
        print(f"  {name}: {meta['merge_run_id']} ({meta['table_count']} tables)")
    print("\nNow sync the dataset so the dashboard shows it: "
          "poetry run poe langsmith-research-build-dataset")


if __name__ == "__main__":
    main()
