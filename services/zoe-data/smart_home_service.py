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

# Tokens that are grammar, not device identity — stripped before matching a
# spoken/tapped target ("turn off the kitchen lamp switch" → {kitchen}).
_MATCH_STOPWORDS = {
    "the", "a", "an", "my", "our", "please", "zoe",
    "turn", "switch", "flip", "set", "dim", "brighten", "put",
    "on", "off", "to", "up", "down", "is", "are", "all",
    "light", "lights", "lamp", "lamps", "plug", "plugs",
    "outlet", "outlets", "fan", "fans", "scene", "scenes",
    "script", "run", "activate", "start", "trigger", "enable",
}


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
    state = _norm_state(row.get("state"))
    return {
        "entity_id": row.get("entity_id", ""),
        "name": _friendly_name(row),
        "domain": "switch",
        "state": state,
        "on": state == "on",
        "available": state not in ("", "unavailable", "unknown"),
        "dimmable": False,
        "brightness": None,
    }


def _friendly_name(row: dict[str, Any]) -> str:
    name = str(row.get("name") or row.get("friendly_name") or "").strip()
    if name:
        return name
    # Fall back to a humanized entity id ("switch.living_room" → "Living Room").
    ent = str(row.get("entity_id") or "device")
    tail = ent.split(".", 1)[-1]
    return tail.replace("_", " ").title() or "Device"


async def list_devices() -> Optional[list[dict[str, Any]]]:
    """Combined lights + switches as normalized device dicts. None = bridge down."""
    lights = await _ha_get("/lights")
    switches = await _ha_get("/switches")
    if lights is None and switches is None:
        return None  # bridge unreachable — caller renders the offline card
    devices: list[dict[str, Any]] = []
    for row in (lights or {}).get("lights", []) if isinstance(lights, dict) else []:
        devices.append(_device_from_light(row))
    for row in (switches or {}).get("switches", []) if isinstance(switches, dict) else []:
        devices.append(_device_from_switch(row))
    return devices


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


def _match_devices(devices: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    """Return devices whose name/entity matches the query tokens. An empty query
    (generic "the lights") matches every light-domain device."""
    want = _tokens(query)
    if not want:
        lights = [d for d in devices if d.get("domain") == "light"]
        return lights or list(devices)
    scored: list[tuple[int, dict[str, Any]]] = []
    for d in devices:
        hay = f"{d.get('name', '')} {d.get('entity_id', '')}".lower()
        hits = sum(1 for tok in want if tok in hay)
        if hits:
            scored.append((hits, d))
    if not scored:
        return []
    best = max(s for s, _ in scored)
    return [d for s, d in scored if s == best]


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
    domain noun ("switch"/"light") so the classifier's device anchor matches on
    the round-trip even for oddly-named entities."""
    verb = "turn off" if device.get("on") else "turn on"
    noun = "light" if device.get("domain") == "light" else "switch"
    return f"{verb} the {device.get('name', 'device')} {noun}"


def _scene_query(scene: dict[str, Any]) -> str:
    return f"activate the {scene.get('name', 'scene')} scene"


def _home_card(
    devices: list[dict[str, Any]],
    scenes: list[dict[str, Any]],
    *,
    title: str = "Home",
    status: str = "Home",
    summary: str = "",
) -> dict[str, Any]:
    tiles = [
        {
            "name": d.get("name"),
            "entity_id": d.get("entity_id"),
            "domain": d.get("domain"),
            "state": d.get("state"),
            "on": bool(d.get("on")),
            "available": bool(d.get("available")),
            "dimmable": bool(d.get("dimmable")),
            "brightness": d.get("brightness"),
            "query": _device_query(d),
        }
        for d in devices
    ]
    scene_tiles = [
        {"name": s.get("name"), "scene_id": s.get("scene_id"), "query": _scene_query(s)}
        for s in scenes
    ]
    return {
        "component": "smart_home",
        "props": {
            "title": title,
            "status": status,
            "summary": summary,
            "devices": tiles,
            "scenes": scene_tiles,
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
        return "No devices are set up yet."
    if not on:
        return "Everything is off."
    names = ", ".join(str(d.get("name")) for d in on[:3])
    return f"{len(on)} on: {names}." if len(on) > 1 else f"{names} is on."


# ── Skybridge resolver ────────────────────────────────────────────────────────

async def resolve_smart_home(intent: Any) -> dict[str, Any]:
    """The Skybridge smart_home domain resolver. `intent` has .action, .query,
    and (for brightness) .duration_seconds carrying the target percent."""
    action = getattr(intent, "action", "status")
    query = (getattr(intent, "query", "") or "").strip()

    devices = await list_devices()
    if devices is None:
        return _result("I couldn't reach the home hub.", _offline_card(), action)
    scenes = await list_scenes()

    if action == "activate_scene":
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
        targets = _match_devices(devices, query)
        if not targets:
            return _result(
                f"I couldn't find “{query}” among your devices.",
                _home_card(devices, scenes, status="Home", summary="No matching device."),
                action,
            )
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
    return _result(summary, _home_card(devices, scenes, status="Home", summary=summary), "status")
