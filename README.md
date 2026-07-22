# Lameh QA

QA tooling and scope documentation for the Lameh platform.

## Contents

- **[QA-SCOPE.md](QA-SCOPE.md)** — one-page inventory of what needs testing across the platform (modules, testing types, known risk areas).
- **[AGENT.md](AGENT.md)** — detailed developer/agent notes for `tests/integration/sector_analysis_ratios.py`.
- **`tests/integration/sector_analysis_ratios.py`** — standalone script that verifies internal consistency of every "Financial Ratios" metric in a Sector Analysis chart-builder export, by recomputing each ratio from its own reported input components.
- **`results/`** — generated CSV reports from running the ratio-verification script (gitignored).

## Setup

Requires Python >= 3.11 and [Poetry](https://python-poetry.org/).

```
poetry install
```

## Usage

Run the Sector Analysis ratio check against an exported `.xlsx`:

```
poetry run poe ratios path/to/export.xlsx [--tolerance 0.005] [--csv out.csv] [--fail-only] [--web-only]
```

See [AGENT.md](AGENT.md) for full details on the script's behavior, conventions, and known bugs it has found.
