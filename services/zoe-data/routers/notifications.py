"""
FastAPI router for notifications.
Mounted at prefix="/api/notifications" with tag "notifications".
"""
import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from auth import get_current_user
from database import get_db
from push import broadcaster

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


@router.get("/", response_model=dict)
async def list_notifications(
    limit: int = Query(20, ge=1, le=100),
    unread_only: bool = Query(False),
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """List notifications for the current user."""
    user_id = user["user_id"]
    conditions = ["user_id = ?"]
    params = [user_id]

    if unread_only:
        conditions.append("delivered = 0")

    where = " AND ".join(conditions)
    params.append(limit)
    cursor = await db.execute(
        f"SELECT id, type, title, message, data, delivered, action_taken, created_at FROM notifications WHERE {where} ORDER BY created_at DESC LIMIT ?",
        params,
    )
    rows = await cursor.fetchall()
    notifications = [dict(r) for r in rows]
    for n in notifications:
        n["delivered"] = bool(n.get("delivered", 0))
    return {"notifications": notifications}


@router.get("/pending", response_model=dict)
async def get_pending_notifications(
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Get unread/pending notifications count and items."""
    user_id = user["user_id"]
    cursor = await db.execute(
        "SELECT id, type, title, message, data, created_at FROM notifications WHERE user_id = ? AND delivered = 0 ORDER BY created_at DESC LIMIT 10",
        (user_id,),
    )
    rows = await cursor.fetchall()
    notifications = []
    for r in rows:
        d = dict(r)
        if d.get("data"):
            try:
                d["data_parsed"] = json.loads(d["data"])
            except Exception:
                d["data_parsed"] = None
        notifications.append(d)
    return {"count": len(notifications), "notifications": notifications}


@router.post("/", response_model=dict)
async def create_notification(
    payload: dict,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Create a notification."""
    user_id = user["user_id"]
    nid = str(uuid.uuid4())
    await db.execute(
        "INSERT INTO notifications (id, user_id, type, title, message, delivered, created_at) VALUES (?,?,?,?,?,0,datetime('now'))",
        (nid, user_id, payload.get("type", "info"), payload.get("title", ""), payload.get("message", "")),
    )
    await db.commit()
    notif = {
        "id": nid, "type": payload.get("type", "info"),
        "title": payload.get("title", ""), "message": payload.get("message", ""),
        "delivered": False,
    }
    await broadcaster.broadcast("all", "notification_created", notif)
    return notif


@router.post("/{notification_id}/read", response_model=dict)
async def mark_read(
    notification_id: str,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Mark a notification as read/delivered."""
    await db.execute(
        "UPDATE notifications SET delivered = 1, action_taken = 'read' WHERE id = ? AND user_id = ?",
        (notification_id, user["user_id"]),
    )
    await db.commit()
    return {"ok": True, "id": notification_id}


@router.post("/{notification_id}/interaction")
async def track_interaction(
    notification_id: str,
    action: str = "dismiss",
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    cursor = await db.execute(
        "SELECT id FROM notifications WHERE id = ?",
        (notification_id,),
    )
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Notification not found")

    await db.execute(
        "UPDATE notifications SET delivered = 1, action_taken = ? WHERE id = ?",
        (action, notification_id),
    )
    await db.commit()
    return {"message": f"Notification {action}ed successfully"}


@router.delete("/{notification_id}", response_model=dict)
async def delete_notification(
    notification_id: str,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Delete a notification."""
    await db.execute(
        "DELETE FROM notifications WHERE id = ? AND user_id = ?",
        (notification_id, user["user_id"]),
    )
    await db.commit()
    return {"ok": True, "id": notification_id}
