"""member_owned_forget - a member delete removes EVERYTHING of the member (owner decision 2026-11-15: "deleting a member should delete
everything").

``MemoryService.delete_user`` already erased the palace, the derived stores (``member_derived_forget``) and every store with its own
eraser. This module erases the rest: the transcript, what the member wrote (notes, journal, reminders, transactions), their queued and
scheduled sends, their ambient recordings, their own contacts and their entries on shared ones, their personal events, and the
smaller per-member tables. The per-store rule, in plain words, is in docs/knowledge/forget-reach.md ("What a member delete removes").

THE SHARED-OBJECT RULE. A row the member wrote alone (a note, a reminder, a transaction, a journal entry) goes, whatever its
visibility. A household-SHARED container (a family list, a family event, a family-visible contact) is NOT deleted: only the entries
inside it that record the member as their author go, and the member's own rows elsewhere. Where a row does not record its author
(``list_items``, ``people_field_values``, the AG-UI runs of a session someone else also spoke in) nothing is guessed: it stays, and
forget-reach lists it as an authorship gap with the column that would be needed.

Fail closed like the other erasers: a store that cannot be cleared RAISES (the caller retries the whole delete); a table that is not
migrated, or a process that never had a database pool, holds nothing. Scheduled sends are consumed BEFORE their rows go (a job whose row
is gone claims nothing and never fires) and their scheduler jobs cancelled where this process runs the scheduler. Takes no erase gate:
it runs before the memory lock, like ``member_derived_forget``. Logs counts only.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

#: single-author tables: every row whose ``user_id`` is the member goes (children before parents where they reference each other)
OWNED = ("chat_feedback", "background_tasks", "ui_actions", "notes", "journal_entries", "reminders", "transactions",
         "proactive_pending", "voice_announcements", "openclaw_approvals", "openclaw_run_state", "memory_consolidation_state",
         "person_activities", "person_bucket_list", "person_gift_ideas", "person_important_dates", "person_relationships",
         "event_attendees")
_CHUNK = 200
_GUESTS = ("guest", "kiosk", "unknown", "")


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


class _Run:
    def __init__(self, db):
        self.db, self.removed = db, 0

    async def rows(self, sql: str, params: tuple) -> list:
        try:
            cur = await self.db.execute(sql, params)
            return list(await cur.fetchall())
        except Exception as exc:  # noqa: BLE001
            if _missing_table(exc):
                return []
            raise

    async def run(self, sql: str, params: tuple) -> int:
        try:
            cur = await self.db.execute(sql, params)
        except Exception as exc:  # noqa: BLE001
            if _missing_table(exc):
                return 0
            raise
        n = int(getattr(cur, "rowcount", 0) or 0)
        self.removed += max(n, 0)
        return n

    async def by_ids(self, sql_prefix: str, ids: list) -> None:
        """``DELETE ... WHERE <col> IN (ids)`` in chunks (``sql_prefix`` ends with the column name)."""
        for at in range(0, len(ids), _CHUNK):
            part = ids[at:at + _CHUNK]
            await self.run(f"{sql_prefix} IN ({', '.join('?' * len(part))})", tuple(part))


def _turn_owner(metadata: Any, session_owner: Any) -> str:
    """The owner of one chat turn: the per-turn ``metadata.user_id`` when it names one, else the session owner (the ownership rule of
    ``user_filters.message_owner_expr``, in Python so it is the same on every database)."""
    meta = metadata
    if isinstance(meta, str):
        try:
            meta = json.loads(meta) if meta.strip().startswith("{") else {}
        except ValueError:
            meta = {}
    uid = str((meta or {}).get("user_id") or "").strip() if isinstance(meta, dict) else ""
    return uid or str(session_owner or "").strip()


async def _scheduled_sends(r: _Run, user_id: str) -> None:
    """The member's scheduled reminder sends: consumed first (a job that fires finds no claimable row), jobs cancelled where this
    process runs the scheduler, then the rows deleted."""
    jobs = [str(x[0]) for x in await r.rows("SELECT apscheduler_job_id FROM proactive_scheduled WHERE user_id = ?", (user_id,)) if x[0]]
    await r.run("UPDATE proactive_scheduled SET fired = 1 WHERE user_id = ?", (user_id,))
    await r.db.commit()
    for job in jobs:
        try:
            from proactive.scheduler import cancel_job
            cancel_job(job)
        except Exception:  # noqa: BLE001 - no scheduler here (another process / not started): the consumed row cannot fire
            pass
    await r.run("DELETE FROM proactive_scheduled WHERE user_id = ?", (user_id,))


async def _transcript(r: _Run, user_id: str) -> None:
    """The member's chat turns (any session, by the per-turn owner rule); then the sessions they own that are now empty (with their
    AG-UI runs); a session they own that still holds someone else's turns keeps those turns and loses its title (the member's words)."""
    like = [f'%"user_id": "{user_id}"%', f'%"user_id":"{user_id}"%']
    rows = await r.rows(
        "SELECT cm.id, cm.session_id, cm.metadata, cs.user_id FROM chat_messages cm LEFT JOIN chat_sessions cs ON cs.id = cm.session_id "
        "WHERE cs.user_id = ? OR cm.metadata LIKE ? OR cm.metadata LIKE ?", (user_id, *like))
    mine = [str(x[0]) for x in rows if _turn_owner(x[2], x[3]) == user_id]
    touched = sorted({str(x[1]) for x in rows if _turn_owner(x[2], x[3]) == user_id and x[1]})
    await r.by_ids("DELETE FROM chat_messages WHERE id", mine)
    owned = [str(x[0]) for x in await r.rows("SELECT id FROM chat_sessions WHERE user_id = ?", (user_id,))]
    candidates = sorted(set(owned) | set(touched))
    empty, shared = [], []
    for at in range(0, len(candidates), _CHUNK):
        part = candidates[at:at + _CHUNK]
        marks = ", ".join("?" * len(part))
        left = {str(x[0]) for x in await r.rows(f"SELECT DISTINCT session_id FROM chat_messages WHERE session_id IN ({marks})", tuple(part))}
        for sid in part:
            (shared if sid in left else empty).append(sid)
    # an emptied session goes when the member owns it or nobody does (a guest / kiosk session holding only their turns)
    sess_owner = {}
    for at in range(0, len(empty), _CHUNK):
        part = empty[at:at + _CHUNK]
        for x in await r.rows(f"SELECT id, user_id FROM chat_sessions WHERE id IN ({', '.join('?' * len(part))})", tuple(part)):
            sess_owner[str(x[0])] = str(x[1] or "")
    gone = [s for s in empty if sess_owner.get(s, "") in (user_id, *_GUESTS) or s not in sess_owner]
    await r.by_ids("DELETE FROM chat_ag_ui_runs WHERE session_id", gone)
    await r.by_ids("DELETE FROM chat_sessions WHERE id", gone)
    kept_owned = [s for s in shared if s in set(owned)]
    for at in range(0, len(kept_owned), _CHUNK):
        part = kept_owned[at:at + _CHUNK]
        await r.run(f"UPDATE chat_sessions SET title = 'New Chat' WHERE id IN ({', '.join('?' * len(part))})", tuple(part))


async def _lists(r: _Run, user_id: str) -> None:
    """The member's PRIVATE lists and their items go; a family list keeps its items (they record no author); the member's
    assignments on any item are cleared."""
    mine = [str(x[0]) for x in await r.rows("SELECT id FROM lists WHERE user_id = ? AND COALESCE(visibility, 'family') <> 'family'",
                                            (user_id,))]
    await r.by_ids("DELETE FROM list_items WHERE list_id", mine)
    await r.by_ids("DELETE FROM lists WHERE id", mine)
    await r.run("UPDATE list_items SET assigned_to = NULL WHERE assigned_to = ?", (user_id,))


async def _people(r: _Run, user_id: str) -> None:
    """The member's PRIVATE contacts go with every row about them; a family-visible contact stays (other members know them) and
    loses only the member's own entries (the ``OWNED`` person_* rows and relationships, by ``user_id``)."""
    mine = [str(x[0]) for x in await r.rows("SELECT id FROM people WHERE user_id = ? AND COALESCE(visibility, 'family') <> 'family'",
                                            (user_id,))]
    for t in ("people_field_values", "person_activities", "person_bucket_list", "person_gift_ideas", "person_important_dates"):
        await r.by_ids(f"DELETE FROM {t} WHERE person_id", mine)
    await r.by_ids("DELETE FROM person_relationships WHERE person_a_id", mine)
    await r.by_ids("DELETE FROM person_relationships WHERE person_b_id", mine)
    await r.by_ids("DELETE FROM people WHERE id", mine)


