#!/usr/bin/env python3
"""Run Zoe's nightly memory maintenance independently of model training."""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request

ZOE_DATA = pathlib.Path("/home/zoe/assistant/services/zoe-data")
sys.path.insert(0, str(ZOE_DATA))


def memory_quality_snapshot() -> dict:
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "lib"))
    from palace_client import open_palace_client  # refuses a client/format mismatch (B0.8)

    client = open_palace_client(str(pathlib.Path.home() / ".mempalace"))
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


def surface_count_logs() -> None:
    """Print the counts-only ``OPEN_LOOPS user=… extracted=… inserted=…`` line.

    This runner configures no logging, so Python's last-resort handler shows
    WARNING+ only. Attach a handler to that ONE logger rather than raising the
    root level: memory_digest's other INFO lines carry fact text.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    counts = logging.getLogger("memory_digest.open_loops")
    counts.setLevel(logging.INFO)
    counts.addHandler(handler)
    counts.propagate = False


# ── Weekly drawers-index compaction trigger (flag-dark on the service side) ──────────
# chroma 1.x never compacts the HNSW index; demo-user churn leaves tombstones (1,591
# elements for 258 rows on 2026-10-04 → empty owner-filtered queries). On the configured
# weekday this job asks zoe-data for the index health and, when a compaction is advised,
# asks zoe-data to compact IN-PROCESS. The rebuild is never done from here: a second
# chroma client must not delete the live collection under the running service.
_WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4,
             "saturday": 5, "sunday": 6}
_DEFAULT_COMPACT_DAY = 6  # Sunday, Zoe-local


def compaction_day_index(raw: str | None) -> int:
    """``ZOE_MEMORY_INDEX_COMPACT_DAY`` → weekday index (Mon=0). Accepts a name, a
    3-letter prefix or 0-6; anything else is the default (Sunday)."""
    text = (raw or "").strip().lower()
    if text.isdigit() and 0 <= int(text) <= 6:
        return int(text)
    for name, ix in _WEEKDAYS.items():
        if text and name.startswith(text[:3]) and len(text) >= 3:
            return ix
    return _DEFAULT_COMPACT_DAY


def index_compaction_decision(now_local: datetime.datetime, health: dict | None, *,
                              day: int = _DEFAULT_COMPACT_DAY) -> tuple[bool, str]:
    """(run?, reason). Pure: the clock and the health payload are injected."""
    if now_local.weekday() != day:
        return False, f"not compaction day (today={now_local.strftime('%a')}, day={day})"
    if not isinstance(health, dict):
        return False, "index health unavailable"
    if health.get("fresh"):
        return False, "fresh index (not persisted yet, ratio 1.0)"
    ratio = health.get("tombstone_ratio")
    if health.get("compaction_advised") is True:
        return True, f"advised: ratio={ratio} >= {health.get('threshold')}"
    return False, f"not advised: ratio={ratio}"


def _api(method: str, path: str, timeout: float) -> tuple[int, dict]:
    base = os.environ.get("ZOE_DATA_URL", "http://127.0.0.1:8000").rstrip("/")
    headers = {"Accept": "application/json"}
    token = os.environ.get("ZOE_INTERNAL_TOKEN", "")
    if token:
        headers["X-Internal-Token"] = token   # never printed
    req = urllib.request.Request(base + path, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — loopback API
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read() or b"{}")
        except Exception:  # noqa: BLE001
            body = {}
        return exc.code, body if isinstance(body, dict) else {"detail": body}


def weekly_index_compaction(now_local: datetime.datetime | None = None) -> int:
    """0 = nothing to do / done; 1 = the compaction itself failed (operator attention)."""
    if now_local is None:
        from time_utils import zoe_timezone  # services/zoe-data is on sys.path (see top)

        now_local = datetime.datetime.now(zoe_timezone())
    day = compaction_day_index(os.environ.get("ZOE_MEMORY_INDEX_COMPACT_DAY"))
    if now_local.weekday() != day:
        print(index_compaction_decision(now_local, None, day=day)[1])
        return 0
    try:
        status, health = _api("GET", "/api/memories/maintenance/index-health", timeout=30)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print(f"index health unavailable: {exc}", file=sys.stderr)
        return 0
    if status != 200:
        print(f"index health unavailable: HTTP {status} {health.get('detail', '')}", file=sys.stderr)
        return 0
    print(json.dumps({k: health.get(k) for k in ("live_rows", "elements_added", "tombstone_ratio",
                                                   "compaction_advised", "fresh", "note")}))
    run, reason = index_compaction_decision(now_local, health, day=day)
    print(reason)
    if not run:
        return 0
    try:
        status, result = _api("POST", "/api/memories/maintenance/compact-index", timeout=900)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print(f"compaction request failed: {exc}", file=sys.stderr)
        return 1
    if status == 404:
        print("compaction disabled on the service (ZOE_MEMORY_INDEX_COMPACT off) — manual fallback: "
              "scripts/maintenance/compact_drawers_index.py --compact")
        return 0
    print(json.dumps(result, indent=2, default=str))
    return 0 if status == 200 and result.get("ok") else 1


async def main() -> int:
    from db_pool import close_pool, get_db_ctx, init_pool

    surface_count_logs()

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

        print("\n=== Weekly drawers index compaction ===")
        try:
            rc = weekly_index_compaction()
        except Exception as exc:  # noqa: BLE001 — never let the trigger fail the night's run
            print(f"Index compaction trigger failed: {exc}", file=sys.stderr)
            rc = 0
        if rc:
            print("Index compaction FAILED — see MEMORY_INDEX_COMPACT in the zoe-data log", file=sys.stderr)
            return 1

        print("\nzoe-nightly-dreaming: complete")
        return 0
    finally:
        await close_pool()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
