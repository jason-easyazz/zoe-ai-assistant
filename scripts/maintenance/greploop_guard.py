#!/usr/bin/env python3
"""Operator CLI for Zoe's bounded Greploop guard."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "zoe-data"))

from greploop_guard import (  # noqa: E402
    GuardError,
    merge_pr_when_ready,
    read_observed_guard_state,
    run_guard_once,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run one Zoe Greploop guard iteration or merge when ready.",
        epilog=(
            "Modes: --packet-only emits one cheap-model repair packet (no merge). "
            "--once runs one guard iteration (fix packet or wait for Greptile). "
            "--merge-when-ready squash-merges via `gh pr merge --squash` only when "
            "Greptile confidence is met, comments are addressed, and CI is green "
            "(never --admin, never force). Combine --once --merge-when-ready to "
            "iterate once then merge if ready."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--pr", type=int, help="PR number to guard")
    parser.add_argument("--target-confidence", type=int, default=5, help="Greptile confidence to clear merge")
    parser.add_argument("--once", action="store_true", help="Run one bounded guard iteration")
    parser.add_argument(
        "--packet-only",
        action="store_true",
        help="Build packet and stop before model execution (Cursor/cheap runners)",
    )
    parser.add_argument(
        "--merge-when-ready",
        action="store_true",
        help="Squash-merge when READY (Greptile clear + CI green); optional after --once",
    )
    parser.add_argument("--state", action="store_true", help="Print file-backed guard state")
    args = parser.parse_args()

    try:
        if not args.pr:
            parser.error("--pr is required")
        if args.state:
            print(json.dumps(read_observed_guard_state(args.pr), indent=2, sort_keys=True))
            return 0
        if args.packet_only and args.merge_when_ready:
            parser.error("--merge-when-ready is incompatible with --packet-only")
        if not (args.once or args.packet_only or args.merge_when_ready):
            parser.error("choose --once, --packet-only, and/or --merge-when-ready")
        result: dict
        if args.once or args.packet_only:
            result = asyncio.run(
                run_guard_once(
                    args.pr,
                    packet_only=args.packet_only,
                    target_confidence=args.target_confidence,
                )
            )
            print(json.dumps(result, indent=2, sort_keys=True))
            if not result.get("ok") and not args.merge_when_ready:
                return 2
        if args.merge_when_ready:
            merge_result = asyncio.run(
                merge_pr_when_ready(args.pr, target_confidence=args.target_confidence)
            )
            print(json.dumps(merge_result, indent=2, sort_keys=True))
            return 0 if merge_result.get("ok") else 2
        return 0 if result.get("ok") else 2
    except GuardError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
