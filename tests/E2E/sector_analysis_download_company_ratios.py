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

Usage
-----
    poetry run poe download-ratios

Edit COMPANIES below with the list to run. Each entry is used as the
search term, and "Select all companies in <results>" is clicked - so make
each entry specific enough to match exactly one company.
"""

import logging
import re
import sys
from datetime import datetime
from pathlib import Path

from playwright.sync_api import Playwright, sync_playwright, TimeoutError as PlaywrightTimeoutError

BASE_URL = "https://frontend-dev-36462279645.me-central2.run.app"
LOGIN_URL = f"{BASE_URL}/login"

# Edit this list with the companies to run in this batch. `name` is used as
# the search term (full name, so the search narrows to a single match);
# `company_id` is only used to make the downloaded filename unique/traceable.
# Filtered from the sector export to companies with type == "public" and
# uploaded == true; `name` is name_en, falling back to name_ar where no
# English name was recorded.
COMPANIES = [
    {"company_id": 3518, "name": "Alwasail Industrial Co."},
    {"company_id": 3535, "name": "Anmat Technology for Trading Co."},
    {"company_id": 3536, "name": "Arabian United Float  Glass Co."},
    {"company_id": 3575, "name": "Electrical Industries Co."},
    {"company_id": 3560, "name": "Gas Arabian Services Co."},
    {"company_id": 3717, "name": "United Mining Industries Co."},
    {"company_id": 3748, "name": "Al Mawarid Manpower Co."},
    {"company_id": 3541, "name": "Baazeem Trading Co."},
    {"company_id": 3577, "name": "Alfakhera for Mens Tailoring Co."},
    {"company_id": 3590, "name": "Fitaihi Holding Group"},
    {"company_id": 3657, "name": "Americana Restaurants International PLC - Foreign Company"},
    {"company_id": 3597, "name": "Armah Sports Co."},
    {"company_id": 3672, "name": "Herfy Food Services Co."},
    {"company_id": 3596, "name": "Leejam Sports Co."},
    {"company_id": 3559, "name": "National Company for Learning and Education"},
    {"company_id": 3533, "name": "Ratio Speciality Company for Trading"},
    {"company_id": 3747, "name": "Shatirah House Restaurant Co."},
    {"company_id": 3631, "name": "BinDawood Holding Co."},
    {"company_id": 3595, "name": "Nahdi Medical Co."},
    {"company_id": 3552, "name": "Nayifat Finance Co."},
    {"company_id": 3555, "name": "Almarai Co."},
    {"company_id": 3635, "name": "First Milling Co."},
    {"company_id": 3662, "name": "Al Hammadi Holding"},
    {"company_id": 3572, "name": "Al-Modawat Specialized Medical Co."},
    {"company_id": 3583, "name": "Al-Razi Medical Co."},
    {"company_id": 3654, "name": "Arabian International Healthcare Holding Co."},
    {"company_id": 3519, "name": "Canadian Medical Center Co."},
    {"company_id": 3668, "name": "Dallah Healthcare Co."},
    {"company_id": 3674, "name": "Dr. Sulaiman Al Habib Medical Services Group"},
    {"company_id": 3588, "name": "Lana Medical Co."},
    {"company_id": 3663, "name": "Middle East Healthcare Co."},
    {"company_id": 3666, "name": "Mouwasat Medical Services Co."},
    {"company_id": 3538, "name": "MOBI Industry Co."},
    {"company_id": 3539, "name": "Mutakamela Insurance Co."},
    {"company_id": 3738, "name": "ASG Plastic Factory Co."},
    {"company_id": 3727, "name": "Advanced Building Industries Co."},
    {"company_id": 3684, "name": "Advanced Petrochemical Co."},
    {"company_id": 3689, "name": "Al Jouf Cement Co."},
    {"company_id": 3715, "name": "Al Kathiri Holding Co."},
    {"company_id": 3556, "name": "Al Rashid Industrial Co."},
    {"company_id": 3640, "name": "Al Taiseer Group Talco Industrial Co."},
    {"company_id": 3719, "name": "Al Yamamah Steel Industries Co."},
    {"company_id": 3735, "name": "Albattal Factory for Chemical Industries Co."},
    {"company_id": 3665, "name": "Almasane Alkobra Mining Co."},
    {"company_id": 3716, "name": "Alujain Corp."},
    {"company_id": 3734, "name": "Aqaseem Factory for Chemicals and Plastics Co."},
    {"company_id": 3704, "name": "Arabian Cement Co."},
    {"company_id": 3682, "name": "Arabian Pipes Co."},
    {"company_id": 3728, "name": "Arabian Plastic Industrial Co."},
    {"company_id": 3713, "name": "Basic Chemical Industries Co."},
    {"company_id": 3720, "name": "Bena Steel Industries Co."},
    {"company_id": 3656, "name": "City Cement Co."},
    {"company_id": 3701, "name": "East Pipes Integrated Company for Industry"},
    {"company_id": 3694, "name": "Eastern Province Cement Co."},
    {"company_id": 3721, "name": "Filing and Packing Materials Manufacturing Co."},
    {"company_id": 3723, "name": "Group Five Pipe Saudi Co."},
    {"company_id": 3737, "name": "Marble Design Co."},
    {"company_id": 3731, "name": "Methanol Chemicals Co."},
    {"company_id": 3739, "name": "Meyar Co."},
    {"company_id": 3710, "name": "Middle East Paper Co."},
    {"company_id": 3733, "name": "Mohammed Hadi Al Rasheed and Partners Co."},
    {"company_id": 3687, "name": "Mohammed Hasan AlNaqool Sons Co."},
    {"company_id": 3740, "name": "Molan Steel Co."},
    {"company_id": 3736, "name": "Naas Petrol Factory Co."},
    {"company_id": 3699, "name": "Najran Cement Co."},
    {"company_id": 3743, "name": "Nama Chemicals Co."},
    {"company_id": 3661, "name": "National Gypsum Co."},
    {"company_id": 3659, "name": "National Industrialization Co."},
    {"company_id": 3685, "name": "National Metal Manufacturing and Casting Co."},
    {"company_id": 3742, "name": "Neft Alsharq Company for Chemical Industries"},
    {"company_id": 3695, "name": "Northern Region Cement Co."},
    {"company_id": 3741, "name": "Paper Home Co."},
    {"company_id": 3691, "name": "Qassim Cement Co."},
    {"company_id": 3690, "name": "Riyadh Cement Co."},
    {"company_id": 3709, "name": "Riyadh Steel Co."},
    {"company_id": 3676, "name": "SABIC Agri-Nutrients Co."},
    {"company_id": 3532, "name": "Sahara International Petrochemical Co."},
    {"company_id": 3726, "name": "Saleh Abdulaziz Al Rashed and Sons Co."},
    {"company_id": 3660, "name": "Saudi Arabian Mining Co."},
    {"company_id": 3655, "name": "Saudi Aramco Base Oil Co."},
    {"company_id": 3675, "name": "Saudi Basic Industries Corp."},
    {"company_id": 3703, "name": "Saudi Cement Co."},
    {"company_id": 3686, "name": "Saudi Industrial Investment Group"},
    {"company_id": 3730, "name": "Saudi Kayan Petrochemical Co."},
    {"company_id": 3681, "name": "Saudi Lime Industries Co."},
    {"company_id": 3653, "name": "Saudi Paper Manufacturing Co."},
    {"company_id": 3677, "name": "Saudi Steel Pipe Co."},
    {"company_id": 3729, "name": "Saudi Top for Trading Co."},
    {"company_id": 3652, "name": "Saudi Vitrified Clay Pipes Co."},
    {"company_id": 3693, "name": "Southern Province Cement Co."},
    {"company_id": 3698, "name": "Tabuk Cement Co."},
    {"company_id": 3722, "name": "Takween Advanced Industries Co."},
    {"company_id": 3714, "name": "Taqat Mineral Trading Co."},
    {"company_id": 3664, "name": "The National Company for Glass Industries"},
    {"company_id": 3697, "name": "Umm Al-Qura Cement Co."},
    {"company_id": 3683, "name": "United Carton Industries Co."},
    {"company_id": 3658, "name": "United Wire Factories Co."},
    {"company_id": 3673, "name": "Watani Iron Steel Co."},
    {"company_id": 3696, "name": "Yamama Cement Co."},
    {"company_id": 3700, "name": "Yanbu Cement Co."},
    {"company_id": 3746, "name": "Yanbu National Petrochemical Co."},
    {"company_id": 3724, "name": "Zahrat Al Waha for Trading Co."},
    {"company_id": 3587, "name": "Time Entertainment Co."},
    {"company_id": 3649, "name": "Jamjoom Pharmaceuticals Factory Co."},
    {"company_id": 3650, "name": "Middle East Pharmaceutical Industries Co."},
    {"company_id": 3651, "name": "Saudi Pharmaceutical Industries and Medical Appliances Corp."},
    {"company_id": 3551, "name": "Alramz Real Estate Co."},
    {"company_id": 3639, "name": "Arriyadh Development Co."},
    {"company_id": 3608, "name": "Banan Real Estate Co."},
    {"company_id": 3537, "name": "Dar Al Majed Real Estate Co."},
    {"company_id": 3636, "name": "First Avenue for Real Estate Development Co."},
    {"company_id": 3542, "name": "Jabal Omar Development Co."},
    {"company_id": 3645, "name": "Ladun Investment Co."},
    {"company_id": 3648, "name": "Retal Urban Development Co."},
    {"company_id": 3578, "name": "Saudi Real Estate Co."},
    {"company_id": 3589, "name": "Advance International Company for Communication and Information Technology"},
    {"company_id": 3573, "name": "Al Moammar Information Systems Co."},
    {"company_id": 3582, "name": "Etihad GO Telecom Co."},
    {"company_id": 3571, "name": "Khaled Dhafer and Brothers for Logistics Services Co."},
    {"company_id": 3581, "name": "Saudi Ground Services Co."},
    {"company_id": 3586, "name": "Natural Gas Distribution Co."},
]

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


class NoMetricsFoundError(Exception):
    """Raised when the built analysis has no ratio metrics at all for this
    company - not an error to retry, just nothing to select/export."""


def click_ratio_if_present(page, label, exact=False, logger=None):
    """Click a ratio's label if it's on the page; if it doesn't exist for
    this company (not every company has every ratio), log and move on
    instead of failing the whole company over one missing metric."""
    locator = page.get_by_text(label, exact=exact)
    try:
        locator.wait_for(state="visible", timeout=ELEMENT_CLICK_TIMEOUT_MS)
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
        "Financing", "DuPont Analysis", "Coverage", "Asset Valuation",
    ]:
        click_section_if_present(page, section, logger=logger)
    page.get_by_role("button", name="Expand subsection").click()

    ratio_labels = [
        "Cash Conversion Cycle", "Days Inventory on Hand (DIO)",
        "Days Payables Outstanding (", "Days Sales Outstanding (DSO)",
        "Inventory Turnover", "Payables Turnover", "Receivables Turnover",
        "Total Assets Turnover", "Working Capital Turnover",
        "Book Value of Equity", "CFO Finance Cost Coverage", "Debt Coverage",
        "Debt Payment Ratio", "Dividend Payment Coverage", "EBITDA Coverage",
        "Interest Coverage", "OCF Debt Service Ratio (OCF",
        "Operating Cash Flow to", "Reinvestment Ratio", "Interest Burden",
        "Tax Burden", "Net Borrowing", "Total Debt Service",
        "Free Cash Flow (FCF)", "Free Cash Flow to Equity (",
        "Free Cash Flow to Firm - FCFF", "Cash Ratio", "Current Ratio",
        "Quick Ratio", "EBIT Margin", "EBITDA Margin", "Gross Profit Margin",
        "Net Profit Margin", "Operating Profit Margin", "Pretax Profit Margin",
        "CapEx to Depreciation", "CapEx to Revenue", "Cash Flow Quality",
        "Cash Flow to Revenue", "Cash Return on Assets",
        "Cash Return on Equity", "Cash to Operating Income",
        "Dividend Payout Ratio", "Retention Ratio",
        "NOPAT (Tax Rate Assumed Zero)", "Operating Return on Assets",
        "ROA Adjusted (Tax Rate", "ROE (DuPont 3-Factor)",
        "ROE (DuPont 5-Factor)", "Return on Assets (ROA)",
        "Return on Equity (ROE)", "Debt to Assets", "Debt to Capital",
        "Debt to Equity", "Financial Leverage", "Net Debt to EBITDA",
    ]
    for label in ratio_labels:
        click_ratio_if_present(page, label, logger=logger)

    # Exact-match labels: substrings above (e.g. "Debt to EBITDA" vs
    # "Net Debt to EBITDA") would otherwise match the wrong element.
    for label in ["Working Capital", "EBITDA", "Gross Profit", "Debt to EBITDA", "Net Debt", "Total Debt"]:
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


with sync_playwright() as playwright:
    run(playwright, COMPANIES)
