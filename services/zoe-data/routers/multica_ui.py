"""routers/multica_ui - the auth_request target + cookie minter for the Multica board (multica_ui_gate.py).

- GET  /api/multica-ui/gate     nginx ``auth_request`` for ``/multica/``, ``/multica-api/`` and ``/_next/``: 204 for a valid
                                cookie or an adult member's own session, 401 for no / an invalid / a guest credential, 403 for
                                a signed-in member who may not use the board (a child, an unknown role, the panel). Never
                                returns a body or a secret.
- POST /api/multica-ui/session  an adult member (normal credentials) -> sets the path-scoped cookies the gate accepts.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response

import multica_ui_gate
from auth import get_current_user

router = APIRouter(prefix="/api/multica-ui", tags=["multica"])

_NO_STORE = {"Cache-Control": "no-store"}
_SIGN_IN = "Sign in to Zoe to open the board"
_NOT_FOR_YOU = "The board is for adult household members"


async def _decide(request: Request, user: dict) -> None:
    """Raise 401 / 403 unless ``user`` may use the board."""
    if not multica_ui_gate.is_signed_in(user):
        raise HTTPException(status_code=401, detail=_SIGN_IN, headers=_NO_STORE)
    if not await multica_ui_gate.adult_member(user):
        raise HTTPException(status_code=403, detail=_NOT_FOR_YOU, headers=_NO_STORE)


@router.get("/gate")
async def gate(request: Request) -> Response:
    """Answer nginx's ``auth_request``. The cookie is checked first so a page full of Next.js assets costs one HMAC each,
    not a session lookup; credentials are resolved only when there is no valid cookie. An invalid session is a 401 (raised
    by ``get_current_user``); zoe-auth being down is a 503, which nginx turns into a 500 - closed."""
    if not multica_ui_gate.enabled() or multica_ui_gate.cookie_valid(request):
        return Response(status_code=204, headers=_NO_STORE)
    await _decide(request, await get_current_user(request))
    return Response(status_code=204, headers=_NO_STORE)


@router.post("/session")
async def session(request: Request, response: Response, user: dict = Depends(get_current_user)) -> dict:
    """Set the board cookies for an adult member."""
    await _decide(request, user)
    minted = multica_ui_gate.mint(str((user or {}).get("user_id") or ""))
    secure = str(request.headers.get("x-forwarded-proto") or request.url.scheme or "").lower() == "https"
    for path in multica_ui_gate.COOKIE_PATHS:
        response.set_cookie(
            multica_ui_gate.COOKIE, minted["token"], max_age=minted["expires_in"], path=path,
            secure=secure, httponly=True, samesite="strict",
        )
    response.headers["Cache-Control"] = "no-store"
    return {"ok": True, "expires_in": minted["expires_in"]}
