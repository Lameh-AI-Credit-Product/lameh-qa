"""
Load-time performance test (E2E, Playwright) - the runner
==========================================================
Measures how long the app takes to finish a handful of user-visible
actions, repeated N times in one authenticated browser session, and writes
a CSV + JSON + HTML report per run.

Scenarios, in the order they run (each depends on where the previous one
left the browser):

  1. dashboard_load_s        - navigating to "/" until the search bar is
                               enabled (proxy for "the cards finished
                               loading").
  2. analysis_load_s         - clicking "View Analysis" on the searched-for
                               company's card until the analysis panel is
                               visible on the resulting /company/{id}
                               page. The search that isolates that card is
                               setup, and runs before the timer.
  3. sector_analysis_load_s  - opening Sector Analysis from the sidebar
                               until its saved-analyses list has real text
                               in it (it renders as a skeleton while
                               loading).
  4. sector_analysis_build_s - clicking Build Now at the end of the "New
                               Analysis" wizard until the result table is
                               visible. The wizard - companies, then a few
                               ratios from the Ratio tab - runs *before*
                               the timer: only the build itself is
                               measured.

Login uses OTP, entered by hand: the script opens a real browser window and
waits for you to press Enter, before any timer starts. Every execution gets
a fresh browser context, so nothing (cookies, cache, storage) carries over
between runs of the script.

A scenario failure does not stop the test. A full-page screenshot is saved,
the metric is recorded as "F" for that run, and the remaining scenarios and
runs still execute - though later scenarios in the same run may fail too if
they needed state the failed one would have set up (e.g. being on the right
page).

Usage
-----
    poetry run poe perf
    poetry run poe perf --runs 10
    poetry run poe perf --runs 3 --url https://frontend-uat-36462279645.me-central2.run.app

Reports land in `results/perf/<ENV>-<timestamp>/` (`<ENV>-` is dropped when
$ENV is unset - see "The ENV label" in AGENT.md; unlike the download step,
it is optional here).

Layout
------
Three files that travel together, imported as siblings (running this script
by path puts its own directory on sys.path, so no package wiring is
needed):

  pages.py      every page object, and therefore every selector
  reporting.py  stats and the CSV/JSON/HTML writers - no Playwright
  load_times.py this file: config, timing, session, scenarios, main

Adding a scenario
-----------------
Write `scenario_xxx(pages, page, ctx) -> {"metric_name_s": seconds}` and
add `("metric_name_s", scenario_xxx)` to SCENARIOS. Everything downstream -
stats, CSV columns, HTML charts - is driven by whichever metric keys show
up in the records, so there is nothing else to register. `pages` bundles
every page object; `ctx` carries per-run info (run index, run directory,
base URL).
"""
import argparse
import os
import sys
import time
import traceback
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

import reporting
from pages import (
    AnalysisPage,
    DashboardPage,
    DEFAULT_TIMEOUT_MS,
    SectorAnalysisPage,
)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:  # optional - fall back to the real environment
    pass


# --- config -----------------------------------------------------------------

BASE_URL = os.environ.get("BASE_URL", "https://frontend-dev-36462279645.me-central2.run.app")

HEADLESS = False  # OTP login needs a visible window

# Fixed inputs for the "New Analysis" wizard.
SECTOR_ANALYSIS_COMPANIES = [
    "Armah Sports Co.",
    "MOBI Industry Co.",
    "Saudi Vitrified Clay Pipes Co."
]
# The company whose analysis the dashboard scenario opens. Searched for by
# name rather than taking whichever card renders first, so the same
# analysis is measured every run.
DASHBOARD_COMPANY = "Armah Sports Co."

# Ratios to select in the wizard's Ratio tab. Keep this list short and to
# ratios that exist everywhere: all four read 100% coverage across all 131
# companies in the 2026-08-10 dev run, so the build measures the same work
# on every run instead of quietly shrinking when a ratio goes missing (the
# app drops and renames these - see AGENT.md).
SECTOR_ANALYSIS_RATIOS = [
    "Current Ratio",
    "Quick Ratio",
    "Net Profit Margin",
    "Return on Equity (ROE)",
]

