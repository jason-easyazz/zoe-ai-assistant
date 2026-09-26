"""user_prefs — per-user preference flags on the existing ``user_preferences`` store.

The store is the ``user_preferences`` table (alembic ``0001``): one row per
user, ``prefs`` = a JSON dict. This module is the ONE reader/writer of that
dict, so a preference key is spelled once and every writer shares the same
concurrency contract:

* ``set_pref`` / ``delete_pref`` are SINGLE atomic statements (``jsonb ||`` /
  ``jsonb -``) — no read-modify-write, so two overlapping requests that touch
  different keys (an opt-out PUT racing a Telegram link) can never clobber
  each other. ``delete_pref(only_if=…)`` is conditional in SQL, so a stale
  "clear the other claimant" step cannot remove a key that was re-pointed
  under it. Proven on the live Postgres (rolled back) 2026-09-26.
* ``write_prefs`` replaces the WHOLE dict. Keep it for bulk/import paths
  only; never pair it with a prior ``read_prefs`` to change one key.

Keys:
    ``KEY_MEMORY_OPT_OUT`` — bool. When true, every AUTOMATIC memory writer
    (``MEMORY_OPT_OUT_SOURCES``: the per-turn extractor + turn digest + person
    extractors, the idle/nightly digest + consolidation, synthesis) drops the
    write at the ``MemoryService.ingest`` chokepoint. Explicit teach paths
    (``brain_tool``, ``voice_fact``, ``review_ui``, ``proposal``, notes/people/
    journal) are unaffected, and flipping it never purges past memories.

Every helper takes an optional ``db`` (a request-scoped ``Depends(get_db)``
connection); when omitted it opens one via ``db_pool.get_db_ctx``.
"""
from __future__ import annotations

import json
import time
from contextlib import asynccontextmanager
from typing import Any

KEY_MEMORY_OPT_OUT = "memory_opt_out"

# Sources whose writes are AUTOMATIC (mined from what the user said, not an
# explicit "remember this"). These honour KEY_MEMORY_OPT_OUT at ingest.
MEMORY_OPT_OUT_SOURCES = frozenset({
    "chat_regex",       # memory_extractor.extract_and_ingest (chat + voice + zoe_agent)
    "turn_digest",      # memory_digest.run_turn_digest (same turn)
    "conversation",     # person_extractor / person_extractor_llm (same turn)
    "ambient",          # ambient audio capture
    "digest",           # memory_digest nightly / idle
    "consolidation",    # idle consolidation
    "synthesis",        # memory_digest higher-order insights
    "music_digest",     # listening-journal digest
})

_UNSET = object()

# is_memory_opted_out is consulted on EVERY automatic ingest (a chat turn fans
# out to four writers; a digest run ingests many rows), so successful lookups
# are cached per user for a short TTL. Only successes are cached; a write
# through this module invalidates the user's entry (zoe-data is one process).
_OPT_OUT_TTL_S = 30.0
_opt_out_cache: dict[str, tuple[bool, float]] = {}
# Bumped by every invalidation. A read captures it before awaiting the DB and
# stores its result only if nothing was invalidated meanwhile — otherwise a
# read that began before an opt-out PUT would resume after the PUT cleared the
# cache and park its stale False for a full TTL (Greptile #1704).
_cache_gen = 0


def clear_pref_cache(user_id: str | None = None) -> None:
    global _cache_gen
    _cache_gen += 1
    if user_id is None:
        _opt_out_cache.clear()
    else:
        _opt_out_cache.pop(user_id, None)


@asynccontextmanager
async def _conn(db):
    if db is not None:
        yield db
        return
    from db_pool import get_db_ctx

    async with get_db_ctx() as conn:
        yield conn


async def read_prefs(db, user_id: str) -> dict:
    """Return the ``user_preferences.prefs`` JSON dict for ``user_id`` (or ``{}``)."""
    cursor = await db.execute(
        "SELECT prefs FROM user_preferences WHERE user_id = ?",
        (user_id,),
    )
    row = await cursor.fetchone()
    if not row:
        return {}
    try:
        raw = row["prefs"]
        parsed = json.loads(raw) if isinstance(raw, str) else raw
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


async def write_prefs(db, user_id: str, prefs: dict) -> None:
    """Replace the WHOLE prefs dict for ``user_id`` (bulk paths only — see module doc)."""
    await db.execute(
        """INSERT INTO user_preferences (user_id, prefs, updated_at)
           VALUES (?, ?, NOW())
           ON CONFLICT(user_id) DO UPDATE SET prefs = excluded.prefs, updated_at = NOW()""",
        (user_id, json.dumps(prefs)),
    )
    await db.commit()
    clear_pref_cache(user_id)


async def get_pref(user_id: str, key: str, default: Any = None, *, db=None) -> Any:
    """Read one preference key for ``user_id``; ``default`` when unset."""
    async with _conn(db) as conn:
        return (await read_prefs(conn, user_id)).get(key, default)


async def set_pref(user_id: str, key: str, value: Any, *, db=None) -> None:
    """Atomically set ONE key for ``user_id``, preserving every other key.

    A single upsert whose conflict branch merges the new key into the stored
    JSON server-side (``jsonb ||``), so it cannot lose a concurrent writer's key.
    """
    async with _conn(db) as conn:
        await conn.execute(
            """INSERT INTO user_preferences (user_id, prefs, updated_at)
               VALUES (?, ?, NOW())
               ON CONFLICT(user_id) DO UPDATE
               SET prefs = (COALESCE(user_preferences.prefs, '{}')::jsonb || excluded.prefs::jsonb)::text,
                   updated_at = NOW()""",
            (user_id, json.dumps({key: value})),
        )
        await conn.commit()
    clear_pref_cache(user_id)


async def delete_pref(user_id: str, key: str, *, db=None, only_if: Any = _UNSET) -> None:
    """Atomically remove ONE key for ``user_id`` (no-op when absent).

    With ``only_if``, the key is removed only while it still holds that value
    (compared as text) — the check and the write are one statement.
    """
    sql = """UPDATE user_preferences
             SET prefs = (COALESCE(prefs, '{}')::jsonb - ?::text)::text, updated_at = NOW()
             WHERE user_id = ?"""
    params: tuple = (key, user_id)
    if only_if is not _UNSET:
        sql += " AND prefs::jsonb ->> ?::text = ?::text"
        params = (key, user_id, key, str(only_if))
    async with _conn(db) as conn:
        await conn.execute(sql, params)
        await conn.commit()
    clear_pref_cache(user_id)


async def is_memory_opted_out(user_id: str, *, db=None) -> bool:
    """True when ``user_id`` has opted out of automatic memory capture. Default False."""
    now = time.monotonic()
    hit = _opt_out_cache.get(user_id)
    if hit is not None and hit[1] > now:
        return hit[0]
    gen = _cache_gen
    flag = bool(await get_pref(user_id, KEY_MEMORY_OPT_OUT, False, db=db))
    if _cache_gen == gen:          # nothing invalidated while we were reading
        _opt_out_cache[user_id] = (flag, time.monotonic() + _OPT_OUT_TTL_S)
    return flag
