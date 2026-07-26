"""smart_home_service — Zoe's bridge to the local Home Assistant engine.

Doctrine (see project_zoe_over_ha_ma_doctrine): Home Assistant is an invisible
organ. Zoe never exposes HA directly; this module is the ONLY place in the
Skybridge resolver that speaks the HA MCP-bridge REST protocol, and it hands the
rest of Zoe normalized device shapes + a Skybridge ``smart_home`` card. Local-first,
plain names, no "Home Assistant" jargon reaches the panel.

Every call is best-effort and never raises to the caller: a dead bridge degrades
to a friendly "couldn't reach the home hub" card, never a broken turn.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

_TIMEOUT_S = 6.0

# Pure grammar — stripped before matching. Device-class nouns (light/lamp/…) are
# DELIBERATELY kept: they carry the domain and singular-vs-plural, which is how we
# tell "the lamp" (one specific device → disambiguate) from "the lights" (an
# intentional all-lights sweep). _select_targets consumes them.
_MATCH_STOPWORDS = {"the", "a", "an", "my", "our", "your", "please", "zoe", "to", "on", "off", "and", "of"}

# Device-class vocabulary. Plurals (and "all"/"everything") mark a sweep of the
# whole class; a bare singular names one device and disambiguates when several tie.
_LIGHT_WORDS = {"light", "lamp", "lights", "lamps"}
_LIGHT_PLURAL = {"lights", "lamps"}
_SWITCH_WORDS = {"switch", "plug", "outlet", "fan", "switches", "plugs", "outlets", "fans"}
_SWITCH_PLURAL = {"switches", "plugs", "outlets", "fans"}
_CLASS_WORDS = _LIGHT_WORDS | _SWITCH_WORDS
_SWEEP_WORDS = {"all", "every", "everything"}


def _ha_url() -> str:
    return os.environ.get("ZOE_HA_BRIDGE_URL", "http://127.0.0.1:8007").rstrip("/")


async def _ha_get(path: str) -> Any:
    """GET a bridge path. Returns parsed JSON or None on any failure."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as c:
            r = await c.get(f"{_ha_url()}{path}")
            if r.status_code != 200:
                logger.debug("HA GET %s -> HTTP %s", path, r.status_code)
                return None
            return r.json()
    except Exception as exc:  # noqa: BLE001 — HA is optional; never break Zoe
        logger.debug("HA GET %s unreachable: %s", path, exc)
        return None


