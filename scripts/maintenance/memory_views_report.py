#!/usr/bin/env python3
"""The memory-views shadow report: what the pointer views WOULD do for a member, before any of them is switched on.

Why (Samantha initiative item 15, lane C E4 / E7; contract in ``services/zoe-data/memory_views.py``): the views ship flag-dark, and
the lane's rule is shadow first. This prints, per member, ONE JSON object of counts and ids:

  slots     rows read, rows keyed to a person + single-value slot, every contradicting pair (ids, slot, action), the ids a
            ``ZOE_MEMORY_SLOT_CHECK=on`` packet would hold or hide, the rows a "still true?" check is due on, and the one question
  episodes  the member's conversations over the last ``--days`` (count, turns, ids)
  dormant   today's own turns against the member's quiet night-mind threads (at most one candidate: thread id <- turn id, score)

It NEVER prints a row's text, a quote, a slot value or what anyone said, and it writes nothing. Memory rows are read from the
palace's sqlite opened read-only (``memory_authority_backfill.load_rows``), so it is safe while zoe-data runs; turns and threads are
plain SELECTs on Postgres.

Usage
  python3 scripts/maintenance/memory_views_report.py --user <member-id> [--user <member-id> ...] [--days 7] [--pretty]

Exit codes: 0 = report printed; 2 = refused (no --user, palace unreadable, no database pool).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "services" / "zoe-data"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Memory-views shadow report (counts and ids only; writes nothing)")
    ap.add_argument("--user", action="append", default=[], help="member id (repeatable)")
    ap.add_argument("--days", type=int, default=7, help="episode window in days (default 7)")
    ap.add_argument("--palace", default=os.environ.get("MEMPALACE_DATA_DIR") or "~/.mempalace")
    ap.add_argument("--pretty", action="store_true")
    return ap


def member_rows(all_rows: "list[dict]", user_id: str) -> "list[dict]":
    """One member's rows in the views' shape (``{id, text, metadata}``). Pure."""
    return [{"id": r["id"], "text": r["text"], "metadata": r["meta"]} for r in all_rows if r.get("user_id") == user_id]


async def run(args) -> int:
    from memory_authority_backfill import load_rows

    import memory_views

    try:
        rows = load_rows(args.palace)
    except SystemExit as exc:
        print(json.dumps({"status": "refused", "reason": "palace_unreadable", "detail": str(exc)[:200]}))
        return 2
    from db_pool import close_pool, init_pool

    try:
        await init_pool()
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"status": "refused", "reason": "no_db_pool", "kind": type(exc).__name__}))
        return 2
    try:
        members = [await memory_views.report(uid, member_rows(rows, uid), days=max(1, args.days)) for uid in args.user]
    finally:
        await close_pool()
    print(json.dumps({"status": "ok", "members": members}, indent=2 if args.pretty else None, sort_keys=True))
    return 0


def main(argv: "list[str] | None" = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.user:
        print(json.dumps({"status": "refused", "reason": "no --user given"}))
        return 2
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
