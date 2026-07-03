"""
Proactive Engine REST router.

Endpoints:
  POST   /api/proactive/schedule        — schedule a one-shot nudge
  GET    /api/proactive/schedule        — list scheduled (admin/self)
  DELETE /api/proactive/schedule/{id}   — cancel a scheduled nudge
  POST   /api/proactive/pending/{id}    — claim a pending notification → session
  POST   /api/proactive/trigger-morning — manually trigger morning brief (admin/self)
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth import get_current_user
from database import get_db
from guest_policy import require_feature_access
from proactive.session_utils import claim_pending
from proactive.triggers.reminders import schedule_reminder, cancel_reminder

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/proactive", tags=["proactive"])


class ScheduleRequest(BaseModel):
    message: str
    send_at: str                # ISO-8601 UTC, e.g. "2026-05-04T12:00:00Z"
    user_id: str | None = None  # admin only; defaults to calling user


@router.post("/schedule")
async def create_schedule(
    body: ScheduleRequest,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Schedule a one-shot proactive nudge."""
    await require_feature_access(db, user, feature="proactive", action="create")
    caller_id = user["user_id"]
    target_id = body.user_id if body.user_id and user.get("role") == "admin" else caller_id

    try:
        send_at = datetime.fromisoformat(body.send_at.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=422, detail="send_at must be ISO-8601 UTC")

    if send_at <= datetime.now(timezone.utc):
        raise HTTPException(status_code=422, detail="send_at must be in the future")

    scheduled_id = await schedule_reminder(
        user_id=target_id,
        message=body.message,
        send_at=send_at,
    )
    return {"id": scheduled_id, "user_id": target_id, "send_at": body.send_at}


@router.get("/schedule")
async def list_schedules(user: dict = Depends(get_current_user), db=Depends(get_db)):
    """List pending scheduled nudges for the calling user."""
    await require_feature_access(db, user, feature="proactive", action="read")
    user_id = user["user_id"]
    async with db.execute(
        """SELECT id, user_id, message, send_at, fired, created_at
           FROM proactive_scheduled
           WHERE user_id = ? AND fired = 0
           ORDER BY send_at""",
        (user_id,),
    ) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


@router.delete("/schedule/{scheduled_id}")
async def delete_schedule(
    scheduled_id: str,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Cancel a scheduled nudge."""
    await require_feature_access(db, user, feature="proactive", action="delete")
    async with db.execute(
        "SELECT user_id FROM proactive_scheduled WHERE id = ?", (scheduled_id,)
    ) as cur:
        row = await cur.fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Scheduled nudge not found")
    if row[0] != user["user_id"] and user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Not your nudge")

    cancelled = await cancel_reminder(scheduled_id)
    return {"cancelled": cancelled}


@router.post("/pending/{pending_id}")
async def claim_pending_endpoint(pending_id: str):
    """
    Claim a pending proactive notification (called when user taps push).
    Creates a chat session seeded with the notification message and returns
    {session_id, message}.  No auth required (the pending_id IS the token).
    """
    result = await claim_pending(pending_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Pending notification not found or expired")
    return result


@router.post("/trigger-morning")
async def trigger_morning_brief(user: dict = Depends(get_current_user), db=Depends(get_db)):
    """
    Manually trigger the morning brief for the calling user.
    Useful for testing Phase 3.4 without waiting for the 7:30am schedule.
    """
    await require_feature_access(db, user, feature="proactive", action="trigger")
    from proactive.triggers.morning_checkin import _build_morning_context, _compose_morning_message
    from proactive.engine import fire_notification

    user_id = user["user_id"]
    username = user.get("username", "")

    # Use the same local-timezone resolution as the scheduled path
    # (proactive/triggers/morning_checkin.py's _ZOE_TZ) so `today` matches the
    # user's actual local date, not UTC. During the Perth morning (00:00-08:00
    # local == still "yesterday" in UTC) the UTC-based date previously queried
    # yesterday's calendar/context while the displayed day string (already
    # tz-correct below) showed today — a self-contradictory brief.
    from proactive.triggers.morning_checkin import _ZOE_TZ
    now_local = datetime.now(_ZOE_TZ)
    today = now_local.date().isoformat()
    day_str = now_local.strftime("%A, %B %d")

    try:
        ctx = await _build_morning_context(db, user_id, today)
    except Exception as exc:
        log.error("trigger-morning: context build failed: %s", exc)
        ctx = {}

    ctx["day"] = day_str

    message = _compose_morning_message(ctx, username, day_str)

    await fire_notification(
        user_id=user_id,
        message=message,
        trigger_type="morning_checkin",
        context={**ctx, "force_send": True},
        item_id="morning_checkin_manual",
    )

    return {
        "ok": True,
        "message": message,
        "context": {k: v for k, v in ctx.items() if k not in ("portrait_snippet",)},
    }


@router.get("/suggestions")
async def list_pending_suggestions(
    session_id: str,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """List unresolved save offers for the current chat session."""
    await require_feature_access(db, user, feature="proactive", action="read")
    from pending_suggestions import list_active

    items = await list_active(user["user_id"], session_id)
    return {"suggestions": items, "count": len(items)}


@router.post("/suggestions/{suggestion_id}/accept")
async def accept_pending_suggestion(
    suggestion_id: str,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Execute a pending save offer (direct API — no intent re-parse)."""
    await require_feature_access(db, user, feature="proactive", action="accept")
    from pending_suggestions import execute_suggestion

    result = await execute_suggestion(suggestion_id, user["user_id"])
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "failed"))
    return result


@router.post("/suggestions/{suggestion_id}/dismiss")
async def dismiss_pending_suggestion(
    suggestion_id: str,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    await require_feature_access(db, user, feature="proactive", action="dismiss")
    from pending_suggestions import mark_resolved

    ok = await mark_resolved(suggestion_id, user["user_id"])
    return {"ok": ok}
