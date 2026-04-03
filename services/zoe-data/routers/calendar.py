"""
FastAPI router for calendar events.
Mounted at prefix="/api/calendar" with tag "calendar".
"""
import json
import uuid
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from auth import get_current_user
from database import get_db
from models import EventCreate, EventUpdate
from push import broadcaster

router = APIRouter(prefix="/api/calendar", tags=["calendar"])


def _row_to_event(row) -> dict:
    """Convert aiosqlite Row to dict, parsing metadata JSON."""
    d = dict(row)
    if d.get("metadata") is not None and isinstance(d["metadata"], str):
        try:
            d["metadata"] = json.loads(d["metadata"]) if d["metadata"] else None
        except json.JSONDecodeError:
            d["metadata"] = None
    if "all_day" in d and d["all_day"] is not None:
        d["all_day"] = bool(d["all_day"])
    if "deleted" in d and d["deleted"] is not None:
        d["deleted"] = bool(d["deleted"])
    return d


def _visibility_filter_sql() -> str:
    """SQL fragment: (visibility='family' OR user_id=?) AND deleted=0"""
    return "(visibility = 'family' OR user_id = ?) AND deleted = 0"


@router.get("/events", response_model=dict)
async def list_events(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """List events with optional start_date, end_date, category filters."""
    user_id = user["user_id"]
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
    user_id = user["user_id"]
    today = date.today().isoformat()
    where = f"{_visibility_filter_sql()} AND start_date = ?"
    sql = f"SELECT * FROM events WHERE {where} ORDER BY start_time"
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
    user_id = user["user_id"]
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
    user_id = user["user_id"]
    event_id = str(uuid.uuid4())
    metadata_json = json.dumps(payload.metadata) if payload.metadata else None

    await db.execute(
        """INSERT INTO events (
            id, user_id, title, start_date, start_time, end_date, end_time,
            duration, category, location, all_day, recurring, metadata,
            visibility, deleted
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)""",
        (
            event_id,
            user_id,
            payload.title,
            payload.start_date,
            payload.start_time,
            payload.end_date,
            payload.end_time,
            payload.duration,
            payload.category,
            payload.location,
            1 if payload.all_day else 0,
            payload.recurring,
            metadata_json,
            payload.visibility,
        ),
    )
    await db.commit()

    cursor = await db.execute("SELECT * FROM events WHERE id = ?", [event_id])
    row = await cursor.fetchone()
    event = _row_to_event(row)

    await broadcaster.broadcast("calendar", "event_created", event)
    return event


@router.put("/events/{event_id}", response_model=dict)
async def update_event(
    event_id: str,
    payload: EventUpdate,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Update an existing event."""
    user_id = user["user_id"]
    where = f"{_visibility_filter_sql()} AND id = ?"
    cursor = await db.execute(
        "SELECT * FROM events WHERE " + where,
        [user_id, event_id],
    )
    row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Event not found")

    updates = []
    params = []
    data = payload.model_dump(exclude_unset=True)

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

    updates.append("updated_at = datetime('now')")
    params.extend([event_id])
    sql = f"UPDATE events SET {', '.join(updates)} WHERE id = ?"
    await db.execute(sql, params)
    await db.commit()

    cursor = await db.execute("SELECT * FROM events WHERE id = ?", [event_id])
    row = await cursor.fetchone()
    event = _row_to_event(row)

    await broadcaster.broadcast("calendar", "event_updated", event)
    return event


@router.delete("/events/{event_id}", response_model=dict)
async def delete_event(
    event_id: str,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Soft delete an event (set deleted=1)."""
    user_id = user["user_id"]
    where = f"{_visibility_filter_sql()} AND id = ?"
    cursor = await db.execute(
        "SELECT * FROM events WHERE " + where,
        [user_id, event_id],
    )
    row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Event not found")

    await db.execute(
        "UPDATE events SET deleted = 1, updated_at = datetime('now') WHERE id = ?",
        [event_id],
    )
    await db.commit()

    event = _row_to_event(row)
    event["deleted"] = True

    await broadcaster.broadcast("calendar", "event_deleted", {"id": event_id, **event})
    return {"ok": True, "id": event_id}
