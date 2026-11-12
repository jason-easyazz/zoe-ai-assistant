"""
Stub reads for frontend surfaces that have no backend yet, plus ``/api/settings``.

A stub may only answer a READ with the truth ("no layout"). A write to a feature that is not built
is absent (405) - never a 200 that pretends it worked.
"""
import json
import logging
import os
from fastapi import APIRouter, Depends, HTTPException, Request
from auth import get_current_user, require_admin, require_signed_in
from db_pool import get_db

logger = logging.getLogger(__name__)
router = APIRouter(tags=["stubs"])


# There are no /api/projects or /api/collections routes: they always answered "none" (create = 501) with no backend
# behind them (gap audit 2026-11-13), so a caller now gets an honest 404.


@router.get("/api/user/layout")
async def get_user_layout(user: dict = Depends(get_current_user)):
    return {"layout": None}


# There is no ``POST /api/user/layout``: the stub answered ``{"status": "ok"}`` and saved
# nothing (fake success), and no page ever called it. A save that is not built is a 405.


# ── /api/settings ─────────────────────────────────────────────────────────────
# ``system_preferences`` plus ``homeassistant_url`` (the music widget's link, its only browser
# caller). Security fix 2026-10-18: this returned EVERY row to an anonymous guest, incl. the
# household's weather lat/lon and other members' ``intelligence_settings:<user_id>`` rows. Now a
# non-member (guest, the panel) gets ``_PUBLIC_SETTING_KEYS`` only; a member gets the household
# rows (no ``:``) and only their OWN ``<key>:<user_id>`` rows.
_PUBLIC_SETTING_KEYS = frozenset({
    "homeassistant_url",    # the music widget's link to HA (a LAN URL, already in the page)
    "panel_idle_logout_s",  # how long the panel waits before signing out: panel behaviour, not data
    "_status", "_error",    # the degraded-read markers, so every caller can tell "storage down" from "no settings"
})


def _visible_settings(settings: dict, user: dict) -> dict:
    from panel_principal import is_member

    if not is_member(user):
        return {k: v for k, v in settings.items() if k in _PUBLIC_SETTING_KEYS}
    own = str((user or {}).get("user_id") or "")
    return {k: v for k, v in settings.items() if ":" not in k or k.split(":", 1)[1] == own}

_INTELLIGENCE_SETTINGS_KEY = "intelligence_settings"
_INTELLIGENCE_DEFAULTS = {
    "proactive_enabled": True,
    "relationship_monitoring": False,
    "task_suggestions": True,
    "calendar_insights": True,
    "learning_enabled": True,
    "show_orb": True,
    "do_not_disturb": False,
}


def _add_env_settings(settings: dict) -> dict:
    if "homeassistant_url" not in settings:
        ha_url = os.environ.get("ZOE_HA_URL", os.environ.get("ZOE_HA_BRIDGE_URL", ""))
        if ha_url:
            settings["homeassistant_url"] = ha_url
    return settings


@router.get("/api/settings")
async def get_settings(user: dict = Depends(get_current_user), db=Depends(get_db)):
    """Return general system settings (HA URL, feature flags, etc.)."""
    settings: dict = {}
    try:
        rows = await db.execute_fetchall("SELECT key, value FROM system_preferences", ())
        for row in rows:
            try:
                settings[row["key"]] = json.loads(row["value"])
            except (json.JSONDecodeError, TypeError):
                settings[row["key"]] = row["value"]
    except Exception:
        logger.exception("Failed to load system settings")
        settings["_status"] = "degraded"
        settings["_error"] = "settings_storage_unavailable"
        return _visible_settings(_add_env_settings(settings), user)
    # Expose HA bridge URL for widgets that need it
    return _visible_settings(_add_env_settings(settings), user)


@router.get("/api/settings/dream-window")
async def get_dream_window(user: dict = Depends(require_signed_in), db=Depends(get_db)):
    """The household's dreaming window (``dream_window.py``): when Zoe may start her night session, the quiet minutes before it, and when she is awake again. Any signed-in
    member may read it (``require_signed_in``: a credential-less LAN guest gets 401/403, not the household's schedule); ``can_change`` says whether THIS caller may change it. An invalid stored value is reported with the defaults in force."""
    import dream_window as dw
    stored = None
    try:
        rows = await db.execute_fetchall("SELECT value FROM system_preferences WHERE key = ?", (dw.KEY,))
        if rows and rows[0]["value"]:
            stored = json.loads(rows[0]["value"])
    except Exception:
        logger.exception("Failed to load the dreaming window")
        return {"status": "degraded", "error": "settings_storage_unavailable", "window": dw.env_defaults(), "errors": [], "source": "default", "can_change": False}
    window, errors = dw.validate(stored) if stored is not None else (dw.env_defaults(), [])
    return {"window": window, "errors": errors, "source": "stored" if stored is not None and not errors else "default", "can_change": dw.may_change(user.get("role")),
            "rules": {"min_window_min": dw.MIN_WINDOW_MIN, "min_chain_min": dw.MIN_CHAIN_MIN, "voice_check_slot": "04:10-04:52"}}


