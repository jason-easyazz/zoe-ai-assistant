#!/usr/bin/env python3
"""Is there anything NEW for tonight's 12B window to think about? Read-only, COUNTS ONLY (never a turn's text), no model.

The night window (scripts/night/night_window.py) asks this before it stops anything. If no member has said anything since the last window that
did its work, loading the 12B burns ~3 minutes of household silence for jobs that read nothing (measured 2026-10-18: 0 prompt tokens in the
digest and the night mind) -- so it reports ``noop`` instead.

    night_precheck.py --since 2026-10-17T18:51:58+00:00 [--omi] [--struggle]

Prints ONE line, ``NIGHT_PRECHECK {"members_with_new_turns": N, "omi_members": M | null, "struggle_pending": K | null, "since": ...}``, and
exits 0. Exit 1 on any failure (the window then runs as before: it fails OPEN).

  members_with_new_turns   members (synthetic / demo ids dropped, the same filter the digest uses) with a user chat turn after --since
  omi_members              with --omi: members with a past pendant day not reviewed yet (omi_day_review.days_to_review); null without --omi
  struggle_pending         with --struggle: proposal work waiting for the night pass (struggle_proposals.pending_maintenance: struggle events
                           after --since, a guest's included, plus proposed rows whose evidence has aged out of the lookback); null without
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
ZOE_DATA = Path(os.environ.get("NIGHT_ZOE_DATA_DIR", str(REPO / "services" / "zoe-data")))
sys.path.insert(0, str(ZOE_DATA))


def new_turn_members_sql(owner_expr: str) -> str:
    """Members with a user turn after the watermark. The ``::text`` / ``::timestamptz`` casts are required by the asyncpg positional-compat layer (an uncast
    placeholder binds as unknown and the comparison errors). Keep this SQL free of literal question marks: the layer counts them as placeholders."""
    return f"""
        SELECT DISTINCT owner.user_id
        FROM (
            SELECT {owner_expr} AS user_id
            FROM chat_messages cm
            JOIN chat_sessions cs ON cm.session_id = cs.id
            WHERE cm.role = 'user'
              AND cm.created_at::timestamptz > (?::text)::timestamptz
        ) owner
        WHERE owner.user_id IS NOT NULL
        """


def parse_since(text: str) -> str:
    """A timezone-aware ISO instant (the window passes UTC); anything else is refused, so a naive stamp can never be read in the wrong zone."""
    d = dt.datetime.fromisoformat(text)
    if d.tzinfo is None:
        raise ValueError("--since must carry a UTC offset")
    return d.astimezone(dt.timezone.utc).isoformat(timespec="seconds")


async def count_members(since: str, want_omi: bool, want_struggle: bool = False) -> dict:
    from db_pool import close_pool, get_db_ctx, init_pool
    from user_filters import drop_synthetic_users, message_owner_expr

    await init_pool()
    try:
        async with get_db_ctx() as db:
            rows = await (await db.execute(new_turn_members_sql(message_owner_expr()), (since,))).fetchall()
        members = drop_synthetic_users([r[0] for r in rows], pass_name="night_precheck", log=None)
        omi = None
        if want_omi:
            import omi_day_review as dr

            omi = 0
            for uid in await dr.members_with_recordings():
                if await dr.days_to_review(uid):
                    omi += 1
        struggle = None
        if want_struggle:
            import struggle_proposals as sp

            struggle = await sp.pending_maintenance(dt.datetime.fromisoformat(since).timestamp())
        return {"members_with_new_turns": len(members), "omi_members": omi, "struggle_pending": struggle, "since": since}
    finally:
        await close_pool()


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--since", required=True, help="ISO instant WITH a UTC offset: the start of the last window that did its work")
    ap.add_argument("--omi", action="store_true", help="also count members with an unreviewed pendant day")
    ap.add_argument("--struggle", action="store_true", help="also count struggle-proposal maintenance waiting for the night pass")
    args = ap.parse_args(argv)
    try:
        out = asyncio.run(count_members(parse_since(args.since), args.omi, args.struggle))
    except Exception as exc:  # noqa: BLE001 - the window fails open on ANY failure
        print(f"NIGHT_PRECHECK failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    print("NIGHT_PRECHECK " + json.dumps(out, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
