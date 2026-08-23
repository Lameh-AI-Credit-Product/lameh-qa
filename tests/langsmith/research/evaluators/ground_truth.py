"""
Lameh Intelligence (research) - board-analysis ground truth
============================================================
Fetches a company's merged board-report tables from
`/v0/board-analysis/merge/tables` - the same data the Intelligence agent reads
for these questions - and flattens them into searchable rows.

Frozen for grading, live only for refresh
------------------------------------------
**Evaluators read the frozen snapshot** (`dataset/ground_truth.json`), never
the API. `BoardAnalysisClient` below exists to *produce* that snapshot and to
check it for drift - see `dataset/freeze_ground_truth.py` for why the eval
grades against a committed file rather than a live fetch.

The short version: an eval whose ground truth can move underneath it cannot
distinguish an agent regression from data drift, which is the one question it
exists to answer. Freezing also lets the ground truth ride along on the
LangSmith examples as reference outputs, so the dashboard shows what an answer
was graded against.

What this is used for, and what it is NOT used for
---------------------------------------------------
It is used to give the judge the *relevant slice of the source* alongside the
answer, so "no hallucinated data" is a comparison rather than a vibe.

It is **not** used for a numeric_accuracy-style value check, for two reasons
found empirically:

1. **The agent cites beyond merge/tables.** A 2026-08-23 probe returned a
   figure tagged `page="47"` / `evidence_block_id="4a56ea3d-..."` whose claim
   ("zero serious or disabling injuries") has no corresponding row in the
   merge/tables payload at all, while another figure in the same table
   (17,500,000 working hours) does. Failing the first as fabricated on
   merge/tables evidence alone would be wrong at least some of the time - the
   agent appears able to read source documents this endpoint does not surface.
2. **A merge run is not stable.** Two pulls for the same company, a fortnight
   and one organization-id apart, returned different `merge_run_id`s, 347 vs
   367 tables, a re-cut section taxonomy, and values typed as floats in one
   and strings in the other. Anything in this suite that pinned itself to a
   table index or a section name would rot silently.

So: ground truth here is *facts stated in the payload*, located by searching
row names and cell values. Never by index. The prompt_set's `traps[].locator`
fields are prose for a human reader and for the judge, not selectors.
"""

import json
import re
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import BOARD_ANALYSIS_ORGANIZATION_ID, BOARD_ANALYSIS_URL  # noqa: E402
from env_config import ORCHESTRATOR_API_KEY  # noqa: E402

DEFAULT_TIMEOUT_SECONDS = 120

# The committed snapshot the evaluators grade against.
_DEFAULT_SNAPSHOT_PATH = Path(__file__).resolve().parent.parent / "dataset" / "ground_truth.json"

# A cell whose value_kind says the source reported nothing. Filtered out of
# flattened rows so a search for a figure doesn't match on absence.
_EMPTY_VALUES = (None, "", "not reported", "-")


def _headers():
    return {
        "accept": "application/json",
        "organization-id": BOARD_ANALYSIS_ORGANIZATION_ID,
        "x-api-key": ORCHESTRATOR_API_KEY,
        "Authorization": f"Bearer {ORCHESTRATOR_API_KEY}",
    }


