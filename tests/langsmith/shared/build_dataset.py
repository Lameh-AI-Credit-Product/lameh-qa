"""
Lameh Intelligence - LangSmith dataset builder
===============================================
Converts a suite's prompt_set.json (one example per question) into that
suite's LangSmith dataset. Shared by both suites - the only thing that differs
between them is the dataset name and the prompt-set file, and both come off
the SuiteSpec.

No ground-truth values are stored here - by design. Ground truth is always
fetched live at eval-run time (fs/evaluators/ground_truth.py against
chart-data/batch, research/evaluators/ground_truth.py against
board-analysis/merge/tables), so the dataset carries only prompt text plus
metadata describing what a passing response should cover.

Adding a prompt is adding a row to that suite's prompt_set.json - this script
does not need to change. Adding a *suite* means a new SuiteSpec in
shared/suites.py.

Usage
-----
    poetry run python tests/langsmith/shared/build_dataset.py --suite fs
    poetry run python tests/langsmith/shared/build_dataset.py --suite research
"""

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv
from langsmith import Client

sys.path.insert(0, str(Path(__file__).resolve().parent))
from suites import SUITE_NAMES, load_suite  # noqa: E402


def load_prompt_set(path):
    with open(path, encoding="utf-8") as f:
        rows = json.load(f)
    ids = [row["id"] for row in rows]
    duplicates = {i for i in ids if ids.count(i) > 1}
    if duplicates:
        raise ValueError(f"Duplicate prompt ids in {path}: {duplicates}")
    return rows


GROUND_TRUTH_FILENAME = "ground_truth.json"


def load_ground_truth(prompt_set_path):
    """A suite's frozen ground truth, if it has one.

    Optional by design: the FS suite fetches ground truth live at eval time
    from a database that is the system of record, so it has no snapshot to
    ship. The research suite grades against a committed file and wants that
    file visible in the dashboard. One rule covers both - if
    `ground_truth.json` sits beside the prompt set, its per-example entries
    become the examples' reference outputs.
    """
    path = Path(prompt_set_path).parent / GROUND_TRUTH_FILENAME
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return (json.load(f).get("examples") or {})


def row_to_example(row, ground_truth=None):
    """Split a prompt_set.json row into LangSmith's (inputs, outputs, metadata).

    `outputs` is LangSmith's reference-output slot and is rendered beside the
    answer in the dashboard, which is exactly where the frozen ground truth
    belongs: a reviewer looking at a failed example can see the source rows it
    was graded against without re-running a query. Rows with no frozen entry
    get `{}`, which LangSmith accepts and shows as empty."""
    inputs = {"prompt": row["input"]}
    metadata = {k: v for k, v in row.items() if k != "input"}
    outputs = (ground_truth or {}).get(row["id"], {})
    return inputs, outputs, metadata


def get_or_create_dataset(client, dataset_name, description=None):
    existing = list(client.list_datasets(dataset_name=dataset_name))
    if existing:
        return existing[0]
    return client.create_dataset(
        dataset_name=dataset_name,
        description=description or f"Lameh Intelligence eval set - built from prompt_set.json ({dataset_name})",
    )


# Keys LangSmith maintains on an example's metadata itself. They are not ours
# to write and are absent from prompt_set.json, so a plain dict comparison
# against our own rows never matches and every sync rewrites every example.
# That is exactly what happened: `0 created, 8 updated` on a dataset nothing
# had touched, on every run. Change detection ignores them instead.
SERVER_MANAGED_METADATA_KEYS = ("dataset_split",)


def _authored_metadata(example):
    """An existing example's metadata with the server's own keys removed, so it
    can be compared against a prompt_set row."""
    return {k: v for k, v in (example.metadata or {}).items()
            if k not in SERVER_MANAGED_METADATA_KEYS}


def existing_examples_by_prompt_id(client, dataset_id):
    """Maps our own `id` field (not LangSmith's example UUID) to its existing
    LangSmith example, for both duplicate-prevention and change detection."""
    by_prompt_id = {}
    for example in client.list_examples(dataset_id=dataset_id):
        prompt_id = (example.metadata or {}).get("id")
        if prompt_id:
            by_prompt_id[prompt_id] = example
    return by_prompt_id


