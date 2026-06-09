#!/usr/bin/env python3
"""Run Zoe's MemPalace baseline against the local MemoryService.

This script uses a synthetic benchmark user and deletes that user's rows by
default after the run so the baseline does not pollute real user memory.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ZOE_DATA = ROOT / "services" / "zoe-data"
sys.path.insert(0, str(ZOE_DATA))

from mempalace_baseline import run_mempalace_baseline  # noqa: E402
from memory_service import get_memory_service  # noqa: E402


async def _main() -> int:
    parser = argparse.ArgumentParser(description="Run Zoe MemPalace baseline")
    parser.add_argument("--user-id", default="zoe-mempalace-baseline")
    parser.add_argument("--keep", action="store_true", help="keep synthetic benchmark rows")
    parser.add_argument("--timeout", type=float, default=2.0)
    args = parser.parse_args()

    service = get_memory_service()
    try:
        summary = await run_mempalace_baseline(service, user_id=args.user_id, timeout_s=args.timeout)
        print(json.dumps(summary, indent=2, sort_keys=True))
    finally:
        if not args.keep:
            removed = await service.delete_user(args.user_id, actor="mempalace_baseline")
            print(json.dumps({"cleanup_removed": removed, "user_id": args.user_id}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
