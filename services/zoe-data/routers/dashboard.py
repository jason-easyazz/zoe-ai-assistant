"""Dashboard widget layout management API."""
import json
import logging
from fastapi import APIRouter, Request, Depends, HTTPException
from auth import get_current_user
from database import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])
MAX_WIDGET_IDS_PER_REQUEST = 200

AVAILABLE_WIDGETS = [
    {"id": "weather", "name": "Weather", "icon": "🌤️", "category": "info", "default_size": {"w": 2, "h": 2}},
    {"id": "events", "name": "Calendar Events", "icon": "📅", "category": "productivity", "default_size": {"w": 2, "h": 3}},
    {"id": "tasks", "name": "Tasks", "icon": "✅", "category": "productivity", "default_size": {"w": 2, "h": 3}},
    {"id": "shopping", "name": "Shopping List", "icon": "🛒", "category": "productivity", "default_size": {"w": 2, "h": 3}},
    {"id": "notes", "name": "Quick Notes", "icon": "📝", "category": "productivity", "default_size": {"w": 2, "h": 2}},
    {"id": "reminders", "name": "Reminders", "icon": "⏰", "category": "productivity", "default_size": {"w": 2, "h": 2}},
    {"id": "calendar", "name": "Calendar Grid", "icon": "📆", "category": "productivity", "default_size": {"w": 4, "h": 4}},
    {"id": "people", "name": "People", "icon": "👥", "category": "social", "default_size": {"w": 2, "h": 3}},
    {"id": "journal", "name": "Journal", "icon": "📔", "category": "personal", "default_size": {"w": 2, "h": 2}},
    {"id": "home", "name": "Home Control", "icon": "🏠", "category": "home", "default_size": {"w": 2, "h": 2}},
    {"id": "time", "name": "Clock", "icon": "🕐", "category": "info", "default_size": {"w": 2, "h": 1}},
    {"id": "zoe-orb", "name": "Zoe Orb", "icon": "🔮", "category": "core", "default_size": {"w": 2, "h": 2}},
    {"id": "week-planner", "name": "Week Planner", "icon": "📋", "category": "productivity", "default_size": {"w": 4, "h": 3}},
    {"id": "personal", "name": "Personal Todos", "icon": "📌", "category": "productivity", "default_size": {"w": 2, "h": 3}},
    {"id": "work", "name": "Work Todos", "icon": "💼", "category": "productivity", "default_size": {"w": 2, "h": 3}},
    {"id": "bucket", "name": "Bucket List", "icon": "⭐", "category": "personal", "default_size": {"w": 2, "h": 3}},
]


async def _fetchone_layout_row(db, user_id: str):
    cur = await db.execute(
        "SELECT layout, updated_at FROM dashboard_layouts WHERE user_id = ?",
        (user_id,),
    )
    return await cur.fetchone()


async def _fetchone_layout_for_update(db, user_id: str):
    return await db.fetchrow(
        "SELECT layout FROM dashboard_layouts WHERE user_id = $1 FOR UPDATE",
        user_id,
    )


async def _ensure_layout_row(db, user_id: str):
    await db.execute(
        "INSERT INTO dashboard_layouts (user_id, layout, updated_at) "
        "VALUES ($1, $2::jsonb, CURRENT_TIMESTAMP) "
        "ON CONFLICT(user_id) DO NOTHING",
        user_id,
        json.dumps([]),
    )


def _decode_layout(value):
    if isinstance(value, str):
        return json.loads(value)
    return value or []


def _requested_widget_ids(body: dict) -> list[str]:
    widget_ids = body.get("widgets", [])
    if not isinstance(widget_ids, list):
        raise HTTPException(status_code=400, detail="widgets must be a list")
    if len(widget_ids) > MAX_WIDGET_IDS_PER_REQUEST:
        raise HTTPException(
            status_code=400,
            detail=f"widgets must contain at most {MAX_WIDGET_IDS_PER_REQUEST} IDs",
        )

    valid_ids = {w["id"] for w in AVAILABLE_WIDGETS}
    seen = set()
    requested = []
    for wid in widget_ids:
        if wid in valid_ids and wid not in seen:
            seen.add(wid)
            requested.append(wid)
    return requested


@router.get("/layout/")
async def get_layout(user: dict = Depends(get_current_user)):
    user_id = user["user_id"]
    async for db in get_db():
        row = await _fetchone_layout_row(db, user_id)
        if row:
            return {"layout": json.loads(row["layout"]), "updated_at": row["updated_at"]}
        return {"layout": None, "updated_at": None}


@router.put("/layout/")
async def save_layout(request: Request, user: dict = Depends(get_current_user)):
    body = await request.json()
    user_id = user["user_id"]
    layout = json.dumps(body.get("layout", []))
    async for db in get_db():
        async with db.transaction():
            await _ensure_layout_row(db, user_id)
            await _fetchone_layout_for_update(db, user_id)
            await db.execute(
                "UPDATE dashboard_layouts "
                "SET layout = $1::jsonb, updated_at = CURRENT_TIMESTAMP "
                "WHERE user_id = $2",
                layout,
                user_id,
            )
    return {"status": "ok"}


@router.post("/widgets/")
async def add_widgets(request: Request, user: dict = Depends(get_current_user)):
    """Add widget(s) to a user's dashboard layout."""
    body = await request.json()
    user_id = user["user_id"]
    to_add = _requested_widget_ids(body)
    if not to_add:
        return {"status": "error", "message": "No valid widget IDs provided"}

    async for db in get_db():
        async with db.transaction():
            await _ensure_layout_row(db, user_id)
            row = await _fetchone_layout_for_update(db, user_id)
            current = _decode_layout(row["layout"]) if row else []
            existing_ids = {w.get("id") for w in current if isinstance(w, dict)}

            widget_lookup = {w["id"]: w for w in AVAILABLE_WIDGETS}
            added = []
            max_y = max((w.get("y", 0) + w.get("h", 2) for w in current), default=0)

            for wid in to_add:
                if wid in existing_ids:
                    continue
                meta = widget_lookup[wid]
                current.append(
                    {
                        "id": wid,
                        "x": 0,
                        "y": max_y,
                        "w": meta["default_size"]["w"],
                        "h": meta["default_size"]["h"],
                    }
                )
                existing_ids.add(wid)
                max_y += meta["default_size"]["h"]
                added.append(wid)

            await db.execute(
                "UPDATE dashboard_layouts "
                "SET layout = $1::jsonb, updated_at = CURRENT_TIMESTAMP "
                "WHERE user_id = $2",
                json.dumps(current),
                user_id,
            )
    return {"status": "ok", "added": added}


@router.delete("/widgets/{widget_id}")
async def remove_widget(widget_id: str, user: dict = Depends(get_current_user)):
    user_id = user["user_id"]
    async for db in get_db():
        async with db.transaction():
            row = await _fetchone_layout_for_update(db, user_id)
            if not row:
                return {"status": "error", "message": "No layout found"}
            current = _decode_layout(row["layout"])
            updated = [w for w in current if w.get("id") != widget_id]
            await db.execute(
                "UPDATE dashboard_layouts "
                "SET layout = $1::jsonb, updated_at = CURRENT_TIMESTAMP "
                "WHERE user_id = $2",
                json.dumps(updated),
                user_id,
            )
    return {"status": "ok", "removed": widget_id}


@router.get("/widgets/available")
async def list_available_widgets():
    return {"widgets": AVAILABLE_WIDGETS}
