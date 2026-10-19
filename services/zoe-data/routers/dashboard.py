"""Dashboard widget layout management API.

Layout entries are widget INSTANCES, not widget types (Wave 4b, 2026-10-19): each entry is
``{uid, type, x, y, w, h, options?}``. ``uid`` is unique per card, so a board can hold two weather
cards; ``type`` names the widget. A legacy entry ``{id, ...}`` (``id`` was the type) is read as
``type=id`` with a uid minted on read, so nothing stored before this change is lost.

``PUT /layout/`` (the desktop dashboard's save) is the authoritative instance list and keeps
duplicates. ``POST /widgets/`` (chat's "add weather to my dashboard") stays an ENSURE-PRESENT
command: it adds a type the board does not have yet and never stacks a second copy.
``DELETE /widgets/{key}`` removes the card whose uid is ``key``, else every card of type ``key``.
"""
import json
import logging
import re
import uuid
from fastapi import APIRouter, Request, Depends, HTTPException
from auth import get_current_user
from database import get_db
from db_pool import get_db_ctx

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])
MAX_WIDGET_IDS_PER_REQUEST = 200
MAX_LAYOUT_ENTRIES = 60
MAX_OPTIONS_BYTES = 1024
_TYPE_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
_UID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

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


def _new_uid() -> str:
    return uuid.uuid4().hex[:16]


def _entry_type(entry) -> str | None:
    if not isinstance(entry, dict):
        return None
    t = entry.get("type") or entry.get("id")
    return t if isinstance(t, str) else None


def _normalize_entries(entries) -> list[dict]:
    """Stored entries as instances: every one carries ``uid`` + ``type`` (legacy ``{id}`` too)."""
    out = []
    seen = set()
    for entry in entries or []:
        wtype = _entry_type(entry)
        if not wtype:
            continue
        item = dict(entry)
        item["type"] = wtype
        item.pop("id", None)
        uid = item.get("uid")
        if not isinstance(uid, str) or not _UID_RE.match(uid) or uid in seen:
            uid = _new_uid()
        item["uid"] = uid
        seen.add(uid)
        out.append(item)
    return out


def _validated_layout(body: dict) -> list[dict]:
    """The PUT body's layout as clean instance entries, or 400. Never silently drops a card."""
    layout = body.get("layout", [])
    if not isinstance(layout, list):
        raise HTTPException(status_code=400, detail="layout must be a list")
    if len(layout) > MAX_LAYOUT_ENTRIES:
        raise HTTPException(status_code=400, detail=f"layout must hold at most {MAX_LAYOUT_ENTRIES} widgets")
    clean = []
    seen = set()
    for i, entry in enumerate(layout):
        if not isinstance(entry, dict):
            raise HTTPException(status_code=400, detail=f"layout[{i}] must be an object")
        wtype = entry.get("type")
        if not isinstance(wtype, str) or not _TYPE_RE.match(wtype):
            raise HTTPException(status_code=400, detail=f"layout[{i}].type is not a widget type")
        uid = entry.get("uid")
        if not isinstance(uid, str) or not _UID_RE.match(uid):
            raise HTTPException(status_code=400, detail=f"layout[{i}].uid is required (1-64 of A-Z a-z 0-9 _ -)")
        if uid in seen:
            raise HTTPException(status_code=400, detail=f"layout[{i}].uid repeats another card's uid")
        seen.add(uid)
        item = {"uid": uid, "type": wtype}
        for key in ("x", "y", "w", "h"):
            val = entry.get(key)
            if not isinstance(val, int) or isinstance(val, bool) or not 0 <= val <= 1000:
                raise HTTPException(status_code=400, detail=f"layout[{i}].{key} must be an integer 0-1000")
            item[key] = val
        options = entry.get("options")
        if options is not None:
            if not isinstance(options, dict) or len(json.dumps(options)) > MAX_OPTIONS_BYTES:
                raise HTTPException(status_code=400, detail=f"layout[{i}].options must be an object under {MAX_OPTIONS_BYTES} bytes")
            item["options"] = options
        clean.append(item)
    return clean


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
    # get_db_ctx, not `async for db in get_db()`: returning from inside the
    # generator leaks the pooled connection (#953 / the 2026-07-03 pool drain).
    async with get_db_ctx() as db:
        row = await _fetchone_layout_row(db, user_id)
        if row:
            return {"layout": _normalize_entries(_decode_layout(row["layout"])), "updated_at": row["updated_at"]}
        return {"layout": None, "updated_at": None}


@router.put("/layout/")
async def save_layout(request: Request, user: dict = Depends(get_current_user)):
    body = await request.json()
    user_id = user["user_id"]
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body must be an object")
    layout = json.dumps(_validated_layout(body))
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
            current = _normalize_entries(_decode_layout(row["layout"]) if row else [])
            existing_ids = {w["type"] for w in current}

            widget_lookup = {w["id"]: w for w in AVAILABLE_WIDGETS}
            added = []
            max_y = max((w.get("y", 0) + w.get("h", 2) for w in current), default=0)

            for wid in to_add:
                if wid in existing_ids:
                    continue
                meta = widget_lookup[wid]
                current.append(
                    {
                        "uid": _new_uid(),
                        "type": wid,
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
    # get_db_ctx: the early "No layout found" return leaked the conn (#953).
    async with get_db_ctx() as db:
        async with db.transaction():
            row = await _fetchone_layout_for_update(db, user_id)
            if not row:
                return {"status": "error", "message": "No layout found"}
            current = _normalize_entries(_decode_layout(row["layout"]))
            if any(w["uid"] == widget_id for w in current):
                updated = [w for w in current if w["uid"] != widget_id]
            else:
                updated = [w for w in current if w["type"] != widget_id]
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
