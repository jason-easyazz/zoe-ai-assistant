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

from greploop_guard import GuardError, read_guard_state, run_guard_once  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one Zoe Greploop guard iteration.")
    parser.add_argument("--pr", type=int, help="PR number to guard")
    parser.add_argument("--target-confidence", type=int, default=5, help="Greptile confidence to clear merge")
    parser.add_argument("--once", action="store_true", help="Run one bounded guard iteration")
    parser.add_argument("--packet-only", action="store_true", help="Build packet and stop before model execution")
    parser.add_argument("--state", action="store_true", help="Print file-backed guard state")
    args = parser.parse_args()

    try:
        if not args.pr:
            parser.error("--pr is required")
        if args.state:
            print(json.dumps(read_guard_state(args.pr), indent=2, sort_keys=True))
            return 0
        if not (args.once or args.packet_only):
            parser.error("choose --once or --packet-only")
        result = asyncio.run(
            run_guard_once(args.pr, packet_only=args.packet_only, target_confidence=args.target_confidence)
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result.get("ok") else 2
    except GuardError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
