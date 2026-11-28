"""
FastAPI router for reminders.
Mounted at prefix="/api/reminders" with tag "reminders".
"""
import json
import logging
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from auth import get_current_user
from database import get_db
from guest_policy import require_feature_access
from models import ReminderCreate, ReminderUpdate, SnoozeBody
from reminder_service import (  # noqa: F401 - the two job helpers are re-exported for older importers
    _cancel_reminder_jobs_safe,
    _reschedule_reminder_due_safe,
    acknowledge_reminder_record,
    create_reminder_record,
    delete_reminder_record,
    row_to_dict,
    snooze_reminder_record,
    stamps_now,
    update_reminder_record,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/reminders", tags=["reminders"])


def _visibility_filter_sql() -> str:
    """SQL fragment: (visibility='family' OR user_id=?) AND deleted=0"""
    return "(visibility = 'family' OR user_id = ?) AND deleted = 0"


@router.post("/", response_model=dict)
async def create_reminder(
    payload: ReminderCreate,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Create a new reminder."""
    stamps_at = stamps_now()      # the Zoe OS reminders door is told who the reminder is for: stamped before anything is awaited
    return await create_reminder_record(payload, user=user, db=db, stamps_at=stamps_at)


@router.get("/", response_model=dict)
async def list_reminders(
    category: Optional[str] = Query(None),
    priority: Optional[str] = Query(None),
    is_active: Optional[bool] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """List reminders with optional filters."""
    await require_feature_access(db, user, feature="reminders", action="read")
    user_id = user["user_id"]
    conditions = [_visibility_filter_sql()]
    params: list = [user_id]

    if category:
        conditions.append("category = ?")
        params.append(category)
    if priority:
        conditions.append("priority = ?")
        params.append(priority)
    if is_active is not None:
        conditions.append("is_active = ?")
        params.append(1 if is_active else 0)

    where = " AND ".join(conditions)
    sql = f"SELECT * FROM reminders WHERE {where} ORDER BY due_date, created_at LIMIT ?"
    params.append(limit)
    cursor = await db.execute(sql, params)
    rows = await cursor.fetchall()
    reminders = [row_to_dict(r) for r in rows]
    return {"reminders": reminders}


@router.get("/today", response_model=dict)
async def list_today_reminders(
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Get today's reminders (active, due today)."""
    await require_feature_access(db, user, feature="reminders", action="read")
    user_id = user["user_id"]
    # "Today" is the HOUSEHOLD day (ZOE_TIMEZONE, the clock the scan fires
    # against), never the server's local date.
    from proactive.triggers import reminder_scan

    today_d = reminder_scan.zoe_now().date()
    today = today_d.isoformat()
    # A recurring reminder's due_date is its ANCHOR (first occurrence), not the
    # day it is due, so rows with a pattern and an anchor on/before today are
    # fetched too and kept only when the rule fires today (reminder_recurrence).
    sql = """
        SELECT * FROM reminders
        WHERE (visibility = 'family' OR user_id = ?) AND deleted = 0
          AND is_active = 1
          AND (due_date = ? OR due_date IS NULL
               OR (recurring_pattern IS NOT NULL AND recurring_pattern != '' AND due_date <= ?))
        ORDER BY due_time, created_at
    """
    cursor = await db.execute(sql, [user_id, today, today])
    rows = await cursor.fetchall()
    reminders = [r for r in (row_to_dict(row) for row in rows) if _due_today(r, today_d)]
    return {"reminders": reminders}


def _due_today(reminder: dict, today: date) -> bool:
    """One-off rows: due_date is today or unset (unchanged). RRULE rows: the
    rule has an occurrence today. Legacy free-text patterns keep the old test."""
    due_date = reminder.get("due_date")
    if not due_date or str(due_date) == today.isoformat():
        return True
    from reminder_recurrence import occurs_on, parse_rrule

    rule = parse_rrule(reminder.get("recurring_pattern") or "")
    if rule is None:
        return False
    try:
        anchor = date.fromisoformat(str(due_date))
    except ValueError:
        return False
    return occurs_on(rule, anchor, today)


@router.put("/{reminder_id}", response_model=dict)
async def update_reminder(
    reminder_id: str,
    payload: ReminderUpdate,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Update an existing reminder (any member may edit a family reminder; only the owner changes visibility)."""
    return await update_reminder_record(db, user, reminder_id, payload.model_dump(exclude_unset=True))


@router.delete("/{reminder_id}", response_model=dict)
async def delete_reminder(
    reminder_id: str,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Soft delete a reminder (deletable family-wide)."""
    return await delete_reminder_record(db, user, reminder_id)


@router.post("/{reminder_id}/snooze", response_model=dict)
async def snooze_reminder(
    reminder_id: str,
    body: SnoozeBody,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Snooze a reminder by N minutes (owner only)."""
    return await snooze_reminder_record(db, user, reminder_id, body.snooze_minutes)


@router.post("/{reminder_id}/acknowledge", response_model=dict)
async def acknowledge_reminder(
    reminder_id: str,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Mark a reminder as acknowledged (owner only; an RRULE row is done for this occurrence)."""
    return await acknowledge_reminder_record(db, user, reminder_id)


@router.get("/notifications/pending", response_model=dict)
async def list_pending_notifications(
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """List pending notifications from notifications table."""
    await require_feature_access(db, user, feature="reminders", action="read")
    user_id = user["user_id"]
    cursor = await db.execute(
        """SELECT * FROM notifications
         WHERE user_id = ? AND delivered = 0
         ORDER BY created_at DESC""",
        [user_id],
    )
    rows = await cursor.fetchall()
    notifications = []
    for r in rows:
        d = dict(r)
        if d.get("data") and isinstance(d["data"], str):
            try:
                d["data"] = json.loads(d["data"]) if d["data"] else None
            except json.JSONDecodeError:
                d["data"] = None
        if "delivered" in d and d["delivered"] is not None:
            d["delivered"] = bool(d["delivered"])
        notifications.append(d)
    return {"notifications": notifications}


@router.post("/notifications/{notification_id}/deliver", response_model=dict)
async def mark_notification_delivered(
    notification_id: str,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Mark a notification as delivered."""
    await require_feature_access(db, user, feature="reminders", action="deliver_notification")
    user_id = user["user_id"]
    cursor = await db.execute(
        "SELECT * FROM notifications WHERE id = ? AND user_id = ?",
        [notification_id, user_id],
    )
    row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Notification not found")

    await db.execute(
        "UPDATE notifications SET delivered = 1 WHERE id = ?",
        [notification_id],
    )
    await db.commit()

    return {"ok": True, "id": notification_id}