# The statement-metric path the suite used before, kept because
# SectorAnalysisPage still exposes it: swap the two calls in
# scenario_sector_analysis_build to measure that instead.
SECTOR_ANALYSIS_METRIC_SECTION = "Income Statement"
SECTOR_ANALYSIS_METRICS = [
    "Income Tax / Zakat Expense",
    "Net Profit for the Period",
]

REPO_ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIR = REPO_ROOT / "results" / "perf"

VALID_ENVS = ("DEV", "UAT", "CORE")


# --- timing -----------------------------------------------------------------

class Timer:
    """Context manager recording elapsed wall-clock seconds in `.elapsed`."""

    def __enter__(self):
        self._start = time.perf_counter()
        return self

    def __exit__(self, *exc_info):
        self.elapsed = time.perf_counter() - self._start


# --- session ----------------------------------------------------------------

@dataclass
class Pages:
    """Every page object, bundled so scenarios take one argument."""
    dashboard: DashboardPage
    analysis: AnalysisPage
    sector: SectorAnalysisPage


@dataclass
class RunContext:
    run_index: int
    run_dir: Path
    base_url: str


@contextmanager
def start_session(base_url):
    """A logged-in browser session, yielding (page, pages).

    The browser context is created fresh each execution, so no cookies,
    cache or storage survive from a previous run of this script.

    Login is manual OTP and deliberately sits outside every timer - the
    same step the download suite uses, and how long a human takes to read
    an SMS is not a property of the app.
    """
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=HEADLESS)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()
        try:
            page.goto(f"{base_url.rstrip('/')}/login")
            input("Complete OTP login in the browser window, "
                  "then press Enter here to continue...")
            page.get_by_role("link", name="Sector Analysis").wait_for(
                state="visible", timeout=DEFAULT_TIMEOUT_MS)

            yield page, Pages(
                dashboard=DashboardPage(page, base_url),
                analysis=AnalysisPage(page),
                sector=SectorAnalysisPage(page),
            )
        finally:
            context.close()
            browser.close()


# --- scenarios --------------------------------------------------------------

def scenario_dashboard_load(pages, page, ctx):
    with Timer() as t:
        pages.dashboard.goto()
        pages.dashboard.wait_until_loaded()
    return {"dashboard_load_s": t.elapsed}


def scenario_analysis_load(pages, page, ctx):
    # Finding the company is setup, not the measured load: the timer
    # starts once the right card is on screen. Add a "dashboard_search_s"
    # scenario if the search itself is worth tracking.
    pages.dashboard.search_company(DASHBOARD_COMPANY)

    with Timer() as t:
        pages.dashboard.open_first_card_analysis()
        pages.analysis.wait_for_navigation()
        pages.analysis.wait_until_loaded()
    return {"analysis_load_s": t.elapsed}


def scenario_sector_analysis_load(pages, page, ctx):
    with Timer() as t:
        pages.sector.open()
        pages.sector.wait_until_loaded()
    return {"sector_analysis_load_s": t.elapsed}


def scenario_sector_analysis_build(pages, page, ctx):
    pages.sector.click_new_analysis()
    pages.sector.select_companies(SECTOR_ANALYSIS_COMPANIES)
    pages.sector.click_next()
    pages.sector.select_ratios(SECTOR_ANALYSIS_RATIOS)
    pages.sector.click_next()
    # Renaming to dodge a name collision is setup, not part of the measured
    # build, so it happens before the timer starts.
    pages.sector.make_chart_name_unique()

    with Timer() as t:
        pages.sector.click_build_now()
        pages.sector.wait_for_result_table()
    return {"sector_analysis_build_s": t.elapsed}


