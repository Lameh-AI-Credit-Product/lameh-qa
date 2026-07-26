"""
Lameh Intelligence - LangSmith dataset builder
===============================================
Converts prompt_set.json (the Materials-sector prompt set, one example per
question) into a LangSmith dataset.

No ground-truth values are stored here - by design. Ground truth is always
fetched live from the internal API at eval-run time (see evaluators/ground_truth.py),
so the dataset only carries the prompt text plus metadata describing what a
passing response should cover.

Adding a new sector or prompt type is just adding a row to prompt_set.json -
this script does not need to change.

Usage
-----
    poetry run python tests/langsmith/dataset/build_dataset.py
"""

import json
import sys
from pathlib import Path

from dotenv import load_dotenv
from langsmith import Client

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import DATASET_NAME  # noqa: E402

PROMPT_SET_PATH = Path(__file__).resolve().parent / "prompt_set.json"


def load_prompt_set(path=PROMPT_SET_PATH):
    with open(path, encoding="utf-8") as f:
        rows = json.load(f)
    ids = [row["id"] for row in rows]
    duplicates = {i for i in ids if ids.count(i) > 1}
    if duplicates:
        raise ValueError(f"Duplicate prompt ids in {path}: {duplicates}")
    return rows


def row_to_example(row):
    """Split a prompt_set.json row into LangSmith's (inputs, metadata) shape."""
    inputs = {"prompt": row["input"]}
    metadata = {k: v for k, v in row.items() if k != "input"}
    return inputs, metadata


def get_or_create_dataset(client, dataset_name=DATASET_NAME):
    existing = list(client.list_datasets(dataset_name=dataset_name))
    if existing:
        return existing[0]
    return client.create_dataset(
        dataset_name=dataset_name,
        description="Lameh Intelligence module eval set - built from prompt_set.json",
    )


def existing_examples_by_prompt_id(client, dataset_id):
    """Maps our own `id` field (not LangSmith's example UUID) to its existing
    LangSmith example, for both duplicate-prevention and change detection."""
    by_prompt_id = {}
    for example in client.list_examples(dataset_id=dataset_id):
        prompt_id = (example.metadata or {}).get("id")
        if prompt_id:
            by_prompt_id[prompt_id] = example
    return by_prompt_id


def sync_dataset(client=None, prompt_set_path=PROMPT_SET_PATH, dataset_name=DATASET_NAME):
    """Ensures `dataset_name` exists and matches prompt_set_path exactly:
    creates examples for new `id`s, and pushes an update for any existing
    example whose inputs/metadata have changed since it was last synced (e.g.
    editing a prompt's wording in prompt_set.json and rerunning this).
    Returns (created_count, updated_count)."""
    client = client or Client()
    rows = load_prompt_set(prompt_set_path)

    dataset = get_or_create_dataset(client, dataset_name)
    existing = existing_examples_by_prompt_id(client, dataset.id)

    new_rows = [row for row in rows if row["id"] not in existing]
    changed = []
    for row in rows:
        if row["id"] not in existing:
            continue
        inputs, metadata = row_to_example(row)
        example = existing[row["id"]]
        if example.inputs != inputs or (example.metadata or {}) != metadata:
            changed.append((example.id, inputs, metadata))

    if new_rows:
        inputs, metadata = zip(*(row_to_example(row) for row in new_rows))
        client.create_examples(inputs=list(inputs), metadata=list(metadata), dataset_id=dataset.id)

    for example_id, inputs, metadata in changed:
        client.update_example(example_id, inputs=inputs, metadata=metadata)

    return len(new_rows), len(changed)


def main():
    load_dotenv()
    created, updated = sync_dataset()
    print(f"Dataset '{DATASET_NAME}': {created} example(s) created, {updated} example(s) updated.")


if __name__ == "__main__":
    main()
