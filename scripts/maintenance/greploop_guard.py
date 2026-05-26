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
    parser.add_argument("--task-id", help="Zoe engineering_tasks id")
    parser.add_argument("--pr", type=int, help="Read guard state for a PR number")
    parser.add_argument("--once", action="store_true", help="Run one bounded guard iteration")
    parser.add_argument("--packet-only", action="store_true", help="Build packet and stop before model execution")
    parser.add_argument("--state", action="store_true", help="Print file-backed guard state")
    args = parser.parse_args()

    try:
        if args.state:
            if not args.pr:
                parser.error("--state requires --pr")
            print(json.dumps(read_guard_state(args.pr), indent=2, sort_keys=True))
            return 0
        if not args.task_id:
            parser.error("--task-id is required for --once/--packet-only")
        if not (args.once or args.packet_only):
            parser.error("choose --once or --packet-only")
        result = asyncio.run(run_guard_once(args.task_id, packet_only=args.packet_only))
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result.get("ok") else 2
    except GuardError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
