"""routers/handoff — the panel side of the app-connection handoff (``auth_handoff``, B7.5).

- POST /api/handoff/start                 (panel, member) → a handoff + its QR path
- GET  /api/handoff/{id}/status           (panel, owner)  → the live status (fallback to the push)
- POST /api/handoff/{id}/send-to-phone    (panel, owner)  → the phone link via Telegram
- GET  /api/handoff/{id}/qr/{handle}      (panel <img>)   → the QR, by single-use handle

Every panel route needs a signed-in MEMBER session (``require_signed_in``: an
anonymous kiosk and a degraded-auth principal both resolve to guest and are
refused), and only the member who started a handoff may read or send it. The phone never calls these — it finishes on the provider flow's
own phone page (``setup-music.html``), gated by that flow's one-time token in the
URL ``#fragment`` + ``X-Setup-Token`` header. No route here takes a token, and
none takes anything secret in a query string (pinned by
``tests/test_setup_token_not_in_query.py``).
"""
from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response

import auth_handoff
import music_service
import setup_qr
from auth import require_signed_in

router = APIRouter(prefix="/api/handoff", tags=["handoff"])

_PANEL_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")


async def _owned(handoff_id: str, user: dict) -> None:
    owner = await auth_handoff.owner_of(handoff_id)
    if owner is None:
        raise HTTPException(status_code=404, detail="Unknown handoff")
    if owner != user.get("user_id"):
        raise HTTPException(status_code=403, detail="Not your handoff")


@router.post("/start")
async def handoff_start(payload: dict, request: Request,
                        user: dict = Depends(require_signed_in)) -> dict[str, Any]:
    """Panel: start connecting ``provider`` for the signed-in member."""
    kind = str((payload or {}).get("kind") or "").strip()
    provider = str((payload or {}).get("provider") or "").strip()
    panel_id = str((payload or {}).get("panel_id") or "").strip()
    if not _PANEL_ID.match(panel_id):
        raise HTTPException(status_code=400, detail="panel_id required")
    if kind != "music":
        raise HTTPException(status_code=400, detail="unknown kind")
    form = await music_service.provider_setup_form(provider)
    if form is None:
        raise HTTPException(status_code=400, detail="unknown provider")
    if form.get("auth") == "free":  # no account, no phone step (radio)
        saved = await music_service.save_provider(provider, {})
        return {"ok": bool(saved), "provider": provider, "immediate": True}
    try:
        h = await auth_handoff.start(kind, actor_user=user, panel_id=panel_id, provider=provider,
                                     base_url=auth_handoff.phone_base_url(request))
    except auth_handoff.HandoffError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    h.pop("phone_url", None)  # the token-bearing link never goes back to the panel
    h.pop("qr_handle", None)
    return {"ok": True, "auth": form.get("auth"), **h}


@router.get("/{handoff_id}/status")
async def handoff_status(handoff_id: str, user: dict = Depends(require_signed_in)) -> dict[str, Any]:
    await _owned(handoff_id, user)
    view = await auth_handoff.status(handoff_id)
    if view is None:
        raise HTTPException(status_code=404, detail="Unknown handoff")
    return {"ok": True, **view}


@router.post("/{handoff_id}/send-to-phone")
async def handoff_send(handoff_id: str, user: dict = Depends(require_signed_in)) -> dict[str, Any]:
    await _owned(handoff_id, user)
    return await auth_handoff.send_to_phone(handoff_id)


@router.get("/{handoff_id}/qr/{handle}")
async def handoff_qr(handoff_id: str, handle: str) -> Response:
    """The QR image. The single-use handle IS the capability (same model as
    ``/api/music/setup/qr/{handle}``); a spent, expired or foreign handle is 404."""
    url = auth_handoff.qr_url(handoff_id, handle)
    if url is None:
        return Response(status_code=404)
    return Response(content=setup_qr.render_svg(url), media_type="image/svg+xml",
                    headers={"Cache-Control": "no-store"})
