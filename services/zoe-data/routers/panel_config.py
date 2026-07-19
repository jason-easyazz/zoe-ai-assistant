"""Per-panel configuration: location, default speaker, pinned dock controls.

Answers, for ONE panel: *where is it, what speaker does it target, and which
controls are pinned to its dock*. Today the dock renders whatever HA happens to
return first (``_ha.lights.slice(0,3)``); this router is the storage + API that
lets someone choose instead.

Contract shape — FLAT and already-resolved. The panel does not reach into nested
payloads and does not make a second round-trip to learn what it is rendering:
the seam resolves shape, the client renders. (Nested/raw-passthrough payloads
are what made the queue endpoint render "[object Object]".)

Two shape decisions worth knowing, both forced by the LIVE house (verified
2026-07-17 against ``/api/ha/entities``, 45 entities):

1. **A pin is a read/write PAIR, always.** The thermostat is two entities —
   ``sensor.current_temperature`` (read) and
   ``input_number.thermostat_temperature`` (write, min 16 / max 30 / step 0.5).
   A single ``entity_id`` cannot express that, and "one light and the temp
   control for the room" is the operator's headline case. Every resolved pin
   therefore carries BOTH ``read_eid`` and ``write_eid``; for a light or a scene
   they are simply equal. Callers may POST the short ``{entity_id, name}`` form
   and it is canonicalised to the pair — but the RESPONSE is always the pair, so
   the panel never branches on which form was stored.

2. **``kind`` and ``icon`` are resolved SERVER-SIDE, never inferred from the
   domain.** ``input_boolean.fan`` and ``input_boolean.tv`` share a domain with
   the four lights but are a fan and a TV — domain-derived iconography is
   already wrong today (everything draws a lightbulb; only ``slice(0,3)`` hides
   it). HA itself carries the truth in ``attributes.icon`` (``mdi:fan``,
   ``mdi:television``, ``mdi:ceiling-light``), so that is what we surface.

There are ZERO ``light.*`` and ZERO ``climate.*`` entities in this house, so
``entity_id`` is validated for SHAPE ONLY — never against a domain allow-list,
which would reject every real control.

Storage lives in the ``panels`` row (see alembic 0021): ``location`` already
existed, ``default_player`` + ``pinned`` are new. ``panel_id`` == the panel's
``device_id`` (``localStorage.zoe_panel_id``).

Auth mirrors the existing per-device settings store in ``system.py``:
  - GET is deliberately unauthenticated — the kiosk runs as a guest and polls
    this at boot, exactly like ``GET /api/system/display/preferences``. No
    sensitive data is exposed (HA entity state is already readable unauthed).
  - PUT requires ``get_current_user``, matching ``PUT /api/system/display/
    preferences``.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request

from auth import get_current_user
from database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/panels", tags=["panel-config"])

# The dock is a glance surface at 1280x720 — the pins sit beside the now-playing
# chip. Four one-word pins (including a temp tile) measure narrower than three
# two-word ones, so LABEL LENGTH is the binding constraint, not pin count.
MAX_PINS = 4
# "a single word name, so not to take up too much space, so no Living Room, it
# would be just Living" — this is the width fix, hence server-side enforcement.
MAX_PIN_NAME_LEN = 12

# Kinds the dock knows how to render. Resolved from the WRITE entity's domain.
KIND_TOGGLE = "toggle"
KIND_SCENE = "scene"
KIND_TEMP = "temp"

# `temp` means "numeric setpoint" — it is derived from an input_number/number
# write entity and carries min/max/step/unit. (`climate.*` would also be a
# setpoint, but there are none in this house and it writes via a different
# service; add it here, with its own write_action, when one actually exists.)
_NUMBER_DOMAINS = {"input_number", "number"}

# HA service to call for a write, by kind. Carried per-pin so the dock does not
# hardcode a service name. `set_value` is unmapped in ha_control's action_map,
# which does `action_map.get(action, action)` — unmapped actions pass straight
# through to the bridge, so no control-path change is needed.
_WRITE_ACTION = {
    KIND_TOGGLE: "toggle",
    KIND_SCENE: "turn_on",
    KIND_TEMP: "set_value",
}

# Only used when HA supplies no `attributes.icon` (true for every scene today).
_DEFAULT_ICON = {
    KIND_TOGGLE: "mdi:toggle-switch-outline",
    KIND_SCENE: "mdi:palette-outline",
    KIND_TEMP: "mdi:thermometer",
}

_UNUSABLE_STATES = (None, "unavailable", "unknown")

# The panel-config columns on the `panels` row. A fixed whitelist: PUT builds its
# ON CONFLICT SET list from this, never from request-body keys.
#
# `room_id` (alembic 0026) is the STRUCTURED answer to "where is this panel" and
# is what resolves "the light in HERE". `location` is the older free-text label
# and is deliberately left alone rather than mirrored: nothing reads it for
# behaviour (it is displayed in the settings surface and the panel admin
# listings only), so keeping them in sync would be a dual-write with no
# consumer. Where both exist, `room_id` is authoritative.
_WRITABLE = ("location", "default_player", "pinned", "room_id")


# ── validation / normalisation (pure — unit-tested without a DB) ──────────────

def normalize_pin_name(raw: Any) -> str:
    """Coerce an operator label to a single short word.

    Enforced server-side on purpose: the UI must not be the only thing keeping
    the dock legible. Collapses surrounding whitespace, rejects anything with
    whitespace left inside it (that would be two words), and caps the length.
    """
    if not isinstance(raw, str):
        raise ValueError("name must be a string")
    name = raw.strip()
    if not name:
        raise ValueError("name must not be empty")
    if any(ch.isspace() for ch in name):
        raise ValueError("name must be a single word (no spaces)")
    if len(name) > MAX_PIN_NAME_LEN:
        raise ValueError(f"name must be at most {MAX_PIN_NAME_LEN} characters")
    return name


def normalize_entity_id(raw: Any, field: str = "entity_id") -> str:
    """Accept ANY Home Assistant entity id — SHAPE ONLY.

    Deliberately no domain allow-list: this house has zero ``light.*`` and zero
    ``climate.*``; its controls are ``input_boolean.*``, ``input_number.*`` and
    ``scene.*``. A domain allow-list would reject every real control.
    """
    if not isinstance(raw, str):
        raise ValueError(f"{field} must be a string")
    entity_id = raw.strip()
    if not entity_id:
        raise ValueError(f"{field} must not be empty")
    if len(entity_id) > 256:
        raise ValueError(f"{field} is too long")
    domain, sep, object_id = entity_id.partition(".")
    if not sep or not domain or not object_id:
        raise ValueError(f"{field} must look like 'domain.object_id'")
    return entity_id


def resolve_kind(write_eid: str) -> str:
    """The dock's render/behaviour class for a pin, from the WRITE entity."""
    domain = write_eid.partition(".")[0]
    if domain == "scene":
        return KIND_SCENE
    if domain in _NUMBER_DOMAINS:
        return KIND_TEMP
    return KIND_TOGGLE


