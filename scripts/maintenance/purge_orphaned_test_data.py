#!/usr/bin/env python3
"""Soft-delete orphaned test/junk data left in the calendar + lists by earlier
testing (the kiosk `guest` account and throwaway `test-sec-b-*` security-test
users). These rows are family-visible so they show on the panel, but no API
session authenticates as their owner, so they can't be cleared via the UI.

Scope (deliberately narrow — never touches a real household user's data):
  * events    : user_id = 'guest'  OR  user_id LIKE 'test-sec-b-%'
  * list_items: rows whose owning list has user_id = 'guest'
                OR user_id LIKE 'test-sec-b-%'

It only sets deleted = 1 (reversible; the panel already hides deleted rows) and
bumps updated_at so audit/sync paths that key off it don't miss the change.
Contacts (people) are intentionally NOT touched.

Usage (run on the zoe-data host):
    python3 scripts/maintenance/purge_orphaned_test_data.py            # dry-run (counts only)
    python3 scripts/maintenance/purge_orphaned_test_data.py --execute  # apply (asks to confirm)
    python3 scripts/maintenance/purge_orphaned_test_data.py --execute --yes  # non-interactive

POSTGRES_URL is read from the environment; if unset it is loaded from the
zoe-data service .env (same file the service uses), so the documented command
works without manually sourcing anything.
"""
import argparse
import asyncio
import os
import sys
from urllib.parse import urlsplit

import asyncpg

EVENT_PRED = "(user_id = 'guest' OR user_id LIKE 'test-sec-b-%')"
LIST_OWNER_PRED = "(user_id = 'guest' OR user_id LIKE 'test-sec-b-%')"

# Sibling PostgreSQL tooling reads the same service env file.
_SERVICE_ENV = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "services", "zoe-data", ".env",
)


def _resolve_dsn() -> str:
    """POSTGRES_URL from the environment, else from the zoe-data service .env."""
    dsn = os.environ.get("POSTGRES_URL", "")
    if dsn:
        return dsn
    try:
        with open(_SERVICE_ENV) as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("POSTGRES_URL="):
                    return line[len("POSTGRES_URL="):].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def _redacted_target(dsn: str) -> str:
    """host:port/dbname (as user) with the password never printed."""
    p = urlsplit(dsn)
    who = p.username or "?"
    host = p.hostname or "?"
    port = f":{p.port}" if p.port else ""
    db = (p.path or "/").lstrip("/") or "?"
    return f"{db} on {host}{port} (as {who})"


async def main(execute: bool, assume_yes: bool) -> int:
    dsn = _resolve_dsn()
    if not dsn:
        print(
            "POSTGRES_URL is not set and could not be read from the service .env "
            f"({_SERVICE_ENV}). Run this on the zoe-data host.",
            file=sys.stderr,
        )
        return 2

    target = _redacted_target(dsn)
    print(f"Target database: {target}")

    conn = await asyncpg.connect(dsn)
    try:
        ev_n = await conn.fetchval(
            f"SELECT count(*) FROM events WHERE {EVENT_PRED} AND deleted = 0"
        )
        # by owner+title, so the operator can eyeball exactly what will go
        rows = await conn.fetch(
            f"SELECT user_id, title, count(*) c FROM events "
            f"WHERE {EVENT_PRED} AND deleted = 0 GROUP BY user_id, title ORDER BY c DESC LIMIT 20"
        )
        li_n = await conn.fetchval(
            "SELECT count(*) FROM list_items i JOIN lists l ON i.list_id = l.id "
            f"WHERE {LIST_OWNER_PRED.replace('user_id', 'l.user_id')} AND i.deleted = 0"
        )
        print(f"events to soft-delete : {ev_n}")
        for r in rows:
            print(f"    {r['c']:>4}  {r['user_id']:<20} {r['title']!r}")
        print(f"list_items to soft-delete: {li_n}")

        if not execute:
            print("\nDRY-RUN — nothing changed. Re-run with --execute to apply.")
            return 0

        if ev_n == 0 and li_n == 0:
            print("\nNothing to do.")
            return 0

        if not assume_yes:
            print(f"\nAbout to soft-delete {ev_n} events + {li_n} list_items in: {target}")
            reply = input("Type 'yes' to proceed: ").strip().lower()
            if reply != "yes":
                print("Aborted — nothing changed.")
                return 1

        async with conn.transaction():
            ev_done = await conn.execute(
                f"UPDATE events SET deleted = 1, updated_at = NOW() "
                f"WHERE {EVENT_PRED} AND deleted = 0"
            )
            li_done = await conn.execute(
                "UPDATE list_items SET deleted = 1, updated_at = NOW() "
                "WHERE deleted = 0 AND list_id IN "
                f"(SELECT id FROM lists WHERE {LIST_OWNER_PRED})"
            )
        print(f"\nAPPLIED to {target} — events: {ev_done}, list_items: {li_done}")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true", help="apply the soft-delete (default is dry-run)")
    ap.add_argument("--yes", action="store_true", help="skip the interactive confirmation (for automation)")
    args = ap.parse_args()
    sys.exit(asyncio.run(main(args.execute, args.yes)))
