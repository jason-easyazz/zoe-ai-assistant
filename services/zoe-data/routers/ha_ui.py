"""routers/ha_ui - the gate in front of Home Assistant's own web UI, REST API and WebSocket through nginx (``/ha/``).

nginx proxied ``/ha/`` straight to Home Assistant, so anyone who reached Zoe's nginx (the LAN, and the tunnel) reached HA's login
flow (``/ha/auth/*``), its REST API, its WebSocket (``/ha/api/websocket``) and its unauthenticated webhook endpoints with no Zoe
check at all. It is now an ``auth_request``: nginx asks ``GET /api/ha-ui/gate`` before every request to the path (HTTP and the
WebSocket handshake alike) and proxies only on a 2xx. Home Assistant's own login still applies behind it.

- GET  /api/ha-ui/gate     nginx ``auth_request``: 204 for a valid ``zoe_ha_ui`` cookie or a member / the panel's own
                           credentials, else 401. Never returns a body or a secret.
- POST /api/ha-ui/session  a member or the panel (normal credentials) -> sets the cookie, scoped to ``/ha/`` only.

Who passes: ``panel_principal.member_or_panel`` - a signed-in member, or the panel in its strict ``registered`` form (device
token, kiosk credential, registered address; never over the Cloudflare tunnel). ``ZOE_HA_CONTROL_SCOPE=open`` does NOT open
this. ``ZOE_HA_UI_GATE=off`` is the rollback for this gate alone (read per request; the gate then answers 204 to all).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response

import panel_principal
from auth import get_current_user
from path_cookie_gate import PathCookieGate, derived_key
from typed_env import env_bool, env_int, env_str

router = APIRouter(prefix="/api/ha-ui", tags=["home-assistant"])

_NO_STORE = {"Cache-Control": "no-store"}
_SIGN_IN = "Sign in, or use the wall panel"


def enabled() -> bool:
    """``ZOE_HA_UI_GATE`` (default on). Off = the gate answers 204 to everyone: the pre-2026-10-19 behaviour, an operator
    ROLLBACK only. Read per request."""
    return env_bool("ZOE_HA_UI_GATE", default=True)


def ttl_s() -> int:
    """Cookie lifetime (``ZOE_HA_UI_TTL_S``, default 8 h, floor 5 min). A Zoe sign-out does not revoke a minted cookie; this
    bound is the revocation."""
    return max(300, env_int("ZOE_HA_UI_TTL_S", 28800))


def _key():
    return derived_key(env_str("ZOE_HA_UI_SECRET"), env_str("ZOE_INTERNAL_TOKEN"), "zoe-ha-ui-v1")


GATE = PathCookieGate(
    purpose="ha-ui", cookie="zoe_ha_ui", paths=("/ha/",), secret_env="ZOE_HA_UI_SECRET",
    key=_key, ttl_s=ttl_s, enabled=enabled,
)


@router.get("/gate")
async def gate(request: Request) -> Response:
    """Answer nginx's ``auth_request``. The cookie is checked first so a page of HA assets costs one HMAC each, not a session
    lookup. An invalid session is a 401 (``get_current_user``); zoe-auth down is a 503, which nginx turns into a 500 - closed."""
    if not GATE.enabled() or GATE.cookie_valid(request):
        return Response(status_code=204, headers=_NO_STORE)
    user = await get_current_user(request)
    if await panel_principal.member_or_panel(request, user):
        return Response(status_code=204, headers=_NO_STORE)
    raise HTTPException(status_code=401, detail=_SIGN_IN, headers=_NO_STORE)


@router.post("/session")
async def session(request: Request, response: Response, user: dict = Depends(get_current_user)) -> dict:
    """Set the Home Assistant UI cookie for a signed-in member or the panel."""
    if not await panel_principal.member_or_panel(request, user):
        raise panel_principal._refuse("neither a member nor the panel", request, user, _SIGN_IN)
    minted = GATE.mint(str((user or {}).get("user_id") or ""))
    GATE.set_cookies(request, response, minted)
    return {"ok": True, "expires_in": minted["expires_in"]}
