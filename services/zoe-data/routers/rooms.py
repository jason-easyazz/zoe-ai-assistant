"""Zoe-owned rooms: create rooms, put devices in them.

Rooms are a ZOE concept, not a Home Assistant one. A user with no HA knowledge
creates "Bedroom" and drops devices into it; a power user can additionally point
a room at an HA area (``ha_area_id``) so a later import can pull that area's
devices in. The link is enrichment — a room without one is completely normal.

Why this exists at all: rooms today are inferred by reading a room word out of a
device's friendly name (``smart_home_service._room_of``). That is invisible
storage the user cannot edit, and it silently fails on any device whose name
omits its room — the live house's first real switch arrived as "Bedroom 1 Switch
1". Storing the mapping makes it correctable and makes "the light in HERE"
answerable.

Contract shape — FLAT and already-resolved, matching ``panel_config.py``: a room
carries its devices with their live name/state/icon resolved server-side, so the
client renders rather than making a second round-trip and re-deriving shape.

``entity_id`` is validated for SHAPE ONLY, never against a domain allow-list —
the same rule ``panel_config.py`` documents. This house has zero ``light.*``;
its controls are ``input_boolean.*``, ``switch.*``, ``input_number.*`` and
``scene.*``, so an allow-list would reject every real device.

Auth mirrors ``panel_config.py``:
  - GET is unauthenticated — the kiosk runs as a guest and needs to read rooms
    to render them, exactly like it reads its own panel config. No sensitive
    data is exposed (HA entity state is already readable unauthed).
  - Writes require ``get_current_user``.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from auth import get_current_user
from database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/rooms", tags=["rooms"])

MAX_ROOM_NAME_LEN = 40

# Domains the PICKER suggests. This is a suggestion filter, NOT a storage rule:
# `normalize_entity_id` still accepts any well-formed id, so an operator or a
# future importer can put anything in a room. Without it the picker offers all
# 48 live entities — `event.backup_automatic_backup`, every diagnostic
# `sensor.backup_*`, the assist-satellite plumbing — none of which are things
# anyone means by "a device in this room". Mirrors the dock's `_PINNABLE`.
_PICKABLE_DOMAINS = {
    "light", "switch", "input_boolean", "fan", "cover", "media_player",
    "climate", "scene", "input_number", "number", "lock", "vacuum",
}

# A device is in exactly one room, so a move is an UPSERT that steals the row
# from whatever room previously held it (see _link_device). Without that, the
# UNIQUE(entity_id) constraint would turn an ordinary "move the lamp to the
# bedroom" into a 500.


def normalize_room_name(raw: Any) -> str:
    """A display name. Free text (rooms are prose, not dock labels) but bounded.

    Unlike a dock pin name this may contain spaces — "Living Room" is the
    canonical example and forcing it to one word would be actively wrong.
    """
    if not isinstance(raw, str):
        raise ValueError("name must be a string")
    name = " ".join(raw.split())
    if not name:
        raise ValueError("name must not be empty")
    if len(name) > MAX_ROOM_NAME_LEN:
        raise ValueError(f"name must be at most {MAX_ROOM_NAME_LEN} characters")
    return name


def slugify(name: str) -> str:
    """The stable key an intent or panel matches on ("Living Room" → living_room).

    Derived once at creation and NOT re-derived on rename: a rename must not
    silently break a panel that is already pointing at the slug.
    """
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return slug or "room"


def normalize_entity_id(raw: Any) -> str:
    """Accept ANY Home Assistant entity id — SHAPE ONLY (see module docstring)."""
    if not isinstance(raw, str):
        raise ValueError("entity_id must be a string")
    entity_id = raw.strip()
    if not entity_id:
        raise ValueError("entity_id must not be empty")
    if len(entity_id) > 256:
        raise ValueError("entity_id is too long")
    domain, sep, object_id = entity_id.partition(".")
    if not sep or not domain or not object_id:
        raise ValueError("entity_id must look like 'domain.object_id'")
    return entity_id


def normalize_ha_area_id(raw: Any) -> str | None:
    """Optional link to a Home Assistant area. Empty/None clears it."""
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ValueError("ha_area_id must be a string")
    area = raw.strip()
    if not area:
        return None
    if len(area) > 128:
        raise ValueError("ha_area_id is too long")
    return area


# ── resolution (pure — unit-tested without a DB) ──────────────────────────────

def resolve_device(entity_id: str, entity_index: dict[str, dict] | None) -> dict[str, Any]:
    """One device, flat and render-ready.

    A device HA does not currently return is kept and marked ``available:false``
    rather than dropped: the user put it in this room deliberately, and an HA
    hiccup must not silently empty their room. (``panel_config`` drops stale
    PINS because a pin it cannot resolve is unrenderable; a room membership is
    the user's own record and outlives HA's view of it.)
    """
    base: dict[str, Any] = {
        "entity_id": entity_id,
        "name": None,
        "state": None,
        "icon": None,
        "available": False,
    }
    if entity_index is None:
        return base
    entity = entity_index.get(entity_id)
    if entity is None:
        return base
    attrs = entity.get("attributes") or {}
    state = entity.get("state")
    base["name"] = attrs.get("friendly_name")
    base["state"] = state
    base["icon"] = attrs.get("icon")
    base["available"] = state not in (None, "unavailable", "unknown")
    return base


def build_room_payload(
    row: Any,
    entity_ids: list[str],
    entity_index: dict[str, dict] | None,
) -> dict[str, Any]:
    """Compose one flat room payload."""
    return {
        "id": row["id"],
        "name": row["name"],
        "slug": row["slug"],
        "ha_area_id": row["ha_area_id"],
        "linked_to_ha": bool(row["ha_area_id"]),
        "devices": [resolve_device(eid, entity_index) for eid in entity_ids],
        "ha_available": entity_index is not None,
    }


# ── live lookups ──────────────────────────────────────────────────────────────

async def _entity_index() -> dict[str, dict] | None:
    """Live HA entities by entity_id, or None when HA is unreachable.

    Reuses ``panel_config``'s reader rather than duplicating an entity store —
    None (not {}) means "unknown", so a blip cannot mark every device stale.
    """
    from routers.panel_config import _entity_index as panel_entity_index

    return await panel_entity_index()


async def _room_row(db, room_id: str):
    cursor = await db.execute(
        "SELECT id, name, slug, ha_area_id FROM rooms WHERE id = ?", (room_id,)
    )
    return await cursor.fetchone()


async def _device_ids(db, room_id: str) -> list[str]:
    cursor = await db.execute(
        "SELECT entity_id FROM room_devices WHERE room_id = ? ORDER BY sort, entity_id",
        (room_id,),
    )
    return [str(r["entity_id"]) for r in await cursor.fetchall()]


# ── routes ────────────────────────────────────────────────────────────────────

@router.get("")
async def list_rooms(db=Depends(get_db)) -> dict[str, Any]:
    """Every room with its devices resolved. Unauthenticated (kiosk reads it)."""
    cursor = await db.execute(
        "SELECT id, name, slug, ha_area_id FROM rooms ORDER BY sort, name"
    )
    rows = await cursor.fetchall()
    entity_index = await _entity_index()
    rooms = []
    for row in rows:
        rooms.append(build_room_payload(row, await _device_ids(db, row["id"]), entity_index))
    return {"rooms": rooms, "ha_available": entity_index is not None}


def is_pickable(entity_id: str) -> bool:
    """Whether the PICKER should suggest this entity (see ``_PICKABLE_DOMAINS``)."""
    return entity_id.partition(".")[0] in _PICKABLE_DOMAINS


@router.get("/unassigned")
async def unassigned_devices(db=Depends(get_db)) -> dict[str, Any]:
    """Controllable devices not yet in any room — the picker's source.

    Filtered to `_PICKABLE_DOMAINS`: the live house exposes 48 entities, most of
    which are backup/diagnostic sensors nobody would call "a device in this
    room". Storage stays permissive; only the suggestion list is narrowed.

    Returns an empty list (with ``ha_available:false``) when HA is unreachable,
    never an error: a picker that cannot be populated should say so, not 500.
    """
    entity_index = await _entity_index()
    if entity_index is None:
        return {"devices": [], "ha_available": False}
    cursor = await db.execute("SELECT entity_id FROM room_devices")
    taken = {str(r["entity_id"]) for r in await cursor.fetchall()}
    devices = [
        resolve_device(eid, entity_index)
        for eid in entity_index
        if eid not in taken and is_pickable(eid)
    ]
    devices.sort(key=lambda d: (d.get("name") or d["entity_id"]).lower())
    return {"devices": devices, "ha_available": True}


@router.post("")
async def create_room(
    request: Request,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
) -> dict[str, Any]:
    """Create a room. Only ``name`` is required — no Home Assistant needed."""
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Expected JSON object")
    try:
        name = normalize_room_name(body.get("name"))
        ha_area_id = normalize_ha_area_id(body.get("ha_area_id"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    slug = slugify(name)
    cursor = await db.execute("SELECT id FROM rooms WHERE slug = ?", (slug,))
    if await cursor.fetchone():
        raise HTTPException(status_code=409, detail=f"a room called “{name}” already exists")

    room_id = str(uuid.uuid4())
    await db.execute(
        "INSERT INTO rooms (id, name, slug, ha_area_id, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
        (room_id, name, slug, ha_area_id),
    )
    await db.commit()
    row = await _room_row(db, room_id)
    return build_room_payload(row, [], await _entity_index())


@router.put("/{room_id}")
async def update_room(
    room_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
) -> dict[str, Any]:
    """Rename a room or (un)link its HA area. Partial — an absent key is untouched.

    The slug is deliberately NOT re-derived on rename: it is the key a panel or
    intent already points at, and silently rotating it would unbind them.
    """
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Expected JSON object")
    if await _room_row(db, room_id) is None:
        raise HTTPException(status_code=404, detail="room not found")

    if "name" in body:
        try:
            name = normalize_room_name(body["name"])
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        await db.execute(
            "UPDATE rooms SET name = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (name, room_id),
        )
    if "ha_area_id" in body:
        try:
            ha_area_id = normalize_ha_area_id(body["ha_area_id"])
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        await db.execute(
            "UPDATE rooms SET ha_area_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (ha_area_id, room_id),
        )
    await db.commit()
    row = await _room_row(db, room_id)
    return build_room_payload(row, await _device_ids(db, room_id), await _entity_index())


@router.delete("/{room_id}")
async def delete_room(
    room_id: str,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
) -> dict[str, Any]:
    """Delete a room. Its device links go with it (FK ON DELETE CASCADE) — the
    devices themselves are untouched and simply become unassigned again.
    """
    if await _room_row(db, room_id) is None:
        raise HTTPException(status_code=404, detail="room not found")
    # Explicit: SQLite does not enforce FKs unless PRAGMA foreign_keys is ON,
    # so relying on the CASCADE alone would orphan rows on a test DB.
    await db.execute("DELETE FROM room_devices WHERE room_id = ?", (room_id,))
    await db.execute("DELETE FROM rooms WHERE id = ?", (room_id,))
    await db.commit()
    return {"deleted": room_id}


@router.put("/{room_id}/devices")
async def set_room_devices(
    room_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
) -> dict[str, Any]:
    """Replace this room's device list, in order.

    A device already in ANOTHER room is MOVED here, not rejected: "put the lamp
    in the bedroom" is the same gesture whether or not it was somewhere else,
    and a 409 would make the user hunt for its previous room first.
    """
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Expected JSON object")
    if await _room_row(db, room_id) is None:
        raise HTTPException(status_code=404, detail="room not found")

    raw = body.get("devices")
    if not isinstance(raw, list):
        raise HTTPException(status_code=400, detail="devices must be a list")
    entity_ids: list[str] = []
    seen: set[str] = set()
    for item in raw:
        candidate = item.get("entity_id") if isinstance(item, dict) else item
        try:
            entity_id = normalize_entity_id(candidate)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        if entity_id in seen:
            raise HTTPException(status_code=400, detail=f"duplicate device {entity_id}")
        seen.add(entity_id)
        entity_ids.append(entity_id)

    await db.execute("DELETE FROM room_devices WHERE room_id = ?", (room_id,))
    for sort, entity_id in enumerate(entity_ids):
        # Steal the row from any other room: UNIQUE(entity_id) means a plain
        # INSERT would 500 on a move.
        await db.execute("DELETE FROM room_devices WHERE entity_id = ?", (entity_id,))
        await db.execute(
            "INSERT INTO room_devices (room_id, entity_id, sort, created_at)"
            " VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
            (room_id, entity_id, sort),
        )
    await db.commit()
    row = await _room_row(db, room_id)
    return build_room_payload(row, await _device_ids(db, room_id), await _entity_index())


@router.delete("/{room_id}/devices/{entity_id}")
async def unlink_device(
    room_id: str,
    entity_id: str,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
) -> dict[str, Any]:
    """Take one device out of a room. It becomes unassigned, not deleted."""
    if await _room_row(db, room_id) is None:
        raise HTTPException(status_code=404, detail="room not found")
    await db.execute(
        "DELETE FROM room_devices WHERE room_id = ? AND entity_id = ?", (room_id, entity_id)
    )
    await db.commit()
    row = await _room_row(db, room_id)
    return build_room_payload(row, await _device_ids(db, room_id), await _entity_index())
