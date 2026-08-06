"""
Sector Analysis - bulk ratio-export download (E2E, Playwright)
================================================================
Logs in once (you complete OTP by hand), then loops over a list of
companies without closing the browser, building a "select all ratios"
Sector Analysis and downloading the Excel export for each one into
`data/sector-analysis/<run timestamp>/`.

A failure on one company is logged and does not stop the run - the loop
moves on to the next company. See the run's log file (same folder as
the downloads) for anything that went wrong.

The company list comes from `uploaded_companies()` (see
`tests/utils/lameh_roster.py`): every company the system has financials for,
asked for at the start of each run. It used to be a literal list here, which
had drifted ten companies behind by the time it was replaced.

Downloads are named `<ticker>_<company>.xlsx`. The ticker comes from the
roster; runs from before it did carry an internal id instead, so filenames
across the two are not comparable (nothing reads them - the verification
scripts take the company name from inside the workbook).

Usage
-----
    poetry run poe download-ratios

Each company's name is used as the search term, and "Select all companies in
<results>" is clicked, so the name has to match exactly one company - the
roster's full English names do.
"""

import logging
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import Playwright, sync_playwright, TimeoutError as PlaywrightTimeoutError

# utils/ is a plain directory rather than a package, and this script is run by
# path, so there is no parent package to import it relative to either.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "utils"))
from lameh_roster import uploaded_companies  # noqa: E402

load_dotenv()

BASE_URL = os.environ["BASE_URL"]
LOGIN_URL = f"{BASE_URL}/login"

REPO_ROOT = Path(__file__).resolve().parents[2]
RUN_DIR = REPO_ROOT / "data" / "sector-analysis" / datetime.now().strftime("%Y-%m-%d-%H-%M-%S")

NAV_TIMEOUT_MS = 30_000
BUILD_TIMEOUT_MS = 180_000  # ratio-set generation / data fetch can take up to ~3 minutes


def sanitize_filename(text: str) -> str:
    return re.sub(r'[\\/:*?"<>|,\s]+', "_", str(text)).strip("_")


def wait_ready(page):
    """Best-effort wait for in-flight requests to settle after an action
    that triggers a fetch (search, section navigation, Build Now, ...).
    Swallowed on timeout - some pages keep a long-poll/websocket open and
    would never reach "networkidle", so this is advisory, not load-bearing."""
    try:
        page.wait_for_load_state("networkidle", timeout=NAV_TIMEOUT_MS)
    except PlaywrightTimeoutError:
        pass


# By the time the Ratio button/sections/ratios render, the underlying data
# fetch already completed (that's what BUILD_TIMEOUT_MS on the Ratio button
# itself waits out) - so a missing section or ratio here means it genuinely
# isn't on the page, not that it's still loading. Skip it fast.
ELEMENT_CLICK_TIMEOUT_MS = 1_000

# Individual ratios are the tightest case: once their section is expanded the
# whole list is in the DOM at once, so a ratio that isn't there after a beat
# will never be. This runs ~120 times per company, so the budget is kept low -
# it is the difference between seconds and minutes across a full run.
RATIO_CLICK_TIMEOUT_MS = 200


class NoMetricsFoundError(Exception):
    """Raised when the built analysis has no ratio metrics at all for this
    company - not an error to retry, just nothing to select/export."""


def click_ratio_if_present(page, label, exact=False, logger=None):
    """Click a ratio's label if it's on the page; if it doesn't exist for
    this company (not every company has every ratio), log and move on
    instead of failing the whole company over one missing metric."""
    locator = page.get_by_text(label, exact=exact)
    try:
        locator.wait_for(state="visible", timeout=RATIO_CLICK_TIMEOUT_MS)
        locator.click()
    except PlaywrightTimeoutError:
        if logger:
            logger.info(f"ratio not available, skipping: {label!r}")


