"""calendar_forget - the member erase reaches the calendar store (gap audit 2026-11-13, lane D X2; migrations 0052 / 0078).

``MemoryService.delete_user`` calls ``forget_user``. It hard-deletes, under the member's erase gate:
  * every calendar the member owns (``calendars``: the name, description, sync cursor and account reference),
  * the member's OWN events in those calendars, and every SYNCED event of the member (an ``external_uid`` or a non-local ``source``),
  * the attendees and alarms of those events.

ANOTHER member's event that sits in a calendar the forgotten member owns is never deleted: it is DETACHED (``calendar_id`` set to
NULL, so it stays that member's own event with its attendees and alarms) before the calendar row goes.

The member's LOCAL events outside a calendar (``calendar_id`` NULL, ``source`` local) are not touched: they default to household
visibility, and whether a forgotten member's household-visible items are deleted or kept for the household is an owner decision
(ledgered as the ``events`` / ``event_attendees`` gaps in ``forget_reach.py``).
"""
from __future__ import annotations

import logging

import user_erase_gate as erase_gate
from connected_accounts import _no_store

logger = logging.getLogger(__name__)

# the forgotten member's OWN rows only: an event is erased because it is THEIRS, never because of whose calendar it sits in
_EVENTS = ("SELECT id FROM events WHERE user_id = ? AND (calendar_id IN (SELECT id FROM calendars WHERE user_id = ?) "
           "OR external_uid IS NOT NULL OR source <> 'local')")
_DETACH = ("UPDATE events SET calendar_id = NULL WHERE user_id <> ? "
           "AND calendar_id IN (SELECT id FROM calendars WHERE user_id = ?)")


async def forget_user(user_id: str) -> int:
    """Rows removed. Raises on any failure but "nothing stored" (a database without 0052, or no pool ever): the caller fails closed."""
    uid = str(user_id or "").strip()
    if not uid:
        return 0
    n = 0
    try:
        async with erase_gate.erasing(uid):
            from db_compat import get_compat_db
            async with get_compat_db() as db:
                async with db.transaction():
                    for sql in (f"DELETE FROM event_alarms WHERE event_id IN ({_EVENTS})",
                                f"DELETE FROM event_attendees WHERE event_id IN ({_EVENTS})",
                                f"DELETE FROM events WHERE id IN ({_EVENTS})"):
                        cur = await db.execute(sql, (uid, uid))
                        n += int(getattr(cur, "rowcount", 0) or 0)
                    await db.execute(_DETACH, (uid, uid))          # someone else's event in my calendar: kept, detached
                    cur = await db.execute("DELETE FROM calendars WHERE user_id = ?", (uid,))
                    n += int(getattr(cur, "rowcount", 0) or 0)
    except Exception as exc:  # noqa: BLE001
        if _no_store(exc):
            return 0
        logger.error("CALENDAR forget failed (%s): rows NOT erased", type(exc).__name__)
        raise
    logger.info("CALENDAR forget rows=%d", n)
    return n
