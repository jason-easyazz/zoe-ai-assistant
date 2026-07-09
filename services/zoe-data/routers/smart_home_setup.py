"""routers/smart_home_setup — the QR→phone "add a device to your home" flow.

- GET /api/home/setup/qr    (panel) → QR image encoding the phone setup URL
- GET /api/home/setup/info  (phone) → validate token → the branded setup guide

The panel mints the token in the smart_home resolver (smart_home_service._add_device_card)
and shows the QR. The phone endpoint is gated ONLY by the one-time setup token
(the phone is unauthenticated), and it does nothing but return static, public
guidance — Home Assistant runs headless and its MCP bridge exposes no
config-flow/pairing endpoint, so there is deliberately nothing here that mutates
the home. Full auto-discovery is deferred until the bridge grows a pairing API.
"""
from __future__ import annotations

import io
import logging
import os
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import Response

import smart_home_setup

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/home/setup", tags=["home-setup"])


def _base_url(request: "Request") -> str:
    # The LAN HTTPS origin the phone will hit — same wifi as the panel. Prefer an
    # explicit override; else derive from the incoming request (never a hardcoded IP).
    override = os.environ.get("ZOE_PUBLIC_URL", "").strip()
    if override:
        return override.rstrip("/")
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
    scheme = request.headers.get("x-forwarded-proto") or request.url.scheme or "https"
    if host:
        return f"{scheme}://{host}".rstrip("/")
    return str(request.base_url).rstrip("/")


def _setup_url(request: "Request", token: str) -> str:
    # Token in the fragment (never sent to the server in logs/referers until the
    # page deliberately reads it).
    return f"{_base_url(request)}/setup-device.html#t={token}"


# Static, branded, HA-jargon-free guidance. Grandma-friendly steps per device
# type. Honest: none of this completes pairing automatically today.
_DEVICE_TYPES: list[dict[str, Any]] = [
    {
        "id": "light",
        "label": "Smart light or lamp",
        "emoji": "💡",
        "steps": [
            "Screw the smart bulb in (or plug the lamp into a smart plug) and switch it on at the wall.",
            "Open the bulb maker's app once to put it into pairing mode — the light will usually blink.",
            "Ask your Zoe helper to add it to your home hub, then say “Zoe, show my home.”",
        ],
    },
    {
        "id": "plug",
        "label": "Smart plug or switch",
        "emoji": "🔌",
        "steps": [
            "Plug the smart plug into the wall and turn whatever it powers on.",
            "Hold its button until the light blinks — that's pairing mode.",
            "Ask your Zoe helper to add it, then say “Zoe, show my home.”",
        ],
    },
    {
        "id": "speaker",
        "label": "Speaker or TV",
        "emoji": "🔊",
        "steps": [
            "Make sure the speaker or TV is on the same Wi-Fi as this screen.",
            "Ask your Zoe helper to connect it to your home hub.",
            "Say “Zoe, show my home” to see it appear.",
        ],
    },
    {
        "id": "sensor",
        "label": "Sensor (motion, door, temperature)",
        "emoji": "📡",
        "steps": [
            "Pop the battery tab out of the sensor so it powers up.",
            "Ask your Zoe helper to pair it with your home hub.",
            "Say “Zoe, show my home” once it's added.",
        ],
    },
]


@router.get("/qr")
async def setup_qr(request: Request, token: str = "") -> Response:
    """QR image (SVG) encoding the phone setup URL — rendered on the panel."""
    if smart_home_setup.verify(token) is None:
        return Response(status_code=404)
    import segno
    buf = io.BytesIO()
    segno.make(_setup_url(request, token), error="m").save(
        buf, kind="svg", scale=1, border=2, dark="#0b1020", light="#ffffff")
    return Response(content=buf.getvalue(), media_type="image/svg+xml",
                    headers={"Cache-Control": "no-store"})


@router.get("/info")
async def setup_info(token: str = "") -> dict[str, Any]:
    """Phone: the branded setup guide. Gated by a one-time token which is SPENT
    here (the guide is the terminal step of this read-only flow, so consuming on
    fetch keeps a photographed/leaked QR from re-opening it). Returns guidance
    content only — never mutates the home."""
    if smart_home_setup.consume(token) is None:
        return {"ok": False, "reason": "This setup link has expired. Tap “Add a device” on your Zoe screen again."}
    return {
        "ok": True,
        "title": "Add a device to your home",
        "intro": "Zoe can control lights, plugs, speakers and sensors once they're on your home hub. Pick what you're adding:",
        "device_types": _DEVICE_TYPES,
        # Honest disclosure — no faked success. When the hub grows a pairing API,
        # this note is where the automatic path lands.
        "note": "Zoe can't finish pairing brand-new hardware on her own just yet — a quick hand from your Zoe helper links it to your home hub. After that, Zoe controls it by voice and on this screen.",
    }
