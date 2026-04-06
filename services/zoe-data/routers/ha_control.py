"""
Home Assistant direct control endpoint.

Allows the touch panel to control HA entities (lights, switches, media players)
directly without going through the full chat → OpenClaw pipeline.
Authenticates via session OR device token (same as voice endpoints).

Routes:
  POST /api/ha/control         — call a HA service (toggle, turn_on, turn_off, etc.)
  GET  /api/ha/entities        — list entities for a domain / area
  GET  /api/ha/state/{entity}  — get current state of a single entity
"""
import logging
import os
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException

from auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/ha", tags=["ha-control"])

_HA_BRIDGE = os.environ.get("ZOE_HA_BRIDGE_URL", "http://127.0.0.1:8007")
_TIMEOUT = 10.0

# ── auth: session or device token ────────────────────────────────────────────

async def _require_caller(user: dict = Depends(get_current_user)) -> dict:
    """Accept any authenticated caller (session user or device token)."""
    return user


# ── helpers ───────────────────────────────────────────────────────────────────

async def _bridge_get(path: str) -> dict:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.get(f"{_HA_BRIDGE}{path}")
        r.raise_for_status()
        return r.json()


async def _bridge_post(path: str, body: dict) -> dict:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.post(f"{_HA_BRIDGE}{path}", json=body)
        r.raise_for_status()
        return r.json()


# ── routes ────────────────────────────────────────────────────────────────────

@router.get("/entities")
async def list_entities(
    domain: Optional[str] = None,
    area: Optional[str] = None,
    caller: dict = Depends(_require_caller),
):
    """Return entity list from the HA bridge, optionally filtered by domain/area."""
    try:
        data = await _bridge_get("/entities")
        entities = data if isinstance(data, list) else data.get("entities", [])
        if domain:
            entities = [e for e in entities if str(e.get("entity_id", "")).startswith(domain + ".")]
        if area:
            entities = [e for e in entities if str(e.get("area_id", "") or e.get("area", "")).lower() == area.lower()]
        return {"entities": entities, "count": len(entities)}
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="HA bridge offline")
    except Exception as exc:
        logger.error("ha/entities error: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/state/{entity_id:path}")
async def get_entity_state(entity_id: str, caller: dict = Depends(_require_caller)):
    """Get current state of a single HA entity."""
    try:
        return await _bridge_get(f"/state/{entity_id}")
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="HA bridge offline")
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=exc.response.status_code, detail=f"HA bridge: {exc}")
    except Exception as exc:
        logger.error("ha/state error entity=%s: %s", entity_id, exc)
        raise HTTPException(status_code=502, detail=str(exc))


@router.post("/control")
async def ha_control(payload: dict, caller: dict = Depends(_require_caller)):
    """
    Call a HA service directly from the touch panel.

    Body:
      { "entity_id": "light.living_room", "action": "toggle" }
      { "entity_id": "light.kitchen", "action": "turn_on", "params": { "brightness_pct": 80 } }
      { "domain": "scene", "service": "turn_on", "entity_id": "scene.movie_time" }
    """
    entity_id = (payload.get("entity_id") or "").strip()
    action = (payload.get("action") or payload.get("service") or "").strip()
    params = payload.get("params") or payload.get("service_data") or {}

    if not entity_id:
        raise HTTPException(status_code=400, detail="entity_id is required")

    # Resolve domain from entity_id if not provided
    domain = payload.get("domain") or (entity_id.split(".")[0] if "." in entity_id else "")
    if not domain:
        raise HTTPException(status_code=400, detail="Cannot determine domain from entity_id")

    # Map friendly action names to HA service names
    action_map = {
        "toggle": "toggle",
        "on": "turn_on", "turn_on": "turn_on",
        "off": "turn_off", "turn_off": "turn_off",
        "open": "open_cover", "close": "close_cover",
        "play": "media_play", "pause": "media_pause",
        "next": "media_next_track", "previous": "media_previous_track",
        "volume_up": "volume_up", "volume_down": "volume_down",
    }
    service = action_map.get(action, action)
    if not service:
        raise HTTPException(status_code=400, detail=f"Unknown action: {action}")

    try:
        body = {"entity_id": entity_id, "domain": domain, "service": service, "service_data": params}
        result = await _bridge_post("/service", body)
        logger.info("ha/control entity=%s service=%s caller=%s", entity_id, service, caller.get("user_id"))
        return {"ok": True, "entity_id": entity_id, "service": service, "result": result}
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="HA bridge offline")
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=exc.response.status_code, detail=f"HA bridge: {exc.response.text}")
    except Exception as exc:
        logger.error("ha/control error: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc))
