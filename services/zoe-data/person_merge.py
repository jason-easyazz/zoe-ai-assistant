"""Person merge / entity resolution — fold an ``is_partial`` stub into a real contact.

Roadmap item 4 of ``docs/adr/ADR-relationship-memory.md`` (Precision + identity):
the relationship extractor auto-creates a **partial** ``people`` stub for an unknown
name (``person_extractor._write_relationship`` → stub). When that stub turns out to be
someone Zoe already has as a full contact (a name collision, or a stub that gets
promoted to a real person), we need to fold the stub into the real row and **re-point
all of its data** so there is one node, no duplicate edges, no orphaned satellite facts.

This is an **explicit, user-triggered** capability (a merge endpoint), NOT something that
runs on every chat/voice turn — so it is off the chat hot path and needs no voice-replay
gate. It is flag-gated behind ``ZOE_PERSON_MERGE_ENABLED`` (default OFF), read lazily per
call (same idiom as ``zoe_memory_compose.compose_enabled``): when OFF the endpoint 404s
before any DB work, so this module is a true no-op until deliberately enabled.

Everything is **owner-scoped** on ``user_id`` — a merge can only touch the caller's own
people/edges/satellite rows; a source or target owned by another user is invisible
(raises ``PersonMergeError`` → 404 at the endpoint).

Portable Postgres/SQLite: every statement is written twice — a ``$N``/asyncpg form and a
``?``/sqlite form — with the try-``$N``-except-``?`` idiom used throughout
``person_extractor``. (In production the router hands us an ``AsyncpgCompat`` connection
that accepts either style; in tests it is a raw ``aiosqlite`` connection.)
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

_TRUTHY = frozenset({"1", "true", "yes", "on"})

# Satellite tables that carry a ``person_id`` FK to ``people(id)`` and a ``user_id``
# owner column (migrations 0006). Each is re-pointed source→target, owner-scoped.
_SATELLITE_TABLES = (
    "person_activities",
    "person_bucket_list",
    "person_gift_ideas",
    "person_important_dates",
)


class PersonMergeError(Exception):
    """Merge cannot proceed (missing person, same id, cross-user). Endpoint → 404/400."""


def person_merge_enabled() -> bool:
    """Cheap per-call read of the person-merge flag (default OFF).

    Mirrors ``zoe_memory_compose.compose_enabled`` — a plain truthiness check with no
    side effects, so OFF is a true no-op.
    """
    return os.environ.get("ZOE_PERSON_MERGE_ENABLED", "").strip().lower() in _TRUTHY


async def _close_cursor(cur) -> None:
    """Best-effort cursor close (aiosqlite exposes close(); asyncpg may not)."""
    close = getattr(cur, "close", None)
    if close is None:
        return
    try:
        res = close()
        if asyncio.iscoroutine(res):
            await res
    except Exception:
        pass


async def _fetchone(db, sql_pg: str, sql_q: str, params: tuple):
    """Run a SELECT with the $N form, falling back to the ? form; return one row."""
    try:
        cur = await db.execute(sql_pg, *params)
    except Exception:
        cur = await db.execute(sql_q, params)
    try:
        return await cur.fetchone()
    finally:
        await _close_cursor(cur)


async def _exec(db, sql_pg: str, sql_q: str, params: tuple) -> int:
    """Run a write with the $N form, falling back to the ? form; return rowcount.

    ``rowcount`` is best-effort (``AsyncpgCompat._Cursor`` and aiosqlite both expose it);
    a driver that doesn't report it yields ``0`` here, which only affects the reported
    count, never correctness.
    """
    try:
        cur = await db.execute(sql_pg, *params)
    except Exception:
        cur = await db.execute(sql_q, params)
    try:
        return getattr(cur, "rowcount", 0) or 0
    finally:
        await _close_cursor(cur)


async def _load_person(db, user_id: str, person_id: str) -> Optional[dict]:
    """Return the (non-deleted) people row for this owner, or None."""
    row = await _fetchone(
        db,
        "SELECT id, is_partial, relationship, how_we_met, first_met_date, notes "
        "FROM people WHERE id=$1 AND user_id=$2 AND deleted=0",
        "SELECT id, is_partial, relationship, how_we_met, first_met_date, notes "
        "FROM people WHERE id=? AND user_id=? AND deleted=0",
        (person_id, user_id),
    )
    if row is None:
        return None
    # aiosqlite Row and asyncpg Record both support key access; normalise to dict.
    try:
        return dict(row)
    except (TypeError, ValueError):
        keys = ("id", "is_partial", "relationship", "how_we_met", "first_met_date", "notes")
        return {k: row[i] for i, k in enumerate(keys)}


def _empty(value) -> bool:
    """A field is fillable-from-source when it is NULL or blank/whitespace."""
    return value is None or (isinstance(value, str) and value.strip() == "")


async def merge_person(db, user_id: str, source_id: str, target_id: str) -> dict:
    """Fold ``source_id`` into ``target_id`` for ``user_id``; re-point all its data.

    Steps (all owner-scoped):
      1. Validate both people exist for this user (not deleted) and are distinct.
      2. Re-point the satellite tables (activities / bucket_list / gift_ideas /
         important_dates): ``UPDATE ... SET person_id=target WHERE person_id=source``.
      3. Re-point ``people.introduced_by_person_id`` (self-FK) source→target.
      4. Re-point ``person_relationships`` (both endpoints), resolving the two conflicts
         a re-point can create under the partial current-edge unique index:
           * **self-edge** — after re-point ``person_a_id == person_b_id`` → delete it.
           * **duplicate current edge** — target already has a *current* edge for the
             pair the source edge would collapse onto → drop the source-derived current
             edge first so ``person_relationships_pair_active`` is never violated. Only
             CURRENT (``valid_to IS NULL``) edges collide; historical rows are untouched.
      5. Merge ``people`` row fields — target wins; fill target's NULL/empty
         ``how_we_met`` / ``first_met_date`` / ``notes`` / ``relationship`` from source;
         the survivor is partial only if BOTH were partial.
      6. Soft-delete the source (``deleted=1``).

    MemPalace facts for the source live under ``entity_id=source_id`` (see
    ``person_extractor._ingest_to_mempalace`` and ``routers.people._archive_person_mempalace``);
    re-homing them is best-effort and intentionally NOT done here — merge must not block
    on the vector store. Returns a summary dict for the endpoint.
    """
    if source_id == target_id:
        raise PersonMergeError("source and target are the same person")

    source = await _load_person(db, user_id, source_id)
    if source is None:
        raise PersonMergeError(f"source person {source_id!r} not found for this user")
    target = await _load_person(db, user_id, target_id)
    if target is None:
        raise PersonMergeError(f"target person {target_id!r} not found for this user")

    repointed: dict[str, int] = {}

    # ── 2. Re-point satellite tables ─────────────────────────────────────────
    for table in _SATELLITE_TABLES:
        moved = await _exec(
            db,
            f"UPDATE {table} SET person_id=$1 WHERE person_id=$2 AND user_id=$3",
            f"UPDATE {table} SET person_id=? WHERE person_id=? AND user_id=?",
            (target_id, source_id, user_id),
        )
        repointed[table] = moved

    # ── 3. Re-point the introduced_by self-FK ────────────────────────────────
    repointed["people.introduced_by_person_id"] = await _exec(
        db,
        "UPDATE people SET introduced_by_person_id=$1 "
        "WHERE introduced_by_person_id=$2 AND user_id=$3",
        "UPDATE people SET introduced_by_person_id=? "
        "WHERE introduced_by_person_id=? AND user_id=?",
        (target_id, source_id, user_id),
    )

    # ── 4. Re-point relationship edges + resolve conflicts ───────────────────
    deduped_edges, dropped_self_edges, repointed_edges = await _repoint_relationships(
        db, user_id, source_id, target_id
    )
    repointed["person_relationships"] = repointed_edges

    # ── 5. Merge people row fields (target wins; fill blanks from source) ─────
    await _merge_people_fields(db, user_id, source, target)

    # ── 6. Soft-delete the source ────────────────────────────────────────────
    await _exec(
        db,
        "UPDATE people SET deleted=1, updated_at=$1 WHERE id=$2 AND user_id=$3",
        "UPDATE people SET deleted=1, updated_at=? WHERE id=? AND user_id=?",
        (datetime.utcnow().isoformat() + "Z", source_id, user_id),
    )

    await db.commit()

    return {
        "source_id": source_id,
        "target_id": target_id,
        "repointed": repointed,
        "dropped_self_edges": dropped_self_edges,
        "deduped_edges": deduped_edges,
    }


async def _fetchall(db, sql_pg: str, sql_q: str, params: tuple) -> list:
    try:
        cur = await db.execute(sql_pg, *params)
    except Exception:
        cur = await db.execute(sql_q, params)
    try:
        return list(await cur.fetchall())
    finally:
        await _close_cursor(cur)


async def _repoint_relationships(
    db, user_id: str, source_id: str, target_id: str
) -> tuple[int, int, int]:
    """Re-point every edge touching ``source_id`` onto ``target_id`` for this owner.

    Returns ``(deduped_edges, dropped_self_edges, repointed_edges)``.

    The partial unique index ``person_relationships_pair_active`` allows only ONE row per
    ``(user, a, b)`` where ``valid_to IS NULL``. Naively UPDATE-ing source→target can:
      * turn an edge into a **self-edge** (a==b after re-point) — impossible relationship;
      * collide two **current** edges onto the same pair — index violation.

    Both are resolved edge-by-edge, PRE-checking colliding current edges BEFORE the UPDATE
    so no failed UPDATE is ever attempted. Historical (``valid_to`` set) rows are never a
    conflict and are simply re-pointed.
    """
    dropped_self = 0
    deduped = 0
    repointed = 0

    # Every edge (current OR historical) with source on either endpoint, owner-scoped.
    rows = await _fetchall(
        db,
        "SELECT id, person_a_id, person_b_id, valid_to FROM person_relationships "
        "WHERE user_id=$1 AND ($2 IN (person_a_id, person_b_id))",
        "SELECT id, person_a_id, person_b_id, valid_to FROM person_relationships "
        "WHERE user_id=? AND (? IN (person_a_id, person_b_id))",
        (user_id, source_id),
    )

    for row in rows:
        try:
            edge = dict(row)
        except (TypeError, ValueError):
            edge = {
                "id": row[0],
                "person_a_id": row[1],
                "person_b_id": row[2],
                "valid_to": row[3],
            }
        edge_id = edge["id"]
        new_a = target_id if edge["person_a_id"] == source_id else edge["person_a_id"]
        new_b = target_id if edge["person_b_id"] == source_id else edge["person_b_id"]
        is_current = edge["valid_to"] is None

        # (a) Self-edge: a person cannot relate to themselves — drop it.
        if new_a == new_b:
            await _exec(
                db,
                "DELETE FROM person_relationships WHERE id=$1 AND user_id=$2",
                "DELETE FROM person_relationships WHERE id=? AND user_id=?",
                (edge_id, user_id),
            )
            dropped_self += 1
            continue

        # (b) Duplicate CURRENT edge: only current rows collide on the partial index.
        if is_current:
            collision = await _fetchone(
                db,
                "SELECT id FROM person_relationships WHERE user_id=$1 AND person_a_id=$2 "
                "AND person_b_id=$3 AND valid_to IS NULL AND id<>$4 LIMIT 1",
                "SELECT id FROM person_relationships WHERE user_id=? AND person_a_id=? "
                "AND person_b_id=? AND valid_to IS NULL AND id<>? LIMIT 1",
                (user_id, new_a, new_b, edge_id),
            )
            if collision is not None:
                # Target already has a current edge for this pair — keep it, drop the
                # source-derived duplicate BEFORE any UPDATE (index never violated).
                await _exec(
                    db,
                    "DELETE FROM person_relationships WHERE id=$1 AND user_id=$2",
                    "DELETE FROM person_relationships WHERE id=? AND user_id=?",
                    (edge_id, user_id),
                )
                deduped += 1
                continue

        # (c) Safe to re-point this edge.
        await _exec(
            db,
            "UPDATE person_relationships SET person_a_id=$1, person_b_id=$2 "
            "WHERE id=$3 AND user_id=$4",
            "UPDATE person_relationships SET person_a_id=?, person_b_id=? "
            "WHERE id=? AND user_id=?",
            (new_a, new_b, edge_id, user_id),
        )
        repointed += 1

    return deduped, dropped_self, repointed


async def _merge_people_fields(db, user_id: str, source: dict, target: dict) -> None:
    """Fill target's NULL/empty how_we_met/first_met_date/notes/relationship from source;
    resolve is_partial (survivor is partial only if BOTH were partial)."""
    updates: list[tuple[str, object]] = []
    for field in ("how_we_met", "first_met_date", "notes", "relationship"):
        if _empty(target.get(field)) and not _empty(source.get(field)):
            updates.append((field, source[field]))

    # is_partial: survivor is a full contact if EITHER side is a full contact.
    src_partial = int(source.get("is_partial") or 0)
    tgt_partial = int(target.get("is_partial") or 0)
    survivor_partial = 1 if (src_partial and tgt_partial) else 0
    if survivor_partial != tgt_partial:
        updates.append(("is_partial", survivor_partial))

    if not updates:
        return

    # Fields changed → bump updated_at so ordering/audit reflect the merge.
    updates.append(("updated_at", datetime.utcnow().isoformat() + "Z"))

    set_cols = ", ".join(f"{col}=$" + str(i + 1) for i, (col, _) in enumerate(updates))
    set_cols_q = ", ".join(f"{col}=?" for col, _ in updates)
    values = [v for _, v in updates]
    n = len(values)
    await _exec(
        db,
        f"UPDATE people SET {set_cols} WHERE id=${n + 1} AND user_id=${n + 2}",
        f"UPDATE people SET {set_cols_q} WHERE id=? AND user_id=?",
        (*values, target["id"], user_id),
    )
