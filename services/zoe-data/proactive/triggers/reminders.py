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

import aiosqlite

from database import DB_PATH
from proactive.scheduler import register_job, cancel_job

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

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO proactive_scheduled
               (id, user_id, message, trigger_type, send_at, apscheduler_job_id, fired, item_id)
               VALUES (?, ?, ?, 'reminder', ?, ?, 0, ?)""",
            (row_id, user_id, message, send_str, job_id, item_id),
        )
        await db.commit()

    register_job(
        func=_fire_reminder,
        run_at=send_at,
        job_id=job_id,
        kwargs={"pending_id": row_id, "user_id": user_id, "message": message},
    )
    log.info("Scheduled reminder %s for user %s at %s", row_id, user_id, send_str)
    return row_id


async def cancel_reminder(scheduled_id: str) -> bool:
    """Cancel a scheduled reminder by its proactive_scheduled.id."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT apscheduler_job_id FROM proactive_scheduled WHERE id = ?",
            (scheduled_id,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return False
        job_id = row["apscheduler_job_id"]
        await db.execute(
            "DELETE FROM proactive_scheduled WHERE id = ?", (scheduled_id,)
        )
        await db.commit()

    return cancel_job(job_id)


async def reschedule_reminder(scheduled_id: str, new_send_at: datetime) -> bool:
    """Snooze / reschedule a reminder. Returns True on success."""
    # Read BEFORE cancelling (cancel deletes the DB row).
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
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
