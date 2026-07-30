"""
Lameh Intelligence - live ground-truth lookup
===============================================
Fetches the current DB value for a (company, metric, fiscal_year) from the
chart-builder/chart-data/batch endpoint - the same data source the agent's
own tools (get_ratio_with_breakdown, get_main_statement_data, ...) read from.
No ground truth is ever snapshotted into the dataset; it's always read live
at eval-run time, so a correctness failure reflects today's data, not a
stale copy.

Two kinds of lookups, both via the same endpoint:
  - Pre-computed ratios: attributes=[{"section": "Financial Ratios",
    "name": "Return on Equity (ROE)"}] returns the ratio directly, already
    computed server-side (with formulaInfo documenting how) - no need to
    recompute via sector_analysis_ratios.py's formula registry for these.
  - Raw line items: attributes=[{"section": "Balance Sheet",
    "name": "Total Debt"}] etc. - used for grounding checks and for ad-hoc
    ratios like "Debt to CapEx" that aren't pre-stored (the agent computes
    those manually too, per the sample response - see extraction.py).

Unit note: the API returns percent-type ratios (e.g. ROE) as a raw fraction
(0.0535), while the agent's <calc> tags display them as a percent ("5.35%"
-> parsed as 5.35 by extraction.py). Comparisons must scale for this - see
correctness.py, which does the scaling based on the extracted fact's own
parsed unit (not the API's `unit` metadata field, which is unreliable: it
labels DSO's unit as "SAR" even though it's actually a day count).
"""

import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import (CHART_DATA_ORGANIZATION_ID, CHART_DATA_URL,  # noqa: E402
                     ORCHESTRATOR_API_KEY, SECTORS_GROUPED_URL)

DEFAULT_TIMEOUT_SECONDS = 60


def _headers():
    return {
        "accept": "application/json",
        "organization-id": CHART_DATA_ORGANIZATION_ID,
        "x-api-key": ORCHESTRATOR_API_KEY,
        "Authorization": f"Bearer {ORCHESTRATOR_API_KEY}",
        "Content-Type": "application/json",
    }


def fetch_chart_data(companies, attributes, start_period, end_period, period_type="yearly",
                      aggregation_type="avg", timeout=DEFAULT_TIMEOUT_SECONDS):
    """Raw call to chart-data/batch. `attributes` is a list of
    {"section": ..., "name": ...} dicts (mixing raw line items and
    "Financial Ratios" entries is fine - the API handles both in one call).
    Returns the parsed JSON response as-is (metadata + periods)."""
    payload = {
        "companies": companies,
        "sectors": [],
        "attributes": attributes,
        "periodType": period_type,
        "startPeriod": str(start_period),
        "endPeriod": str(end_period),
        "aggregationType": aggregation_type,
    }
    response = requests.post(CHART_DATA_URL, headers=_headers(), json=payload, timeout=timeout)
    response.raise_for_status()
    return response.json()


def _attribute_id(chart_data, section, name):
    for attr in chart_data["metadata"]["attributes"]:
        if attr["section"] == section and attr["name"] == name:
            return attr["id"]
    return None


def _value_for(chart_data, company, attribute_id, fiscal_year):
    for period in chart_data["periods"]:
        if period["period"] == str(fiscal_year):
            return period["values"].get(f"{company}_{attribute_id}")
    return None


def _fetch_live(company, metric, fiscal_year, section="Financial Ratios"):
    """Fetches one (company, metric, fiscal_year) value. `section` defaults
    to "Financial Ratios" (pre-computed ratio lookup); pass the statement
    section (e.g. "Balance Sheet") for a raw line item instead. Returns None
    if the metric genuinely isn't available for that company/year (a real
    "not available", not an error) - callers should not treat None as a
    failed fetch."""
    if CHART_DATA_URL is None:
        raise RuntimeError(
            "Ground-truth API not configured - set LAMEH_ORCHESTRATOR_URL / "
            "LAMEH_API_KEY in .env."
        )
    chart_data = fetch_chart_data(
        companies=[company],
        attributes=[{"section": section, "name": metric}],
        start_period=fiscal_year,
        end_period=fiscal_year,
    )
    attribute_id = _attribute_id(chart_data, section, metric)
    if attribute_id is None:
        return None
    return _value_for(chart_data, company, attribute_id, fiscal_year)


