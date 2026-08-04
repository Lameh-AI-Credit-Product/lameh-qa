"""
Sector Analysis - full audit run
=================================

Runs the whole Sector Analysis pipeline end to end, in order, and reports
where everything landed:

  1. download-ratios   - Playwright: build a "select all ratios" analysis per
                         company and download the export (interactive: opens a
                         browser and waits for you to complete OTP login).
  2. ratios            - verify every ratio in every export against its own
                         reported inputs (--dir, --fail-only).
  3. ratios-summary    - aggregate the FAIL rows into one row per ratio.
  4. ratio-coverage    - of the ratios we ask for, how many companies got each.
  5. ratio-emptiness   - of the ratios that arrived, which ever held a number.

The five have always been run by hand, one after another, each needing a path
copied out of the previous one's output. That copying is the whole reason this
exists: steps 2, 4 and 5 all take the download's run directory, and step 3
takes the results directory the run directory implies.

Usage
-----
    python tests/sector_analysis_audit.py
    python tests/sector_analysis_audit.py --run-dir data/sector-analysis/<ts>
    python tests/sector_analysis_audit.py --web-only

--run-dir starts from step 2 against a run that has already been downloaded.
Worth having its own flag rather than being a rerun of everything: step 1 takes
the better part of an hour, needs a human at the OTP prompt, and hits the live
app - so re-running the four analysis steps over an existing download is the
common case, not the exception.

Finding the run directory
-------------------------
Step 1 names its own output directory after the clock at the moment it starts,
and does not report it anywhere a caller can read - it logs it, mixed into
Playwright's own console output. Rather than parse that back out, this snapshots
data/sector-analysis/ before and after and takes the directory that appeared.
That keeps step 1's stdin and stdout attached straight to the terminal, which
matters more than it sounds: it prompts for OTP with input(), so capturing its
output to scrape a path would hide the prompt the run is waiting on.

Steps 2 and 3 chain without any such guessing, because the ratio checker names
its results folder after the input folder it was given.

Failures
--------
A step that fails does not necessarily stop the audit - only the steps that
actually needed its output are skipped. Coverage and emptiness read the exports
directly and are reported even when the ratio check fell over, since a run whose
verification failed is exactly when you want to know what the download contained.
"""

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data" / "sector-analysis"
RESULTS_DIR = REPO_ROOT / "results" / "sector-analysis"

DOWNLOAD_SCRIPT = REPO_ROOT / "tests" / "E2E" / "sector_analysis_download_company_ratios.py"
RATIOS_SCRIPT = REPO_ROOT / "tests" / "integration" / "sector_analysis_ratios.py"
SUMMARY_SCRIPT = REPO_ROOT / "tests" / "integration" / "summarize_ratio_failures.py"
COVERAGE_SCRIPT = REPO_ROOT / "tests" / "coverage" / "ratio_coverage.py"
EMPTINESS_SCRIPT = REPO_ROOT / "tests" / "coverage" / "ratio_emptiness.py"


def run_step(number, total, title, script, args):
    """Run one step with its streams attached to the terminal.

    Deliberately not captured. Step 1 asks for OTP on stdin, and the rest are
    long enough that watching them work is the difference between a run you
    can tell is progressing and one you have to guess about.

    Uses sys.executable rather than shelling out to `poetry run poe`, so the
    audit runs in whatever interpreter invoked it - one process tree, no
    dependency on poe resolving, and no second virtualenv to get wrong.
    """
    print(f"\n{'=' * 78}")
    print(f"STEP {number}/{total}: {title}")
    print(f"  {script.relative_to(REPO_ROOT)} {' '.join(str(a) for a in args)}")
    print("=" * 78, flush=True)

    completed = subprocess.run([sys.executable, str(script), *[str(a) for a in args]],
                               cwd=REPO_ROOT)
    if completed.returncode != 0:
        print(f"\n!! STEP {number} FAILED (exit {completed.returncode}): {title}", flush=True)
        return False
    return True


def snapshot_runs():
    return {p.name for p in DATA_DIR.iterdir() if p.is_dir()} if DATA_DIR.is_dir() else set()


