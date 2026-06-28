"""
Tier 1 trigger: calendar/reminder-based — fires via APScheduler.

Integrates with the existing `events` table (category='reminder') and the
new `proactive_scheduled` table for agent-created one-shot nudges.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone


from db_compat import get_compat_db as _get_compat_db
from proactive.scheduler import register_job, cancel_job, CancelResult

log = logging.getLogger(__name__)


async def _fire_reminder(pending_id: str, user_id: str, message: str) -> None:
    """
    Called by APScheduler at the scheduled time.
    Imports engine here to avoid circular imports at module load.
    """
    from proactive.engine import fire_notification  # deferred import
    try:
        await fire_notification(
            user_id=user_id,
            message=message,
            trigger_type="reminder",
            pending_id=pending_id,
        )
    except Exception as exc:
        log.error("_fire_reminder failed for pending %s: %s", pending_id, exc)


async def schedule_reminder(
    user_id: str,
    message: str,
    send_at: datetime,
    item_id: str = "",
) -> str:
    """
    Insert a proactive_scheduled row and register an APScheduler job.
    Returns the proactive_scheduled.id.
    """
    row_id = str(uuid.uuid4())
    job_id = f"reminder-{row_id}"

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    send_str = send_at.strftime("%Y-%m-%dT%H:%M:%SZ")

    async with _get_compat_db() as db:
        await db.execute(
            """INSERT INTO proactive_scheduled
               (id, user_id, message, trigger_type, send_at, apscheduler_job_id, fired, item_id)
               VALUES (?, ?, ?, 'reminder', ?, ?, 0, ?)""",
            (row_id, user_id, message, send_str, job_id, item_id),
        )
        await db.commit()

    # Register the scheduler job AFTER the row exists. If registration fails the
    # row would otherwise linger as a "scheduled" reminder that can never fire,
    # so compensate by deleting the orphan row before re-raising.
    try:
        register_job(
            func=_fire_reminder,
            run_at=send_at,
            job_id=job_id,
            kwargs={"pending_id": row_id, "user_id": user_id, "message": message},
        )
    except Exception:
        log.exception(
            "schedule_reminder: job registration failed for %s; removing orphan row", row_id
        )
        try:
            async with _get_compat_db() as db:
                await db.execute(
                    "DELETE FROM proactive_scheduled WHERE id = ?", (row_id,)
                )
                await db.commit()
        except Exception:
            log.error("schedule_reminder: failed to remove orphan row %s", row_id)
        raise
    log.info("Scheduled reminder %s for user %s at %s", row_id, user_id, send_str)
    return row_id


async def cancel_reminder(scheduled_id: str) -> bool:
    """Cancel a scheduled reminder by its proactive_scheduled.id.

    Cancels the APScheduler job FIRST, then deletes the DB row only once the job
    is CONFIRMED gone — either removed now (REMOVED) or already absent (ABSENT).
    Deleting the row before cancelling (the old order) could leave an orphan job
    that still fires against a row that no longer exists.

    A real cancel failure (transient jobstore error) propagates out of
    cancel_job: we do NOT delete the row, so the reminder is not silently
    orphaned and the error surfaces to the caller. Returns True only when a live
    job was actually removed; False when the job was already absent.
    """
    async with _get_compat_db() as db:
        async with db.execute(
            "SELECT apscheduler_job_id FROM proactive_scheduled WHERE id = ?",
            (scheduled_id,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return False
    job_id = row["apscheduler_job_id"]

    # May raise on a real scheduler failure — intentionally NOT caught, so the
    # DB row below is reached only after a confirmed REMOVED/ABSENT outcome.
    result = cancel_job(job_id)

    async with _get_compat_db() as db:
        await db.execute(
            "DELETE FROM proactive_scheduled WHERE id = ?", (scheduled_id,)
        )
        await db.commit()

    if result is CancelResult.ABSENT:
        log.warning(
            "cancel_reminder: scheduler job %s was already absent; removed stale row %s",
            job_id, scheduled_id,
        )
    return result is CancelResult.REMOVED


async def reschedule_reminder(scheduled_id: str, new_send_at: datetime) -> bool:
    """Snooze / reschedule a reminder. Returns True on success."""
    # Read BEFORE cancelling (cancel deletes the DB row).
    async with _get_compat_db() as db:
        async with db.execute(
            "SELECT user_id, message, item_id FROM proactive_scheduled WHERE id = ?",
            (scheduled_id,),
        ) as cur:
            row = await cur.fetchone()

    if row is None:
        log.warning("reschedule_reminder: %s not found", scheduled_id)
        return False

    await cancel_reminder(scheduled_id)
    await schedule_reminder(
        user_id=row["user_id"],
        message=row["message"],
        send_at=new_send_at,
        item_id=row["item_id"] or "",
    )
    return True