async def _events(r: _Run, user_id: str) -> None:
    """The member's PERSONAL local events go with their attendee rows; a family event stays (a household calendar entry)."""
    mine = [str(x[0]) for x in await r.rows("SELECT id FROM events WHERE user_id = ? AND COALESCE(visibility, 'family') <> 'family'",
                                            (user_id,))]
    await r.by_ids("DELETE FROM event_attendees WHERE event_id", mine)
    await r.by_ids("DELETE FROM events WHERE id", mine)


async def _ambient(r: _Run, user_id: str) -> None:
    """Ambient recordings: rows scoped to the member (``user_id``, every row since migration 0017), and legacy rows whose
    ``speaker_id`` is one of the member's speaker profiles."""
    await r.run("DELETE FROM ambient_memory WHERE user_id = ?", (user_id,))
    profiles = [str(x[0]) for x in await r.rows("SELECT id FROM speaker_profiles WHERE user_id = ?", (user_id,))]
    await r.by_ids("DELETE FROM ambient_memory WHERE speaker_id", profiles)


async def _panel_voices(r: _Run, user_id: str) -> None:
    clusters = [str(x[0]) for x in await r.rows("SELECT id FROM panel_voice_clusters WHERE user_id = ?", (user_id,))]
    await r.by_ids("DELETE FROM panel_voice_samples WHERE cluster_id", clusters)
    await r.by_ids("DELETE FROM panel_voice_clusters WHERE id", clusters)


async def forget_user(user_id: str) -> int:
    """Erase every member-owned row the other erasers do not reach. Returns rows changed. RAISES on any failure but a missing table /
    no pool."""
    uid = str(user_id or "").strip()
    if not uid:
        return 0
    try:
        async with _db() as db:
            r = _Run(db)
            await _scheduled_sends(r, uid)
            await _transcript(r, uid)
            await _lists(r, uid)
            await _people(r, uid)
            await _events(r, uid)
            await _ambient(r, uid)
            await _panel_voices(r, uid)
            for table in OWNED:
                await r.run(f"DELETE FROM {table} WHERE user_id = ?", (uid,))
            await db.commit()
    except Exception as exc:  # noqa: BLE001
        if _no_pool(exc):
            return 0
        logger.error("member_owned_forget: member-owned stores NOT erased (%s)", type(exc).__name__)
        raise
    if r.removed:
        logger.info("member_owned_forget: removed or cleared %d row(s)", r.removed)
    return r.removed