async def _ha_post(path: str, payload: dict[str, Any]) -> Optional[dict[str, Any]]:
    """POST a bridge command. Returns parsed JSON on success, None on any failure."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as c:
            r = await c.post(f"{_ha_url()}{path}", json=payload)
            if r.status_code != 200:
                logger.debug("HA POST %s -> HTTP %s", path, r.status_code)
                return None
            return r.json()
    except Exception as exc:  # noqa: BLE001 — HA is optional; never break Zoe
        logger.debug("HA POST %s unreachable: %s", path, exc)
        return None


# ── Read: normalized device / scene / script lists ───────────────────────────

def _norm_state(raw: Any) -> str:
    return str(raw or "").strip().lower()


def _device_from_light(row: dict[str, Any]) -> dict[str, Any]:
    state = _norm_state(row.get("state"))
    brightness = row.get("brightness")
    return {
        "entity_id": row.get("entity_id", ""),
        "name": _friendly_name(row),
        "domain": "light",
        "state": state,
        "on": state == "on",
        "available": state not in ("", "unavailable", "unknown"),
        "dimmable": True,  # light-domain entities support brightness
        "brightness": int(brightness) if isinstance(brightness, (int, float)) else None,
    }


def _device_from_switch(row: dict[str, Any]) -> dict[str, Any]:
    """A real ``switch.*`` entity from the hub.

    The display class is RESOLVED from the name/icon, not pinned to "switch".
    Home Assistant's ``switch`` domain is a wiring fact, not a product one: a
    smart LIGHT switch — the single most common thing anyone puts on a wall —
    lands there, and so do plugs, kettles and relays. Hardcoding "switch" here
    meant a real wall light could never answer to "the light" no matter what it
    was called, while the simulated ``input_boolean`` helpers (which DO go
    through ``_entity_domain``) always could. That asymmetry only stayed hidden
    while this house had no real hardware; the first Grid Connect light switch
    ("Bedroom Light", ``switch.bedroom_1_switch_1``) exposed it immediately.

    Control is unaffected — the bridge maps the service from the entity's OWN
    HA domain, so this changes what Zoe CALLS the device, never how she drives
    it.
    """
    state = _norm_state(row.get("state"))
    name = _friendly_name(row)
    entity_id = str(row.get("entity_id") or "")
    attrs = row.get("attributes") if isinstance(row.get("attributes"), dict) else {}
    icon = str(row.get("icon") or attrs.get("icon") or "")
    return {
        "entity_id": entity_id,
        "name": name,
        "domain": _entity_domain(name, entity_id, icon),
        "state": state,
        "on": state == "on",
        "available": state not in ("", "unavailable", "unknown"),
        "dimmable": False,  # a switch has no brightness channel, whatever it drives
        "brightness": None,
    }


def _friendly_name(row: dict[str, Any]) -> str:
    # /entities rows carry the name under attributes.friendly_name; /lights and
    # /switches rows expose it flat as "name". Accept both shapes.
    attrs = row.get("attributes") if isinstance(row.get("attributes"), dict) else {}
    name = str(row.get("name") or row.get("friendly_name") or attrs.get("friendly_name") or "").strip()
    if name:
        return name
    # Fall back to a humanized entity id ("switch.living_room" → "Living Room").
    ent = str(row.get("entity_id") or "device")
    tail = ent.split(".", 1)[-1]
    return tail.replace("_", " ").title() or "Device"


# Simulated devices in a headless Home Assistant are almost always `input_boolean`
# helpers (the owner's real home is exactly this: Living Room Light, Kitchen Light,
# Ceiling Fan, TV, …). They never appear under /lights or /switches, which is why
# the card used to look empty. We pull them from /entities and infer a display
# class from the friendly name + mdi icon so each tile gets the right glyph. They
# toggle through the same /devices/control turn_on/turn_off path (the bridge maps
# the service from the entity's own domain), so control stays exact + safe.
def _entity_domain(name: str, entity_id: str, icon: str) -> str:
    hay = f"{name} {entity_id} {icon}".lower()
    if "fan" in hay:
        return "fan"
    if "tv" in hay or "television" in hay or "media" in hay:
        return "tv"
    if any(w in hay for w in ("light", "lamp", "bulb", "lantern", "sconce", "chandelier")):
        return "light"
    return "switch"


def _device_from_entity(row: dict[str, Any]) -> dict[str, Any]:
    """Normalize a raw /entities row (input_boolean helper, etc.) to a device."""
    ent = str(row.get("entity_id") or "")
    attrs = row.get("attributes") if isinstance(row.get("attributes"), dict) else {}
    name = _friendly_name(row)
    icon = str(attrs.get("icon") or "")
    state = _norm_state(row.get("state"))
    return {
        "entity_id": ent,
        "name": name,
        "domain": _entity_domain(name, ent, icon),
        "state": state,
        "on": state == "on",
        "available": state in ("on", "off"),
        "dimmable": False,  # input_boolean has no brightness channel
        "brightness": None,
    }


async def _entities_in(domain: str) -> list[dict[str, Any]]:
    data = await _ha_get(f"/entities?domain={domain}")
    return (data or {}).get("entities", []) if isinstance(data, dict) else []


def _is_internal(device: dict[str, Any]) -> bool:
    """Zoe's own voice-satellite exposes internal switches (Mute, Thinking Sound)
    as `switch.lva_*` named "zoe-touch-pi …". They are plumbing, not household
    devices — hide them so the card shows the owner's real home, never Zoe's guts.
    The check is tied to the satellite's auto-generated id/name shape (the `lva_`
    id token, or the hyphenated "zoe-touch" device slug) so a genuine device is
    never dropped for merely containing a plain word — `input_boolean.assistance_light`,
    `switch.satellite_dish`, and a friendly "Zoe Touch Lamp" all survive."""
    entity_id = str(device.get("entity_id") or "").lower()
    name = str(device.get("name") or "").lower()
    return "lva_" in entity_id or "zoe-touch" in name


async def list_devices() -> Optional[list[dict[str, Any]]]:
    """Every controllable device as normalized dicts. None = bridge down.

    Sources: real HA lights (/lights, dimmable) + switches (/switches) + helper
    toggles (input_boolean via /entities), which is how a headless HA models a
    simulated home. If EVERY source is unreachable the bridge is down; a reachable
    bridge with no devices returns [] (an inviting empty card, not the offline one).
    """
    lights = await _ha_get("/lights")
    switches = await _ha_get("/switches")
    helpers = await _ha_get("/entities?domain=input_boolean")
    if lights is None and switches is None and helpers is None:
        return None  # bridge unreachable — caller renders the offline card
    devices: list[dict[str, Any]] = []
    for row in (lights or {}).get("lights", []) if isinstance(lights, dict) else []:
        devices.append(_device_from_light(row))
    for row in (switches or {}).get("switches", []) if isinstance(switches, dict) else []:
        devices.append(_device_from_switch(row))
    for row in (helpers or {}).get("entities", []) if isinstance(helpers, dict) else []:
        devices.append(_device_from_entity(row))
    # Stable de-dupe by entity id + drop Zoe's own voice-satellite internals.
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for d in devices:
        ent = str(d.get("entity_id") or "")
        if ent and ent in seen:
            continue
        if _is_internal(d):
            continue
        seen.add(ent)
        unique.append(d)
    return unique


# ── Rooms + climate (presentation enrichment) ────────────────────────────────

# Room words we recognise in a device's friendly name so we can group tiles by
# space ("Living Room Light" → Living Room). Multi-word rooms are matched first.
_ROOM_PHRASES = [
    "living room", "family room", "dining room", "laundry room", "guest room",
    "media room", "rec room", "games room", "utility room",
]
_ROOM_WORDS = [
    "kitchen", "bedroom", "bathroom", "porch", "hallway", "hall", "office",
    "garage", "patio", "garden", "lounge", "study", "nursery", "laundry",
    "entry", "entryway", "den", "basement", "attic", "deck", "yard", "loft",
    "conservatory", "pantry", "landing", "closet", "balcony", "veranda",
]


def _room_of(name: str) -> Optional[str]:
    low = f" {name.lower()} "
    for phrase in _ROOM_PHRASES:
        if f" {phrase} " in low:
            return phrase.title()
    for word in _ROOM_WORDS:
        if f" {word} " in low:
            return word.title()
    return None


_AROUND_HOME = "Around the home"


def _group_rooms(devices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Bucket device tiles by inferred room, preserving first-seen order. Devices
    with no recognisable room fall into a single 'Around the home' group so nothing
    is stranded. Returns [] when there are no devices (renderer shows empty state)."""
    order: list[str] = []
    buckets: dict[str, list[dict[str, Any]]] = {}
    for d in devices:
        room = _room_of(str(d.get("name") or "")) or _AROUND_HOME
        if room not in buckets:
            buckets[room] = []
            order.append(room)
        buckets[room].append(d)
    # Keep the catch-all group last for a tidy reading order.
    order.sort(key=lambda r: (r == _AROUND_HOME,))
    return [{"name": r, "devices": buckets[r]} for r in order]


