#!/usr/bin/env python3
"""Merge duplicate HOUSEHOLD lists into ONE canonical list per type (the "two shopping lists" fix, 2026-10-15).

The household has one shopping list (a fridge list). Before the household-lists change, a guest's or a second member's
"add milk" that found no list of their own created another one, so the panel showed two. This script folds the strays back:

  canonical = the FAMILY-visible list of the type with the most live items (then the oldest);
  merged    = every other live list of that type that is EITHER owned by a shared account (``guest``, ``voice-guest``,
              ``family-admin`` - the kiosk and the household, never a person) OR family-visible with the same name
              (ignoring a trailing " list");
  untouched = any list a real member owns PRIVATELY. A personal list is never merged, moved, or deleted here.

Each merged list's live items MOVE to the canonical list (an item whose text already sits on the canonical list in the same
state is soft-deleted rather than duplicated), then the emptied duplicate is soft-deleted (``deleted = 1``, reversible).
Nothing is ever hard-deleted.

DRY-RUN BY DEFAULT: connects read-only, prints COUNTS ONLY (no list names, no item text) and changes nothing.
``--execute`` applies the plan in one transaction after an interactive confirmation (or ``--yes`` with a full
``--expect-db`` / ``--expect-host`` target assertion, the same guard ``purge_orphaned_test_data.py`` uses).

Usage (on the zoe-data host):
    python3 scripts/maintenance/merge_duplicate_lists.py                 # dry-run
    python3 scripts/maintenance/merge_duplicate_lists.py --types shopping,bucket
    python3 scripts/maintenance/merge_duplicate_lists.py --execute       # apply (asks to confirm)

POSTGRES_URL comes from the environment, else the zoe-data service .env (shared resolver with the purge script).
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass
from urllib.parse import urlsplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Accounts that own lists on behalf of the kiosk / the household, never a person.
SHARED_OWNERS = frozenset({"guest", "voice-guest", "family-admin"})
DEFAULT_TYPES = ("shopping",)


@dataclass(frozen=True)
class Merge:
    list_type: str
    canonical_id: str
    duplicate_id: str
    items: int  # live items on the duplicate that will move (or be de-duplicated)


def _name_key(name: object) -> str:
    n = " ".join(str(name or "").lower().split())
    return n[: -len(" list")].strip() if n.endswith(" list") else n


def plan_merges(rows: list[dict]) -> list[Merge]:
    """The merges to make from live list rows ``{id, user_id, name, list_type, visibility, created_at, n}``. Pure."""
    by_type: dict[str, list[dict]] = {}
    for r in rows:
        by_type.setdefault(str(r.get("list_type") or ""), []).append(r)
    plan: list[Merge] = []
    for list_type, lists in sorted(by_type.items()):
        family = [r for r in lists if r.get("visibility") == "family"]
        if not family:
            continue  # no household list to merge into: nothing is invented here
        canonical = sorted(family, key=lambda r: (-int(r.get("n") or 0), str(r.get("created_at") or ""), str(r["id"])))[0]
        for r in sorted(lists, key=lambda r: (str(r.get("created_at") or ""), str(r["id"]))):
            if r["id"] == canonical["id"]:
                continue
            shared_owned = r.get("user_id") in SHARED_OWNERS
            same_family = r.get("visibility") == "family" and _name_key(r.get("name")) == _name_key(canonical.get("name"))
            if shared_owned or same_family:
                plan.append(Merge(list_type, canonical["id"], r["id"], int(r.get("n") or 0)))
    return plan


def summarise(plan: list[Merge]) -> str:
    """Counts only - safe to paste anywhere."""
    by_type: dict[str, list[Merge]] = {}
    for m in plan:
        by_type.setdefault(m.list_type, []).append(m)
    if not plan:
        return "duplicate lists: 0 (nothing to merge)"
    lines = [f"duplicate lists to merge: {len(plan)}  (live items that move: {sum(m.items for m in plan)}; "
             f"empty duplicates: {sum(1 for m in plan if m.items == 0)})"]
    for t, ms in sorted(by_type.items()):
        lines.append(f"    {t}: {len(ms)} duplicate list(s) -> 1 canonical, {sum(m.items for m in ms)} item(s) move")
    return "\n".join(lines)


_LIVE_LISTS_SQL = (
    "SELECT l.id, l.user_id, l.name, l.list_type, l.visibility, l.created_at, "
    "(SELECT COUNT(*) FROM list_items i WHERE i.list_id = l.id AND i.deleted = 0) AS n "
    "FROM lists l WHERE l.deleted = 0 AND l.list_type = ANY($1::text[])"
)


async def fetch_rows(conn, types: tuple[str, ...]) -> list[dict]:
    return [dict(r) for r in await conn.fetch(_LIVE_LISTS_SQL, list(types))]


async def apply_plan(conn, plan: list[Merge]) -> dict[str, int]:
    """Apply ``plan`` through ``conn`` (the caller owns the transaction). Returns counts."""
    moved = deduped = lists_removed = 0
    for m in plan:
        # An item whose text (case-insensitive) is already live on the canonical list in the same completed state would only
        # be a duplicate: retire it instead of moving it.
        dup_tag = await conn.execute(
            "UPDATE list_items AS d SET deleted = 1, updated_at = NOW() "
            "WHERE d.list_id = $1 AND d.deleted = 0 AND EXISTS ("
            "  SELECT 1 FROM list_items c WHERE c.list_id = $2 AND c.deleted = 0 "
            "  AND lower(c.text) = lower(d.text) AND COALESCE(c.completed, 0) = COALESCE(d.completed, 0))",
            m.duplicate_id, m.canonical_id,
        )
        deduped += _rowcount(dup_tag)
        move_tag = await conn.execute(
            "UPDATE list_items SET list_id = $2, updated_at = NOW() WHERE list_id = $1 AND deleted = 0",
            m.duplicate_id, m.canonical_id,
        )
        moved += _rowcount(move_tag)
        gone = await conn.execute(
            "UPDATE lists SET deleted = 1, updated_at = NOW() WHERE id = $1 AND deleted = 0 "
            "AND NOT EXISTS (SELECT 1 FROM list_items WHERE list_id = $1 AND deleted = 0)",
            m.duplicate_id,
        )
        lists_removed += _rowcount(gone)
    return {"moved": moved, "deduped": deduped, "lists_removed": lists_removed}


def _rowcount(tag: object) -> int:
    """asyncpg returns a command tag such as 'UPDATE 3'."""
    try:
        return int(str(tag).rsplit(" ", 1)[-1])
    except (ValueError, IndexError):
        return 0


async def main(execute: bool, assume_yes: bool, expect_db: str, expect_host: str, types: tuple[str, ...]) -> int:
    import asyncpg  # deferred: the planner above must import without a PostgreSQL driver
    from purge_orphaned_test_data import _redacted_target, _resolve_dsn

    dsn = _resolve_dsn()
    if not dsn:
        print("POSTGRES_URL is not set and could not be read from the service .env. Run this on the zoe-data host.",
              file=sys.stderr)
        return 2
    parts = urlsplit(dsn)
    target_db = (parts.path or "/").lstrip("/")
    target_hostport = f"{parts.hostname or ''}:{parts.port or 5432}"
    print(f"Target database: {_redacted_target(dsn)}")
    if execute and assume_yes:
        if parts.query:
            print("Refusing non-interactive --yes: POSTGRES_URL carries query options.", file=sys.stderr)
            return 2
        if not expect_db or not expect_host:
            print(f"Refusing --yes without --expect-db {target_db!r} --expect-host {target_hostport!r}.", file=sys.stderr)
            return 2
        expect_hostport = expect_host if ":" in expect_host else f"{expect_host}:5432"
        if expect_db != target_db or expect_hostport != target_hostport:
            print("Target mismatch: aborting, nothing changed.", file=sys.stderr)
            return 2

    conn = await asyncpg.connect(dsn)
    try:
        # The dry run is read-only by construction, not just by omission.
        async with conn.transaction(readonly=not execute):
            plan = plan_merges(await fetch_rows(conn, types))
            print(summarise(plan))
            if not execute:
                print("\nDRY-RUN - nothing changed. Re-run with --execute to apply.")
                return 0
            if not plan:
                print("\nNothing to do.")
                return 0
            if not assume_yes and input("\nType 'yes' to merge these lists: ").strip().lower() != "yes":
                print("Aborted - nothing changed.")
                return 1
            counts = await apply_plan(conn, plan)
        print(f"\nAPPLIED - items moved: {counts['moved']}, items de-duplicated: {counts['deduped']}, "
              f"duplicate lists soft-deleted: {counts['lists_removed']}")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--execute", action="store_true", help="apply the merge (default is a read-only dry run)")
    ap.add_argument("--yes", action="store_true", help="skip the interactive confirmation; requires --expect-db/--expect-host")
    ap.add_argument("--expect-db", default="", help="assert the resolved target DB name (required with --yes)")
    ap.add_argument("--expect-host", default="", help="assert the resolved DB host[:port] (required with --yes)")
    ap.add_argument("--types", default=",".join(DEFAULT_TYPES), help="comma-separated list types to merge (default: shopping)")
    args = ap.parse_args()
    wanted = tuple(t.strip() for t in args.types.split(",") if t.strip())
    sys.exit(asyncio.run(main(args.execute, args.yes, args.expect_db, args.expect_host, wanted)))
