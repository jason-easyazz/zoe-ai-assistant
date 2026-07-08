"""routers/music_setup — the QR→phone "add a music source through Zoe" flow.

- POST /api/music/setup/start   (panel) → mint a one-time token + QR for a provider
- GET  /api/music/setup/catalogue (panel) → the "Add music" list
- GET  /api/music/setup/form     (phone) → validate token → the provider's form schema
- POST /api/music/setup/save     (phone) → validate+consume token → save to MA

The panel calls /start behind the normal panel auth. The phone endpoints are
gated ONLY by the one-time setup token (the phone is unauthenticated), so they
must never do anything but read a provider's public form schema and save the
values the user just typed for THAT provider.
"""
from __future__ import annotations

import io
import logging
import os
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import Response

import music_service
import music_setup
from auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/music/setup", tags=["music-setup"])


def _base_url() -> str:
    # The LAN HTTPS origin the phone will hit (same wifi as the panel).
    return os.environ.get("ZOE_PUBLIC_URL", "https://192.168.1.218").rstrip("/")


def _setup_url(token: str, provider: str) -> str:
    # Token + provider in the fragment (never sent to the server in logs/referers
    # until the page deliberately posts them).
    return f"{_base_url()}/setup-music.html#provider={provider}&t={token}"


@router.get("/catalogue")
async def setup_catalogue(user: dict = Depends(get_current_user)) -> dict[str, Any]:
    """The 'Add music' list for the panel."""
    return {"providers": await music_service.provider_catalogue()}


@router.post("/start")
async def setup_start(payload: dict, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    """Panel: mint a one-time setup token + QR for a provider."""
    provider = str((payload or {}).get("provider") or "").strip()
    form = await music_service.provider_setup_form(provider)
    if form is None:
        return {"ok": False, "reason": "unknown provider"}
    # Free providers don't need the phone — enable immediately.
    if form.get("auth") == "free":
        saved = await music_service.save_provider(provider, {})
        return {"ok": bool(saved), "provider": provider, "immediate": True}
    minted = music_setup.mint(provider, user.get("user_id", ""))
    return {
        "ok": True,
        "provider": provider,
        "auth": form.get("auth"),
        "setup_url": _setup_url(minted["token"], provider),
        "qr_path": f"/api/music/setup/qr?token={minted['token']}&provider={provider}",
        "expires_in": minted["expires_in"],
    }


@router.get("/qr")
async def setup_qr(token: str = "", provider: str = "") -> Response:
    """QR image (SVG) for the setup URL — rendered on the panel. Token is opaque
    here; the image just encodes the URL."""
    if not music_setup.verify(token):
        return Response(status_code=404)
    import segno
    buf = io.BytesIO()
    segno.make(_setup_url(token, provider), error="m").save(
        buf, kind="svg", scale=1, border=2, dark="#0b1020", light="#ffffff")
    return Response(content=buf.getvalue(), media_type="image/svg+xml",
                    headers={"Cache-Control": "no-store"})


# ── Phone endpoints — gated ONLY by the one-time token ───────────────────────

@router.get("/form")
async def setup_form(token: str = "", provider: str = "") -> dict[str, Any]:
    """Phone: the provider's setup form (fields to fill). Token must be valid."""
    payload = music_setup.verify(token)
    if payload is None or payload.get("p") != provider:
        return {"ok": False, "reason": "invalid or expired setup link"}
    form = await music_service.provider_setup_form(provider)
    if form is None:
        return {"ok": False, "reason": "unknown provider"}
    return {"ok": True, "form": form}


@router.post("/save")
async def setup_save(payload: dict) -> dict[str, Any]:
    """Phone: save the values the user typed. Consumes the one-time token."""
    token = str((payload or {}).get("token") or "")
    provider = str((payload or {}).get("provider") or "")
    values = (payload or {}).get("values") or {}
    tok = music_setup.consume(token)  # single-use: spent here
    if tok is None or tok.get("p") != provider:
        return {"ok": False, "reason": "invalid or already-used setup link"}
    if not isinstance(values, dict):
        return {"ok": False, "reason": "bad values"}
    saved = await music_service.save_provider(provider, values)
    if not saved:
        return {"ok": False, "reason": "couldn't connect — check your details"}
    return {"ok": True, "provider": provider, "name": saved.get("name") or provider}
