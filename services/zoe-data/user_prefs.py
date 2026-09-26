"""user_prefs — per-user preference flags on the existing ``user_preferences`` store.

The store is the ``user_preferences`` table (alembic ``0001``): one row per
user, ``prefs`` = a JSON dict. This module is the ONE reader/writer for that
dict outside a request (``routers/user_profile.py`` re-exports the same
helpers for its route code), so a preference key is spelled once.

Keys:
    ``KEY_MEMORY_OPT_OUT`` — bool. When true, the post-turn extractor drops
    chat-derived memories for the user (see ``memory_extractor.extract_and_ingest``
    and ``MemoryService.ingest(opt_out=...)``). Explicit teach/ingest paths
    are unaffected, and flipping it never purges past memories.

Every helper takes an optional ``db`` (a request-scoped ``Depends(get_db)``
connection); when omitted it opens one via ``db_pool.get_db_ctx``.
"""
from __future__ import annotations

import json
from typing import Any, Optional

KEY_MEMORY_OPT_OUT = "memory_opt_out"


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
    """Upsert the whole prefs dict for ``user_id``."""
    await db.execute(
        """INSERT INTO user_preferences (user_id, prefs, updated_at)
           VALUES (?, ?, NOW())
           ON CONFLICT(user_id) DO UPDATE SET prefs = excluded.prefs, updated_at = NOW()""",
        (user_id, json.dumps(prefs)),
    )
    await db.commit()


async def get_pref(user_id: str, key: str, default: Any = None, *, db=None) -> Any:
    """Read one preference key for ``user_id``; ``default`` when unset."""
    if db is not None:
        return (await read_prefs(db, user_id)).get(key, default)
    from db_pool import get_db_ctx

    async with get_db_ctx() as conn:
        return (await read_prefs(conn, user_id)).get(key, default)


async def set_pref(user_id: str, key: str, value: Any, *, db=None) -> None:
    """Write one preference key for ``user_id``, preserving the other keys."""
    if db is not None:
        prefs = await read_prefs(db, user_id)
        prefs[key] = value
        await write_prefs(db, user_id, prefs)
        return
    from db_pool import get_db_ctx

    async with get_db_ctx() as conn:
        prefs = await read_prefs(conn, user_id)
        prefs[key] = value
        await write_prefs(conn, user_id, prefs)


async def is_memory_opted_out(user_id: str, *, db=None) -> bool:
    """True when ``user_id`` has opted out of automatic memory capture. Default False."""
    return bool(await get_pref(user_id, KEY_MEMORY_OPT_OUT, False, db=db))
