"""
Home Assistant direct control endpoint.

Allows the touch panel to control HA entities (lights, switches, media players)
directly without going through the full chat pipeline.

Auth (security fix 2026-10-17): every route needs a signed-in MEMBER or THE PANEL
(``panel_principal.may_control_home``: a device token, the kiosk credential, or a
registered panel address). ``get_current_user`` resolves a credential-less request
as ``guest``, so "any principal" used to mean any device on the home LAN. An
anonymous LAN guest, a forged X-Real-IP / X-Forwarded-For, a Cloudflare-tunnel
request without a member and an auth-outage principal get 403. The entity reads are
gated too: HA state carries presence (person/device_tracker incl. GPS), lock and
alarm state and camera access tokens. ``ZOE_HA_CONTROL_SCOPE=open`` is the operator
rollback to the old behaviour (default ``panel``).

Routes:
  POST /api/ha/control        — call a HA service (toggle, turn_on, turn_off, etc.)
  GET  /api/ha/entities       — list entities for a domain / area
  GET  /api/ha/state/{entity} — get current state of a single entity
"""
import logging
import os
import re
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

import panel_principal
from auth import get_current_user
from log_throttle import log_throttled, log_upstream_failure

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/ha", tags=["ha-control"])



def _ent(value: object) -> str:
    """Bounded, single-line rendering of a caller-supplied id for a log line."""
    return str(value).replace("\n", " ")[:120]


_HA_BRIDGE = os.environ.get("ZOE_HA_BRIDGE_URL", "http://127.0.0.1:8007")
_TIMEOUT = 10.0


class HAControlPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    entity_id: str | None = Field(default=None, max_length=256)
    action: str | None = Field(default=None, max_length=128)
    service: str | None = Field(default=None, max_length=128)
    params: dict[str, Any] | None = Field(default=None, max_length=100)
    service_data: dict[str, Any] | None = Field(default=None, max_length=100)

# ── auth: a member or the panel, never a plain anonymous guest ──────────────

_REFUSED = "Sign in, or use the wall panel, to control the house"
# Home Assistant entity ids are ``domain.object_id`` (lower-case, digits, underscore). Anything else is refused before it can
# reach the loopback-only bridge as a path (``/entities/{id}``).
_ENTITY_ID_RE = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")


async def _require_caller(request: Request, user: dict = Depends(get_current_user)) -> dict:
    """A signed-in member or the panel (``panel_principal.may_control_home``); anyone else gets 403."""
    if await panel_principal.may_control_home(request, user):
        return user
    log_throttled(
        logger, logging.WARNING, f"ha-refused:{request.method}",
        "ha refused: %s %s from a caller that is neither a member nor the panel (user=%s)",
        request.method, request.url.path, _ent((user or {}).get("user_id")),
    )
    raise HTTPException(status_code=403, detail=_REFUSED)


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
        log_upstream_failure(logger, "ha/entities error", exc)
        raise HTTPException(status_code=502, detail="HA bridge request failed")


@router.get("/state/{entity_id:path}")
async def get_entity_state(entity_id: str, caller: dict = Depends(_require_caller)):
    """Get current state of a single HA entity."""
    if not _ENTITY_ID_RE.match(entity_id or ""):
        raise HTTPException(status_code=400, detail="Invalid entity_id")
    try:
        return await _bridge_get(f"/entities/{entity_id}")
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="HA bridge offline")
    except httpx.HTTPStatusError as exc:
        log_upstream_failure(logger, f"ha/state bridge status error entity={_ent(entity_id)}", exc)
        raise HTTPException(status_code=exc.response.status_code, detail="HA bridge request failed")
    except Exception as exc:
        log_upstream_failure(logger, f"ha/state error entity={_ent(entity_id)}", exc)
        raise HTTPException(status_code=502, detail="HA bridge request failed")


@router.post("/control")
async def ha_control(payload: HAControlPayload, caller: dict = Depends(_require_caller)):
    """
    Call a HA service directly from the touch panel.

    Body:
      { "entity_id": "light.living_room", "action": "toggle" }
      { "entity_id": "light.kitchen", "action": "turn_on", "params": { "brightness_pct": 80 } }
      { "domain": "scene", "service": "turn_on", "entity_id": "scene.movie_time" }
    """
    entity_id = (payload.entity_id or "").strip()
    action = (payload.action or payload.service or "").strip()
    params = payload.params or payload.service_data or {}
    if not entity_id:
        raise HTTPException(status_code=400, detail="entity_id is required")

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
        body = {"entity_id": entity_id, "action": service, "data": params}
        result = await _bridge_post("/devices/control", body)
        logger.info("ha/control entity=%s service=%s caller=%s", entity_id, service, caller.get("user_id"))
        return {"ok": True, "entity_id": entity_id, "service": service, "result": result}
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="HA bridge offline")
    except httpx.HTTPStatusError as exc:
        log_upstream_failure(logger, f"ha/control bridge status error entity={_ent(entity_id)} service={_ent(service)}", exc)
        raise HTTPException(status_code=exc.response.status_code, detail="HA bridge request failed")
    except Exception as exc:
        log_upstream_failure(logger, f"ha/control error entity={_ent(entity_id)} service={_ent(service)}", exc)
        raise HTTPException(status_code=502, detail="HA bridge request failed")
