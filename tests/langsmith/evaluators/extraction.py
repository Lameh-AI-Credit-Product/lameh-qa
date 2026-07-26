"""
Lameh Intelligence - response value extraction
================================================
Parses the agent's `full_answer` text into structured facts. The response
format is NOT free prose to fuzzy-match - it's self-annotating markup, e.g.:

    <calc v="11.72%" f="Net Profit / Average Total Equity"
          rid="Return on Equity (ROE)" id="a9f7c2b7-..." />

    <number id="8e661af9-..." type="main_statement" year="2023"
            period="yearly" section="Balance Sheet" raw="303076973.0"
            multiplier="1" company="...شركة..."
            metric="Total Debt">303.1</number>

`<number>` tags carry an explicit company/year/metric/raw source value - the
most reliable extraction target, used for grounding. `<calc>` tags carry the
agent's own computed ratio value (`rid` = canonical ratio name matching
sector_analysis_ratios.py's conventions) plus optional `ops` - a JSON list of
the raw <number>-like facts that fed the calculation, for full lineage.

Known limitation: <calc> tags don't carry their own top-level year/company
attribute (unlike <number>) - both are recovered best-effort. Year: prefer
each ops entry's own "year" field when present, else regex "FY\\d{4}" out of
its "l" label, else nearest "FY\\d{4}" mention on the same line (works for
single-year-per-row tables; a wide "one column per year" summary row relies
on the ops-based paths since same-line text can't disambiguate them).
Company: prefer each ops entry's own "company" field when present (seen in
multi-company/sector-wide responses) - only some responses' ops include it,
so callers should still pass `default_company` for single-company prompts as
a fallback for when it's absent.

Rule-based today by design, per the "swap for an LLM-extractor later" plan -
callers should depend only on extract_all()'s return shape, not on how it's
produced internally.
"""

import json
import re

_ATTR_RE = re.compile(r'''(\w+)=(?:"([^"]*)"|'([^']*)')''')
_CALC_RE = re.compile(r"<calc\s+([^>]*?)/>")
_NUMBER_RE = re.compile(r"<number\s+([^>]*?)>(.*?)</number>", re.DOTALL)
_DATA_TABLE_RE = re.compile(r'<data_table\s+title="([^"]*)"\s*>')
_FY_RE = re.compile(r"FY(\d{4})")

_VALUE_UNIT_RE = re.compile(r"^\s*([+-]?[\d,]+(?:\.\d+)?)\s*([a-zA-Z%]*)\s*$")
_UNIT_MULTIPLIERS = {
    "m": 1_000_000,
    "million": 1_000_000,
    "millions": 1_000_000,
    "b": 1_000_000_000,
    "billion": 1_000_000_000,
}
_UNIT_LABELS = {
    "%": "percent",
    "x": "multiple",
    "days": "days",
    "day": "days",
    "m": "amount",
    "million": "amount",
    "millions": "amount",
    "b": "amount",
    "billion": "amount",
}


def parse_attrs(attr_string):
    """'v="11.72%" rid="Return on Equity (ROE)"' -> {"v": "11.72%", "rid": "..."}
    Handles both double-quoted and single-quoted attribute values, since the
    `ops` attribute is single-quoted to hold embedded double-quoted JSON."""
    attrs = {}
    for match in _ATTR_RE.finditer(attr_string):
        name = match.group(1)
        value = match.group(2) if match.group(2) is not None else match.group(3)
        attrs[name] = value
    return attrs


def parse_formatted_value(v_str):
    """"11.72%" -> (11.72, "percent"); "5.31x" -> (5.31, "multiple");
    "0.14 days" -> (0.14, "days"); "127.6M" -> (127_600_000.0, "amount").
    Returns (None, None) if it can't be parsed."""
    if not v_str:
        return None, None
    match = _VALUE_UNIT_RE.match(v_str)
    if not match:
        return None, None
    number_str, unit = match.group(1).replace(",", ""), match.group(2).strip().lower()
    try:
        number = float(number_str)
    except ValueError:
        return None, None
    if unit in _UNIT_MULTIPLIERS:
        number *= _UNIT_MULTIPLIERS[unit]
    return number, _UNIT_LABELS.get(unit)


def _nearest_fiscal_year(text, pos):
    """Same-line "FY\\d{4}" fallback, picking the mention nearest `pos` by
    character distance (in either direction) rather than just the first
    match on the line - prose can read "...from <calc/> in FY2024 to <calc/>
    in FY2025..." where the year *follows* each tag, while table rows read
    "| FY2024 | <calc/> |" where it precedes. Still unreliable when multiple
    tags for different years share one line at similar distances (e.g. a
    "one column per year" summary row) - prefer _fiscal_year_from_ops() when
    `ops` is available, since each tag's own ops labels are tag-specific
    rather than line-shared."""
    line_start = text.rfind("\n", 0, pos) + 1
    line_end = text.find("\n", pos)
    if line_end == -1:
        line_end = len(text)
    line = text[line_start:line_end]
    tag_offset = pos - line_start
    matches = list(_FY_RE.finditer(line))
    if not matches:
        return None
    nearest = min(matches, key=lambda m: abs(m.start() - tag_offset))
    return nearest.group(1)


