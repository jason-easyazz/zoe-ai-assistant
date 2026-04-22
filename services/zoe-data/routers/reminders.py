"""
FastAPI router for reminders.
Mounted at prefix="/api/reminders" with tag "reminders".
"""
import json
import uuid
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from auth import get_current_user
from database import get_db
from guest_policy import require_feature_access
from models import ReminderCreate, ReminderUpdate, SnoozeBody
from push import broadcaster

router = APIRouter(prefix="/api/reminders", tags=["reminders"])


def _row_to_dict(row) -> dict:
    """Convert aiosqlite Row to dict."""
    if row is None:
        return None
    d = dict(row)
    if "is_active" in d and d["is_active"] is not None:
        d["is_active"] = bool(d["is_active"])
    if "acknowledged" in d and d["acknowledged"] is not None:
        d["acknowledged"] = bool(d["acknowledged"])
    if "deleted" in d and d["deleted"] is not None:
        d["deleted"] = bool(d["deleted"])
    return d


def _visibility_filter_sql() -> str:
    """SQL fragment: (visibility='family' OR user_id=?) AND deleted=0"""
    return "(visibility = 'family' OR user_id = ?) AND deleted = 0"


async def _create_notification(db, user_id: str, notif_type: str, title: str, message: str, data: dict):
    await db.execute(
        """INSERT INTO notifications (id, user_id, type, title, message, data, delivered, created_at)
           VALUES (?, ?, ?, ?, ?, ?, 0, datetime('now'))""",
        (
            str(uuid.uuid4()),
            user_id,
            notif_type,
            title,
            message,
            json.dumps(data or {}),
        ),
    )


@router.post("/", response_model=dict)
async def create_reminder(
    payload: ReminderCreate,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Create a new reminder."""
    await require_feature_access(db, user, feature="reminders", action="create")
    user_id = user["user_id"]
    reminder_id = str(uuid.uuid4())

    await db.execute(
        """INSERT INTO reminders (
            id, user_id, title, description, reminder_type, category, priority,
            due_date, due_time, recurring_pattern, is_active, acknowledged,
            snoozed_until, visibility, deleted
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0, NULL, ?, 0)""",
        (
            reminder_id,
            user_id,
            payload.title,
            payload.description,
            payload.reminder_type,
            payload.category,
            payload.priority,
            payload.due_date,
            payload.due_time,
            payload.recurring_pattern,
            payload.visibility,
        ),
    )
    await _create_notification(
        db,
        user_id=user_id,
        notif_type="reminder_created",
        title="Reminder Created",
        message=f"Reminder added: {payload.title}",
        data={"reminder_id": reminder_id, "due_date": payload.due_date, "due_time": payload.due_time},
    )
    await db.commit()

    cursor = await db.execute("SELECT * FROM reminders WHERE id = ?", [reminder_id])
    row = await cursor.fetchone()
    reminder = _row_to_dict(row)

    await broadcaster.broadcast("reminders", "reminder_created", reminder)
    return reminder


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
    params.append(limit)
    sql = f"SELECT * FROM reminders WHERE {where} ORDER BY due_date, due_time LIMIT ?"
    cursor = await db.execute(sql, params)
    rows = await cursor.fetchall()
    reminders = [_row_to_dict(r) for r in rows]
    return {"reminders": reminders}


@router.get("/today", response_model=dict)
async def list_today_reminders(
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Get today's reminders (active, due today)."""
    await require_feature_access(db, user, feature="reminders", action="read")
    user_id = user["user_id"]
    today = date.today().isoformat()
    sql = """
        SELECT * FROM reminders
        WHERE (visibility = 'family' OR user_id = ?) AND deleted = 0
          AND is_active = 1 AND (due_date = ? OR due_date IS NULL)
        ORDER BY due_time, created_at
    """
    cursor = await db.execute(sql, [user_id, today])
    rows = await cursor.fetchall()
    reminders = [_row_to_dict(r) for r in rows]
    return {"reminders": reminders}


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

    updates = []
    params = []
    data = payload.model_dump(exclude_unset=True)
    for key, value in data.items():
        if key == "is_active":
            updates.append("is_active = ?")
            params.append(1 if value else 0)
        else:
            updates.append(f"{key} = ?")
            params.append(value)

    if not updates:
        return _row_to_dict(row)

    updates.append("updated_at = datetime('now')")
    params.append(reminder_id)
    await db.execute(
        f"UPDATE reminders SET {', '.join(updates)} WHERE id = ?",
        params,
    )
    await db.commit()

    cursor = await db.execute("SELECT * FROM reminders WHERE id = ?", [reminder_id])
    row = await cursor.fetchone()
    reminder = _row_to_dict(row)

    await broadcaster.broadcast("reminders", "reminder_updated", reminder)
    return reminder


@router.delete("/{reminder_id}", response_model=dict)
async def delete_reminder(
    reminder_id: str,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Soft delete a reminder."""
    await require_feature_access(db, user, feature="reminders", action="delete")
    user_id = user["user_id"]
    where = f"{_visibility_filter_sql()} AND id = ?"
    cursor = await db.execute(
        "SELECT * FROM reminders WHERE " + where,
        [user_id, reminder_id],
    )
    row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Reminder not found")

    await db.execute(
        "UPDATE reminders SET deleted = 1, updated_at = datetime('now') WHERE id = ?",
        [reminder_id],
    )
    await db.commit()

    await broadcaster.broadcast("reminders", "reminder_deleted", {"id": reminder_id})
    return {"ok": True, "id": reminder_id}


@router.post("/{reminder_id}/snooze", response_model=dict)
async def snooze_reminder(
    reminder_id: str,
    body: SnoozeBody,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Snooze a reminder by N minutes."""
    await require_feature_access(db, user, feature="reminders", action="snooze")
    user_id = user["user_id"]
    where = f"{_visibility_filter_sql()} AND id = ?"
    cursor = await db.execute(
        "SELECT * FROM reminders WHERE " + where,
        [user_id, reminder_id],
    )
    row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Reminder not found")

    snoozed_until = (datetime.utcnow() + timedelta(minutes=body.snooze_minutes)).isoformat() + "Z"
    await db.execute(
        "UPDATE reminders SET snoozed_until = ?, updated_at = datetime('now') WHERE id = ?",
        [snoozed_until, reminder_id],
    )
    await _create_notification(
        db,
        user_id=user_id,
        notif_type="reminder_snoozed",
        title="Reminder Snoozed",
        message=f"Reminder snoozed for {body.snooze_minutes} minutes",
        data={"reminder_id": reminder_id, "snoozed_until": snoozed_until},
    )
    await db.commit()

    cursor = await db.execute("SELECT * FROM reminders WHERE id = ?", [reminder_id])
    row = await cursor.fetchone()
    reminder = _row_to_dict(row)

    await broadcaster.broadcast("reminders", "reminder_snoozed", reminder)
    return reminder


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

    await db.execute(
        "UPDATE reminders SET acknowledged = 1, updated_at = datetime('now') WHERE id = ?",
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

    cursor = await db.execute("SELECT * FROM reminders WHERE id = ?", [reminder_id])
    row = await cursor.fetchone()
    reminder = _row_to_dict(row)

    await broadcaster.broadcast("reminders", "reminder_acknowledged", reminder)
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