def normalize_pin(item: Any) -> dict[str, str]:
    """Canonicalise ONE pin to the stored ``{read_eid, write_eid, name}`` pair.

    Accepts either form::

        {"entity_id": "input_boolean.bedroom_light", "name": "Bed"}   # simple
        {"read_eid": "sensor.current_temperature",                     # pair
         "write_eid": "input_number.thermostat_temperature",
         "name": "Temp"}

    Mixing ``entity_id`` with ``read_eid``/``write_eid`` is rejected rather than
    silently resolved — an ambiguous pin is an operator mistake worth surfacing.
    """
    if not isinstance(item, dict):
        raise ValueError("each pin must be an object with a name and an entity")

    name = normalize_pin_name(item.get("name"))
    has_simple = "entity_id" in item
    has_pair = "read_eid" in item or "write_eid" in item
    if has_simple and has_pair:
        raise ValueError("give either entity_id or read_eid/write_eid, not both")

    if has_simple:
        entity_id = normalize_entity_id(item.get("entity_id"))
        read_eid = write_eid = entity_id
    elif has_pair:
        # A pair needs both halves: read-only pins can't be actioned and
        # write-only pins can't show state.
        read_eid = normalize_entity_id(item.get("read_eid"), "read_eid")
        write_eid = normalize_entity_id(item.get("write_eid"), "write_eid")
    else:
        raise ValueError("each pin needs entity_id or read_eid/write_eid")

    return {"read_eid": read_eid, "write_eid": write_eid, "name": name}


