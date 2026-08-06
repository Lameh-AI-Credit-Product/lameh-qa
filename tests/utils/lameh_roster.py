"""
The live roster of companies that have data
===========================================

One GET against the chart-builder's `sectors/grouped-by-companies` endpoint
returns every sector with its companies, each flagged `uploaded` - which is
the enumeration nothing else in the API offers (chart-data/batch takes
companies as input rather than listing them).

`uploaded_companies()` returns the same `[{"company_id", "name"}, ...]` shape
the download script used to carry as a literal, so it drops straight into
that script's place.

Private companies are excluded: the twelve currently uploaded are test uploads
sitting in a "Private" sector ("microsof", "tawina test", "test prospectus",
...), and they carry a different shape from the public ones anyway - no
`reference` to use as an id, no `name_ar`.
"""

import os

import requests
from dotenv import load_dotenv

load_dotenv()

ORCHESTRATOR_URL = os.environ.get("LAMEH_ORCHESTRATOR_URL")
SECTORS_GROUPED_URL = (f"{ORCHESTRATOR_URL}/v0/chart-builder/sectors/grouped-by-companies"
                       if ORCHESTRATOR_URL else None)
API_KEY = os.environ.get("LAMEH_API_KEY")
ORGANIZATION_ID = os.environ.get("LAMEH_CHART_DATA_ORGANIZATION_ID",
                                 "00000000-0000-0000-0000-000000000000")

DEFAULT_TIMEOUT_SECONDS = 60


def uploaded_companies(timeout=DEFAULT_TIMEOUT_SECONDS):
    """Every public company the system has financials for.

    `[{"company_id": <Tadawul ticker>, "name": <English name>}, ...]`, in the
    API's own sector-by-sector order. `name` falls back to the Arabic name
    where no English one was recorded, and is what the download script types
    into the company search.
    """
    if not SECTORS_GROUPED_URL or not API_KEY:
        raise RuntimeError(
            "Company roster API not configured - set LAMEH_ORCHESTRATOR_URL and "
            "LAMEH_API_KEY in .env.")
    # All three headers are required: without `organization-id` the endpoint
    # answers 422, and without `x-api-key` it answers 401 (a bearer token is
    # not an accepted alternative - it comes back "Invalid token format").
    response = requests.get(SECTORS_GROUPED_URL, timeout=timeout, headers={
        "accept": "application/json",
        "organization-id": ORGANIZATION_ID,
        "x-api-key": API_KEY,
    })
    response.raise_for_status()
    return [
        {"company_id": company.get("reference"),
         "name": company.get("name_en") or company.get("name_ar")}
        for group in response.json()
        for company in group["companies"]
        if company.get("uploaded") and company.get("type") == "public"
    ]
