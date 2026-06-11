"""Skybridge surface health and capability metadata."""

from __future__ import annotations

import os
from fastapi import APIRouter, Depends

from auth import get_current_user
from database import get_db
from skybridge_service import resolve_skybridge_request


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
        "card_contract": {
            "status": "wired_for_calendar_weather_lists_people",
            "supported_major": 1,
            "data_domains": ["calendar", "weather", "lists", "people"],
            "voice_ws_domains": ["calendar", "weather", "lists", "people"],
            "client_capability_cards": True,
        },
        "transports": {
            "local_ws": True,
            "livekit": livekit_configured,
        },
        "capabilities": {
            "pages": 15,
            "settings": 22,
            "dynamic_cards": "calendar_weather_lists_people_data_cards",
        },
    }


@router.post("/resolve")
async def resolve_skybridge_command(
    payload: dict,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Resolve a typed Skybridge command into real data cards when supported."""
    message = str((payload or {}).get("message") or "").strip()
    return await resolve_skybridge_request(message, user.get("user_id", "voice-guest"), db=db)