def validate_pins(raw: Any) -> list[dict[str, str]]:
    """Validate the ORDERED pin list for storage. Order is the operator's choice
    and is preserved verbatim. Raises ValueError with an operator-readable
    message; the caller turns that into a 400.
    """
    if not isinstance(raw, list):
        raise ValueError("pinned must be a list")
    if len(raw) > MAX_PINS:
        raise ValueError(f"at most {MAX_PINS} pins are allowed")
    pins: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw:
        pin = normalize_pin(item)
        if pin["write_eid"] in seen:
            raise ValueError(f"duplicate pin for {pin['write_eid']}")
        seen.add(pin["write_eid"])
        pins.append(pin)
    return pins


def normalize_location(raw: Any) -> str | None:
    """A room name. Free-form (it is prose, not a dock label) but bounded.
    Empty/None clears it.
    """
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ValueError("location must be a string")
    location = " ".join(raw.split())
    if not location:
        return None
    if len(location) > 64:
        raise ValueError("location must be at most 64 characters")
    return location


def _stored_pins(raw: Any) -> list[dict[str, str]] | None:
    """Decode the stored JSON. NULL → None ("never configured"), which is NOT
    the same as [] ("explicitly pinned nothing"). A corrupt payload degrades to
    "never configured" rather than 500ing the panel's boot request.
    """
    if raw is None:
        return None
    if isinstance(raw, list):
        return raw
    try:
        decoded = json.loads(raw)
    except (TypeError, ValueError):
        logger.warning("panel config: unreadable stored pins, treating as unconfigured")
        return None
    return decoded if isinstance(decoded, list) else None


def _pin_eids(pin: dict) -> tuple[str | None, str | None]:
    """Read the stored pair, tolerating a legacy/simple ``entity_id`` row."""
    entity_id = pin.get("entity_id")
    read_eid = pin.get("read_eid") or entity_id
    write_eid = pin.get("write_eid") or entity_id
    return read_eid, write_eid


# ── resolution (pure — unit-tested without a DB) ──────────────────────────────

def _resolve_one(
    read_eid: str,
    write_eid: str,
    name: str,
    entity_index: dict[str, dict] | None,
) -> dict[str, Any] | None:
    """Resolve a single pin to its flat, render-ready shape, or None when the
    entity is gone (stale pin → skip, never 500).
    """
    kind = resolve_kind(write_eid)
    base: dict[str, Any] = {
        "name": name,
        "kind": kind,
        "read_eid": read_eid,
        "write_eid": write_eid,
        "write_action": _WRITE_ACTION[kind],
        # Stable key set: `min`/`max`/`step`/`unit`/`setpoint` are present (as
        # null) on every pin so the client never branches on key existence.
        "state": None,
        "setpoint": None,
        "friendly_name": None,
        "icon": _DEFAULT_ICON[kind],
        "available": False,
        "min": None,
        "max": None,
        "step": None,
        "unit": None,
    }

    if entity_index is None:
        # HA offline — keep the pin, admit we don't know its state.
        return base

    read_entity = entity_index.get(read_eid)
    write_entity = entity_index.get(write_eid)
    if read_entity is None or write_entity is None:
        return None  # stale on either half → unusable → skip

    read_attrs = read_entity.get("attributes") or {}
    write_attrs = write_entity.get("attributes") or {}
    state = read_entity.get("state")

    base["state"] = state
    base["friendly_name"] = read_attrs.get("friendly_name")
    # A scene has no on/off state: HA reports the last-activated timestamp, or
    # "unknown" if it has never been fired. That says nothing about whether it
    # can BE fired, so the state-based availability test — correct for a toggle
    # or a sensor — dims every scene the household hasn't used yet. A scene the
    # entity index knows about is fireable; only an explicit "unavailable"
    # (the integration is down) makes it not.
    if kind == "scene":
        base["available"] = state != "unavailable"
    else:
        base["available"] = state not in _UNUSABLE_STATES
    # Icon from the entity being DISPLAYED (read), falling back to the written
    # one, then to the per-kind default. Scenes carry no icon in HA today.
    base["icon"] = read_attrs.get("icon") or write_attrs.get("icon") or _DEFAULT_ICON[kind]
    # Unit is a display fact: prefer the entity being displayed.
    base["unit"] = read_attrs.get("unit_of_measurement") or write_attrs.get("unit_of_measurement")

    if kind == KIND_TEMP:
        # Bounds come from the entity being WRITTEN — they constrain the write.
        base["min"] = write_attrs.get("min")
        base["max"] = write_attrs.get("max")
        base["step"] = write_attrs.get("step")
        # `state` is the current reading; `setpoint` is the target being set.
        # They differ for a real thermostat, so the tile needs both.
        base["setpoint"] = write_entity.get("state")

    return base


