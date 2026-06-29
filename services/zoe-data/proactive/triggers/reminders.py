"""
Tier 1 trigger: calendar/reminder-based — fires via APScheduler.

Integrates with the existing `events` table (category='reminder') and the
new `proactive_scheduled` table for agent-created one-shot nudges.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone


from db_compat import get_compat_db as _get_compat_db
from proactive.scheduler import register_job, cancel_job, CancelResult

log = logging.getLogger(__name__)

# A reminder claim that has been held longer than this (because the claiming
# process crashed mid-delivery) becomes reclaimable so the reminder isn't lost.
# Shared by the in-job claim and engine.reconcile_scheduled_jobs.
_STUCK_CLAIM_SECONDS = int(os.environ.get("ZOE_REMINDER_STUCK_CLAIM_S", "600"))


def _utc_iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


async def _claim_scheduled_for_fire(scheduled_id: str):
    """Atomically claim an unfired reminder for delivery.

    This is the single gate that makes a reminder fire EXACTLY once across the
    normal scheduled job, the missed-job listener catch-up, and startup
    reconcile: every path runs _fire_reminder, and only the caller whose
    conditional UPDATE matches one row (rowcount==1) delivers. A claim older than
    the stuck timeout is reclaimable so a crash mid-delivery eventually recovers.

    Returns the claimed row (with item_id) if THIS caller won, else None.
    """
    now = datetime.now(timezone.utc)
    stuck_cutoff = _utc_iso(now - timedelta(seconds=_STUCK_CLAIM_SECONDS))
    async with _get_compat_db() as db:
        async with db.execute(
            "UPDATE proactive_scheduled SET claimed_at = ? "
            "WHERE id = ? AND fired = 0 AND (claimed_at IS NULL OR claimed_at < ?) "
            "RETURNING id, item_id",
            (_utc_iso(now), scheduled_id, stuck_cutoff),
        ) as cur:
            row = await cur.fetchone()
        await db.commit()
    return row


async def _reminder_obligation_void(item_id: str) -> bool:
    """True if the backing reminder no longer warrants firing.

    Closes the router commit→cancel window for delete/acknowledge/deactivate: an
    in-flight old job re-reads the reminder's CURRENT state at delivery time and
    aborts if it was deleted / acknowledged / deactivated since scheduling.
    """
    try:
        async with _get_compat_db() as db:
            async with db.execute(
                "SELECT is_active, acknowledged, deleted FROM reminders WHERE id = ?",
                (item_id,),
            ) as cur:
                r = await cur.fetchone()
    except Exception:
        # Don't drop a reminder on a transient read error — let it deliver.
        return False
    if r is None:
        return True  # reminder was deleted/purged
    return (not r["is_active"]) or bool(r["acknowledged"]) or bool(r["deleted"])


async def _consume_scheduled(scheduled_id: str) -> None:
    """Mark a scheduled row fired WITHOUT delivering (its obligation is void)."""
    try:
        async with _get_compat_db() as db:
            await db.execute(
                "UPDATE proactive_scheduled SET fired = 1 WHERE id = ?", (scheduled_id,)
            )
            await db.commit()
    except Exception:
        log.error("could not consume scheduled %s", scheduled_id)


async def _record_fire_failure_and_release(scheduled_id: str, exc: Exception) -> None:
    """On delivery failure: bump attempts, store last_error, and RELEASE the
    claim (claimed_at=NULL) so reconcile/listener can re-claim immediately. The
    attempts cap (engine._MAX_FIRE_ATTEMPTS) still bounds the retries."""
    try:
        async with _get_compat_db() as db:
            await db.execute(
                "UPDATE proactive_scheduled "
                "SET attempts = COALESCE(attempts, 0) + 1, last_error = ?, claimed_at = NULL "
                "WHERE id = ?",
                (str(exc)[:500], scheduled_id),
            )
            await db.commit()
    except Exception:
        log.error("could not record fire failure for %s", scheduled_id)


async def _fire_reminder(pending_id: str, user_id: str, message: str) -> None:
    """
    Called by APScheduler at the scheduled time (and by listener/reconcile-
    registered catch-up jobs). Imports engine here to avoid circular imports.

    Exactly-once delivery is enforced by an atomic claim BEFORE any side effect:
      1. Claim the row (fired=0 → claimed_at). Lose the claim → another path is
         already delivering this reminder; return without firing.
      2. Re-read the reminder's current state; if the obligation is void
         (deleted / acknowledged / deactivated) consume the row and don't deliver
         (closes the router commit→cancel window).
      3. Deliver; fire_notification marks fired=1.
      4. On failure: record attempts/last_error, release the claim, and RE-RAISE
         so APScheduler emits EVENT_JOB_ERROR and reconcile can retry.
    """
    from proactive.engine import fire_notification  # deferred import

    claim = await _claim_scheduled_for_fire(pending_id)
    if claim is None:
        log.info("reminder %s already claimed/fired elsewhere — skipping duplicate", pending_id)
        return

    item_id = claim["item_id"] or ""
    if item_id and await _reminder_obligation_void(item_id):
        log.info("reminder %s obligation void (deleted/ack/inactive) — not delivering", pending_id)
        await _consume_scheduled(pending_id)
        return

    try:
        await fire_notification(
            user_id=user_id,
            message=message,
            trigger_type="reminder",
            pending_id=pending_id,
        )
    except Exception as exc:
        log.error("_fire_reminder failed for pending %s: %s", pending_id, exc)
        await _record_fire_failure_and_release(pending_id, exc)
        raise


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


async def cancel_reminder_jobs(reminder_id: str) -> int:
    """Cancel EVERY unfired scheduled reminder + APScheduler job for a reminder.

    Keyed on proactive_scheduled.item_id == reminder_id. Call this whenever a
    reminder's due-time or state changes (update / snooze / acknowledge / delete)
    so a job scheduled for the OLD due-time can never fire against the mutated
    row. Returns the number of scheduled rows acted on.

    Airtight on partial failure: if APScheduler removal raises for a row, we
    still CONSUME the row (mark fired=1) so any surviving orphan job's atomic
    claim finds nothing to deliver — the stale old-time fire can't slip through.
    """
    async with _get_compat_db() as db:
        async with db.execute(
            "SELECT id FROM proactive_scheduled WHERE item_id = ? AND fired = 0",
            (reminder_id,),
        ) as cur:
            rows = await cur.fetchall()

    count = 0
    for r in rows:
        try:
            await cancel_reminder(r["id"])
            count += 1
        except Exception:
            log.exception("cancel_reminder_jobs: failed to cancel scheduled %s; "
                          "neutralizing row so the orphan job can't fire", r["id"])
            await _consume_scheduled(r["id"])
            count += 1
    return count
