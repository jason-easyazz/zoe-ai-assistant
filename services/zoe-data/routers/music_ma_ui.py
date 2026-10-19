"""routers/music_ma_ui - the auth_request target + cookie minter for Music Assistant's own web UI (ma_ui_gate.py).

- GET  /api/music/ma-ui/gate     nginx ``auth_request`` for ``/modules/music-assistant/``: 204 for a valid cookie or a member / the
                                 panel's own credentials, else 401. Never returns a body or a secret.
- POST /api/music/ma-ui/session  a member or the panel (normal credentials) -> sets the path-scoped cookie the gate accepts.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response

import ma_ui_gate
import panel_principal
from auth import get_current_user

router = APIRouter(prefix="/api/music/ma-ui", tags=["music"])


@router.get("/gate")
async def gate(request: Request) -> Response:
    """Answer nginx's ``auth_request``. The cookie is checked first so a page full of MA assets costs one HMAC each, not a
    session lookup; credentials are resolved only when there is no valid cookie."""
    if not ma_ui_gate.enabled() or ma_ui_gate.cookie_valid(request):
        return Response(status_code=204, headers={"Cache-Control": "no-store"})
    user = await get_current_user(request)
    if await ma_ui_gate.principal_ok(request, user):
        return Response(status_code=204, headers={"Cache-Control": "no-store"})
    raise HTTPException(status_code=401, detail="Sign in, or use the wall panel")


@router.post("/session")
async def session(request: Request, response: Response, user: dict = Depends(get_current_user)) -> dict:
    """Set the Music Assistant UI cookie for a signed-in member or the panel."""
    if not await ma_ui_gate.principal_ok(request, user):
        raise panel_principal._refuse("neither a member nor the panel", request, user, "Sign in, or use the wall panel")
    minted = ma_ui_gate.mint(str((user or {}).get("user_id") or ""))
    secure = str(request.headers.get("x-forwarded-proto") or request.url.scheme or "").lower() == "https"
    response.set_cookie(
        ma_ui_gate.COOKIE, minted["token"], max_age=minted["expires_in"], path=ma_ui_gate.COOKIE_PATH,
        secure=secure, httponly=True, samesite="strict",
    )
    response.headers["Cache-Control"] = "no-store"
    return {"ok": True, "expires_in": minted["expires_in"]}
