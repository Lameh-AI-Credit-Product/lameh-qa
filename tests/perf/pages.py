"""Page objects for the load-time perf test.

Every selector the suite uses lives in this file: when the app moves a
button, this is the only file to open.

Absolute XPaths and the candidate lists
---------------------------------------
Several elements were originally pinned with absolute XPaths
(`/html/body/div[4]/div[1]/...`), which break on any layout change. Those
are expressed here as *candidate lists*: a rewritten role/text locator
first, the original XPath last. `BasePage.wait_first` returns whichever
appears and remembers the winner, so the scan runs at most once per element
per session.

Two consequences worth knowing:

- A rewritten locator that is wrong costs nothing at runtime - the element
  is still found via the legacy XPath, and a note is printed naming the
  candidate that matched. Those notes are the fix list.
- The scan polls, so it can add up to POLL_MS to a measurement. Only the
  first run pays it: once cached, the locator is awaited through
  Playwright's own (exact) expect().
"""
import re
import time

from playwright.sync_api import Page, expect, TimeoutError as PlaywrightTimeoutError

DEFAULT_TIMEOUT_MS = 30_000
# Building a sector analysis is a server-side fetch across several
# companies - the download suite budgets ~3 minutes for the same work.
BUILD_TIMEOUT_MS = 180_000
# By the time the ratio sections render, the underlying fetch has already
# completed (that is what the Ratio tab's own long wait covers), so a
# section that isn't there after a beat genuinely isn't there.
ELEMENT_CLICK_TIMEOUT_MS = 1_000
# How long a click gets to visibly do something before it is treated as
# swallowed. Short on purpose: it is paid twice on a genuine failure, and
# it lands inside a measured window.
CLICK_SETTLE_TIMEOUT_MS = 5_000
# The built analysis has already rendered by the time this is checked, so
# it needs no build-sized budget - see wait_for_result_table.
RESULT_TABLE_TIMEOUT_MS = 10_000

POLL_MS = 50


class NoMetricsFoundError(Exception):
    """The built analysis has no ratio metrics at all for this selection."""

# Resolved candidate index per element key, shared across page instances so
# a re-instantiated page object doesn't repeat the scan.
_resolved = {}


