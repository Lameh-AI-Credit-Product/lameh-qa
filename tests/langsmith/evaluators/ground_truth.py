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

import json
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import CHART_DATA_ORGANIZATION_ID, CHART_DATA_URL, ORCHESTRATOR_API_KEY  # noqa: E402

DEFAULT_TIMEOUT_SECONDS = 60
SECTOR_COMPANIES_PATH = Path(__file__).resolve().parent.parent / "dataset" / "sector_companies.json"


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


class GroundTruthClient:
    """Per-run cache over the chart-data/batch lookup, so repeated
    (company, metric, fiscal_year) lookups across dataset examples in a
    single eval run don't hit the API redundantly. A fresh instance should be
    created per eval run (see run_eval.py), not shared/reused across runs -
    it deliberately does not expire entries.

    Safe to share across the threads LangSmith uses when examples run
    concurrently: the worst case is two threads racing to populate the same
    key, which costs one redundant (idempotent) fetch and never corrupts the
    cache. Not worth a lock, which would serialize every lookup."""

    def __init__(self, fetch_fn=_fetch_live):
        self._fetch_fn = fetch_fn
        self._cache = {}

    def get(self, company, metric, fiscal_year, section="Financial Ratios"):
        key = (company, metric, fiscal_year, section)
        if key not in self._cache:
            self._cache[key] = self._fetch_fn(company, metric, fiscal_year, section)
        return self._cache[key]

    def cache_info(self):
        return {"cached_lookups": len(self._cache)}


def list_sector_companies(sector):
    """All companies the live data considers part of `sector` - used by the
    grounding check for sector-wide prompts (Q2/Q3-style, where the dataset
    row has no fixed `companies` list) and to validate that every company the
    agent names in a `companies`-scoped prompt is real.

    Backed by dataset/sector_companies.json - a static roster captured from a
    real chart-data/batch request (chart-data/batch itself takes an explicit
    company list as input rather than exposing a way to enumerate one, so
    there's no live "list companies in this sector" call to make instead).
    This roster can drift from the live company set over time - re-derive it
    from a fresh batch call if grounding checks start flagging real
    companies as fabricated."""
    with open(SECTOR_COMPANIES_PATH, encoding="utf-8") as f:
        rosters = json.load(f)
    if sector not in rosters:
        raise KeyError(f"No company roster for sector {sector!r} in {SECTOR_COMPANIES_PATH}")
    return rosters[sector]
