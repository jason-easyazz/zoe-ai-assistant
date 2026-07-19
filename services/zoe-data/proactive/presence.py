"""Presence primitive — "is someone plausibly at a panel right now?"

P-W2.1 (Samantha W2, "Zoe speaks first"): a single bounded read over
``ui_panel_sessions`` answering whether ``user_id`` has a foreground panel
session fresh enough to plausibly mean a human is at that panel. The panel
executor refreshes its session row every few seconds while the page is
foregrounded, so a recent ``is_foreground = 1`` row is a good proxy for
"someone is standing there".

Read-only by design: no LLM, no writes, no side effects. Presence is a gate
for OPTIONAL behaviour (e.g. the P-W2.2 spoken-delivery adapter) — a DB
hiccup must read as "nobody there", never break the caller's mandatory
path, so errors are logged and reported as absence.

``last_seen_at`` is stored as TEXT (``NOW()::TEXT`` ISO string, see the
0001 schema), so it MUST be cast to ``timestamptz`` before any timestamp
arithmetic — the same idiom as ``routers/ui_actions.py``'s
``_OWNER_STALE_SQL`` (#1348 pattern).
"""
from __future__ import annotations

import logging
import os

from db_compat import get_compat_db as _get_compat_db

log = logging.getLogger(__name__)

_DEFAULT_PRESENCE_WINDOW_S = 900


def _presence_window_s() -> int:
    """Freshness window in seconds (env ``ZOE_PRESENCE_WINDOW_S``, default 900).

    Read at call time (not import) so operators/tests can tune the window
    without a module reload. Non-numeric or non-positive values fall back to
    the default rather than erroring a best-effort check.
    """
    raw = os.environ.get("ZOE_PRESENCE_WINDOW_S", "")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return _DEFAULT_PRESENCE_WINDOW_S
    return value if value > 0 else _DEFAULT_PRESENCE_WINDOW_S


async def panel_presence(user_id: str, within_s: int | None = None) -> str | None:
    """Return the panel_id of a panel this user is plausibly near, else None.

    A hit is a ``ui_panel_sessions`` row for ``user_id`` with
    ``is_foreground = 1`` whose ``last_seen_at`` is within ``within_s``
    seconds. When several qualify, the most recently seen panel wins.

    ``within_s`` defaults to ``ZOE_PRESENCE_WINDOW_S`` (900 s) when not
    passed explicitly. A non-positive ``within_s`` falls back the same way:
    0 makes the window zero-width and a negative value inverts the SQL
    arithmetic — both would silently always return ``None``, which is the
    same validation the env path already applies (Greptile, PR #1412).
    """
    if within_s is None or within_s <= 0:
        within_s = _presence_window_s()
    try:
        async with _get_compat_db() as db:
            async with db.execute(
                """SELECT panel_id FROM ui_panel_sessions
                   WHERE user_id = ?
                     AND is_foreground = 1
                     AND last_seen_at::timestamptz
                         >= CURRENT_TIMESTAMP - (?::int * INTERVAL '1 second')
                   ORDER BY last_seen_at::timestamptz DESC
                   LIMIT 1""",
                (user_id, int(within_s)),
            ) as cur:
                row = await cur.fetchone()
        return row["panel_id"] if row else None
    except Exception as exc:
        log.warning(
            "panel_presence(user=%s) failed; treating as absent: %s", user_id, exc
        )
        return None
