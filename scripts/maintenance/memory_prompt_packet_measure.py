#!/usr/bin/env python3
"""Measure cached prompt packet compilation from MemoryService prompt reads."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ZOE_DATA = ROOT / "services" / "zoe-data"
sys.path.insert(0, str(ZOE_DATA))

from memory_service import get_memory_service  # noqa: E402
from zoe_memory_prompt_packet_measure import (  # noqa: E402
    DEFAULT_MEASURE_USER_ID,
    measure_cached_prompt_packets,
    require_synthetic_measure_user_id,
    seed_prompt_packet_measure_memories,
)


async def _main() -> int:
    parser = argparse.ArgumentParser(description="Measure Zoe cached memory prompt packet latency")
    parser.add_argument("--user-id", default=DEFAULT_MEASURE_USER_ID)
    parser.add_argument("--seed-synthetic", action="store_true", help="write synthetic benchmark rows before measuring")
    parser.add_argument("--keep", action="store_true", help="keep synthetic benchmark rows after measurement")
    parser.add_argument("--prompt-limit", type=int, default=20)
    parser.add_argument("--max-items", type=int, default=3)
    parser.add_argument("--max-chars", type=int, default=480)
    args = parser.parse_args()

    seeded = 0
    if args.seed_synthetic:
        try:
            args.user_id = require_synthetic_measure_user_id(args.user_id)
        except ValueError as exc:
            parser.error(str(exc))
    service = get_memory_service()
    try:
        if args.seed_synthetic:
            seeded = await seed_prompt_packet_measure_memories(service, user_id=args.user_id)
        summary = await measure_cached_prompt_packets(
            service,
            user_id=args.user_id,
            prompt_limit=args.prompt_limit,
            max_items=args.max_items,
            max_chars=args.max_chars,
        )
        summary["user_id"] = args.user_id
        summary["seeded_count"] = seeded
        print(json.dumps(summary, indent=2, sort_keys=True))
    finally:
        if args.seed_synthetic and not args.keep:
            removed = await service.delete_user(args.user_id, actor="memory_prompt_packet_measure")
            print(json.dumps({"cleanup_removed": removed, "user_id": args.user_id}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