# Chunk sizes for prefetch(). The endpoint returns a company x attribute grid
# per period, so one request's cost is roughly its cell count - these cap a
# single request at 25 x 40 x (years) cells, big enough that a whole example
# is a handful of requests and small enough that one slow request doesn't
# stall the run. Raise only with timing evidence; the win is already ~50x.
BATCH_MAX_COMPANIES = 25
BATCH_MAX_ATTRIBUTES = 40


def _chunked(items, size):
    for start in range(0, len(items), size):
        yield items[start:start + size]


class GroundTruthClient:
    """Per-run cache over the chart-data/batch lookup, so repeated
    (company, metric, fiscal_year) lookups across dataset examples in a
    single eval run don't hit the API redundantly. A fresh instance should be
    created per eval run (see run_eval.py), not shared/reused across runs -
    it deliberately does not expire entries.

    Callers that know their whole key set up front should call prefetch()
    first and then get() - see prefetch's docstring for why that matters a
    great deal.

    Safe to share across the threads LangSmith uses when examples run
    concurrently: the worst case is two threads racing to populate the same
    key, which costs one redundant (idempotent) fetch and never corrupts the
    cache. Not worth a lock, which would serialize every lookup."""

    def __init__(self, fetch_fn=_fetch_live, batch_fetch_fn=fetch_chart_data):
        self._fetch_fn = fetch_fn
        self._batch_fetch_fn = batch_fetch_fn
        self._cache = {}
        self._requests = 0

    def get(self, company, metric, fiscal_year, section="Financial Ratios"):
        key = (company, metric, str(fiscal_year), section)
        if key not in self._cache:
            self._requests += 1
            self._cache[key] = self._fetch_fn(company, metric, str(fiscal_year), section)
        return self._cache[key]

    def prefetch(self, keys):
        """Resolve many (company, metric, fiscal_year, section) keys in as few
        requests as possible, populating the cache so the subsequent get()
        calls are free.

        This is not an optimisation, it's the difference between a usable
        suite and an unusable one. chart-data/batch takes lists of companies
        and attributes and returns the whole grid, so one request answers
        hundreds of keys in the time a single-cell request takes (~2.7s
        measured either way). Grading one sector-wide response one cell at a
        time cost 693 requests and ~31 minutes - four times longer than the
        agent call it was grading. Batched, the same set is a handful of
        requests.

        Keys are grouped by section and chunked (see BATCH_MAX_*); each
        request spans min..max fiscal year of its group, which over-fetches
        slightly and is still far cheaper than splitting by year.

        Every cell in the returned grid is cached, including combinations
        nobody asked for - they're already paid for. Keys the response has no
        value for are cached as None, so a genuinely-unavailable metric isn't
        re-requested one at a time by the get() fallback afterwards."""
        by_section = {}
        for company, metric, fiscal_year, section in keys:
            key = (company, metric, str(fiscal_year), section)
            if key in self._cache:
                continue
            group = by_section.setdefault(section, {"companies": set(), "metrics": set(), "years": set()})
            group["companies"].add(company)
            group["metrics"].add(metric)
            group["years"].add(str(fiscal_year))

        for section, group in by_section.items():
            years = sorted(group["years"])
            companies, metrics = sorted(group["companies"]), sorted(group["metrics"])
            for company_chunk in _chunked(companies, BATCH_MAX_COMPANIES):
                for metric_chunk in _chunked(metrics, BATCH_MAX_ATTRIBUTES):
                    self._fetch_grid(section, company_chunk, metric_chunk, years[0], years[-1])

    def _fetch_grid(self, section, companies, metrics, start_year, end_year):
        """One batched request; caches every cell it covers. A failed request
        is swallowed to a per-key None rather than raised: a single bad
        attribute name shouldn't abort grading of an entire response, and the
        keys it leaves as None are reported as "skipped" by the comparison
        functions, which is the honest outcome - not graded, not failed."""
        self._requests += 1
        try:
            chart_data = self._batch_fetch_fn(
                companies=list(companies),
                attributes=[{"section": section, "name": m} for m in metrics],
                start_period=start_year, end_period=end_year,
            )
        except Exception:  # noqa: BLE001 - see docstring
            chart_data = None

        attribute_ids = {}
        if chart_data:
            for metric in metrics:
                attribute_ids[metric] = _attribute_id(chart_data, section, metric)

        periods = {p["period"]: p["values"] for p in (chart_data or {}).get("periods", [])}
        for period, values in periods.items():
            for company in companies:
                for metric in metrics:
                    attribute_id = attribute_ids.get(metric)
                    value = values.get(f"{company}_{attribute_id}") if attribute_id else None
                    self._cache.setdefault((company, metric, period, section), value)

        # Anything the grid didn't cover (a period the API omitted entirely,
        # or an attribute name it doesn't know) is settled as None here, so
        # get() doesn't fall back to a single-cell request per miss - which
        # would put back exactly the cost this method exists to remove.
        for year in {str(y) for y in range(int(start_year), int(end_year) + 1)}:
            for company in companies:
                for metric in metrics:
                    self._cache.setdefault((company, metric, year, section), None)

    def cache_info(self):
        return {"cached_lookups": len(self._cache), "api_requests": self._requests}