async def get_climate() -> Optional[dict[str, Any]]:
    """Read-only comfort strip: the current room temperature + the thermostat
    set-point, if the home exposes them. Adjusting the thermostat is deferred to a
    later slice — this only surfaces the numbers so the card reads as a real home."""
    numbers = await _entities_in("input_number")
    sensors = await _ha_get("/sensors")
    target = None
    unit = "°"
    for row in numbers:
        attrs = row.get("attributes") if isinstance(row.get("attributes"), dict) else {}
        hay = f"{attrs.get('friendly_name','')} {row.get('entity_id','')}".lower()
        if "thermostat" in hay or "temperature" in hay or "temp" in hay:
            try:
                target = float(row.get("state"))
                unit = str(attrs.get("unit_of_measurement") or unit)
            except (TypeError, ValueError):
                pass
            break
    current = None
    sensor_rows = (sensors or {}).get("sensors", []) if isinstance(sensors, dict) else []
    for row in sensor_rows:
        attrs = row.get("attributes") if isinstance(row.get("attributes"), dict) else {}
        name = str(row.get("name") or attrs.get("friendly_name") or "").lower()
        dclass = str(attrs.get("device_class") or "").lower()
        if dclass == "temperature" or ("current" in name and "temp" in name):
            try:
                current = float(row.get("state"))
                unit = str(attrs.get("unit_of_measurement") or unit)
            except (TypeError, ValueError):
                pass
            break
    if current is None and target is None:
        return None
    return {"current": current, "target": target, "unit": unit}


