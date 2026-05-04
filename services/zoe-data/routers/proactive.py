"""
Proactive Engine REST router.

Endpoints:
  POST   /api/proactive/schedule        — schedule a one-shot nudge
  GET    /api/proactive/schedule        — list scheduled (admin/self)
  DELETE /api/proactive/schedule/{id}   — cancel a scheduled nudge
  POST   /api/proactive/pending/{id}    — claim a pending notification → session
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth import get_current_user
from database import get_db
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
):
    """Schedule a one-shot proactive nudge."""
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
async def list_schedules(user: dict = Depends(get_current_user)):
    """List pending scheduled nudges for the calling user."""
    user_id = user["user_id"]
    async for db in get_db():
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
):
    """Cancel a scheduled nudge."""
    async for db in get_db():
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