class BasePage:
    """Common functionality shared by all page objects."""

    # Clicking a table value shows a highlight overlay on the chart at that
    # value's position. Several of these divs can exist (one per
    # point/transition state); the active one has "opacity: 1;" inline
    # while the others fade to 0.
    #
    # No currently-enabled scenario clicks a value cell - these are here
    # for the chart_load / sector_analysis_value_load scenarios kept in
    # `old/`, which are the reason the helper is shared rather than
    # per-page.
    ACTIVE_VALUE_OVERLAY = 'div.pointer-overlay-transition[style*="opacity: 1;"]'

    def __init__(self, page: Page):
        self.page = page

    def locator(self, selector: str):
        return self.page.locator(selector)

    @staticmethod
    def _as_number(text: str):
        cleaned = text.strip().replace(",", "").replace("%", "").replace("$", "")
        try:
            return float(cleaned)
        except ValueError:
            return None

    def wait_first(self, key: str, candidates, timeout_ms: int = DEFAULT_TIMEOUT_MS):
        """The first candidate locator that becomes visible.

        `candidates` is an ordered list of callables taking the page and
        returning a locator; `key` names the element for caching and for
        the "matched fallback" note.
        """
        if key in _resolved:
            locator = candidates[_resolved[key]](self.page).first
            expect(locator).to_be_visible(timeout=timeout_ms)
            return locator

        deadline = time.monotonic() + timeout_ms / 1000
        while True:
            for index, build in enumerate(candidates):
                locator = build(self.page).first
                try:
                    if locator.is_visible():
                        _resolved[key] = index
                        if index > 0:
                            print(f"  note: {key} matched fallback candidate #{index} "
                                  f"- the preferred locator missed, worth fixing")
                        return locator
                except Exception:
                    continue  # one malformed candidate must not mask the rest
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"none of the {len(candidates)} candidate locators for {key!r} "
                    f"became visible within {timeout_ms}ms")
            self.page.wait_for_timeout(POLL_MS)

    def wait_for_list_text(self, key, candidates, timeout_ms=DEFAULT_TIMEOUT_MS):
        """Wait until a list's first cell holds real text.

        The list pages render skeleton rows while fetching, so the row
        exists well before the data does - text is the only honest
        "loaded" signal, visibility is not.
        """
        expect(self.wait_first(key, candidates, timeout_ms)).to_have_text(
            re.compile(r"\S"), timeout=timeout_ms)

    def wait_ready(self, timeout_ms: int = DEFAULT_TIMEOUT_MS):
        """Best-effort wait for in-flight requests to settle after an
        action that triggers a fetch (a search, a section switch).

        Swallowed on timeout, as in the download suite: some pages hold a
        long-poll or websocket open and never reach "networkidle", so this
        is advisory - never the only thing a caller waits on.
        """
        try:
            self.page.wait_for_load_state("networkidle", timeout=timeout_ms)
        except PlaywrightTimeoutError:
            pass

    def get_active_value_overlay_style(self):
        """The active overlay's style attribute, or None if absent."""
        overlay = self.locator(self.ACTIVE_VALUE_OVERLAY)
        if overlay.count() == 0:
            return None
        return overlay.first.get_attribute("style")

    def wait_for_value_overlay(self, previous_style: str = None,
                               timeout: int = DEFAULT_TIMEOUT_MS) -> str:
        """Wait for the active value overlay, then return its style.

        With `previous_style` given, visibility alone isn't enough: the
        previous click's overlay can still be visible (same element, or a
        new one rendering before the old detaches), so wait until the
        style (position/transform) actually differs before calling the new
        value "shown".
        """
        if previous_style is None:
            expect(self.locator(self.ACTIVE_VALUE_OVERLAY).first).to_be_visible(timeout=timeout)
        else:
            self.page.wait_for_function(
                """(prevStyle) => {
                    const el = document.querySelector('div.pointer-overlay-transition[style*="opacity: 1;"]');
                    return el && el.getAttribute('style') !== prevStyle;
                }""",
                arg=previous_style,
                timeout=timeout,
            )
        return self.get_active_value_overlay_style()


class DashboardPage(BasePage):
    # Enabled once the cards have finished loading. The accessible name is
    # confirmed against the live app, so the absolute XPath is kept only
    # as a fallback for older layouts.
    SEARCH_BAR = [
        lambda page: page.get_by_role("textbox", name="Search by name or reference"),
        lambda page: page.locator(
            "xpath=/html/body/div[4]/div[1]/div/div[2]/main/div[2]/div/div/input"),
    ]

    # "View Analysis" on the first dashboard card.
    FIRST_CARD_ANALYSIS_BUTTON = [
        lambda page: page.get_by_role("button", name=re.compile(r"View Analysis", re.I)),
        lambda page: page.locator(
            "xpath=/html/body/div[4]/div[1]/div/div[2]/main/div[3]/div[1]/div[1]/div/div/footer/div[3]/button[1]"),
    ]

    def __init__(self, page: Page, base_url: str):
        super().__init__(page)
        self.url = f"{base_url.rstrip('/')}/"

    def goto(self):
        self.page.goto(self.url, wait_until="load")

    def wait_until_loaded(self):
        expect(self.wait_first("dashboard_search", self.SEARCH_BAR)).to_be_enabled(
            timeout=DEFAULT_TIMEOUT_MS)

    def search_company(self, name: str):
        """Filter the dashboard cards down to one company by name.

        Without this the suite opened whichever card happened to render
        first, so the analysis it measured changed with the dashboard's
        own ordering - and companies differ enough in size that the timing
        was not comparable run to run.

        The results are fetched asynchronously, so a settle wait comes
        before checking the name is on screen: the searched company may
        already be visible in the unfiltered grid, which would make a bare
        text check pass against the pre-search cards.
        """
        search_bar = self.wait_first("dashboard_search", self.SEARCH_BAR)
        search_bar.click()
        search_bar.fill(name)
        self.wait_ready()
        expect(self.page.get_by_text(name).first).to_be_visible(timeout=DEFAULT_TIMEOUT_MS)

    def open_first_card_analysis(self):
        """Open the first card's analysis, and confirm it opened.

        Called after search_company, "first" is "the only one" - the
        search is what makes this deterministic.

        The click is verified rather than fired and forgotten: a card can
        render before its click handler is attached, and a click in that
        window lands on a live element, does nothing, and still counts as
        a successful click to Playwright. The symptom is a button that is
        plainly visible followed by a navigation wait that never resolves.
        One retry covers it. A retry inflates that run's measurement by up
        to CLICK_SETTLE_TIMEOUT_MS, so it says when it happens.
        """
        button = self.wait_first("first_card_analysis_button", self.FIRST_CARD_ANALYSIS_BUTTON)
        expect(button).to_be_enabled(timeout=DEFAULT_TIMEOUT_MS)

        for attempt in (1, 2):
            button.click()
            try:
                self.page.wait_for_url(AnalysisPage.URL_PATTERN,
                                       timeout=CLICK_SETTLE_TIMEOUT_MS)
                return
            except PlaywrightTimeoutError:
                if attempt == 2:
                    raise RuntimeError(
                        "clicking \"View Analysis\" never navigated to a company "
                        "page, twice - the button is visible but not wired up, or "
                        "the card belongs to a different company than the search "
                        "left on screen")
                print("  note: View Analysis click did not navigate, retrying once "
                      "(this run's analysis_load_s includes the wasted wait)")