@router.put("/api/settings/dream-window")
async def save_dream_window(request: Request, user: dict = Depends(require_admin), db=Depends(get_db)):
    """Change the household's dreaming window. Adult household admins only (``require_admin``: never a guest or a child). The body is validated whole
    (``dream_window.validate``); an invalid window is a 422 with every reason and nothing is stored. A valid one is stored and mirrored to the file the night session reads."""
    import dream_window as dw
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail={"errors": ["expected an object"]})
    missing = [k for k in (*dw.FIELDS, "quiet_min") if k not in body]
    if missing:
        raise HTTPException(status_code=422, detail={"errors": [f"send {k}" for k in missing]})
    window, errors = dw.validate({k: body[k] for k in (*dw.FIELDS, "quiet_min")}, fallback=False)
    if errors:
        raise HTTPException(status_code=422, detail={"errors": errors})
    try:
        await db.execute(
            """INSERT INTO system_preferences (key, value, updated_by, updated_at)
               VALUES (?, ?, ?, NOW()::text)
               ON CONFLICT (key) DO UPDATE SET
                   value = EXCLUDED.value,
                   updated_by = EXCLUDED.updated_by,
                   updated_at = EXCLUDED.updated_at""",
            (dw.KEY, json.dumps(window), user["user_id"]),
        )
        await db.commit()
    except Exception as exc:
        logger.warning("save_dream_window failed - NOT persisted: %s", type(exc).__name__)
        raise HTTPException(status_code=500, detail="Failed to save the dreaming window") from exc
    try:
        dw.save_mirror(window)              # what the night session and night_busy.sh read each night
    except OSError as exc:
        logger.warning("dreaming window stored but the mirror file could not be written: %s", type(exc).__name__)
    return {"status": "ok", "window": window}


@router.get("/api/settings/intelligence")
async def get_intelligence_settings(user: dict = Depends(get_current_user), db=Depends(get_db)):
    """Return intelligence/proactive feature settings for the current user."""
    user_id = user["user_id"]
    try:
        rows = await db.execute_fetchall(
            "SELECT value FROM system_preferences WHERE key = ?",
            (f"{_INTELLIGENCE_SETTINGS_KEY}:{user_id}",),
        )
        if rows:
            stored = json.loads(rows[0]["value"]) if rows[0]["value"] else {}
            merged = {**_INTELLIGENCE_DEFAULTS, **stored}
            return {"settings": merged}
    except Exception:
        logger.exception("Failed to load intelligence settings")
        return {
            "status": "degraded",
            "error": "settings_storage_unavailable",
            "settings": dict(_INTELLIGENCE_DEFAULTS),
        }
    return {"settings": dict(_INTELLIGENCE_DEFAULTS)}


@router.put("/api/settings/intelligence")
async def save_intelligence_settings(
    request: Request,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Persist intelligence/proactive feature settings for the current user."""
    user_id = user["user_id"]
    body = await request.json()
    # Whitelist only known keys
    allowed = set(_INTELLIGENCE_DEFAULTS.keys())
    filtered = {k: bool(v) for k, v in body.items() if k in allowed}
    value = json.dumps(filtered)
    try:
        await db.execute(
            """INSERT INTO system_preferences (key, value, updated_by, updated_at)
               VALUES (?, ?, ?, NOW()::text)
               ON CONFLICT (key) DO UPDATE SET
                   value = EXCLUDED.value,
                   updated_by = EXCLUDED.updated_by,
                   updated_at = EXCLUDED.updated_at""",
            (f"{_INTELLIGENCE_SETTINGS_KEY}:{user_id}", value, user_id),
        )
        await db.commit()
    except Exception as exc:
        logger.warning(
            "save_intelligence_settings failed for user=%s — settings NOT "
            "persisted: %s", user_id, exc)
        # A write either persisted or it didn't — never answer 200 for a failed
        # save. The old body ({"status": "error"} with HTTP 200) made the panel's
        # `if (res.ok) alert('Saved')` tell the user their settings were saved
        # when nothing was written. The read path's "degraded" shape is right for
        # GETs; it has no honest meaning for a write.
        # `detail` stays generic: the raw exception can carry DB schema/connection
        # detail and this endpoint is reachable by any signed-in user.
        raise HTTPException(
            status_code=500,
            detail="Failed to save intelligence settings",
        ) from exc
    return {"status": "ok", "settings": filtered}