async def list_scenes() -> list[dict[str, Any]]:
    data = await _ha_get("/scenes")
    rows = (data or {}).get("scenes", []) if isinstance(data, dict) else []
    return [{"scene_id": r.get("entity_id", ""), "name": _friendly_name(r)} for r in rows]


# ── Write: control primitives (never raise) ──────────────────────────────────

async def set_device(entity_id: str, on: bool, *, brightness_pct: int | None = None) -> bool:
    """Turn a light/switch on or off (optionally with brightness). True on success."""
    data: dict[str, Any] = {}
    if on and brightness_pct is not None:
        data["brightness_pct"] = max(1, min(100, int(brightness_pct)))
    payload: dict[str, Any] = {
        "entity_id": entity_id,
        "action": "turn_on" if on else "turn_off",
    }
    if data:
        payload["data"] = data
    result = await _ha_post("/devices/control", payload)
    return _control_ok(result, entity_id)


async def activate_scene(scene_id: str) -> bool:
    result = await _ha_post("/scenes/activate", {"scene_id": scene_id})
    return isinstance(result, dict)


def _control_ok(result: Any, entity_id: str) -> bool:
    # The bridge returns HTTP 200 with a "Successfully executed …" message even
    # for unknown entities, so we only send control to entities we listed first;
    # here we just confirm the bridge accepted and acknowledged the call.
    if not isinstance(result, dict):
        return False
    message = str(result.get("message") or "")
    return "success" in message.lower() or "result" in result


# ── Target matching ──────────────────────────────────────────────────────────

def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", (text or "").lower()) if t and t not in _MATCH_STOPWORDS]


def _room_subset(
    pool: list[dict[str, Any]], room_eids: set[str] | None
) -> list[dict[str, Any]]:
    """The devices in ``pool`` that belong to the speaker's room.

    Empty when no room is known — which is the state of every install that has
    not created a room, and the reason this whole feature is inert by default.
    """
    if not room_eids:
        return []
    return [d for d in pool if d.get("entity_id") in room_eids]


