"""nudge_claims - a durable, exact "once per day" claim for a proactive nudge, kept in ``app_settings`` (no migration).

A once-a-day trigger reads the day's ``proactive_pending`` row as a cheap "already sent" check, but two overlapping processes (a
deploy overlap) can both read "no mark" and both deliver. The CLAIM makes it exact: one ``app_settings`` row per (nudge kind,
member, person, household-local day), written with ``INSERT ... ON CONFLICT DO NOTHING`` (``app_settings.key`` is the primary key,
on PostgreSQL and SQLite alike): the process whose insert lands delivers, the other skips. The claim is RELEASED on every path that
did not deliver (``fire_notification`` calls ``release`` exactly where it removes the pending row), so a failed send is retried on
the next tick. A crash between the claim and the send leaves the claim for that day only (the nudge is skipped once, never repeated).

A member erase wins every race with a claim (see ``claim``); a PERSON forgotten (entity forget) while a claim is in flight is not
guarded: the key holds only that person's id and a date and expires in a week, and an entity forget does not erase a person's id
from the other id-only stores either. Keys hold ids and a date, never a name or text: ``nudge:<kind>:<member>:<person>:<YYYY-MM-DD>``. Claims older than ``KEEP_DAYS`` are
deleted when a new one is taken (bounded, no cron). The member erase deletes the member's keys (``forget_user``; registered in
``forget_reach``)."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

PREFIX = "nudge:"
KEEP_DAYS = 7


def _db():
    from db_compat import get_compat_db
    return get_compat_db()


def key_for(kind: str, user_id: str, person_id: str, local_day: str) -> str:
    return f"{PREFIX}{kind}:{user_id}:{person_id}:{local_day}"


def _like_prefix(user_id: str) -> str:
    return (PREFIX + "%:" + str(user_id)).replace("\\", "\\\\").replace("_", "\\_") + ":%"


async def claim(key: str, *, user_id: str, gen: int, started_at: datetime, now: datetime | None = None) -> bool:
    """True when THIS caller took the claim. Raises on a store error (the caller then sends nothing: fail closed).

    A claim whose work BEGAN before the member's erase is refused (forgetting wins every race), two ways, both taken from the work's
    start: ``gen`` is the member's ``user_erase_gate`` generation snapshotted BEFORE the trigger's first read of the member (moved =
    an erase ran since: refused), held under the gate's lock through the insert so ``forget_user`` (which takes the gate as an eraser)
    either runs before it (the generation moved) or after it (and erases the row); ``started_at`` is checked against the DURABLE erase
    mark (``erase_watermark``: ``memory_forgotten``, written first thing in ``delete_user``, so it covers another process and a
    restart) INSIDE the insert, as ONE statement: ``INSERT ... SELECT ... WHERE NOT EXISTS (the member's mark at or after the
    start) ON CONFLICT DO NOTHING``. One statement is atomic on PostgreSQL and on SQLite (no separate read to race, no transaction
    needed although the compat ``commit`` is a no-op on PostgreSQL). The person id in the key is not guarded: see ``forget_user``."""
    import erase_watermark
    import user_erase_gate as erase_gate

    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=KEEP_DAYS)).isoformat()
    async with erase_gate.writing(user_id, gen) as live:
        if not live:
            return False
        async with _db() as db:
            await db.execute("DELETE FROM app_settings WHERE key LIKE ? AND updated_at < ?", (PREFIX + "%", cutoff))
            cur = await db.execute(
                "INSERT INTO app_settings (key, value, updated_at) SELECT CAST(? AS TEXT), 'claimed', CAST(? AS TEXT) "
                "WHERE NOT EXISTS (SELECT 1 FROM memory_forgotten WHERE user_id = ? AND key_hash = ? AND forgotten_at >= ?) "
                "ON CONFLICT (key) DO NOTHING",
                (key, now.isoformat(), user_id, erase_watermark.KEY, erase_watermark._iso(started_at)))
            await db.commit()
    return (getattr(cur, "rowcount", 1) or 0) > 0


async def release(key: str) -> None:
    """Give the claim back (the send did not go through). Best-effort: a failure leaves the claim for the rest of the day."""
    try:
        async with _db() as db:
            await db.execute("DELETE FROM app_settings WHERE key = ?", (key,))
            await db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("nudge claim not released (%s): the nudge is skipped for today", type(exc).__name__)


async def forget_user(user_id: str) -> int:
    """Delete every claim key of the member (ids and a date only). Raises on a DB error; nothing stored = 0."""
    import user_erase_gate as erase_gate

    uid = str(user_id or "").strip()
    if not uid:
        return 0
    try:
        async with erase_gate.erasing(uid):       # an in-flight claim lands before this (and goes with it) or is refused after it
            async with _db() as db:
                cur = await db.execute("DELETE FROM app_settings WHERE key LIKE ? ESCAPE '\\'", (_like_prefix(uid),))
                await db.commit()
    except Exception as exc:  # noqa: BLE001
        from conversation_notes import _no_store       # the one "nothing stored to erase" rule (a lost pool still raises)

        if _no_store(exc):
            return 0
        logger.error("NUDGE_CLAIMS forget failed (%s): rows NOT erased", type(exc).__name__)
        raise
    return int(getattr(cur, "rowcount", 0) or 0)
