#!/usr/bin/env python3
"""Owner/operator BACKFILL: conversation notes for a named guest from the panel turns the Flue brain kept.

For a friend who talked to Zoe on the panel before she was enrolled: reads the Flue session log READ-ONLY, picks the turns of a time
window that came from an unidentified speaker (a member's turn in the same window is left alone), and stores a short note per
conversation in the guest's own layer (guest scope only), exactly as the nightly pass would. Runbook:
``docs/knowledge/conversation-notes.md`` "Backfill a guest's earlier chat".

    # 1. make the note and print it, store nothing (always first)
    python3 scripts/maintenance/conversation_notes_backfill.py --owner <member user id> --name Quinn \\
        --since "2026-11-08 02:40" --until "2026-11-08 02:50" --dry-run
    # 2. store it (same command without --dry-run)
    # 3. look at what is stored for the guest (any time)
    python3 scripts/maintenance/conversation_notes_backfill.py --owner <member user id> --name Quinn --show

Times are household-local (``ZOE_TIMEZONE``, default Australia/Perth). Run ON THE JETSON from the live checkout, in the household's
quiet hours but NEVER between 01:50 and 04:55 (the night window; this refuses). The database comes from zoe-data's ``.env``
(``runtime_env``); the model call goes to the local brain (``GEMMA_SERVER_URL``), one call per conversation. Exit 0 = ok, 3 = refused or
a setup error.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import pathlib
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "services" / "zoe-data"))
DEFAULT_FLUE_DB = REPO / "labs" / "flue-zoe-brain-2x" / "data" / "zoe-brain.db"


def _epoch(text: str, tz: ZoneInfo) -> float:
    d = datetime.fromisoformat(text.strip().replace(" ", "T", 1))
    return (d.replace(tzinfo=tz) if d.tzinfo is None else d).timestamp()


def _in_night_window(now: datetime) -> bool:
    m = now.hour * 60 + now.minute
    return 110 <= m < 295        # 01:50 - 04:55


async def _show(owner: str, name: str) -> int:
    import conversation_notes as cn
    import named_guest_memory as ngm

    guests = [g for g in await ngm.list_for(owner) if g["name"].strip().lower() == name.strip().lower()]
    if len(guests) != 1:
        print(json.dumps({"status": "refused", "error": "no single enrolled guest with that name"}))
        return 3
    async with cn._db() as db:
        async with db.execute(f"SELECT {cn._COLS} FROM conversation_notes WHERE user_id = ? AND people_id = ? ORDER BY ended_at",
                              (owner, guests[0]["guest_id"])) as cur:
            rows = [cn._stored(r).__dict__ for r in await cur.fetchall()]
    print(json.dumps({"status": "ok", "guest": guests[0]["name"], "notes": rows}, indent=2))
    return 0


async def _main(args) -> int:
    import runtime_env

    runtime_env.bootstrap_runtime_env()
    from db_pool import close_pool, init_pool

    try:
        await init_pool()
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"status": "error", "error": f"database: {type(exc).__name__}"}))
        return 3
    try:
        if args.show:
            return await _show(args.owner, args.name)
        import conversation_notes_backfill as bf

        tz = ZoneInfo(os.environ.get("ZOE_TIMEZONE", "Australia/Perth"))
        if _in_night_window(datetime.now(tz)):
            print(json.dumps({"status": "refused", "error": "the night window (01:50-04:55) owns the brain; run this outside it"}))
            return 3
        report = await bf.run(args.owner, args.name, str(args.flue_db), _epoch(args.since, tz), _epoch(args.until, tz),
                              dry_run=args.dry_run)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"status": "error", "error": type(exc).__name__}))
        return 3
    finally:
        await close_pool()
    print(json.dumps(report, indent=2))
    return 0 if report.get("status") == "ok" else 3


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--owner", required=True, help="the member user id whose layer the guest lives in")
    ap.add_argument("--name", required=True, help="the enrolled guest's name")
    ap.add_argument("--since", help="window start, household-local, e.g. '2026-11-08 02:40'")
    ap.add_argument("--until", help="window end, household-local")
    ap.add_argument("--flue-db", default=str(DEFAULT_FLUE_DB), help="the Flue brain's SQLite file (read-only)")
    ap.add_argument("--dry-run", action="store_true", help="make the note and print it; store nothing")
    ap.add_argument("--show", action="store_true", help="print the notes stored for this guest and exit")
    args = ap.parse_args(argv)
    if not args.show and not (args.since and args.until):
        ap.error("--since and --until are required (or use --show)")
    return asyncio.run(_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