def _select_targets(
    devices: list[dict[str, Any]], query: str, action: str,
    room_eids: set[str] | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    """Resolve a control query to (targets, ambiguous).

    - ``[], False`` — no target named (caller asks which device).
    - ``ambiguous=True`` — a SPECIFIC command tied across several devices; the
      caller asks rather than toggling unintended ones.
    - a bare PLURAL/all class word ("the lights") sweeps that class (not ambiguous);
      a bare SINGULAR ("the lamp") names one device and disambiguates when several
      tie. set_brightness only ever targets dimmable devices.

    ``room_eids`` is the set of entity ids in the room the SPEAKER is in (from
    the panel's Zoe room). It is used ONLY to break a tie that would otherwise
    be answered with "which one?", and only for a bare SINGULAR class command —
    "turn off the light" said in the bedroom. Deliberately narrow:

      * A query that NAMES a room or device never reaches here (it takes the
        scored branch below), so an explicit "kitchen light" always wins over
        the room you happen to be standing in.
      * A PLURAL sweep ("turn off the lights") is left alone. It already
        succeeds house-wide today, and quietly shrinking it to one room would
        change a working command's meaning — the opposite of this fix, which
        only rescues a command that currently FAILS with a question.
      * With no rooms configured ``room_eids`` is empty/None and every path
        below behaves exactly as before.
    """
    want = _tokens(query)
    if not want:
        return [], False
    q = set(want)
    is_plural = bool((q & _LIGHT_PLURAL) or (q & _SWITCH_PLURAL) or (q & _SWEEP_WORDS))
    if q & _LIGHT_WORDS:
        domains = {"light"}
    elif q & _SWITCH_WORDS:
        domains = {"switch", "fan"}
    else:
        domains = set()  # only a name/room was given
    name_tokens = [t for t in want if t not in _CLASS_WORDS and t not in _SWEEP_WORDS]

    def _dimmable_only(pool: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if action != "set_brightness":
            return pool
        return [d for d in pool if d.get("dimmable")] or pool

    if not name_tokens:
        # A device literally NAMED with only class words ("Light Switch", "Fan
        # Light"): a MULTI-word class-only phrase is a specific name, not a generic
        # class term, so pin the device it uniquely identifies (keeps a tapped tile
        # exact even for class-only names). If several collide — e.g. a light AND a
        # switch both named "Light Switch" — ASK rather than guess the wrong one. A
        # single bare class word ("lights"/"lamp") falls through to the class logic.
        if len(want) >= 2:
            exact = _dimmable_only([
                d for d in devices
                if all(t in f"{d.get('name', '')} {d.get('entity_id', '')}".lower() for t in want)
            ])
            if len(exact) == 1:
                return exact, False
            if len(exact) > 1:
                return exact, True
        # Bare class command: "the lamp" / "the lights" / "all lights".
        pool = [d for d in devices if d.get("domain") in domains] if domains else list(devices)
        pool = _dimmable_only(pool)
        if is_plural or len(pool) <= 1:
            return pool, False
        # Several match a bare SINGULAR ("the light") — today that is always a
        # question. If the speaker's room owns exactly one of them, that is the
        # non-arbitrary answer "in here" was asking for. Anything else (the room
        # owns none, or owns several) falls through to the same question as
        # before, which is the operator's stated preference over guessing.
        in_room = _room_subset(pool, room_eids)
        if len(in_room) == 1:
            return in_room, False
        return pool, True  # one singular device implied but several match → ask

    # A name/room was given — score by token overlap within the named class.
    scored: list[tuple[int, dict[str, Any]]] = []
    for d in devices:
        if domains and d.get("domain") not in domains:
            continue
        hay = f"{d.get('name', '')} {d.get('entity_id', '')}".lower()
        hits = sum(1 for tok in name_tokens if tok in hay)
        if hits:
            scored.append((hits, d))
    if not scored:
        return [], False
    best = max(s for s, _ in scored)
    pool = _dimmable_only([d for s, d in scored if s == best])
    return (pool, False) if len(pool) <= 1 else (pool, True)


def _match_scene(scenes: list[dict[str, Any]], query: str) -> Optional[dict[str, Any]]:
    want = _tokens(query)
    if not want:
        return None
    best_scene, best_hits = None, 0
    for s in scenes:
        hay = f"{s.get('name', '')} {s.get('scene_id', '')}".lower()
        hits = sum(1 for tok in want if tok in hay)
        if hits > best_hits:
            best_scene, best_hits = s, hits
    return best_scene


# ── Card + result shaping ─────────────────────────────────────────────────────

def _device_query(device: dict[str, Any]) -> str:
    """The re-entrant resolver query a device tile taps to toggle. We append the
    EXACT HA entity id as an "@entity" marker so a tap controls precisely that
    device (never a fuzzy name match); the readable prefix is for the transcript."""
    verb = "turn off" if device.get("on") else "turn on"
    name = device.get("name", "device")
    ent = device.get("entity_id", "")
    return f"{verb} {name} @{ent}" if ent else f"{verb} the {name} {'light' if device.get('domain') == 'light' else 'switch'}"


def _scene_query(scene: dict[str, Any]) -> str:
    ent = scene.get("scene_id", "")
    name = scene.get("name", "scene")
    return f"activate {name} @{ent}" if ent else f"activate the {name} scene"


def _tile(d: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": d.get("name"),
        "entity_id": d.get("entity_id"),
        "domain": d.get("domain"),
        "state": d.get("state"),
        "on": bool(d.get("on")),
        "available": bool(d.get("available")),
        "dimmable": bool(d.get("dimmable")),
        "brightness": d.get("brightness"),
        # Room caption on the tile itself (Apple-Home style) so a sparse home reads
        # as composed — tiles flow across the full width instead of a thin column.
        "room": _room_of(str(d.get("name") or "")) or "",
        "query": _device_query(d),
    }


def _home_card(
    devices: list[dict[str, Any]],
    scenes: list[dict[str, Any]],
    *,
    title: str = "Home",
    status: str = "Home",
    summary: str = "",
    climate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    tiles = [_tile(d) for d in devices]
    # Room-grouped view (tiles carry their full contract, so the renderer can use
    # either `rooms` or the flat `devices` list). Grouping fills the stage even
    # when the home is sparse — a couple of tiles read as "Living Room" not "a gap".
    rooms = [
        {"name": g["name"], "devices": [_tile(d) for d in g["devices"]]}
        for g in _group_rooms(devices)
    ]
    scene_tiles = [
        {"name": s.get("name"), "scene_id": s.get("scene_id"), "query": _scene_query(s)}
        for s in scenes
    ]
    props: dict[str, Any] = {
        "title": title,
        "status": status,
        "summary": summary,
        "devices": tiles,
        "rooms": rooms,
        "scenes": scene_tiles,
        # Every home card offers a way to grow — the owner's #1 ask. The panel tile
        # re-enters the resolver with this query (tap + voice share one path).
        "add_query": "add a device",
        "device_count": len(tiles),
    }
    if climate:
        props["climate"] = climate
    return {"component": "smart_home", "props": props}


def _add_device_card() -> dict[str, Any]:
    """The '＋ Add a device' surface: a QR the owner scans with their phone to open
    Zoe's own branded setup guide. Home Assistant is headless and its MCP bridge
    exposes no config-flow/pairing endpoint, so Zoe can't silently finish pairing —
    the phone page walks the owner through it honestly (see routers/smart_home_setup)."""
    import smart_home_setup  # local import: keeps the bridge module import-light
    minted = smart_home_setup.mint()
    qr_path = f"/api/home/setup/qr?token={minted['token']}"
    return {
        "component": "smart_home",
        "props": {
            "title": "Add a device",
            "status": "Setup",
            "mode": "setup",
            "summary": "Scan with your phone to add a light, plug, or speaker.",
            "qr_path": qr_path,
            "back_query": "smart home",
        },
    }


def _offline_card() -> dict[str, Any]:
    return {
        "component": "smart_home",
        "props": {
            "title": "Home",
            "status": "Offline",
            "summary": "I couldn't reach the home hub.",
            "devices": [],
            "scenes": [],
            "offline": True,
        },
    }


def _result(spoken: str, card: dict[str, Any], action: str) -> dict[str, Any]:
    return {
        "handled": True,
        "intent": {"domain": "smart_home", "action": action},
        "spoken_summary": spoken,
        "cards": [card],
        "actions": [],
    }


def _summarize(devices: list[dict[str, Any]]) -> str:
    on = [d for d in devices if d.get("on")]
    if not devices:
        return "Your home is ready — add a device to get started."
    if not on:
        return "Everything is off."
    names = ", ".join(str(d.get("name")) for d in on[:3])
    return f"{len(on)} on: {names}." if len(on) > 1 else f"{names} is on."


# ── Skybridge resolver ────────────────────────────────────────────────────────

async def resolve_smart_home(
    intent: Any, room_eids: set[str] | None = None
) -> dict[str, Any]:
    """The Skybridge smart_home domain resolver. `intent` has .action, .query,
    and (for brightness) .duration_seconds carrying the target percent.

    ``room_eids`` is the entity ids of the room the speaker is standing in, used
    only to break an otherwise-ambiguous bare singular ("the light") — see
    `_select_targets`. None/empty leaves every decision exactly as it was.
    """
    action = getattr(intent, "action", "status")
    query = (getattr(intent, "query", "") or "").strip()
    entity_id = (getattr(intent, "entity_id", "") or "").strip()

    # "Add a device" is answerable even if the hub is momentarily unreachable — it
    # just shows a QR for the owner's phone, so resolve it before the hub read.
    if action == "add_device":
        return _result(
            "Scan the code with your phone and I'll walk you through adding a device.",
            _add_device_card(), "add_device",
        )

    devices = await list_devices()
    if devices is None:
        return _result("I couldn't reach the home hub.", _offline_card(), action)
    scenes = await list_scenes()

    if action == "activate_scene":
        # A scene chip tap carries the exact scene id — activate that one directly.
        if entity_id:
            scene = next((s for s in scenes if s.get("scene_id") == entity_id), None) or {
                "scene_id": entity_id, "name": entity_id.split(".", 1)[-1].replace("_", " ").title()}
        else:
            scene = _match_scene(scenes, query)
        if scene is None:
            names = ", ".join(str(s.get("name")) for s in scenes) or "none"
            return _result(
                f"I couldn't find a scene called “{query}”. You have: {names}.",
                _home_card(devices, scenes, status="Home", summary="Pick a scene."),
                "activate_scene",
            )
        ok = await activate_scene(str(scene.get("scene_id")))
        name = scene.get("name") or "that scene"
        spoken = f"Activated {name}." if ok else f"I couldn't activate {name}."
        refreshed = await list_devices() or devices
        return _result(spoken, _home_card(refreshed, scenes, status="Scene", summary=spoken), "activate_scene")

    if action in ("turn_on", "turn_off", "set_brightness"):
        on = action != "turn_off"
        pct = int(getattr(intent, "duration_seconds", 0) or 0) or None
        # A TILE tap carries the exact HA entity id → control precisely that device,
        # never a fuzzy name match. Voice (no id) falls back to name selection.
        if entity_id:
            dev = next((d for d in devices if d.get("entity_id") == entity_id), None)
            if dev is None:
                return _result(
                    "That device isn't available right now.",
                    _home_card(devices, scenes, status="Home", summary="Device unavailable."),
                    action,
                )
            targets, ambiguous = [dev], False
        else:
            targets, ambiguous = _select_targets(devices, query, action, room_eids)
        # A SPECIFIC command tied across several devices — ask rather than toggle
        # unintended ones. (Bare plural "the lights" sweeps and is not ambiguous.)
        if ambiguous:
            names = ", ".join(str(d.get("name")) for d in targets)
            return _result(
                f"I found a few matches: {names}. Which one?",
                _home_card(devices, scenes, status="Home", summary="Which device?"),
                action,
            )
        if not targets:
            if query:
                spoken = f"I couldn't find “{query}” among your devices."
            else:
                spoken = "Which light or switch would you like me to control?"
            return _result(spoken, _home_card(devices, scenes, status="Home", summary="Pick a device."), action)
        # Never send control to an offline device — the bridge 200s regardless, so
        # we'd otherwise falsely report success on something that isn't there.
        live = [d for d in targets if d.get("available")]
        if not live:
            nm = targets[0].get("name") or "that device"
            return _result(
                f"{nm} looks offline right now.",
                _home_card(devices, scenes, status="Home", summary=f"{nm} is offline."),
                action,
            )
        targets = live
        ok_any = False
        for d in targets:
            if await set_device(str(d.get("entity_id")), on, brightness_pct=pct if on else None):
                ok_any = True
        refreshed = await list_devices() or devices
        label = "dimmed" if action == "set_brightness" else ("on" if on else "off")
        if len(targets) == 1:
            nm = targets[0].get("name") or "that"
            spoken = f"Turned {nm} {label}." if ok_any else f"I couldn't reach {nm}."
        else:
            spoken = f"Turned {len(targets)} devices {label}." if ok_any else "I couldn't reach those devices."
        return _result(spoken, _home_card(refreshed, scenes, status="Home", summary=spoken), action)

    # status / "show me the lights" / "smart home"
    summary = _summarize(devices)
    climate = await get_climate()
    return _result(summary, _home_card(devices, scenes, status="Home", summary=summary, climate=climate), "status")