def newest_new_run(before):
    """The run directory step 1 created, or None if it created none.

    Newest-of-the-new rather than just the-new, because a download that dies
    partway and gets retried inside one audit would leave two.
    """
    appeared = snapshot_runs() - before
    if not appeared:
        return None
    return max((DATA_DIR / name for name in appeared), key=lambda p: p.stat().st_mtime)


def main():
    # Company names in the downstream output can be Arabic; Windows consoles
    # default to a legacy codepage that raises on those.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="Run the whole Sector Analysis pipeline: download, verify, summarize, "
                    "and report coverage and emptiness.")
    parser.add_argument("--run-dir", default=None,
                        help="Skip the download and audit this existing run directory instead "
                             "(a data/sector-analysis/<timestamp>/ folder).")
    parser.add_argument("--web-only", action="store_true",
                        help="Pass --web-only to the ratio check: skip the Excel-recalculation "
                             "pass, which needs a local Excel and roughly doubles step 2.")
    parser.add_argument("--tolerance", default=None,
                        help="Pass --tolerance through to the ratio check.")
    args = parser.parse_args()

    total = 4 if args.run_dir else 5
    step = 0
    done = {}

    if args.run_dir:
        run_dir = Path(args.run_dir).resolve()
        if not run_dir.is_dir():
            print(f"Not a directory: {run_dir}", file=sys.stderr)
            return 1
        print(f"Skipping the download; auditing the existing run at {run_dir}")
    else:
        before = snapshot_runs()
        step += 1
        done["download"] = run_step(step, total, "Download the exports", DOWNLOAD_SCRIPT, [])
        run_dir = newest_new_run(before)
        if run_dir is None:
            print("\nThe download produced no new run directory under "
                  f"{DATA_DIR.relative_to(REPO_ROOT)}, so there is nothing to audit. Stopping.",
                  file=sys.stderr)
            return 1
        if not done["download"]:
            print(f"\nThe download failed, but left {run_dir.name} behind. Auditing what it "
                  f"did manage to fetch - expect gaps that are the download's, not the app's.")

    exports = sorted(p for p in run_dir.glob("*.xlsx") if not p.name.startswith("~$"))
    if not exports:
        print(f"\nNo .xlsx exports in {run_dir}, so there is nothing to audit. Stopping.",
              file=sys.stderr)
        return 1
    print(f"\nAuditing {len(exports)} exports in {run_dir}")

    # The ratio checker names its results folder after the folder it was given,
    # so step 3's input follows from step 2's input with nothing to look up.
    results_dir = RESULTS_DIR / run_dir.name

    ratio_args = ["--dir", run_dir, "--fail-only"]
    if args.web_only:
        ratio_args.append("--web-only")
    if args.tolerance:
        ratio_args += ["--tolerance", args.tolerance]

    step += 1
    done["ratios"] = run_step(step, total, "Verify every ratio", RATIOS_SCRIPT, ratio_args)

    step += 1
    if done["ratios"] or results_dir.is_dir():
        done["summary"] = run_step(step, total, "Summarize the failures",
                                   SUMMARY_SCRIPT, [results_dir])
    else:
        done["summary"] = None
        print(f"\nSTEP {step}/{total} SKIPPED: the ratio check wrote no reports to "
              f"{results_dir}, so there is nothing to summarize.")

    step += 1
    done["coverage"] = run_step(step, total, "Report ratio coverage", COVERAGE_SCRIPT, [run_dir])

    step += 1
    done["emptiness"] = run_step(step, total, "Report ratio emptiness", EMPTINESS_SCRIPT, [run_dir])

    print(f"\n{'=' * 78}")
    print("AUDIT COMPLETE")
    print("=" * 78)
    for name, ok in done.items():
        print(f"  {'ok     ' if ok else ('SKIPPED' if ok is None else 'FAILED ')}  {name}")
    print(f"\n  exports          {run_dir}")
    print(f"  ratio reports    {results_dir}")
    print(f"  failure summary  {results_dir / '_summary.csv'}")
    print(f"  coverage         {run_dir / '_ratio-coverage.csv'}")
    print(f"  emptiness        {run_dir / '_ratio-emptiness.csv'}")

    return 0 if all(ok for ok in done.values() if ok is not None) else 1


if __name__ == "__main__":
    sys.exit(main())
