# `tests/utils/` — shared helpers

Three modules that exist to be used by the scripts one directory up, not run
on their own (with one exception, below). **Each one's module docstring is the
primary documentation and is kept current — read it before changing the
module.** This file covers only what no single module can: how they get
imported, and which of them is dangerous.

| Module | What it is |
|---|---|
| `audit_display.py` | the pinned progress header the audit draws over long steps |
| `lameh_roster.py` | the live list of companies that have financials |
| `nuke_charts.py` | bulk-deletes chart analyses — **destructive**, and untracked |

## Importing from here

`utils/` is a plain directory, not a package, and its callers are run by path
(`poe`, `python tests/...`) rather than as modules — so there is no parent
package to import it relative to, and no `tests.utils` to import absolutely.
Both callers do the same thing:

```python
sys.path.insert(0, str(Path(__file__).resolve().parent / "utils"))
from audit_display import Header, ProgressWatcher, progress_line  # noqa: E402
```

The `# noqa: E402` is load-bearing for the linter, not decorative: the import
has to follow the `sys.path` line. The download script under `tests/E2E/`
reaches one level further up (`parents[1] / "utils"`).

If this ever becomes a real package, both call sites change together, and so
does `ratio_coverage.py` — it locates the download script by *file path*
rather than importing it, deliberately (importing it opens a browser at
module scope).

## `lameh_roster.py` — one list, two consumers

`uploaded_companies()` is the single source of which companies a run covers.
It replaced a 121-entry literal that had been transcribed by hand into the
download script and had drifted ten companies behind the system.

Two things call it, and they must agree or the audit's progress bar lies:

- the download script, for the companies to actually fetch;
- `sector_analysis_audit.company_count()`, for the step 1 bar's denominator
  (cached — the watcher polls once a second, and the roster doesn't change
  mid-run).

It needs `LAMEH_ORCHESTRATOR_URL`, `LAMEH_API_KEY` and
`LAMEH_CHART_DATA_ORGANIZATION_ID` from `.env`, and raises if the first two
are missing. All three request headers are required: without
`organization-id` the endpoint answers 422, and without `x-api-key`, 401 — a
bearer token is not an accepted substitute.

The filter is `uploaded and type == "public"`. Dropping the `type` check
readmits about a dozen private test uploads ("microsof", "tawina test",
"test prospectus") that carry no `reference` to use as an id.

The same endpoint backs the LangSmith suite's sector rosters
(`tests/langsmith/evaluators/ground_truth.py`), filtered slightly differently
— they are separate call sites on purpose, and neither imports the other.

## `nuke_charts.py` — read before you type

Deletes **every** chart analysis belonging to the organization id hardcoded at
the top of the file, one at a time, with no undo and no bulk endpoint to
reverse. It is a dry run unless given `--yes`.

It is also **gitignored** (see `.gitignore`, "Temporarily untracked"), so it
exists on this machine and not in the repo — an agent reading only the tree
will not find it, and a fresh clone will fail `poe nuke-charts` with a missing
file rather than doing something worse.

`BASE_URL` at the top selects the environment, with dev and uat commented out
above the production URL. Check which line is live before running anything.
