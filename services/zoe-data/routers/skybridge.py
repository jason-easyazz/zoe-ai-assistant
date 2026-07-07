"""Skybridge surface health and capability metadata."""

from __future__ import annotations

import os
from fastapi import APIRouter, Depends

import ambient_briefing
from auth import get_current_user
from database import get_db
from skybridge_service import active_timers_for, cancel_timer_by_id, resolve_skybridge_request


router = APIRouter(prefix="/api/skybridge", tags=["skybridge"])


@router.get("/status")
async def get_skybridge_status(user: dict = Depends(get_current_user)):
    """Return the runtime contract for the voice-first Skybridge interface.

    Includes the CURRENT USER so the panel can render real auth state (the
    dashboard's profile chip) — client-side localStorage heuristics cannot see
    the device-session user (glass-verified: signed-in panel showed "Sign in").
    """
    livekit_configured = bool(
        os.environ.get("LIVEKIT_URL")
        and os.environ.get("LIVEKIT_API_KEY")
        and os.environ.get("LIVEKIT_API_SECRET")
    )
    role = str(user.get("role", "guest") or "guest").lower()
    return {
        "ok": True,
        "surface": "skybridge",
        "status": "ready",
        "user": {
            "user_id": user.get("user_id", ""),
            "username": user.get("username", ""),
            "role": role,
            "guest": role == "guest" or not user.get("user_id"),
        },
        "entrypoint": "/touch/skybridge.html",
        "version": 1,
        "card_contract": {
            "status": "wired_for_calendar_weather_lists_people_clock_actions_v1",
            "supported_major": 1,
            "data_domains": ["calendar", "weather", "lists", "people", "clock"],
            "voice_ws_domains": ["calendar", "weather", "lists", "people", "clock"],
            "action_domains": ["calendar", "lists", "people"],
            "context_refresh": True,
            "client_capability_cards": True,
        },
        "transports": {
            "local_ws": True,
            "livekit": livekit_configured,
        },
        "capabilities": {
            "pages": 15,
            "settings": 22,
            "dynamic_cards": "calendar_weather_lists_people_clock_data_and_action_cards",
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
    context = (payload or {}).get("context")
    if not isinstance(context, dict):
        context = None
    return await resolve_skybridge_request(message, user.get("user_id", "voice-guest"), context=context, db=db)


@router.get("/briefing")
async def get_skybridge_briefing(user: dict = Depends(get_current_user)):
    """Ambient composed briefing for the idle panel — cached, never LLM-blocking.

    Returns ``{"card": <compose card|None>}``. ``None`` simply means "nothing
    yet" (first hit warms the cache in the background); the panel keeps showing
    the plain clock. Guests get guest-visible facts only — the resolvers
    enforce that, and auth-required results are never used as briefing facts.
    """
    card = await ambient_briefing.get_briefing_card(user.get("user_id", "voice-guest"))
    return {"card": card}


@router.get("/timers")
async def list_skybridge_timers(user: dict = Depends(get_current_user)):
    """Active timers for this user — lets the panel resume countdowns after a reload."""
    return {"timers": active_timers_for(user.get("user_id", "voice-guest"))}


@router.post("/timers/cancel")
async def cancel_skybridge_timer(payload: dict, user: dict = Depends(get_current_user)):
    """Cancel one timer by id (the panel's per-card tap-cancel)."""
    timer_id = str((payload or {}).get("timer_id") or "").strip()
    cancelled = cancel_timer_by_id(user.get("user_id", "voice-guest"), timer_id)
    return {"ok": bool(cancelled), "timer_id": timer_id}
