"""Who a proactive push is for — one recipient rule shared by the triggers.

The triggers used to pick "anyone who CREATED a chat_sessions row in 7 days".
A session's open time is not activity (a Telegram chat keeps one long-lived
session), a quiet household member dropped out — the morning brief was created
for nobody from 09-11 — and probe ids that wrote to the live DB dropped in.

* ACTIVE = owners of a user turn in ``chat_messages`` in the last 7 days
  (per-turn ``metadata.user_id`` first, then the session owner).
* HOUSEHOLD (``include_panel_members=True``) = ACTIVE ∪ users named by a panel's
  ``default`` binding in ``panel_user_bindings``.
* Both minus guest sentinels and synthetic ids (``user_filters``).

Two separate queries, so a failed arm still leaves the other.
"""
from __future__ import annotations

import logging

from user_filters import drop_synthetic_users, message_owner_expr

log = logging.getLogger(__name__)

_ACTIVE_WINDOW_DAYS = 7


def _active_users_sql() -> str:
    return f"""
        SELECT a.user_id, u.name
        FROM (
            SELECT DISTINCT ({message_owner_expr()}) AS user_id
            FROM chat_messages cm
            JOIN chat_sessions cs ON cm.session_id = cs.id
            WHERE cm.role = 'user'
              AND cm.created_at::timestamptz
                  > (CURRENT_TIMESTAMP - INTERVAL '{_ACTIVE_WINDOW_DAYS} days')
        ) a
        LEFT JOIN users u ON u.id = a.user_id
        WHERE a.user_id IS NOT NULL
    """


_PANEL_MEMBERS_SQL = """
    SELECT DISTINCT b.user_id, u.name
    FROM panel_user_bindings b
    LEFT JOIN users u ON u.id = b.user_id
    WHERE b.binding_type = 'default'
"""


async def _fetch_pairs(db, sql: str) -> list[tuple[str, str]]:
    async with db.execute(sql) as cur:
        return [(row[0], row[1] or "") async for row in cur if row[0]]


async def proactive_recipients(
    db, *, pass_name: str, include_panel_members: bool = True,
) -> list[tuple[str, str]]:
    """Return ``[(user_id, display_name), ...]`` for a proactive pass.

    Panel members come first, then active users, de-duplicated by id. Never
    raises: an arm that fails is logged and contributes nobody.
    """
    arms: list[tuple[str, str]] = []
    if include_panel_members:
        arms.append(("panel members", _PANEL_MEMBERS_SQL))
    arms.append(("active users", _active_users_sql()))

    names: dict[str, str] = {}
    for label, sql in arms:
        try:
            pairs = await _fetch_pairs(db, sql)
        except Exception as exc:
            log.warning("%s: could not list %s: %s", pass_name, label, exc)
            continue
        for uid, name in pairs:
            if uid not in names or (name and not names[uid]):
                names[uid] = name
    kept = drop_synthetic_users(names, pass_name=pass_name, log=log)
    return [(uid, names[uid]) for uid in kept]