class AnalysisPage(BasePage):
    URL_PATTERN = "**/company/**"

    # The id carries a Radix-generated fragment (e.g.
    # "radix-<r5>-content-fs-analysis") that changes between renders, so
    # match a stable substring instead of the literal id.
    PANEL = '[id*="content-fs-analysis"]'

    COMPANY_NAME = 'xpath=//*[@id="split-container"]/div/div[1]/div[1]/div[1]/h1'

    def wait_for_navigation(self):
        self.page.wait_for_url(self.URL_PATTERN, timeout=DEFAULT_TIMEOUT_MS)

    def wait_until_loaded(self):
        expect(self.locator(self.PANEL)).to_be_visible(timeout=DEFAULT_TIMEOUT_MS)

    def get_company_name(self) -> str:
        return self.locator(self.COMPANY_NAME).inner_text().strip()


class SectorAnalysisPage(BasePage):
    URL_PATTERN = "**/chart-builder**"

    # Sidebar entry point; matched on its stable href rather than DOM
    # position.
    SIDEBAR_LINK = 'a[href="/chart-builder"]'

    # First cell of the saved-analyses list.
    LIST_FIRST_CELL = [
        lambda page: page.locator("main table tbody tr").first.locator("td").first,
        lambda page: page.locator(
            "xpath=/html/body/div[4]/div[1]/div/div[2]/div/div/div[2]/section/div/div[1]/table/tbody/tr[1]/td[1]"),
    ]

    # Exact text: a substring match on "New Analysis" also catches a wizard
    # step-card button ("New Analysis Companies, ...").
    NEW_ANALYSIS_BUTTON = 'button:text-is("New Analysis")'

    # Reused across wizard steps. Scoped by the "min-w-[100px]" class shared
    # by every wizard Next/Back button rather than by text alone: once the
    # company list populates, pagination renders its own "Next" and a bare
    # :has-text("Next") becomes ambiguous (strict-mode violation).
    NEXT_BUTTON = 'button[class*="min-w-[100px]"]:has-text("Next")'

    SEARCH_INPUT = 'input[placeholder="Search companies or sectors..."]'

    # After a search, this row selects just the matching result(s) for that
    # term, not the sector's full company list. Once checked its aria-label
    # flips to "Deselect all companies in ...", which is how we confirm the
    # click registered.
    SELECT_ALL_ROW = 'button[aria-label^="Select all companies in"]'
    DESELECT_ALL_ROW = 'button[aria-label^="Deselect all companies in"]'

    BUILD_NOW_BUTTON = 'button:has-text("Build Now")'

    # Build Now redirects to the new analysis's own URL - there is no id
    # to hardcode, so match the shape.
    BUILT_ANALYSIS_URL_RE = re.compile(r"/chart-builder/analysis/.+")
    # Shown once the built analysis has finished rendering.
    SELECTED_METRICS_TEXT = "Selected Metrics"

    # "chart-container" is a stable id; scope the table off it rather than
    # an absolute path, so wrapper divs shifting doesn't break it.
    RESULT_TABLE = "#chart-container table"
    RESULT_ROWS = f"{RESULT_TABLE} tbody tr"
    # Expanding a row reveals nested detail rows whose value cells have
    # role="button".
    VALUE_CELL = f"{RESULT_TABLE} tbody td[role='button']"

    def open(self):
        self.locator(self.SIDEBAR_LINK).click()
        self.page.wait_for_url(self.URL_PATTERN, timeout=DEFAULT_TIMEOUT_MS)

    def wait_until_loaded(self):
        self.wait_for_list_text("sector_list_first_cell", self.LIST_FIRST_CELL)

    def click_new_analysis(self):
        self.locator(self.NEW_ANALYSIS_BUTTON).click()

    def click_next(self):
        next_button = self.locator(self.NEXT_BUTTON)
        expect(next_button).to_be_enabled(timeout=DEFAULT_TIMEOUT_MS)
        next_button.click()

    def select_company(self, name: str):
        """Search for a company and check its "select all" row.

        Two asynchronous steps need waiting out, both of which caused
        intermittent failures before they were handled. The results list is
        debounced, so right after fill() it can still show the previous
        search's rows - clicking one selects the wrong company. And the
        click itself only lands when the row's aria-label flips; without
        that wait, "Next" can stay disabled because a selection never
        registered.
        """
        self.locator(self.SEARCH_INPUT).fill(name)
        expect(self.page.get_by_text(name).first).to_be_visible(timeout=DEFAULT_TIMEOUT_MS)
        self.locator(self.SELECT_ALL_ROW).first.click()
        expect(self.locator(self.DESELECT_ALL_ROW).first).to_be_visible(timeout=DEFAULT_TIMEOUT_MS)

    def select_companies(self, names):
        for name in names:
            self.select_company(name)

    def expand_metric_section(self, section_name: str):
        """Expand a collapsible metric group (e.g. "Income Statement")."""
        self.page.locator(f'button:has(h5:text-is("{section_name}"))').click()

    def select_metric(self, metric_name: str):
        item = self.page.get_by_text(metric_name, exact=True)
        item.scroll_into_view_if_needed()
        item.click()

    def select_metrics(self, names):
        for name in names:
            self.select_metric(name)

    # --- the Ratio tab ------------------------------------------------
    # Borrowed from tests/E2E/sector_analysis_download_company_ratios.py
    # (select_all_ratios), with one deliberate difference: that script
    # skips a ratio it cannot find, because it sweeps ~120 labels across
    # companies that genuinely don't all have them. Here a missing ratio
    # raises. A perf run that silently selected three of four ratios
    # would still produce a build time, just not a comparable one.

    RATIO_TAB_BUTTON = "Ratio"
    NO_METRICS_TEXT = "No metrics found."
    # The Ratio tab shows both "Market Data" and "Financial Ratios" as
    # top-level sections, each with its own "Expand section" button.
    FINANCIAL_RATIOS_SECTION = "Financial Ratios"

    # Ratios live inside collapsed subsections; the wanted ones have to be
    # on screen before they can be clicked. Not every selection has every
    # section (a company with no debt has no Coverage), so a missing one
    # is skipped here - the ratio click is what enforces presence.
    RATIO_SECTIONS = [
        "Solvency Ratios", "Profitability Ratios", re.compile(r"^Profitability$"),
        "Performance", "Margins", "Liquidity Ratios", "Free Cash Flow",
        "Financing", "DuPont Analysis", "Coverage",
        "Asset Valuation", "Activity Ratios",
    ]

    def open_ratio_tab(self):
        """Switch the metric picker to Ratio and expand Financial Ratios.

        The tab depends on a data fetch that can take minutes, well past
        the default action timeout, so the button is waited for explicitly
        rather than left to the click's own shorter budget.
        """
        button = self.page.get_by_role("button", name=self.RATIO_TAB_BUTTON)
        button.wait_for(state="visible", timeout=BUILD_TIMEOUT_MS)
        button.click()

        try:
            self.page.get_by_text(self.NO_METRICS_TEXT).wait_for(
                state="visible", timeout=ELEMENT_CLICK_TIMEOUT_MS)
            raise NoMetricsFoundError(
                "the Ratio tab reports \"No metrics found.\" for this company "
                "selection - nothing to build")
        except PlaywrightTimeoutError:
            pass  # good: metrics are present

        self.page.get_by_role("button", name="Expand section").filter(
            has_text=self.FINANCIAL_RATIOS_SECTION).click()

    def expand_ratio_sections(self, sections=None):
        for section in (self.RATIO_SECTIONS if sections is None else sections):
            locator = self.page.locator("button").filter(has_text=section)
            try:
                locator.wait_for(state="visible", timeout=ELEMENT_CLICK_TIMEOUT_MS)
                locator.click()
            except PlaywrightTimeoutError:
                continue

    def select_ratio(self, label: str, exact: bool = True):
        """Click one ratio by its visible label.

        Same shape as select_metric - the ratio list is long enough that
        the target is usually scrolled out of view - but strict: an absent
        label raises rather than being skipped.
        """
        item = self.page.get_by_text(label, exact=exact)
        try:
            item.scroll_into_view_if_needed(timeout=DEFAULT_TIMEOUT_MS)
        except PlaywrightTimeoutError as e:
            raise RuntimeError(
                f"ratio {label!r} is not in the picker - it may have been "
                f"renamed or dropped, or its section failed to expand") from e
        item.click()

    def select_ratios(self, labels, sections=None):
        self.open_ratio_tab()
        self.expand_ratio_sections(sections)
        for label in labels:
            self.select_ratio(label)

    def make_chart_name_unique(self):
        """Append a timestamp to the auto-generated chart name.

        The default generator isn't unique enough across repeated runs, and
        the build fails with "A chart with the name X already exists in this
        organization". The name field's classes are generic Tailwind
        utilities shared with many inputs, so rather than pin a fragile
        selector, take the first visible input that already holds a value.
        """
        inputs = self.page.locator("input:visible")
        for i in range(inputs.count()):
            candidate = inputs.nth(i)
            value = candidate.input_value()
            if value:
                candidate.fill(f"{value} {time.strftime('%H%M%S')}")
                return
        raise RuntimeError("No visible input with a default chart name found")

    def click_build_now(self):
        button = self.locator(self.BUILD_NOW_BUTTON)
        expect(button).to_be_enabled(timeout=DEFAULT_TIMEOUT_MS)
        button.click()

    def wait_for_result_table(self):
        """Wait for a freshly built analysis to finish rendering.

        The signals here are the download suite's, which run against this
        app constantly, rather than the perf suite's inherited
        `#chart-container table` - that selector waited out the full
        BUILD_TIMEOUT_MS without ever resolving.

        Build Now redirects to the new analysis's own URL, so there is no
        analysis id to hardcode: wait for the navigation, then for
        "Selected Metrics", which is what the download step treats as
        "finished generating" before it exports.

        Both locators are narrowed to `.first`, which the download suite
        does not need to do: it builds one company per analysis, while
        this measures a comparison across several, and every company's
        section carries its own "Selected Metrics" label. Left unnarrowed
        the wait resolves to one element per company and fails the whole
        scenario on a strict-mode violation - after a build that in fact
        succeeded. The first section rendering is the signal here; a
        company whose section renders late is not distinguished.
        """
        self.page.wait_for_url(self.BUILT_ANALYSIS_URL_RE, timeout=BUILD_TIMEOUT_MS)
        self.page.get_by_text(self.SELECTED_METRICS_TEXT).first.wait_for(
            state="visible", timeout=BUILD_TIMEOUT_MS)

        # The result table itself is advisory: it is the older signal and
        # may no longer match, and by this point the analysis has already
        # rendered. Never let it block or fail the measurement - including
        # on a strict-mode violation, for the same multi-company reason.
        try:
            expect(self.locator(self.RESULT_TABLE).first).to_be_visible(
                timeout=RESULT_TABLE_TIMEOUT_MS)
        except Exception:
            print(f"  note: {self.RESULT_TABLE!r} never appeared - timing ended at "
                  f"\"{self.SELECTED_METRICS_TEXT}\"; the selector is likely stale")
