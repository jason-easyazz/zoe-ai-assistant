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


TIER_OWNER = "owner"              # the member's own fresh panel session: full brief
TIER_BOUND_GUEST = "bound_guest"  # guest-held panel whose default member is the user
TIER_ABSENT = "absent"


async def panel_presence_tier(
    user_id: str, within_s: int | None = None,
) -> tuple[str, str | None]:
    """Return ``(tier, panel_id)`` for how plausibly ``user_id`` is at a panel.

    Only fresh (``within_s``) ``is_foreground = 1`` ``ui_panel_sessions`` rows count.

    * ``owner`` — a row owned by the user. A member row is written only by a
      member sign-in / PIN verification and kept fresh by that member's own
      turns (``_touch_panel_session`` refreshes a still-fresh session, never a
      lapsed one), so it is the server's identity-confirmed state.
    * ``bound_guest`` — a row owned by the kiosk ``guest`` on a panel whose
      ``default`` ``panel_user_bindings`` row names the user. The kiosk reclaims
      the row as ``guest`` 300 s after the owner goes quiet, so this is "the
      member's panel is on", NOT "the member is there": callers must speak only
      a non-sensitive line on this tier.
    * ``absent`` — otherwise, and on any error.

    An ``owner`` row beats a ``bound_guest`` one; within a tier the freshest
    panel wins. ``within_s`` defaults to ``ZOE_PRESENCE_WINDOW_S`` (900 s); a
    non-positive value falls back the same way (Greptile, PR #1412).
    """
    if within_s is None or within_s <= 0:
        within_s = _presence_window_s()
    try:
        async with _get_compat_db() as db:
            async with db.execute(
                """SELECT s.panel_id, s.user_id FROM ui_panel_sessions s
                   LEFT JOIN panel_user_bindings b
                     ON b.panel_id = s.panel_id AND b.binding_type = 'default'
                   WHERE (s.user_id = ? OR (s.user_id = 'guest' AND b.user_id = ?))
                     AND s.is_foreground = 1
                     AND s.last_seen_at::timestamptz
                         >= CURRENT_TIMESTAMP - (?::int * INTERVAL '1 second')
                   ORDER BY s.last_seen_at::timestamptz DESC""",
                (user_id, user_id, int(within_s)),
            ) as cur:
                rows = await cur.fetchall()
        for row in rows:
            if row["user_id"] == user_id:
                return TIER_OWNER, row["panel_id"]
        if rows:
            return TIER_BOUND_GUEST, rows[0]["panel_id"]
        return TIER_ABSENT, None
    except Exception as exc:
        log.warning(
            "panel_presence(user=%s) failed; treating as absent: %s", user_id, exc
        )
        return TIER_ABSENT, None


async def panel_presence(user_id: str, within_s: int | None = None) -> str | None:
    """The panel_id where ``user_id`` is present as the OWNER, else None."""
    tier, panel_id = await panel_presence_tier(user_id, within_s)
    return panel_id if tier == TIER_OWNER else None