# Run in order, and the order is load-bearing: the dashboard must be up
# before its search bar and card button exist, the analysis page before
# its panel, and the sector list before "New Analysis" can be clicked. The
# build leaves the browser on the freshly built analysis - the next run's
# dashboard scenario navigates back to "/" itself.
SCENARIOS = [
    ("dashboard_load_s", scenario_dashboard_load),
    ("analysis_load_s", scenario_analysis_load),
    ("sector_analysis_load_s", scenario_sector_analysis_load),
    ("sector_analysis_build_s", scenario_sector_analysis_build),
]


# --- main -------------------------------------------------------------------

def run_env():
    """The deployment label for this run, or "" if $ENV is unset/unknown.

    Optional here, unlike in the download step: a perf run refusing to
    start over a missing label would cost more than the label is worth.
    """
    value = (os.environ.get("ENV") or "").strip().upper()
    return value if value in VALID_ENVS else ""


def main():
    # Company names in the wizard are Arabic, and Windows consoles default
    # to a legacy codepage that crashes on non-ASCII prints.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--runs", type=int, default=5,
                        help="Number of repetitions (default: 5)")
    parser.add_argument("--url", default=BASE_URL,
                        help=f"Base URL to test (default: {BASE_URL})")
    args = parser.parse_args()

    env = run_env()
    timestamp = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
    run_dir = RESULTS_DIR / (f"{env}-{timestamp}" if env else timestamp)
    run_dir.mkdir(parents=True, exist_ok=True)

    meta = {"url": args.url, "env": env, "runs": args.runs, "started": timestamp}
    records = []

    with start_session(args.url) as (page, pages):
        for i in range(1, args.runs + 1):
            ctx = RunContext(run_index=i, run_dir=run_dir, base_url=args.url)
            metrics = {}
            for metric_name, scenario in SCENARIOS:
                print(f"Run {i}/{args.runs}: {scenario.__name__}...")
                try:
                    metrics.update(scenario(pages, page, ctx))
                except Exception as e:
                    screenshots_dir = run_dir / "screenshots"
                    screenshots_dir.mkdir(exist_ok=True)
                    stem = f"run_{i}_{scenario.__name__}"
                    shot = screenshots_dir / f"{stem}.png"
                    try:
                        page.screenshot(path=str(shot), full_page=True)
                    except Exception:
                        pass  # a dead page must not swallow the real error
                    # The full traceback next to the screenshot: the
                    # console message alone truncates Playwright's errors,
                    # which is where the useful part (a strict-mode
                    # violation naming its matches, a selector that timed
                    # out) actually lives.
                    (screenshots_dir / f"{stem}.txt").write_text(
                        f"{scenario.__name__} failed on run {i} of {args.runs}\n"
                        f"url: {page.url}\n\n"
                        + "".join(traceback.format_exception(e)),
                        encoding="utf-8")
                    print(f"FAILED: run {i} {scenario.__name__}: {e} "
                          f"- screenshot and traceback saved to {screenshots_dir}")
                    metrics[metric_name] = reporting.FAILURE_MARK

            display = " ".join(
                f"{k}={v:.3f}s" if isinstance(v, (int, float)) else f"{k}={v}"
                for k, v in metrics.items())
            print(f"Run {i}/{args.runs}: {display}")
            records.append({
                "run": i,
                **{k: (round(v, 3) if isinstance(v, (int, float)) else v)
                   for k, v in metrics.items()},
            })

    if not records:
        print("No runs completed - nothing to report.")
        return

    stats = reporting.summarize_all(records)
    csv_path = run_dir / "results.csv"
    json_path = run_dir / "results.json"
    html_path = run_dir / "report.html"

    reporting.write_csv(csv_path, records)
    reporting.write_json(json_path, records, stats, meta)
    reporting.write_html_report(html_path, records, stats, meta)

    print("\nDone.")
    print(f"  CSV:  {csv_path}")
    print(f"  JSON: {json_path}")
    print(f"  HTML: {html_path}")
    for key, s in stats.items():
        print(f"{key}: mean={s['mean_s']}s min={s['min_s']}s max={s['max_s']}s "
              f"stdev={s['stdev_s']}s failures={s['failures']}/{s['runs'] + s['failures']}")


if __name__ == "__main__":
    main()