def click_section_if_present(page, section, logger=None):
    """Click a ratio section header if it exists; not every company's
    template includes every section (e.g. no Coverage section for a company
    with no debt) - skip and log rather than failing the company."""
    locator = page.locator("button").filter(has_text=section)
    try:
        locator.wait_for(state="visible", timeout=ELEMENT_CLICK_TIMEOUT_MS)
        locator.click()
    except PlaywrightTimeoutError:
        if logger:
            logger.info(f"section not available, skipping: {section!r}")


def select_all_ratios(page, logger=None):
    # The ratio tab depends on a data fetch that can take up to ~3 minutes,
    # well beyond the default action timeout - wait for it explicitly before
    # clicking rather than letting the click's own (shorter) timeout fire.
    ratio_button = page.get_by_role("button", name="Ratio")
    ratio_button.wait_for(state="visible", timeout=BUILD_TIMEOUT_MS)
    ratio_button.click()

    try:
        page.get_by_text("No metrics found.").wait_for(state="visible", timeout=ELEMENT_CLICK_TIMEOUT_MS)
        raise NoMetricsFoundError("No metrics found. for this company")
    except PlaywrightTimeoutError:
        pass  # good - metrics are present, continue as normal

    # The Ratio tab shows both "Market Data" and "Financial Ratios" as
    # top-level sections, each with its own "Expand section" button - we
    # only want the Financial Ratios one.
    page.get_by_role("button", name="Expand section").filter(has_text="Financial Ratios").click()
    for section in [
        "Solvency Ratios", "Profitability Ratios", re.compile(r"^Profitability$"),
        "Performance", "Margins", "Liquidity Ratios", "Free Cash Flow",
        "Financing", "Enterprise Value", "DuPont Analysis", "Coverage",
        "Asset Valuation", "Activity Ratios",
    ]:
        click_section_if_present(page, section, logger=logger)
    # page.get_by_role("button", name="Expand subsection").click()

    # Some of these no longer appear in the app (it drops/renames ratios from
    # time to time) - they're kept on the chance another company template
    # still has them, and just log "ratio not available" when they don't.
    ratio_labels = [
        "Cash Conversion Cycle", "Days Inventory on Hand (DIO)",
        "Days Payables Outstanding (", "Days Sales Outstanding (DSO)",
        "Fixed Asset Turnover", "Inventory Turnover", "Payables Turnover",
        "Receivables Turnover", "Total Assets Turnover",
        "Working Capital Turnover",
        "Book Value of Equity", "CFO Interest Coverage", "Debt Coverage",
        "Debt Payment Ratio", "Dividend Payment Coverage", "EBITDA Coverage",
        "OCF Debt Service Ratio (OCF", "Investing and Financing",
        "Operating Cash Flow to", "Reinvestment Ratio", "Interest Burden",
        "Tax Burden", "EV/EBITDA", "EV/Revenue", "Enterprise Value (EV)",
        "Net Borrowing", "Total Debt Service",
        "Free Cash Flow (FCF)", "Free Cash Flow to Equity (",
        "Free Cash Flow to Firm (FCFF)", "Cash Ratio", "Current Ratio",
        "Quick Ratio", "EBIT Margin", "EBITDA Margin", "Gross Profit Margin",
        "Net Profit Margin", "Operating Profit Margin", "Pretax Profit Margin",
        "Return on Sales",
        "CapEx to Depreciation", "CapEx to Revenue", "Cash Flow Quality",
        "Cash Flow to Revenue", "Cash Return on Assets",
        "Cash Return on Equity", "Cash to Operating Income",
        "Dividend Payout Ratio", "Retention Ratio",
        "NOPAT", "Operating Return on Assets",
        "ROA Adjusted", "ROE (DuPont 3-Factor)",
        "ROE (DuPont 5-Factor)", "Return on Assets (ROA)",
        "Return on Equity (ROE)", "Return on Invested Capital (",
        "Debt to Assets", "Debt to Capital",
        "Debt to Equity", "Financial Leverage", "Net Debt to EBITDA",
    ]
    for label in ratio_labels:
        click_ratio_if_present(page, label, logger=logger)

    # Exact-match labels: substrings above (e.g. "Debt to EBITDA" vs
    # "Net Debt to EBITDA", "Interest Coverage" vs "CFO Interest Coverage")
    # would otherwise match the wrong element.
    for label in [
        "Working Capital", "EBITDA", "Gross Profit", "Debt to EBITDA",
        "Net Debt", "Total Debt", "Interest Coverage", "Operating Income",
    ]:
        click_ratio_if_present(page, label, exact=True, logger=logger)


