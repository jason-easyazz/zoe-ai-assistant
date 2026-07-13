#!/usr/bin/env python3
"""Soft-delete orphaned test/junk data left in the calendar + lists by earlier
testing (the kiosk `guest` account and throwaway `test-sec-b-*` security-test
users). These rows are family-visible so they show on the panel, but no API
session authenticates as their owner, so they can't be cleared via the UI.

Scope (deliberately narrow — never touches a real household user's data):
  * events    : user_id = 'guest'  OR  user_id LIKE 'test-sec-b-%'
  * list_items: rows whose owning list has user_id = 'guest'
                OR user_id LIKE 'test-sec-b-%'

It only sets deleted = 1 (reversible; the panel already hides deleted rows).
Contacts (people) are intentionally NOT touched.

Usage (run on the zoe-data host, in the service environment so POSTGRES_URL is set):
    python3 scripts/maintenance/purge_orphaned_test_data.py            # dry-run (counts only)
    python3 scripts/maintenance/purge_orphaned_test_data.py --execute  # apply soft-delete
"""
import argparse
import asyncio
import os
import sys

import asyncpg

EVENT_PRED = "(user_id = 'guest' OR user_id LIKE 'test-sec-b-%')"
LIST_OWNER_PRED = "(user_id = 'guest' OR user_id LIKE 'test-sec-b-%')"


async def main(execute: bool) -> int:
    dsn = os.environ.get("POSTGRES_URL", "")
    if not dsn:
        print("POSTGRES_URL is not set — run this in the zoe-data service environment.", file=sys.stderr)
        return 2
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

        async with conn.transaction():
            ev_done = await conn.execute(
                f"UPDATE events SET deleted = 1 WHERE {EVENT_PRED} AND deleted = 0"
            )
            li_done = await conn.execute(
                "UPDATE list_items SET deleted = 1 WHERE deleted = 0 AND list_id IN "
                f"(SELECT id FROM lists WHERE {LIST_OWNER_PRED})"
            )
        print(f"\nAPPLIED — events: {ev_done}, list_items: {li_done}")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true", help="apply the soft-delete (default is dry-run)")
    sys.exit(asyncio.run(main(ap.parse_args().execute)))