class BoardAnalysisClient:
    """One payload per company, cached for the run.

    There is no batch endpoint and no need for one: a run is a handful of
    prompts against a single company, and the payload is ~6 MB. Fetching it
    once and slicing it in memory is the whole optimisation.
    """

    def __init__(self, url=BOARD_ANALYSIS_URL, timeout=DEFAULT_TIMEOUT_SECONDS):
        self.url = url
        self.timeout = timeout
        self._cache = {}

    def fetch(self, company_name):
        if company_name in self._cache:
            return self._cache[company_name]
        if not self.url:
            raise RuntimeError("BOARD_ANALYSIS_URL is unset - check LAMEH_ORCHESTRATOR_URL in .env")
        response = requests.get(self.url, headers=_headers(),
                                 params={"company_name": company_name}, timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        self._cache[company_name] = payload
        return payload

    def rows(self, company_name):
        """Every populated row, flattened to
        {section, title, row, cells: [(period, value)]}.

        Table index is deliberately absent from the output - see the module
        docstring on why nothing here may pin itself to one."""
        return flatten(self.fetch(company_name))


class FrozenGroundTruth:
    """The committed snapshot, read once and served from memory.

    Same `rows(company)` shape as BoardAnalysisClient, so the evaluators are
    indifferent to which one they hold - which is what made switching the
    grading path from live to frozen a one-line change in run_eval.
    """

    def __init__(self, path=None):
        self.path = path or _DEFAULT_SNAPSHOT_PATH
        self._snapshot = None

    @property
    def snapshot(self):
        if self._snapshot is None:
            if not self.path.exists():
                raise FileNotFoundError(
                    f"No frozen ground truth at {self.path}. "
                    f"Run: poetry run poe langsmith-research-freeze")
            with open(self.path, encoding="utf-8") as f:
                self._snapshot = json.load(f)
        return self._snapshot

    def rows(self, company_name):
        rows = (self.snapshot.get("rows") or {}).get(company_name)
        if rows is None:
            known = ", ".join((self.snapshot.get("rows") or {}))
            raise KeyError(
                f"{company_name!r} is not in the frozen ground truth (has: {known}). "
                f"Add a prompt for it, then re-freeze.")
        # Cells were serialized as lists; restore the (period, value) tuples the
        # search/render helpers expect.
        return [{**row, "cells": [tuple(cell) for cell in row["cells"]]} for row in rows]

    def meta(self, company_name):
        return (self.snapshot.get("companies") or {}).get(company_name, {})

    def evidence(self, example_id):
        """The per-trap source rows shown in the LangSmith dashboard."""
        return ((self.snapshot.get("examples") or {}).get(example_id) or {}).get("trap_evidence", {})

    @property
    def frozen_at(self):
        return self.snapshot.get("frozen_at")


def flatten(payload):
    rows = []
    for table in payload.get("tables") or []:
        for row in table.get("rows") or []:
            cells = [(c.get("period_column"), c.get("value"))
                     for c in row.get("cells") or []
                     if c.get("value") not in _EMPTY_VALUES]
            if not cells:
                continue
            rows.append({
                "section": table.get("section_name"),
                "title": table.get("title"),
                "row": row.get("canonical_category_name") or "",
                "cells": cells,
            })
    return rows


def _blob(row):
    """The searchable text of a row: its section and table title as well as its
    own name and values.

    Including section and title is not optional - trap locators in the prompt
    set name a *table* ("Safety performance indicators", "Affiliate transaction
    flow"), and a row's own canonical_category_name usually does not repeat it.
    Searching row text alone matched nothing for all twelve traps on the first
    attempt.
    """
    return " ".join([row.get("section") or "", row.get("title") or "", row["row"],
                     " ".join(str(v) for _, v in row["cells"])]).lower()


def search(rows, *terms, limit=40):
    """Rows whose section, title, name or any cell value contains every term,
    case-insensitively.

    Substring matching rather than regex because callers are trap locators
    written by hand ("Working hours recorded", "Yemeni Saudi"), and a locator
    that has to be a valid regex is a locator that will eventually be an
    invalid one.
    """
    needles = [t.lower() for t in terms if t]
    if not needles:
        return []
    hits = []
    for row in rows:
        blob = _blob(row)
        if all(n in blob for n in needles):
            hits.append(row)
            if len(hits) >= limit:
                break
    return hits


def search_any(rows, terms, limit=40, interleave=False):
    """Rows matching ANY of `terms`, deduplicated.

    Trap locators are prose and often name several tables at once
    ("Director service register, Outside-role register, Nomination-panel
    participation"). Splitting them and OR-ing the parts is what makes such a
    locator resolve at all; requiring every part to appear in one row would
    match nothing.

    With `interleave`, results are taken round-robin across terms instead of
    exhausting each in turn. That matters when the budget is tight: on the
    first research run a single broad term ("Director service register")
    returned enough attendance rows to consume the whole limit, so the
    classification rows named by later terms never made it into the excerpt.
    Round-robin gives every term a share.
    """
    per_term = [search(rows, term, limit=limit) for term in terms]
    seen, hits = set(), []

    def take(row):
        marker = (row.get("title"), row["row"])
        if marker in seen:
            return False
        seen.add(marker)
        hits.append(row)
        return len(hits) >= limit

    if interleave:
        for depth in range(limit):
            exhausted = True
            for matches in per_term:
                if depth >= len(matches):
                    continue
                exhausted = False
                if take(matches[depth]):
                    return hits
            if exhausted:
                break
        return hits

    for matches in per_term:
        for row in matches:
            if take(row):
                return hits
    return hits


# Words too generic to locate anything - an expected fact is a sentence, and
# searching it whole matches nothing while searching every word in it matches
# everything.
_FACT_STOPWORDS = frozenset("""
a an and are as at by for from in is it its of on or that the to with
than then they this was were will not no any all only its it's
year years fiscal report reports disclosed value values figure figures
""".split())


def fact_terms(facts, min_length=5, max_terms=12):
    """A prompt_set row's `facts_expected` -> search terms.

    Takes the capitalised runs (proper nouns: company and person names) plus
    the longest remaining words, because those are what actually appear in row
    names. Searching a whole fact sentence matches nothing.
    """
    terms = []
    for fact in facts or []:
        proper = re.findall(r"\b[A-Z][\w'-]*(?:\s+(?:bin|bint|al|Al|of|the)?\s*[A-Z][\w'-]*)*", fact or "")
        terms += [p.strip() for p in proper if len(p.strip()) >= min_length]
        words = [w for w in re.findall(r"[A-Za-z][A-Za-z'-]{4,}", fact or "")
                 if w.lower() not in _FACT_STOPWORDS]
        terms += sorted(set(words), key=len, reverse=True)[:2]
    deduped = []
    for term in terms:
        if term.lower() not in {t.lower() for t in deduped}:
            deduped.append(term)
    return deduped[:max_terms]


def locator_terms(locator):
    """A prompt_set trap locator -> the search terms it actually names.

    Locators read like "Section -> Table A, Table B, some row detail". The
    section prefix is dropped (it is context for a human), the rest is split on
    commas, and fragments too short or too generic to be a table name are
    discarded - "whole payload" is a legitimate locator meaning "nowhere
    specific" and must not match every row in the set."""
    tail = (locator or "").split("->")[-1]
    parts = [p.strip().strip("'\"") for p in tail.split(",")]
    return [p for p in parts if len(p) >= 8 and p.lower() != "whole payload"]


def render_rows(rows, max_cells=8):
    """Flattened rows as plain text for a judge prompt. Plain text rather than
    JSON for the same reason the feedback comments are - a judge reads prose
    better than it reads escaped braces, and nothing parses this back."""
    lines = []
    for row in rows:
        cells = "; ".join(f"{period}={value}" for period, value in row["cells"][:max_cells])
        lines.append(f"[{row['section']} / {row['title']}] {row['row']} -> {cells}")
    return "\n".join(lines)


def run_metadata(payload):
    """The identity of the merge run an answer was graded against.

    Recorded on every feedback comment because merge runs are not stable: a
    score that moved between runs needs to say whether the *source* moved
    under it. See the module docstring."""
    return {
        "merge_run_id": payload.get("merge_run_id"),
        "status": payload.get("status"),
        "is_stale": payload.get("is_stale"),
        "table_count": len(payload.get("tables") or []),
    }


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    client = BoardAnalysisClient()
    name = sys.argv[1] if len(sys.argv) > 1 else "شركة أسمنت اليمامة"
    data = client.fetch(name)
    print(json.dumps(run_metadata(data), ensure_ascii=False, indent=2))
    print(f"{len(client.rows(name))} populated rows")