def resolve_pins(
    stored: list[dict[str, str]] | None,
    entity_index: dict[str, dict] | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Resolve stored pins against live HA state.

    Returns ``(resolved, unresolved_entity_ids)``.

    HA entities get renamed and deleted, so a stale ``entity_id`` is expected,
    not exceptional: it is SKIPPED (reported in ``unresolved``), never a 500.
    When HA is unreachable ``entity_index`` is None — the pins still resolve
    (with ``state: None``) so the dock renders the operator's chosen controls
    through an HA hiccup instead of blanking.
    """
    if not stored:
        return [], []
    resolved: list[dict[str, Any]] = []
    unresolved: list[str] = []
    for pin in stored:
        if not isinstance(pin, dict):
            continue
        read_eid, write_eid = _pin_eids(pin)
        name = pin.get("name")
        if not isinstance(read_eid, str) or not isinstance(write_eid, str):
            continue
        if not isinstance(name, str):
            continue
        one = _resolve_one(read_eid, write_eid, name, entity_index)
        if one is None:
            if entity_index is not None:
                missing = [
                    eid for eid in dict.fromkeys((read_eid, write_eid))
                    if eid not in entity_index
                ]
                unresolved.extend(missing)
            continue
        resolved.append(one)
    return resolved, unresolved


def build_config_payload(
    device_id: str,
    location: str | None,
    stored_default_player: str | None,
    global_default_player: str | None,
    stored_pins: list[dict[str, str]] | None,
    entity_index: dict[str, dict] | None,
    room: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compose the flat, resolved panel-config payload.

    Speaker resolution: the panel's own choice wins; a household-global
    preferred player is the fallback; ``default_player_source`` tells the client
    which one it got without the client re-deriving the rule.
    """
    panel_player = (stored_default_player or "").strip()
    global_player = (global_default_player or "").strip()
    if panel_player:
        default_player, source = panel_player, "panel"
    elif global_player:
        default_player, source = global_player, "global"
    else:
        default_player, source = None, "none"

    resolved, unresolved = resolve_pins(stored_pins, entity_index)
    return {
        "device_id": device_id,
        "location": location,
        # The panel's Zoe room, resolved server-side and FLAT (matching
        # default_player/default_player_source rather than nesting an object):
        # the client gets the id to write back and the name to display without
        # a second round-trip. All three are null when the panel is in no room.
        "room_id": (room or {}).get("id"),
        "room_name": (room or {}).get("name"),
        "room_slug": (room or {}).get("slug"),
        "default_player": default_player,
        "default_player_source": source,
        "pins_configured": stored_pins is not None,
        "pinned": resolved,
        "unresolved": unresolved,
        "ha_available": entity_index is not None,
        "max_pins": MAX_PINS,
    }


# ── live lookups ──────────────────────────────────────────────────────────────

_HA_BRIDGE_TIMEOUT = 5.0


async def _entity_index() -> dict[str, dict] | None:
    """Index live HA entities by entity_id, or None when HA is unreachable.

    Reuses the same bridge the ``/api/ha/entities`` router reads, rather than
    duplicating an entity store. None (not {}) means "unknown" — an empty dict
    would wrongly mark every pin stale and silently drop the operator's pins.
    """
    from routers.ha_control import _HA_BRIDGE

    try:
        async with httpx.AsyncClient(timeout=_HA_BRIDGE_TIMEOUT) as client:
            response = await client.get(f"{_HA_BRIDGE}/entities")
            response.raise_for_status()
            data = response.json()
    except Exception:
        logger.warning("panel config: HA bridge unreachable; pins unresolved", exc_info=True)
        return None
    entities = data if isinstance(data, list) else data.get("entities", [])
    return {
        str(e.get("entity_id")): e
        for e in entities
        if isinstance(e, dict) and e.get("entity_id")
    }


def _global_default_player() -> str:
    import music_service

    try:
        return music_service.get_preferred_player_id() or ""
    except Exception:
        logger.warning("panel config: global preferred player unreadable", exc_info=True)
        return ""


def player_ids_or_unknown(players: Any) -> set[str] | None:
    """The set of known player ids, or None meaning "cannot validate".

    An EMPTY list means None, not an empty set. ``music_service._ma`` never
    raises — it swallows transport errors and returns None, which
    ``get_players()`` turns into ``[]``. So ``[]`` is indistinguishable between
    "Music Assistant is down" and "MA genuinely has no players", and treating it
    as an empty set would reject EVERY player id — locking the operator out of
    setting their panel's speaker for the whole duration of an MA outage. (Live
    round-trip caught this: the harness got ``[]`` and rejected a player id that
    ``/api/music/players`` was listing at that moment.)
    """
    if not isinstance(players, list) or not players:
        return None
    ids = {
        str(p.get("player_id"))
        for p in players
        if isinstance(p, dict) and p.get("player_id")
    }
    return ids or None


async def _known_player_ids() -> set[str] | None:
    """Live MA player ids, or None when MA is unreachable/unavailable."""
    import music_service

    try:
        players = await music_service.get_players()
    except Exception:
        logger.warning("panel config: MA player list unreadable", exc_info=True)
        return None
    return player_ids_or_unknown(players)


async def _load_room(db, room_id: Any) -> dict[str, Any] | None:
    """Resolve the panel's room to {id, name, slug}, or None.

    A room_id pointing at a room that no longer exists degrades to None rather
    than erroring: the room was deleted out from under this panel, and a panel
    that cannot boot its own config because of that is worse than one that
    simply reports having no room.
    """
    if not room_id:
        return None
    cursor = await db.execute(
        "SELECT id, name, slug FROM rooms WHERE id = ?", (str(room_id),)
    )
    row = await cursor.fetchone()
    if row is None:
        logger.warning("panel config: room %s no longer exists", room_id)
        return None
    return {"id": row["id"], "name": row["name"], "slug": row["slug"]}


async def _load_panel_row(db, device_id: str):
    cursor = await db.execute(
        "SELECT location, default_player, pinned, room_id FROM panels WHERE panel_id = ?",
        (device_id,),
    )
    return await cursor.fetchone()


def _row_value(row, key: str):
    if row is None:
        return None
    try:
        if key not in row.keys():
            return None
    except AttributeError:
        pass
    return row[key]


# ── routes ────────────────────────────────────────────────────────────────────

@router.get("/{device_id}/config")
async def get_panel_config(device_id: str, db=Depends(get_db)) -> dict[str, Any]:
    """The resolved config for one panel.

    Deliberately unauthenticated so the kiosk (which runs as a guest) can read
    its own dock config at boot — same rationale as
    ``GET /api/system/display/preferences``.

    An unregistered panel is NOT an error: it returns defaults with
    ``pins_configured: false``, so a brand-new panel boots to the fallback dock.
    """
    row = await _load_panel_row(db, device_id)
    entity_index = await _entity_index()
    return build_config_payload(
        device_id=device_id,
        location=_row_value(row, "location"),
        stored_default_player=_row_value(row, "default_player"),
        global_default_player=_global_default_player(),
        stored_pins=_stored_pins(_row_value(row, "pinned")),
        entity_index=entity_index,
        room=await _load_room(db, _row_value(row, "room_id")),
    )


@router.put("/{device_id}/config")
async def put_panel_config(
    device_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
) -> dict[str, Any]:
    """Update one panel's config. Partial by design — a key that is absent is
    left untouched, so the settings UI can save one field without having to
    round-trip the others.

    Explicit clears:
      - ``location: null`` / ``""``   → clear the room.
      - ``default_player: null`` / ``""`` → fall back to the global player.
      - ``pinned: null``  → reset to "never configured" (dock falls back).
      - ``pinned: []``    → explicitly pin nothing.
    """
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Expected JSON object")

    # Only the columns the caller actually SUPPLIED are written. This is what
    # makes the update atomic: a concurrent PUT saving `location` cannot clobber
    # this one's `pinned`, because each statement's DO UPDATE SET touches only
    # its own fields. (A read-merge-write would race — the later writer would
    # overwrite all three columns from its own stale snapshot.)
    updates: dict[str, Any] = {}

    if "location" in body:
        try:
            updates["location"] = normalize_location(body["location"])
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    if "default_player" in body:
        raw_player = body["default_player"]
        if raw_player is None:
            updates["default_player"] = None
        elif not isinstance(raw_player, str):
            raise HTTPException(status_code=400, detail="default_player must be a string")
        else:
            candidate = raw_player.strip()
            if not candidate:
                updates["default_player"] = None
            else:
                # Match the existing global setter: reject unknown ids against
                # the live list — but only when that list is actually
                # available, so a Music Assistant outage can't block someone
                # from setting their panel's room or pins.
                known = await _known_player_ids()
                if known is not None and candidate not in known:
                    raise HTTPException(status_code=400, detail="unknown player_id")
                updates["default_player"] = candidate

    if "pinned" in body:
        raw_pins = body["pinned"]
        if raw_pins is None:
            updates["pinned"] = None
        else:
            try:
                updates["pinned"] = json.dumps(validate_pins(raw_pins))
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))

    if "room_id" in body:
        raw_room = body["room_id"]
        if raw_room is None or (isinstance(raw_room, str) and not raw_room.strip()):
            updates["room_id"] = None          # explicit "this panel is in no room"
        elif not isinstance(raw_room, str):
            raise HTTPException(status_code=400, detail="room_id must be a string")
        else:
            candidate = raw_room.strip()
            # Unlike the MA player list, the rooms table is LOCAL — an empty
            # result genuinely means "no such room", not "cannot validate", so
            # rejecting an unknown id here cannot lock the operator out during
            # somebody else's outage.
            if await _load_room(db, candidate) is None:
                raise HTTPException(status_code=400, detail="unknown room_id")
            updates["room_id"] = candidate

    # Column names come from this fixed whitelist, never from the request body.
    set_clause = ", ".join(f"{col}=excluded.{col}" for col in _WRITABLE if col in updates)
    if set_clause:
        set_clause += ", "
    # On INSERT (a panel that was never registered) an unsupplied column lands
    # NULL — i.e. "not configured" — which is the right default. On CONFLICT it
    # is simply absent from the SET list and stays untouched.
    # CURRENT_TIMESTAMP (not NOW()) is SQL-standard and identical on PostgreSQL.
    # RETURNING gives the post-write row, so the response reflects what is
    # actually stored without a second read.
    cursor = await db.execute(
        f"""INSERT INTO panels (panel_id, name, location, default_player, pinned, room_id, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(panel_id) DO UPDATE SET
                 {set_clause}updated_at=CURRENT_TIMESTAMP
            RETURNING location, default_player, pinned, room_id""",
        (
            device_id,
            device_id,
            updates.get("location"),
            updates.get("default_player"),
            updates.get("pinned"),
            updates.get("room_id"),
        ),
    )
    row = await cursor.fetchone()
    await db.commit()

    entity_index = await _entity_index()
    return build_config_payload(
        device_id=device_id,
        location=_row_value(row, "location"),
        stored_default_player=_row_value(row, "default_player"),
        global_default_player=_global_default_player(),
        stored_pins=_stored_pins(_row_value(row, "pinned")),
        entity_index=entity_index,
        room=await _load_room(db, _row_value(row, "room_id")),
    )
