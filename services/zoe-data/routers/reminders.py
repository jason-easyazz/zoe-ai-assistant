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
from push import broadcaster
from reminder_service import (
    _cancel_reminder_jobs_safe,
    _create_notification,
    _reschedule_reminder_due_safe,
    create_reminder_record,
    delete_reminder_record,
    normalize_due_date,
    normalize_due_time,
    row_to_dict,
    snooze_reminder_record,
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
    return await create_reminder_record(payload, user=user, db=db)


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


def _is_rrule(pattern) -> bool:
    from reminder_recurrence import parse_rrule

    return bool(pattern) and parse_rrule(str(pattern)) is not None


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
    """Update an existing reminder."""
    await require_feature_access(db, user, feature="reminders", action="update")
    user_id = user["user_id"]
    where = f"{_visibility_filter_sql()} AND id = ?"
    cursor = await db.execute(
        "SELECT * FROM reminders WHERE " + where,
        [user_id, reminder_id],
    )
    row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Reminder not found")
    # Household model: family-visible reminders are editable by any signed-in
    # member (mirrors calendar #1303 / people) — the visibility filter above
    # already scopes the lookup to family-or-own.
    # …but only the OWNER may change visibility: a non-owner flipping a family
    # reminder to 'personal' would hijack it out of the household's (and the
    # owner's) view entirely (Greptile P1).
    is_owner = dict(row).get("user_id") == user_id

    updates = []
    params = []
    data = payload.model_dump(exclude_unset=True)
    if not is_owner:
        data.pop("visibility", None)
    existing = dict(row)
    if "due_date" in data and _is_rrule(existing.get("recurring_pattern")):
        # A recurring row's due_date is the rule's ANCHOR, not a due day.
        if data["due_date"] is not None and str(data["due_date"]) == str(existing.get("due_date")):
            # An edit form re-sending the stored (usually past) anchor is not a
            # date change: keep the anchor and the rule's phase, never 422 it.
            data.pop("due_date")
        else:
            # Re-anchor to the first occurrence on/after the new date (today when
            # cleared). A NULL anchor would make the scan re-anchor to "today"
            # every cycle, so a monthly or every-3-days rule would fire DAILY.
            from reminder_service import normalize_recurrence_fields

            due_time = normalize_due_time(data["due_time"]) if "due_time" in data else existing.get("due_time")
            _, data["due_date"] = normalize_recurrence_fields(
                existing["recurring_pattern"], normalize_due_date(data["due_date"]), due_time
            )
    for key, value in data.items():
        if key == "is_active":
            updates.append("is_active = ?")
            params.append(1 if value else 0)
        elif key == "due_date":
            updates.append("due_date = ?")
            params.append(normalize_due_date(value))
        elif key == "due_time":
            updates.append("due_time = ?")
            params.append(normalize_due_time(value))
        else:
            updates.append(f"{key} = ?")
            params.append(value)

    if not updates:
        return row_to_dict(row)

    updates.append("updated_at = NOW()")
    # Bump the schedule generation so any old-time job that already won its claim
    # self-voids at fire time instead of delivering at the stale time.
    updates.append("schedule_generation = COALESCE(schedule_generation, 0) + 1")
    params.append(reminder_id)

    # B2: cancel the OLD-time job BEFORE the state change auto-commits, so it
    # can't fire in the gap. The in-job claim + generation re-check are the final
    # backstop for a sub-second in-flight boundary.
    await _cancel_reminder_jobs_safe(reminder_id)
    await db.execute(
        f"UPDATE reminders SET {', '.join(updates)} WHERE id = ?",
        params,
    )
    await db.commit()

    # Reschedule at the new due-time.
    await _reschedule_reminder_due_safe(db, reminder_id)

    cursor = await db.execute("SELECT * FROM reminders WHERE id = ?", [reminder_id])
    row = await cursor.fetchone()
    reminder = row_to_dict(row)

    await broadcaster.broadcast("reminders", "reminder_updated", reminder, user_id=user_id)
    return reminder


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
    """Mark a reminder as acknowledged."""
    await require_feature_access(db, user, feature="reminders", action="acknowledge")
    user_id = user["user_id"]
    where = f"{_visibility_filter_sql()} AND id = ?"
    cursor = await db.execute(
        "SELECT * FROM reminders WHERE " + where,
        [user_id, reminder_id],
    )
    row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Reminder not found")
    if dict(row).get("user_id") != user_id:
        raise HTTPException(status_code=403, detail="Not authorised to acknowledge this reminder")

    recurring = _is_rrule(dict(row).get("recurring_pattern"))
    if recurring:
        # A recurring (RRULE) reminder is done for THIS occurrence only: the row
        # stays live and the next occurrence is scheduled (strictly after now).
        # Stopping the series is delete / is_active=false, never "Complete".
        # Clear a pending snooze (it would re-alert this occurrence) and bump the
        # generation so a job already running for it self-voids at fire time.
        # The bump lands BEFORE the cancel (below, after commit): a scan racing
        # the cancel then sees the old unfired job or the NEW generation, never
        # leaves a stale-generation job that would self-void and skip the next one.
        await db.execute(
            "UPDATE reminders SET snoozed_until = NULL, updated_at = NOW(), "
            "schedule_generation = COALESCE(schedule_generation, 0) + 1 WHERE id = ?",
            [reminder_id],
        )
    else:
        # B2: an acknowledged reminder is done — cancel the pending job BEFORE the
        # state change auto-commits so it can't still fire; the in-job obligation
        # re-read also aborts a sub-second in-flight job.
        await _cancel_reminder_jobs_safe(reminder_id)
        await db.execute(
            "UPDATE reminders SET acknowledged = 1, updated_at = NOW() WHERE id = ?",
            [reminder_id],
        )
    await _create_notification(
        db,
        user_id=user_id,
        notif_type="reminder_acknowledged",
        title="Reminder Done",
        message=f"Reminder acknowledged: {dict(row).get('title', 'Reminder')}",
        data={"reminder_id": reminder_id},
    )
    await db.commit()
    if recurring:
        await _cancel_reminder_jobs_safe(reminder_id)
        await _reschedule_reminder_due_safe(db, reminder_id)

    cursor = await db.execute("SELECT * FROM reminders WHERE id = ?", [reminder_id])
    row = await cursor.fetchone()
    reminder = row_to_dict(row)

    await broadcaster.broadcast("reminders", "reminder_acknowledged", reminder, user_id=user_id)
    return reminder


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
