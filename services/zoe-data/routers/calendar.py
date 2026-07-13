"""
FastAPI router for calendar events.
Mounted at prefix="/api/calendar" with tag "calendar".
"""
import json
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from auth import get_current_user
from calendar_service import create_event_record
from calendar_utils import row_to_event
from database import get_db
from guest_policy import require_feature_access
from models import EventCreate, EventUpdate
from push import broadcaster

router = APIRouter(prefix="/api/calendar", tags=["calendar"])


def _row_to_event(row) -> dict:
    """Convert asyncpg Row to dict, parsing metadata JSON."""
    return row_to_event(row)


def _visibility_filter_sql() -> str:
    """SQL fragment: (visibility='family' OR user_id=?) AND deleted=0"""
    return "(visibility = 'family' OR user_id = ?) AND deleted = 0"


async def _enforce_calendar_read_access(db, user: dict) -> str:
    """Apply the shared calendar read gate, then return user_id."""
    await require_feature_access(db, user, feature="calendar", action="read")
    return user["user_id"]


@router.get("/events", response_model=dict)
async def list_events(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """List events with optional start_date, end_date, category filters."""
    user_id = await _enforce_calendar_read_access(db, user)
    conditions = [_visibility_filter_sql()]
    params: list = [user_id]

    if start_date:
        conditions.append("start_date >= ?")
        params.append(start_date)
    if end_date:
        conditions.append("start_date <= ?")
        params.append(end_date)
    if category:
        conditions.append("category = ?")
        params.append(category)

    where = " AND ".join(conditions)
    sql = f"SELECT * FROM events WHERE {where} ORDER BY start_date, start_time"
    cursor = await db.execute(sql, params)
    events = []
    async for row in cursor:
        events.append(_row_to_event(row))
    return {"events": events}


@router.get("/events/today", response_model=dict)
async def list_today_events(
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Get today's events."""
    user_id = await _enforce_calendar_read_access(db, user)
    today = date.today().isoformat()
    sql = "SELECT * FROM events WHERE (visibility = 'family' OR user_id = ?) AND deleted = 0 AND start_date = ? ORDER BY start_time"
    cursor = await db.execute(sql, [user_id, today])
    events = []
    async for row in cursor:
        events.append(_row_to_event(row))
    return {"events": events}


@router.get("/events/{event_id}", response_model=dict)
async def get_event(
    event_id: str,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Get a single event by ID."""
    user_id = await _enforce_calendar_read_access(db, user)
    where = f"{_visibility_filter_sql()} AND id = ?"
    cursor = await db.execute(
        "SELECT * FROM events WHERE " + where,
        [user_id, event_id],
    )
    row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return _row_to_event(row)


@router.post("/events", response_model=dict)
async def create_event(
    payload: EventCreate,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Create a new calendar event."""
    await require_feature_access(db, user, feature="calendar", action="create")
    user_id = user["user_id"]
    metadata_json = json.dumps(payload.metadata) if payload.metadata else None

    record = await create_event_record(
        db,
        user_id=user_id,
        title=payload.title,
        start_date=payload.start_date,
        start_time=payload.start_time,
        end_date=payload.end_date,
        end_time=payload.end_time,
        duration=payload.duration,
        category=payload.category,
        location=payload.location,
        all_day=payload.all_day,
        recurring=payload.recurring,
        metadata=metadata_json,
        visibility=payload.visibility,
    )
    event_id = record["id"]
    await db.commit()

    cursor = await db.execute("SELECT * FROM events WHERE id = ?", [event_id])
    row = await cursor.fetchone()
    event = _row_to_event(row)

    await broadcaster.broadcast("calendar", "event_created", event, user_id=user_id)
    return event


@router.put("/events/{event_id}", response_model=dict)
async def update_event(
    event_id: str,
    payload: EventUpdate,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Update an existing event."""
    await require_feature_access(db, user, feature="calendar", action="update")
    user_id = user["user_id"]
    where = f"{_visibility_filter_sql()} AND id = ?"
    cursor = await db.execute(
        "SELECT * FROM events WHERE " + where,
        [user_id, event_id],
    )
    row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Event not found")
    # Household model: a family-visible event is editable by any signed-in
    # household member (the visibility filter above already scopes the lookup
    # to family-or-own). This mirrors the people router — the panel is a
    # shared surface, and the stricter owner-only check made family events
    # uneditable from it (403s in the operator's 2026-07-13 report).
    # …but only the OWNER may change visibility: a non-owner flipping a family
    # event to 'personal' would hijack it out of the household's (and the
    # owner's) view entirely (same guard as reminders).
    is_owner = dict(row).get("user_id") == user_id

    updates = []
    params = []
    data = payload.model_dump(exclude_unset=True)
    if not is_owner:
        data.pop("visibility", None)

    for key, value in data.items():
        if key == "metadata":
            updates.append("metadata = ?")
            params.append(json.dumps(value) if value is not None else None)
        elif key == "all_day":
            updates.append("all_day = ?")
            params.append(1 if value else 0)
        else:
            updates.append(f"{key} = ?")
            params.append(value)

    if not updates:
        cursor = await db.execute("SELECT * FROM events WHERE id = ?", [event_id])
        row = await cursor.fetchone()
        return _row_to_event(row)

    updates.append("updated_at = NOW()")
    params.extend([event_id])
    sql = f"UPDATE events SET {', '.join(updates)} WHERE id = ?"
    await db.execute(sql, params)
    await db.commit()

    cursor = await db.execute("SELECT * FROM events WHERE id = ?", [event_id])
    row = await cursor.fetchone()
    event = _row_to_event(row)

    await broadcaster.broadcast("calendar", "event_updated", event, user_id=user_id)
    return event


@router.delete("/events/{event_id}", response_model=dict)
async def delete_event(
    event_id: str,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Soft delete an event (set deleted=1)."""
    await require_feature_access(db, user, feature="calendar", action="delete")
    user_id = user["user_id"]
    where = f"{_visibility_filter_sql()} AND id = ?"
    cursor = await db.execute(
        "SELECT * FROM events WHERE " + where,
        [user_id, event_id],
    )
    row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Event not found")
    # Household model: family-visible events are deletable by any signed-in
    # household member — same rationale as update_event above (soft delete,
    # reversible; mirrors the people router's family-or-own semantics).

    await db.execute(
        "UPDATE events SET deleted = 1, updated_at = NOW() WHERE id = ?",
        [event_id],
    )
    await db.commit()

    event = _row_to_event(row)
    event["deleted"] = True

    await broadcaster.broadcast("calendar", "event_deleted", {"id": event_id, **event}, user_id=user_id)
    return {"ok": True, "id": event_id}
