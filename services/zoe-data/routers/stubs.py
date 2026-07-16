"""
Stub routers for frontend endpoints that don't have full implementations yet.
Returns empty/default data so the frontend doesn't get 404 errors.
"""
import json
import logging
import os
from fastapi import APIRouter, Depends, Request
from auth import get_current_user
from db_pool import get_db

logger = logging.getLogger(__name__)
router = APIRouter(tags=["stubs"])


@router.get("/api/projects")
@router.get("/api/projects/")
async def list_projects(user: dict = Depends(get_current_user)):
    return {"projects": [], "count": 0}


@router.post("/api/projects")
@router.post("/api/projects/")
async def create_project(user: dict = Depends(get_current_user)):
    return {"error": "Projects feature coming soon", "status": "not_implemented"}


@router.get("/api/collections")
@router.get("/api/collections/")
async def list_collections(user: dict = Depends(get_current_user)):
    return {"collections": [], "count": 0}


@router.get("/api/collections/{collection_id}/tiles")
async def get_collection_tiles(collection_id: str, user: dict = Depends(get_current_user)):
    return {"tiles": [], "count": 0}


@router.get("/api/user/layout")
async def get_user_layout(user: dict = Depends(get_current_user)):
    return {"layout": None}


@router.post("/api/user/layout")
async def save_user_layout(user: dict = Depends(get_current_user)):
    return {"status": "ok"}


# ── /api/settings ─────────────────────────────────────────────────────────────
# Minimal settings endpoints. Returns system preferences and feature flags.
# The music widget uses homeassistant_url from here; other callers use the
# settings dict. Full implementation would read from system_preferences table.

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
        return _add_env_settings(settings)
    # Expose HA bridge URL for widgets that need it
    return _add_env_settings(settings)


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
        return {"status": "error", "detail": str(exc)}
    return {"status": "ok", "settings": filtered}
