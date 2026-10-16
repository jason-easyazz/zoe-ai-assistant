"""Skybridge surface health and capability metadata."""

from __future__ import annotations

import logging
import os
from fastapi import Request, APIRouter, Depends

import panel_kiosk_credential
import panel_principal
from auth import get_current_user
from database import get_db
from skybridge_service import active_timers_for, cancel_timer_by_id, classify_skybridge_intent, resolve_skybridge_request


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/skybridge", tags=["skybridge"])


@router.get("/status")
async def get_skybridge_status(
    request: Request,
    panel_id: str = "",
    user: dict = Depends(get_current_user),
):
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
    display_user_id = user.get("user_id", "")
    display_name = user.get("username", "")
    identity_source = "session"
    # Household-kiosk identity: a guest BROWSER session on a bound panel shows the
    # panel's default user as its DISPLAY identity (this is whose panel it is).
    # Display != authorization — privileged data still goes through the PIN
    # challenge; this only fixes "Sign in" showing on a panel that knows its owner.
    if (role == "guest" or not display_user_id) and panel_id:
        try:
            from db_pool import get_db_ctx  # lazy: only bound-panel lookups touch the pool

            # Device check (anti-enumeration): the display identity is only served to the PANEL ITSELF, so a guest elsewhere
            # on the LAN can't walk panel_ids to learn household names. The panel is (a) the browser holding this panel's kiosk
            # credential (panel_kiosk_credential, flag-dark: survives an address change - private link, another Wi-Fi), or
            # (b) the machine at the panel's REGISTERED address. The address is nginx's X-Real-IP, trusted only from a proxy of
            # ours (panel_principal.trusted_client_ip): :8000 answers on the LAN, where a caller can write X-Real-IP itself.
            # X-Forwarded-For is APPENDED to (client-supplied leftmost value) and is never used. A request that crossed the
            # Cloudflare tunnel is never the panel by address. Only ``is_active`` is checked here, not ``allow_guest``: this
            # is DISPLAY identity for the panel itself (unchanged from before the credential), while ``allow_guest`` gates
            # what a guest may DO (panel_principal.is_panel_guest).
            via_tunnel = panel_principal._via_public_tunnel(request)
            client_ip = "" if via_tunnel else panel_principal.trusted_client_ip(request)
            kiosk_panel = panel_kiosk_credential.credential_panel_id(request)
            async with get_db_ctx() as db:
                panel = await db.fetchrow(
                    "SELECT ip_address, is_active FROM panels WHERE panel_id = $1",
                    panel_id,
                )
                row = None
                at_address = bool(panel and panel["ip_address"] and client_ip and client_ip == str(panel["ip_address"]))
                if panel and panel["is_active"] and (kiosk_panel == panel_id or at_address):
                    row = await db.fetchrow(
                        "SELECT b.user_id, u.name FROM panel_user_bindings b "
                        "LEFT JOIN users u ON u.id = b.user_id "
                        "WHERE b.panel_id = $1 AND b.binding_type = 'default' "
                        "ORDER BY b.priority DESC LIMIT 1",
                        panel_id,
                    )
                elif panel is not None:
                    logger.debug("panel identity refused: client %s != registered %s for %s",
                                 client_ip, panel["ip_address"], panel_id)
            if row and row["user_id"]:
                display_user_id = row["user_id"]
                display_name = row["name"] or row["user_id"]
                role = "panel"
                identity_source = "panel_binding"
        except Exception as exc:  # display identity is best-effort; guest stands — but LOG it
            logger.warning("panel display-identity lookup failed for %s: %s", panel_id, exc)
    return {
        "ok": True,
        "surface": "skybridge",
        "status": "ready",
        "user": {
            "user_id": display_user_id,
            "username": display_name,
            "role": role,
            "guest": role == "guest" or not display_user_id,
            "source": identity_source,
        },
        "entrypoint": "/touch/home.html",
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
    request: Request = None,
):
    """Resolve a typed Skybridge command into real data cards when supported."""
    message = str((payload or {}).get("message") or "").strip()
    context = (payload or {}).get("context")
    if not isinstance(context, dict):
        context = None
    # Anyone AT THE PANEL may use the household list; an anonymous LAN caller still touches no list.
    await panel_principal.mark_if_panel(request, user, db)
    # Home control (security fix 2026-10-17): the same rule as ``/api/ha/control`` - a member or the panel. Without this an
    # anonymous LAN caller refused there could post "turn off the lights" here instead. Classified only for a non-member, and
    # the voice lanes call ``resolve_skybridge_request`` directly, so a voice turn is unaffected.
    if not panel_principal.is_member(user):
        intent = classify_skybridge_intent(message, context)
        if intent is not None and intent.domain == "smart_home" and not await panel_principal.may_control_home(request, user, db):
            logger.warning("skybridge resolve: smart-home %s refused for a caller that is neither a member nor the panel",
                           intent.action)
            return _home_control_refused(intent.action, context)
    return await resolve_skybridge_request(message, user.get("user_id", "voice-guest"), context=context, db=db)


def _home_control_refused(action: str, context: dict | None) -> dict:
    """The skybridge answer when a non-member, non-panel caller asks to drive the house: nothing ran."""
    return {
        "handled": True,
        "auth_required": True,
        "intent": {"domain": "smart_home", "action": action},
        "spoken_summary": "Sign in, or use the wall panel, to control the house.",
        "cards": [],
        "actions": [{"type": "auth_required", "domain": "smart_home", "action": action}],
        "skybridge_context": context or {},
    }


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
