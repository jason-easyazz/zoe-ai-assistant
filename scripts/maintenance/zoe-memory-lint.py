#!/usr/bin/env python3
"""Run Zoe's memory Lint pass and print a structured, REPORT-ONLY report.

Lint is the third memory operation alongside Ingest (dreaming) and Query
(search). It scans stored MemPalace memories and reports suspect rows:
contradictions, stale/superseded claims, orphans, and duplicates.

This tool NEVER deletes, merges, edits, or otherwise mutates stored memory.
It reads through the MemoryService facade and emits JSON for human / curation
review. Acting on the report is a separate, human-gated step.

Usage:
  zoe-memory-lint.py --user <user_id>     # lint one user
  zoe-memory-lint.py --all                # lint every user with memory
  zoe-memory-lint.py --user jason --pretty
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys

ZOE_DATA = pathlib.Path(__file__).resolve().parents[2] / "services" / "zoe-data"
if str(ZOE_DATA) not in sys.path:
    sys.path.insert(0, str(ZOE_DATA))


async def _run(args: argparse.Namespace) -> int:
    from memory_lint import lint_all, lint_user

    if args.all:
        reports = await lint_all()
        payload = [r.to_dict() for r in reports]
    else:
        report = await lint_user(args.user)
        payload = report.to_dict()

    indent = 2 if args.pretty else None
    print(json.dumps(payload, indent=indent, default=str))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Report-only memory lint pass.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--user", help="user_id to lint")
    group.add_argument("--all", action="store_true", help="lint every user")
    parser.add_argument("--pretty", action="store_true", help="indent JSON output")
    args = parser.parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
