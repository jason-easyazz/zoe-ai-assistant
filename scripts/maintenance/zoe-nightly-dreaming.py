#!/usr/bin/env python3
"""Run Zoe's nightly memory maintenance independently of model training."""

from __future__ import annotations

import asyncio
import json
import pathlib
import sys
import time

ZOE_DATA = pathlib.Path("/home/zoe/assistant/services/zoe-data")
sys.path.insert(0, str(ZOE_DATA))


def memory_quality_snapshot() -> dict:
    import chromadb

    client = chromadb.PersistentClient(path=str(pathlib.Path.home() / ".mempalace"))
    col = client.get_collection("mempalace_drawers")
    results = col.get(include=["metadatas"])
    statuses: dict[str, int] = {}
    for meta in results["metadatas"]:
        status = meta.get("status", "unknown")
        statuses[status] = statuses.get(status, 0) + 1

    entry = {"ts": time.time(), "date": time.strftime("%Y-%m-%d"), **statuses}
    log = pathlib.Path.home() / "training" / "data" / "memory-quality-log.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")
    return entry


async def run_dreaming(db) -> list:
    from memory_digest import run_dreaming_for_all

    return await run_dreaming_for_all(db=db)


async def run_music_digest(db) -> list:
    from memory_digest import run_music_taste_digest_for_all

    return await run_music_taste_digest_for_all(db=db)


async def main() -> int:
    from db_pool import close_pool, get_db_ctx, init_pool

    try:
        await init_pool()
    except Exception as exc:
        print(f"Database pool initialisation failed: {exc}", file=sys.stderr)
        return 1

    try:
        print("=== Memory quality snapshot ===")
        try:
            snapshot = memory_quality_snapshot()
            print(json.dumps(snapshot, indent=2))
        except Exception as exc:
            print(f"Memory quality check failed: {exc}", file=sys.stderr)
            return 1

        async with get_db_ctx() as db:
            print("\n=== Dreaming memory cycle ===")
            try:
                dreaming_results = await run_dreaming(db)
                print(f"Dreaming cycle complete: {len(dreaming_results)} users processed")
                for row in dreaming_results:
                    print(json.dumps(row, indent=2))
            except Exception as exc:
                print(f"Dreaming cycle failed: {exc}", file=sys.stderr)
                return 1

            print("\n=== Music taste digest ===")
            try:
                music_results = await run_music_digest(db)
                print(f"Music taste digest complete: {len(music_results)} users processed")
                for row in music_results:
                    print(json.dumps(row, indent=2))
            except Exception as exc:
                print(f"Music taste digest failed: {exc}", file=sys.stderr)
                return 1

        print("\nzoe-nightly-dreaming: complete")
        return 0
    finally:
        await close_pool()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