# One GET covers every sector, and a run needs at most a couple of rosters,
# so it's fetched once per process and sliced from there. Same threading
# rationale as GroundTruthClient: a race costs one redundant idempotent GET.
_rosters = None


def _fetch_rosters():
    """{name_en: [Arabic company name, ...]} for every sector.

    Only companies with `uploaded: true` are included, and that filter is the
    whole point rather than a detail. `uploaded` marks the companies whose
    financials are actually in the system; the rest are listed on the exchange
    but have nothing for the agent to read. Counting them would make every
    sector-wide prompt fail coverage for companies no answer could ever cover
    - Health Care is 10 uploaded out of 27 listed. (Materials happens to be
    68 of 68, which is why the distinction stayed invisible until a second
    sector was added.)"""
    if SECTORS_GROUPED_URL is None:
        raise RuntimeError(
            "Sector API not configured - set LAMEH_ORCHESTRATOR_URL / LAMEH_API_KEY in .env.")
    response = requests.get(SECTORS_GROUPED_URL, headers=_headers(), timeout=DEFAULT_TIMEOUT_SECONDS)
    response.raise_for_status()
    return {
        group["sector"]["name_en"]: [c["name_ar"] for c in group["companies"] if c.get("uploaded")]
        for group in response.json()
    }


def list_sector_companies(sector):
    """Every company with data that the live system considers part of
    `sector`, by Arabic name - used to give sector-wide prompts (Q2/Q3-style,
    where the dataset row has no fixed `companies` list) a coverage
    expectation, and to validate that every company the agent names is real.

    Fetched live, like every other piece of ground truth in this suite, so a
    roster can't go stale between runs. `sector` is matched against the API's
    `name_en`, which is exactly what prompt_set.json's `sector` field holds
    ("Materials", "Health Care Equipment & Svc").

    Raises KeyError for a sector the API doesn't know; callers treat that as
    "nothing to check" rather than as a failure."""
    global _rosters
    if _rosters is None:
        _rosters = _fetch_rosters()
    if sector not in _rosters:
        raise KeyError(f"No sector named {sector!r} - known sectors: {sorted(_rosters)}")
    return _rosters[sector]
