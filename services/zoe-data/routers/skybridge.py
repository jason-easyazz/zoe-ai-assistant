"""Skybridge surface health and capability metadata."""

from __future__ import annotations

import os
from fastapi import APIRouter


router = APIRouter(prefix="/api/skybridge", tags=["skybridge"])


@router.get("/status")
async def get_skybridge_status():
    """Return the runtime contract for the voice-first Skybridge interface."""
    livekit_configured = bool(
        os.environ.get("LIVEKIT_URL")
        and os.environ.get("LIVEKIT_API_KEY")
        and os.environ.get("LIVEKIT_API_SECRET")
    )
    return {
        "ok": True,
        "surface": "skybridge",
        "status": "ready",
        "entrypoint": "/touch/skybridge.html",
        "version": 1,
        "card_contract": "ag-ui-compatible",
        "transports": {
            "local_ws": True,
            "livekit": livekit_configured,
        },
        "capabilities": {
            "pages": 15,
            "settings": 22,
            "dynamic_cards": True,
        },
    }
