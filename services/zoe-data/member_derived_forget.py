"""member_derived_forget - the member erase reaches the stores Zoe DERIVES from a member's words (forget reach, 2026-11-15).

``MemoryService.delete_user`` erased the palace and every store with its own eraser, but not these derived tables, so a member's
portrait, user-model card, open loops, proactive candidates / deliveries / spoken lines and pending offers outlived the erase
(``forget_reach`` gaps). Every one is written by Zoe ABOUT the member (from their turns, their rows or their calendar) and none is
something the member authored or the household shares, so a member erase deletes all of their rows.

Not here, on purpose (owner decision, ``docs/knowledge/open-problems.md``): what the member AUTHORED or the household SHARES - notes,
journal, lists, reminders (and the reminder sends queued from them: ``proactive_pending`` / ``proactive_scheduled``), people,
relationships, local events, the chat transcript.

Fail closed like the other erasers: a store that cannot be cleared RAISES (the caller retries the whole delete). A table that is not
migrated, or a process that never had a database pool (a lab / bare unit run), holds nothing to clear. Logs counts only.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

#: children before parents (a delivery / spoken line points at a candidate; a candidate at a loop)
TABLES = ("proactive_deliveries", "proactive_ledger_lines", "proactive_candidates", "pending_suggestions", "open_loops",
          "user_model_cards", "user_portraits")


def _missing_table(exc: BaseException) -> bool:
    s = str(exc)
    return "no such table" in s or ("does not exist" in s and "relation" in s)


def _no_pool(exc: BaseException) -> bool:
    if isinstance(exc, RuntimeError) and re.match(r"(?:db[_ ])?pool not initialised", str(exc)):
        from db_pool import pool_ever_initialised

        return not pool_ever_initialised()
    return False


def _db():
    from db_compat import get_compat_db
    return get_compat_db()


async def forget_user(user_id: str) -> int:
    """Delete every row of ``user_id`` in ``TABLES``. Returns rows removed. RAISES on any failure but a missing table / no pool."""
    removed = 0
    try:
        async with _db() as db:
            for table in TABLES:
                try:
                    cur = await db.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
                except Exception as exc:  # noqa: BLE001
                    if _missing_table(exc):
                        continue
                    raise
                removed += int(getattr(cur, "rowcount", 0) or 0)
            await db.commit()
    except Exception as exc:  # noqa: BLE001
        if _no_pool(exc):
            return 0
        logger.error("member_derived_forget: derived stores NOT erased for a forgotten member (%s)", type(exc).__name__)
        raise
    if removed:
        logger.info("member_derived_forget: removed %d derived row(s)", removed)
    return removed