def sync_dataset(spec, client=None, prompt_set_path=None, dataset_name=None, prune=True):
    """Makes the suite's dataset mirror its prompt_set.json: creates examples
    for new `id`s, pushes an update for any existing example whose
    inputs/metadata have changed since it was last synced (e.g. editing a
    prompt's wording and rerunning this), and deletes examples whose `id` is
    no longer in the file at all.

    That last one is why the prompt set is the single source of truth rather
    than a starting point. Without it, deleting a row was a no-op - the sync
    reported `0 created, 0 updated` while the dropped prompt kept running in
    every experiment, because run_eval reads its examples from LangSmith and
    never opens this file.

    Deleting is not free: a LangSmith example is what a past experiment's runs
    point at, so removing one orphans those runs and the row vanishes from
    that experiment's view. That is the right trade for a prompt genuinely
    retired - stale rows in the dataset silently cost a full agent call per
    run - but it is why the deletions are named in the output rather than
    merely counted, and why `prune=False` (`--keep-orphans`) exists for a
    sync you don't want removing anything.

    Returns (created_count, updated_count, deleted_prompt_ids)."""
    client = client or Client()
    prompt_set_path = prompt_set_path or spec.prompt_set_path
    rows = load_prompt_set(prompt_set_path)
    ground_truth = load_ground_truth(prompt_set_path)

    dataset = get_or_create_dataset(client, dataset_name or spec.dataset_name)
    existing = existing_examples_by_prompt_id(client, dataset.id)

    new_rows = [row for row in rows if row["id"] not in existing]
    changed = []
    for row in rows:
        if row["id"] not in existing:
            continue
        inputs, outputs, metadata = row_to_example(row, ground_truth)
        example = existing[row["id"]]
        if (example.inputs != inputs or _authored_metadata(example) != metadata
                or (example.outputs or {}) != outputs):
            changed.append((example.id, inputs, outputs, metadata))

    if new_rows:
        inputs, outputs, metadata = zip(*(row_to_example(row, ground_truth) for row in new_rows))
        client.create_examples(inputs=list(inputs), outputs=list(outputs),
                                metadata=list(metadata), dataset_id=dataset.id)

    for example_id, inputs, outputs, metadata in changed:
        client.update_example(example_id, inputs=inputs, outputs=outputs, metadata=metadata)

    orphans = sorted(set(existing) - {row["id"] for row in rows})
    if prune:
        for prompt_id in orphans:
            client.delete_example(existing[prompt_id].id)

    return len(new_rows), len(changed), orphans


def main():
    ap = argparse.ArgumentParser(description="Sync a suite's prompt_set.json into its LangSmith dataset.")
    ap.add_argument("--suite", required=True, choices=SUITE_NAMES,
                     help="Which eval suite to sync. Decides the dataset name and the prompt-set file.")
    ap.add_argument("--keep-orphans", action="store_true",
                     help="Leave examples that are no longer in prompt_set.json in the dataset. "
                          "By default they are deleted, so the file is the single source of truth; "
                          "use this when past experiments referencing them matter more.")
    args = ap.parse_args()

    load_dotenv()
    spec = load_suite(args.suite)
    created, updated, orphans = sync_dataset(spec, prune=not args.keep_orphans)
    frozen = len(load_ground_truth(spec.prompt_set_path))
    verb = "kept" if args.keep_orphans else "deleted"
    print(f"Dataset '{spec.dataset_name}': {created} example(s) created, {updated} example(s) updated"
          + (f", {len(orphans)} {verb}" if orphans else "")
          + (f", {frozen} carrying frozen ground truth." if frozen else "."))
    for prompt_id in orphans:
        print(f"  {verb}: {prompt_id} (no longer in {Path(spec.prompt_set_path).name})")


if __name__ == "__main__":
    main()
