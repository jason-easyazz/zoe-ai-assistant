"""Kiosk credential endpoints: the panel's loopback gate turns its device token into a credential for the kiosk BROWSER.

  POST /api/panels/PANEL_ID/kiosk-ticket  - ``X-Device-Token`` for that panel -> a single-use ticket (JSON)
  POST /api/panels/kiosk-enter            - form body ``ticket`` (+ optional ``next``) -> sets the kiosk credential cookie,
                                            303 to the estate

Both 404 unless ``ZOE_PANEL_KIOSK_CREDENTIAL`` is on, and both refuse a request that crossed the Cloudflare tunnel. The
mechanics, the cookie and the rules live in ``panel_kiosk_credential``; the design is docs/architecture/panel-remote-access.md
section 4.2. ``kiosk-enter`` is reached by a top-level form POST from the gate page (http://127.0.0.1 on the panel), so it is a
cross-site POST by design: the single-use, 60 s ticket is the credential, not the origin.
"""
from __future__ import annotations

import logging
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

import panel_kiosk_credential as kiosk
from database import get_db
from routers.panel_auth import _hash_token, lookup_device_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/panels", tags=["panel-kiosk"])

_MAX_FORM_BYTES = 4096


def _require_enabled(request: Request) -> None:
    if not kiosk.enabled():
        raise HTTPException(status_code=404, detail="Not Found")
    if kiosk.via_public_tunnel(request):
        raise HTTPException(status_code=403, detail="The panel kiosk credential is never issued over the public tunnel")


async def _read_small_body(request: Request) -> bytes:
    """The form body, refused (413) past ``_MAX_FORM_BYTES`` without ever buffering more than that."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > _MAX_FORM_BYTES:
        raise HTTPException(status_code=413, detail="Request body too large")
    body = b""
    async for chunk in request.stream():
        body += chunk
        if len(body) > _MAX_FORM_BYTES:
            raise HTTPException(status_code=413, detail="Request body too large")
    return body


async def _panel_is_active(db, panel_id: str) -> bool:
    cur = await db.execute("SELECT 1 FROM panels WHERE panel_id = ? AND is_active = 1 LIMIT 1", (panel_id,))
    return await cur.fetchone() is not None


@router.post("/{panel_id}/kiosk-ticket")
async def kiosk_ticket(panel_id: str, request: Request, db=Depends(get_db)):
    """The gate's half: prove the device, get a ticket the kiosk browser can spend once."""
    _require_enabled(request)
    raw = request.headers.get("X-Device-Token", "")
    info = lookup_device_token(raw) if raw else None
    if not info or str(info.get("panel_id")) != panel_id:
        raise HTTPException(status_code=403, detail="A valid device token for this panel is required")
    if not await _panel_is_active(db, panel_id):
        raise HTTPException(status_code=403, detail="This panel is not active")
    ticket = kiosk.mint_ticket(panel_id, _hash_token(raw))
    logger.info("panel_kiosk: ticket minted for panel %s", panel_id)
    return dict(ok=True, ticket=ticket, expires_in=kiosk.ticket_ttl_s(), enter_path=kiosk.ENTER_PATH)


@router.post("/kiosk-enter")
async def kiosk_enter(request: Request, db=Depends(get_db)):
    """The browser's half: spend the ticket, set the credential, go to the estate. A bad ticket still lands on the estate (as a
    plain guest kiosk, exactly today's behaviour) so a slow boot never strands the screen on an error page; the refusal is logged."""
    _require_enabled(request)
    body = (await _read_small_body(request)).decode("utf-8", "replace")
    form = parse_qs(body, keep_blank_values=False)
    ticket = (form.get("ticket") or [""])[0]
    claims = kiosk.redeem_ticket(ticket) if ticket else None
    panel_id = str((claims or dict()).get("pid") or "")
    response = RedirectResponse(kiosk.safe_next((form.get("next") or [""])[0], panel_id), status_code=303)
    response.headers["Cache-Control"] = "no-store"
    if not claims:
        logger.warning("panel_kiosk: kiosk-enter refused a ticket (invalid, expired, spent or token revoked)")
        return response
    if not await _panel_is_active(db, panel_id):
        logger.warning("panel_kiosk: kiosk-enter refused - panel %s is not active", panel_id)
        return response
    response.set_cookie(
        kiosk.COOKIE_NAME,
        kiosk.mint_credential(panel_id, str(claims.get("th") or "")),
        max_age=kiosk.credential_ttl_s(),
        path="/",
        secure=True,
        httponly=True,
        samesite="strict",
    )
    logger.info("panel_kiosk: kiosk credential set for panel %s", panel_id)
    return response