def build_and_download(page, company: dict, out_dir: Path, logger=None) -> Path:
    name = company["name"]

    page.get_by_role("link", name="Sector Analysis").click()
    wait_ready(page)
    page.get_by_role("button", name="New Analysis", exact=True).click()

    search_box = page.get_by_role("textbox", name="Search companies or sectors...")
    search_box.click()
    search_box.fill(name)
    wait_ready(page)

    # Selects every company the search currently matches - the full company
    # name is used as the search term so this should narrow to exactly one.
    page.get_by_role("button", name=re.compile("Select all companies in", re.I)).click()
    page.get_by_role("button", name="Next").click()
    wait_ready(page)

    select_all_ratios(page, logger=logger)

    page.get_by_role("button", name="Next").click()
    wait_ready(page)
    page.get_by_role("button", name="Build Now").click()

    # Build Now redirects to the freshly built analysis's own URL - do not
    # hardcode an analysis id, just wait for the navigation and for the page
    # to finish generating before trying to download.
    page.wait_for_url(re.compile(r"/chart-builder/analysis/.+"), timeout=NAV_TIMEOUT_MS)
    wait_ready(page)

    # "Selected Metrics" showing up signals the built analysis has finished
    # rendering - wait for it before trying to export.
    page.get_by_text("Selected Metrics").wait_for(state="visible", timeout=BUILD_TIMEOUT_MS)

    export_button = page.get_by_role("button", name="Export to Excel")
    export_button.wait_for(state="visible", timeout=BUILD_TIMEOUT_MS)

    with page.expect_download() as download_info:
        export_button.click()
    download = download_info.value

    dest = out_dir / f"{company['company_id']}_{sanitize_filename(name)}.xlsx"
    download.save_as(dest)
    return dest


def run(playwright: Playwright, companies: list[dict]) -> None:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    log_path = RUN_DIR / "run.log"

    logger = logging.getLogger("sector_analysis_download")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(file_handler)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(console_handler)

    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context(accept_downloads=True)
    page = context.new_page()

    page.goto(LOGIN_URL)
    input("Complete OTP login in the browser window, then press Enter here to continue...")
    page.get_by_role("link", name="Sector Analysis").wait_for(state="visible", timeout=NAV_TIMEOUT_MS)

    succeeded, skipped, failed = [], [], []
    for company in companies:
        label = f"{company['company_id']} ({company['name']})"
        logger.info(f"=== {label}: starting ===")
        try:
            dest = build_and_download(page, company, RUN_DIR, logger=logger)
            logger.info(f"{label}: downloaded to {dest}")
            succeeded.append(label)
        except NoMetricsFoundError:
            logger.info(f"{label}: skipped, no metrics found for this company")
            skipped.append(label)
            try:
                page.goto(f"{BASE_URL}/sector-analysis")
                wait_ready(page)
            except Exception:
                logger.exception(f"{label}: could not reset page state after skip")
        except Exception:
            logger.exception(f"{label}: FAILED, skipping to next company")
            failed.append(label)
            # Reset back to a known page so the next iteration starts clean
            # even if this one died mid-flow (e.g. a dialog left open).
            try:
                page.goto(f"{BASE_URL}/sector-analysis")
                wait_ready(page)
            except Exception:
                logger.exception(f"{label}: could not reset page state after failure")

    logger.info(
        f"Run complete. Succeeded: {len(succeeded)} {succeeded}. "
        f"Skipped (no metrics): {len(skipped)} {skipped}. Failed: {len(failed)} {failed}."
    )
    logger.info(f"Log and downloads saved under: {RUN_DIR}")

    context.close()
    browser.close()


COMPANIES = uploaded_companies()

with sync_playwright() as playwright:
    run(playwright, COMPANIES)
