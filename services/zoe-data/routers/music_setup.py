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

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response

import music_service
import music_setup
from auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/music/setup", tags=["music-setup"])


def _base_url(request: "Request") -> str:
    # The LAN HTTPS origin the phone will hit — same wifi as the panel, so the
    # host the panel loaded from IS reachable by the phone. Prefer an explicit
    # override; else derive from the incoming request (never a hardcoded IP).
    override = os.environ.get("ZOE_PUBLIC_URL", "").strip()
    if override:
        return override.rstrip("/")
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
    scheme = request.headers.get("x-forwarded-proto") or request.url.scheme or "https"
    if host:
        return f"{scheme}://{host}".rstrip("/")
    return str(request.base_url).rstrip("/")


def _setup_url(request: "Request", token: str, provider: str) -> str:
    # Token + provider in the fragment (never sent to the server in logs/referers
    # until the page deliberately posts them).
    return f"{_base_url(request)}/setup-music.html#provider={provider}&t={token}"


@router.get("/catalogue")
async def setup_catalogue(user: dict = Depends(get_current_user)) -> dict[str, Any]:
    """The 'Add music' list for the panel."""
    return {"providers": await music_service.provider_catalogue()}


@router.post("/start")
async def setup_start(payload: dict, request: Request, user: dict = Depends(get_current_user)) -> dict[str, Any]:
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
        "setup_url": _setup_url(request, minted["token"], provider),
        "qr_path": f"/api/music/setup/qr?token={minted['token']}&provider={provider}",
        "expires_in": minted["expires_in"],
    }


@router.get("/qr")
async def setup_qr(request: Request, token: str = "", provider: str = "") -> Response:
    """QR image (SVG) for the setup URL — rendered on the panel. Token is opaque
    here; the image just encodes the URL."""
    if not music_setup.verify(token):
        return Response(status_code=404)
    import segno
    buf = io.BytesIO()
    segno.make(_setup_url(request, token, provider), error="m").save(
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
    # YouTube Music can't log in without Zoe's local PO-token generator running.
    # Check it here so a stopped helper reads as an accurate, actionable message
    # rather than a misleading "check your details" credential error.
    if provider == music_service._YTMUSIC_DOMAIN and not await music_service._potoken_reachable(
            music_service._ytmusic_potoken_url()):
        return {"ok": False, "reason": "The YouTube Music helper isn't running yet — "
                "ask your Zoe admin to start it, then try again."}
    saved = await music_service.save_provider(provider, values)
    if not saved:
        return {"ok": False, "reason": "couldn't connect — check your details"}
    return {"ok": True, "provider": provider, "name": saved.get("name") or provider}


# ── OAuth providers (Spotify etc.) — phone-driven, one-time-token gated ──────

@router.post("/oauth/start")
async def oauth_start(payload: dict) -> dict[str, Any]:
    """Phone: begin the provider's OAuth. Returns {oauth_id, auth_url} — the
    phone opens auth_url to sign in. Gated by the one-time setup token."""
    token = str((payload or {}).get("token") or "")
    provider = str((payload or {}).get("provider") or "")
    tok = music_setup.verify(token)
    if tok is None or tok.get("p") != provider:
        return {"ok": False, "reason": "invalid or expired setup link"}
    import music_oauth
    res = await music_oauth.start_oauth(provider)
    if not res.get("auth_url"):
        return {"ok": False, "reason": res.get("error") or "couldn't start sign-in"}
    return {"ok": True, "oauth_id": res["oauth_id"], "auth_url": res["auth_url"]}


@router.get("/oauth/status")
async def oauth_status(oauth_id: str = "", token: str = "") -> dict[str, Any]:
    """Phone: poll the OAuth attempt. Consumes the one-time token on success."""
    if music_setup.verify(token) is None:
        return {"ok": False, "state": "unknown"}
    import music_oauth
    st = music_oauth.oauth_status(oauth_id)
    if st.get("state") == "connected":
        music_setup.consume(token)  # spent once the account is connected
    return {"ok": True, **st}


# ── Browser sign-in (YouTube Music) — phone-driven, one-time-token gated ──────
# YouTube Music has no password/OAuth: the only link is a browser login cookie.
# /browser/start spins up ONE phone-drivable stealth-browser rig; the user signs
# into Google there (their password only touches Google); the backend auto-detects
# the login, harvests the cookie, saves it to MA, and tears the browser down.

@router.post("/browser/start")
async def browser_start(payload: dict) -> dict[str, Any]:
    """Phone: begin the embedded browser sign-in. Returns {session_id, view_url}.
    Gated by the one-time setup token; only offered for ytmusic."""
    token = str((payload or {}).get("token") or "")
    provider = str((payload or {}).get("provider") or "")
    tok = music_setup.verify(token)
    if tok is None or tok.get("p") != provider:
        return {"ok": False, "reason": "invalid or expired setup link"}
    if provider != music_service._YTMUSIC_DOMAIN:
        return {"ok": False, "reason": "browser sign-in isn't available for this service"}
    # The PO-token generator must be up or MA will reject the harvested cookie —
    # check now so a stopped helper reads as an accurate, actionable message.
    if not await music_service._potoken_reachable(music_service._ytmusic_potoken_url()):
        return {"ok": False, "reason": "The YouTube Music helper isn't running yet — "
                "ask your Zoe admin to start it, then try again."}
    import ytmusic_signin
    res = await ytmusic_signin.start_session()
    if not res.get("ok"):
        return {"ok": False, "reason": res.get("message") or "couldn't start sign-in"}
    return {"ok": True, "session_id": res["session_id"], "view_url": res["view_url"],
            "expires_in": res.get("expires_in")}


@router.get("/browser/status")
async def browser_status(session_id: str = "", token: str = "") -> dict[str, Any]:
    """Phone: poll the browser sign-in. Consumes the one-time token on success."""
    if music_setup.verify(token) is None:
        return {"ok": False, "state": "unknown"}
    import ytmusic_signin
    st = ytmusic_signin.session_status(session_id)
    if st.get("state") == "connected":
        music_setup.consume(token)  # spent once the account is connected
    return st


@router.post("/browser/cancel")
async def browser_cancel(payload: dict) -> dict[str, Any]:
    """Phone: user backed out — tear the sign-in browser down now."""
    token = str((payload or {}).get("token") or "")
    session_id = str((payload or {}).get("session_id") or "")
    if music_setup.verify(token) is None:
        return {"ok": False, "reason": "invalid or expired setup link"}
    if not session_id:
        # An empty id must never tear down the active session — a token holder
        # who doesn't know the session id can't cancel someone else's sign-in.
        return {"ok": False, "reason": "missing session id"}
    import ytmusic_signin
    return await ytmusic_signin.cancel_session(session_id)
