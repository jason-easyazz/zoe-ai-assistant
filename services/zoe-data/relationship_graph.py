"""Bounded, read-only multi-hop relationship traversal (ADR roadmap item 3).

Increment 3 of the relationship-memory ADR
(``docs/adr/ADR-relationship-memory.md``): "who are Tom's siblings", "everyone
in my work circle", "how is X related to Y" — real graph queries over the
caller's ``person_relationships`` edges, **without a graph DB**.

Design constraints (per the ADR + the Graphiti bake-off guidance):
- **Read-only, additive, OFF the chat hot path.** Nothing here runs on a chat or
  voice turn; it is an explicit-query capability. It does not touch
  ``zoe_memory_compose``/``person_extractor``/``brain_dispatch`` or any voice
  code, so no voice-replay gate applies.
- **No graph DB.** A single portable ``WITH RECURSIVE`` breadth-first walk over
  the existing edge table, plus one name-resolution query. ``WITH RECURSIVE``,
  ``||`` concat, and ``LIKE`` all behave identically on PostgreSQL (asyncpg) and
  SQLite (tests), and ``people.id`` / ``person_*_id`` are ``TEXT`` on both, so no
  UUID casts are needed.
- **Owner-scoped.** Every edge and name read is filtered on
  ``person_relationships.user_id = <caller>`` / ``people.user_id = <caller>``.
  No cross-user traversal, ever.
- **Bounded + cycle-safe.** ``max_depth`` and ``limit`` are hard-clamped
  (<= ``_MAX_DEPTH_CAP`` / <= ``_LIMIT_CAP``) regardless of caller input, and the
  recursion carries the visited ``path`` and refuses to re-enter a node
  (``path NOT LIKE '%|' || other || '|%'``), so a cycle (A-B-C-A) terminates and
  each node is reported once at its minimum depth.
- **Flag-gated** behind ``ZOE_RELATIONSHIP_GRAPH_ENABLED`` (env, default
  **OFF**), read lazily per-call — same idiom as
  ``zoe_memory_compose.compose_enabled``. The module always imports cleanly; when
  OFF the endpoint returns a disabled response before any DB work.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── Flag (default OFF) ─────────────────────────────────────────────────────

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def relationship_graph_enabled() -> bool:
    """Cheap per-call read of the traversal flag (default OFF).

    Same lazy ``os.environ.get`` idiom as ``zoe_memory_compose.compose_enabled``.
    OFF is a true no-op: the endpoint returns a disabled response before any DB
    work, and importing this module has no side effects.
    """
    return os.environ.get("ZOE_RELATIONSHIP_GRAPH_ENABLED", "").strip().lower() in _TRUTHY


# ── Hard caps (bound the recursion regardless of caller input) ─────────────

_MAX_DEPTH_CAP = 4
_LIMIT_CAP = 200
_DEFAULT_DEPTH = 2
_DEFAULT_LIMIT = 50


def _clamp_depth(max_depth: int) -> int:
    try:
        d = int(max_depth)
    except (TypeError, ValueError):
        return _DEFAULT_DEPTH
    if d < 1:
        return 1
    return min(d, _MAX_DEPTH_CAP)


def _clamp_limit(limit: int) -> int:
    try:
        n = int(limit)
    except (TypeError, ValueError):
        return _DEFAULT_LIMIT
    if n < 1:
        return 1
    return min(n, _LIMIT_CAP)


# ── The bounded BFS recursive CTE ──────────────────────────────────────────
#
# Portable across PostgreSQL and SQLite:
#   * ``person_a_id``/``person_b_id``/``people.id`` are TEXT on both, so the
#     ``'|' || id || '|'`` path bookkeeping and the ``LIKE`` cycle guard need no
#     casts.
#   * Edges are treated as bidirectional (a<->b) by UNION ALL-ing both directions
#     into the ``e`` join subquery.
#   * ``rg.path NOT LIKE '%|' || e.other || '|%'`` refuses to revisit a node
#     already on the current path — this is the cycle guard AND what makes the
#     walk terminate (a finite user graph has finitely many simple paths).
#   * The outer ``MIN(depth) ... GROUP BY pid`` collapses the (possibly many)
#     paths that reach a node down to one row at its shortest distance.
#
# ``?`` placeholders (SQLite-native; AsyncpgCompat rewrites ?->$N). A ``$N``
# variant is kept for a direct-asyncpg fallback (try-$N-except-? idiom).
#
# TODO(temporal): once migration 0015 (PR #1024) lands adding valid_from/valid_to
# to person_relationships, add "AND pr.valid_to IS NULL" to each edge SELECT so we
# traverse only *current* edges (was-married-now-divorced returns current truth).

_NEIGHBORS_SQL_Q = """
WITH RECURSIVE rg(pid, depth, path) AS (
    SELECT ?, 0, '|' || ? || '|'
    UNION ALL
    SELECT e.other, rg.depth + 1, rg.path || e.other || '|'
    FROM rg
    JOIN (
        SELECT pr.person_a_id AS src, pr.person_b_id AS other
        FROM person_relationships pr WHERE pr.user_id = ?
        UNION ALL
        SELECT pr.person_b_id AS src, pr.person_a_id AS other
        FROM person_relationships pr WHERE pr.user_id = ?
    ) e ON e.src = rg.pid
    WHERE rg.depth < ?
      AND rg.path NOT LIKE '%|' || e.other || '|%'
)
SELECT pid, MIN(depth) AS depth
FROM rg
WHERE pid <> ?
GROUP BY pid
ORDER BY depth, pid
LIMIT ?
"""

async def _run(db, sql: str, args: tuple) -> list:
    """Execute a SELECT via the ``?``-placeholder form.

    This is the proven production path: aiosqlite runs ``?`` natively (tests) and
    production's ``AsyncpgCompat`` rewrites ``?``->``$N`` and returns a
    cursor-like exposing ``fetchall`` — exactly what ``zoe_memory_compose`` uses.
    There is deliberately NO raw-asyncpg fallback: ``asyncpg.Connection.execute``
    returns a status string (not a cursor), so ``.fetchall()`` on it would crash;
    if a raw connection is ever needed, use ``connection.fetch``.
    """
    cursor = await db.execute(sql, args)
    return await cursor.fetchall()


async def neighbors(
    db,
    user_id: str,
    start_person_id: str,
    *,
    max_depth: int = _DEFAULT_DEPTH,
    limit: int = _DEFAULT_LIMIT,
) -> list[dict[str, Any]]:
    """Return the caller's people reachable from ``start_person_id`` within
    ``max_depth`` bidirectional relationship hops.

    Owner-scoped: only edges/people with ``user_id = user_id`` are ever visited.
    Bounded + cycle-safe: ``max_depth``/``limit`` are hard-clamped and the walk
    refuses to revisit a node on its path, so a cyclic graph still terminates and
    each node is reported once at its minimum depth.

    Returns ``[{person_id, name, depth, via_label}]`` ordered by (depth, name).
    ``via_label`` is a best-effort relationship label on an edge that reaches the
    node (may be ``None`` if the neighbour or its connecting edge is gone). An
    unknown/empty ``start_person_id`` yields ``[]`` (not an error).
    """
    if not user_id or not start_person_id:
        return []

    md = _clamp_depth(max_depth)
    lim = _clamp_limit(limit)

    # 1) Bounded BFS over the edge graph → [(pid, depth), ...]
    args = (start_person_id, start_person_id, user_id, user_id, md, start_person_id, lim)
    try:
        hop_rows = await _run(db, _NEIGHBORS_SQL_Q, args)
    except Exception as exc:
        logger.warning("neighbors: BFS query failed for %r: %s", start_person_id, exc)
        return []

    depth_by_pid: dict[str, int] = {}
    for row in hop_rows:
        pid = row[0]
        depth = row[1]
        if pid is None or pid == start_person_id:
            continue
        # MIN(depth) already collapses per-pid, but guard anyway.
        if pid not in depth_by_pid or depth < depth_by_pid[pid]:
            depth_by_pid[pid] = int(depth)

    if not depth_by_pid:
        return []

    pids = list(depth_by_pid.keys())

    # 2) Resolve pid -> name (owner-scoped, not soft-deleted).
    names = await _resolve_names(db, user_id, pids)

    # 3) Best-effort connecting label for each reached node.
    via = await _resolve_via_labels(db, user_id, pids)

    out: list[dict[str, Any]] = []
    for pid in pids:
        name = names.get(pid)
        if name is None:
            # Edge points at a soft-deleted / cross-user / missing node — drop it
            # so we never surface a bare id or another user's person.
            continue
        out.append({
            "person_id": pid,
            "name": name,
            "depth": depth_by_pid[pid],
            "via_label": via.get(pid),
        })

    out.sort(key=lambda r: (r["depth"], (r["name"] or "").lower(), r["person_id"]))
    return out


async def _resolve_names(db, user_id: str, pids: list[str]) -> dict[str, str]:
    """Map each pid -> people.name, owner-scoped and excluding soft-deleted.

    Cross-user or soft-deleted ids simply don't appear in the result, so callers
    that filter on membership never leak another user's person.
    """
    if not pids:
        return {}
    placeholders_q = ",".join(["?"] * len(pids))
    sql_q = (
        f"SELECT id, name FROM people "
        f"WHERE user_id = ? AND deleted = 0 AND id IN ({placeholders_q})"
    )
    args = (user_id, *pids)
    try:
        rows = await _run(db, sql_q, args)
    except Exception as exc:
        logger.warning("_resolve_names failed: %s", exc)
        return {}
    return {r[0]: r[1] for r in rows}


async def _resolve_via_labels(db, user_id: str, pids: list[str]) -> dict[str, str]:
    """Best-effort: for each reached pid, one relationship label on an edge
    touching it (owner-scoped). Used for a human-readable "via" hint only.

    If a node is reached only through intermediate hops, the label is whatever
    edge mentions it directly; it may be ``None``. This is a hint, not the path.
    """
    if not pids:
        return {}
    placeholders_q = ",".join(["?"] * len(pids))
    sql_q = (
        f"SELECT person_b_id AS pid, rel_a_to_b AS label FROM person_relationships "
        f"WHERE user_id = ? AND person_b_id IN ({placeholders_q}) "
        f"UNION ALL "
        f"SELECT person_a_id AS pid, rel_b_to_a AS label FROM person_relationships "
        f"WHERE user_id = ? AND person_a_id IN ({placeholders_q})"
    )
    args = (user_id, *pids, user_id, *pids)
    try:
        rows = await _run(db, sql_q, args)
    except Exception as exc:
        logger.warning("_resolve_via_labels failed: %s", exc)
        return {}
    out: dict[str, str] = {}
    for r in rows:
        pid, label = r[0], r[1]
        if pid in out:  # keep first-seen label; best-effort only
            continue
        out[pid] = label
    return out


__all__ = [
    "relationship_graph_enabled",
    "neighbors",
]
