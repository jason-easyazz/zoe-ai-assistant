#!/usr/bin/env python3
"""Run a pytest command and refuse a "green" that did not actually test anything.

Why this exists (2026-10-19, architecture deep dive 2, lane K): the nightly
self-hosted lane ran for 14 nights with every pytest step `continue-on-error`.
`tests/integration` + `tests/e2e` and the `tests/unit` catch-all died in COLLECTION
(zero tests ran) and the full zoe-data suite carried 16-19 failures, yet every run
concluded `success`. pytest's own exit code already says "collection error" (2) and
"nothing collected" (5), but a lane that swallows exit codes, or a collection that
quietly shrinks (a skipped directory, an importorskip that now skips a whole tree),
still reads green. This wrapper makes both loud:

  * pytest's non-zero exit status is propagated unchanged (so a failure fails);
  * the JUnit XML pytest wrote is parsed and the run FAILS when
      - the XML is missing or unreadable (pytest never finished), or
      - any testcase is an ERROR (collection error / fixture error), or
      - fewer than ``--min-tests`` tests actually EXECUTED (collected minus skipped).
    "Executed" excludes skips on purpose: a lane where an `importorskip` turned the
    whole tree into skips collects thousands and proves nothing.
  * ``--max-skipped N`` (optional) additionally fails the run when MORE than N tests
    were skipped. The floor alone cannot see an interpreter that quietly lost a
    package (chromadb missing => every MemPalace test `importorskip`s): a few hundred
    skips out of ~18,000 leaves the executed count above any sane floor.

Usage::

    python3 scripts/maintenance/pytest_junit_guard.py --label zoe-data --min-tests 15000 \\
        -- python3 -m pytest services/zoe-data/tests -q --continue-on-collection-errors

``--junitxml`` is appended to the command by the guard (pass it yourself only via
``--junit``). Stdlib only: it runs on the Jetson runner and in the slim CI lane.
Covered, with negative controls, by ``tests/unit/test_pytest_junit_guard.py``.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path


def summarise(junit: Path) -> dict[str, int]:
    """Totals across every <testsuite> in a pytest JUnit XML file."""
    root = ET.parse(junit).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    totals = {"tests": 0, "errors": 0, "failures": 0, "skipped": 0}
    for suite in suites:
        for key in totals:
            totals[key] += int(suite.attrib.get(key, 0) or 0)
    totals["executed"] = totals["tests"] - totals["skipped"]
    return totals


def judge(
    label: str, totals: dict[str, int] | None, min_tests: int, max_skipped: int | None = None
) -> list[str]:
    """Reasons this run must FAIL even if the caller would have accepted pytest_rc."""
    if totals is None:
        return [f"{label}: pytest wrote no readable JUnit XML (it never finished, or the file is corrupt)"]
    problems: list[str] = []
    if totals["errors"]:
        problems.append(
            f"{label}: {totals['errors']} testcase ERROR(s) (collection/fixture errors) — "
            "tests behind them did not run"
        )
    if totals["executed"] < min_tests:
        problems.append(
            f"{label}: only {totals['executed']} tests executed "
            f"({totals['tests']} collected, {totals['skipped']} skipped); "
            f"the floor is {min_tests} — the suite shrank or was skipped wholesale"
        )
    if max_skipped is not None and totals["skipped"] > max_skipped:
        problems.append(
            f"{label}: {totals['skipped']} tests skipped, over the budget of {max_skipped} — "
            "a dependency or interpreter that these tests importorskip on is probably missing"
        )
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--label", required=True, help="short name used in messages and the step summary")
    parser.add_argument("--min-tests", type=int, required=True, help="fail when fewer tests than this EXECUTED")
    parser.add_argument("--max-skipped", type=int, help="fail when MORE than this many tests were skipped")
    parser.add_argument("--junit", help="where to write the JUnit XML (default: a temp file)")
    parser.add_argument("command", nargs=argparse.REMAINDER, help="-- <the pytest command>")
    args = parser.parse_args(argv)

    cmd = list(args.command)
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        parser.error("no command given (put it after --)")
    if args.min_tests < 1:
        parser.error("--min-tests must be >= 1: a floor of 0 is the vacuous check this guard exists to remove")

    junit = Path(args.junit) if args.junit else Path(tempfile.mkdtemp(prefix="pytest-guard-")) / "junit.xml"
    junit.parent.mkdir(parents=True, exist_ok=True)
    junit.unlink(missing_ok=True)  # a stale file from an earlier run must never satisfy this one

    rc = subprocess.run(cmd + [f"--junitxml={junit}"]).returncode

    totals: dict[str, int] | None
    try:
        totals = summarise(junit)
    except (OSError, ET.ParseError):
        totals = None

    problems = judge(args.label, totals, args.min_tests, args.max_skipped)
    line = (
        f"{args.label}: pytest rc={rc}, "
        + (
            f"{totals['tests']} collected, {totals['executed']} executed, {totals['failures']} failed, "
            f"{totals['errors']} errors, {totals['skipped']} skipped (floor {args.min_tests}"
            + (f", skip budget {args.max_skipped}" if args.max_skipped is not None else "")
            + ")"
            if totals
            else "no JUnit XML"
        )
    )
    print(f"\n[pytest-junit-guard] {line}", flush=True)
    for problem in problems:
        print(f"::error::[pytest-junit-guard] {problem}", flush=True)

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        try:
            with open(summary, "a", encoding="utf-8") as fh:
                fh.write(f"- {line}{'  **FAILED**' if (rc or problems) else ''}\n")
                for problem in problems:
                    fh.write(f"  - {problem}\n")
        except OSError:
            pass

    if rc:
        return rc
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
