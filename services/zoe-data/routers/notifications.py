"""
FastAPI router for notifications.
Mounted at prefix="/api/notifications" with tag "notifications".
"""
import json
import uuid

from fastapi import APIRouter, Body, Depends, FastAPI, HTTPException, Query, Request
from pydantic import BaseModel
import notifications_store as _store
import panel_review_cards as _prc
from auth import get_current_user
from database import get_db
from guest_policy import require_feature_access
from push import broadcaster

router = APIRouter(prefix="/api/notifications", tags=["notifications"])

VALID_INTERACTION_ACTIONS = {"dismiss", "read", "click", "snooze"}


class NotificationInteraction(BaseModel):
    action: str = "dismiss"


@router.get("/", response_model=dict)
async def list_notifications(
    limit: int = Query(20, ge=1, le=100),
    unread_only: bool = Query(False),
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """List notifications for the current user."""
    await require_feature_access(db, user, feature="notifications", action="read")
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
    await require_feature_access(db, user, feature="notifications", action="read")
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
    await require_feature_access(db, user, feature="notifications", action="create")
    user_id = user["user_id"]
    nid = str(uuid.uuid4())
    await db.execute(
        "INSERT INTO notifications (id, user_id, type, title, message, delivered, created_at) VALUES (?,?,?,?,?,0,NOW())",
        (nid, user_id, payload.get("type", "info"), payload.get("title", ""), payload.get("message", "")),
    )
    await db.commit()
    notif = {
        "id": nid, "type": payload.get("type", "info"),
        "title": payload.get("title", ""), "message": payload.get("message", ""),
        "delivered": False,
    }
    await broadcaster.broadcast("all", "notification_created", notif, user_id=user_id)
    return notif


@router.post("/{notification_id}/read", response_model=dict)
async def mark_read(
    notification_id: str,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Mark a notification as read/delivered."""
    await require_feature_access(db, user, feature="notifications", action="mark_read")
    cursor = await db.execute(
        "UPDATE notifications SET delivered = 1, action_taken = 'read' WHERE id = ? AND user_id = ?",
        (notification_id, user["user_id"]),
    )
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Notification not found")
    await db.commit()
    return {"ok": True, "id": notification_id}


@router.post("/{notification_id}/interaction")
async def track_interaction(
    notification_id: str,
    action: str = Query("dismiss"),
    body: NotificationInteraction | None = Body(None),
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    await require_feature_access(db, user, feature="notifications", action="interact")
    action = body.action if body is not None else action
    if action not in VALID_INTERACTION_ACTIONS:
        raise HTTPException(
            status_code=422,
            detail=f"action must be one of {sorted(VALID_INTERACTION_ACTIONS)}",
        )
    cursor = await db.execute(
        "SELECT id FROM notifications WHERE id = ? AND user_id = ?",
        (notification_id, user["user_id"]),
    )
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Notification not found")

    await db.execute(
        "UPDATE notifications SET delivered = 1, action_taken = ? WHERE id = ? AND user_id = ?",
        (action, notification_id, user["user_id"]),
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
    await require_feature_access(db, user, feature="notifications", action="delete")
    cursor = await db.execute(
        "DELETE FROM notifications WHERE id = ? AND user_id = ?",
        (notification_id, user["user_id"]),
    )
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Notification not found")
    await db.commit()
    return {"ok": True, "id": notification_id}


# -- the panel card's taps (ZOE_NOTIFICATIONS; mounted via ``register(app)``, off = routes ABSENT) ---------------------------------
# The card token rides in ``X-Panel-Card``; ``panel_review_cards.authorize`` honours it only while the member's voice session on that
# panel is open, or with the member's own session. An item is a card-local id; anything not on the card is 404.
panel_router = APIRouter(prefix=_store.ENDPOINT, tags=["panel"])


async def _caller(request: Request):
    try:
        return await get_current_user(request)
    except HTTPException:
        return None


async def _require_card(request: Request, caller=Depends(_caller)) -> _prc.Card:
    try:
        card = _prc.authorize(request.headers.get("X-Panel-Card", ""), caller)
    except _prc.CardError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.code) from exc
    if card.kind != _store.CARD_KIND:
        raise HTTPException(status_code=404, detail="card_gone")
    return card


class _Act(BaseModel):
    item: str
    action: str
    choice: str | None = None


@panel_router.post("/act")
async def panel_act(body: _Act, card: _prc.Card = Depends(_require_card)):
    it = card.items.get(str(body.item or ""))
    if it is None:
        raise HTTPException(status_code=404, detail="not on this card")
    try:
        out = await _store.act(card.member_id, it, body.action)
    except _store.ActError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.code) from exc
    card.items.pop(body.item, None)                  # one decision per row
    return out


def register(app: FastAPI) -> bool:
    """Mount the card routes iff ``ZOE_NOTIFICATIONS`` is on. Off = absent (404)."""
    if not _store.enabled():
        return False
    app.include_router(panel_router)
    return True