def _fiscal_year_from_ops(ops):
    """ops entries carry component labels like "Total Debt FY2024" - tag-
    specific, unlike the same-line text. Some responses' ops entries also
    carry an explicit "year" field directly (e.g. "year": "2023") - prefer
    that when present, since it's not a regex guess. Returns the year if
    every entry agrees, else None (ambiguous - don't guess)."""
    years = set()
    for entry in ops:
        year = entry.get("year")
        if year:
            years.add(str(year))
            continue
        match = _FY_RE.search(str(entry.get("l", "")))
        if match:
            years.add(match.group(1))
    return years.pop() if len(years) == 1 else None


def _company_from_ops(ops):
    """Some responses' ops entries carry an explicit "company" field per
    component (seen in multi-company/sector-wide responses, unlike the
    single-company sample where ops only had id/l/v/raw) - this is how a
    <calc> tag's company can be recovered without a caller-supplied
    default_company. Returns the company if every entry agrees, else None."""
    companies = {entry.get("company") for entry in ops if entry.get("company")}
    return companies.pop() if len(companies) == 1 else None


def _enclosing_table_title(text, pos):
    """Title of the nearest preceding <data_table title="..."> before `pos`,
    used as a fallback label when a <calc> tag has no `rid` (e.g. an ad-hoc
    ratio the agent computed on the fly, like "Debt to CapEx" in the sample
    response - it's only named via its table's title, not tagged with a
    canonical ratio id). This is a fuzzy fallback, not an exact metric name -
    callers should fuzzy-match it against expected metric names rather than
    treat it as equal to a canonical rid."""
    last_title = None
    for match in _DATA_TABLE_RE.finditer(text, 0, pos):
        last_title = match.group(1)
    return last_title


def extract_number_facts(text):
    """Every <number>...</number> tag -> one fact dict with an explicit
    company/metric/fiscal_year/value, using `raw` (the true source figure,
    scaled by `multiplier`) rather than the rounded display text."""
    facts = []
    for match in _NUMBER_RE.finditer(text):
        attrs = parse_attrs(match.group(1))
        raw = attrs.get("raw")
        multiplier = attrs.get("multiplier")
        try:
            raw = float(raw) if raw is not None else None
        except ValueError:
            raw = None
        try:
            multiplier = float(multiplier) if multiplier is not None else 1.0
        except ValueError:
            multiplier = 1.0
        value = raw * multiplier if raw is not None else None
        facts.append({
            "kind": "number",
            "id": attrs.get("id"),
            "company": attrs.get("company"),
            "metric": attrs.get("metric"),
            "fiscal_year": attrs.get("year"),
            "period": attrs.get("period"),
            "section": attrs.get("section"),
            "value": value,
            "display_value": match.group(2).strip(),
        })
    return facts


def extract_calc_facts(text, default_company=None):
    """Every <calc .../> tag -> one fact dict for the agent's own computed
    ratio value. `metric` is the `rid` attribute (canonical ratio name).
    `ops`, when present, is the parsed JSON list of raw facts used to derive
    the value (id/label/value/raw per component)."""
    facts = []
    for match in _CALC_RE.finditer(text):
        attrs = parse_attrs(match.group(1))
        value, unit = parse_formatted_value(attrs.get("v", ""))
        ops = []
        if attrs.get("ops"):
            try:
                ops = json.loads(attrs["ops"])
            except json.JSONDecodeError:
                ops = []
        fiscal_year = _fiscal_year_from_ops(ops) or _nearest_fiscal_year(text, match.start())
        facts.append({
            "kind": "calc",
            "id": attrs.get("id"),
            "company": _company_from_ops(ops) or default_company,
            "metric": attrs.get("rid"),
            "table_title": _enclosing_table_title(text, match.start()) if attrs.get("rid") is None else None,
            "formula": attrs.get("f"),
            "fiscal_year": fiscal_year,
            "value": value,
            "unit": unit,
            "display_value": attrs.get("v"),
            "ops": ops,
        })
    return facts


def extract_all(text, default_company=None):
    """All extracted facts from a response's full_answer text - <number> facts
    first (most reliable, used for grounding), then <calc> facts (the agent's
    stated ratio values, used for correctness comparison)."""
    return extract_number_facts(text) + extract_calc_facts(text, default_company=default_company)
