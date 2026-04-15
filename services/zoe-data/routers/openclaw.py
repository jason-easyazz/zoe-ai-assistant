"""
routers/openclaw.py — REST API for OpenClaw plugin and skill management.

Endpoints:
  GET    /api/openclaw/plugins                     → list installed + available plugins
  POST   /api/openclaw/plugins/{name}/install      → install plugin
  DELETE /api/openclaw/plugins/{name}              → remove plugin

  GET    /api/openclaw/skills                      → list workspace + eligible bundled skills
  GET    /api/openclaw/skills/search?q=…           → search ClawHub registry
  GET    /api/openclaw/skills/{name}/preview       → read SKILL.md + run security scan
  POST   /api/openclaw/skills/{name}/install       → install skill (allowlist-gated)
  POST   /api/openclaw/skills/{name}/update        → update workspace skill
  DELETE /api/openclaw/skills/{name}              → remove workspace skill

  POST   /api/openclaw/telegram/setup             → save bot token + enable Telegram channel
  GET    /api/openclaw/telegram/status            → check Telegram connection status

Note on route ordering: static paths (/skills, /skills/search) are registered BEFORE
parameterised paths (/skills/{name}/...) so FastAPI doesn't swallow "search" as a name.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Depends, Request, Query
from auth import get_current_user, require_admin
from openclaw_manager import (
    install_plugin, list_plugins, remove_plugin,
    list_skills, install_skill, update_skill, remove_skill,
    preview_skill, search_clawhub_skills,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/openclaw", tags=["openclaw"])

_OPENCLAW_JSON = Path(os.environ.get("OPENCLAW_DIR", Path.home() / ".openclaw")) / "openclaw.json"

# ── Plugin endpoints ──────────────────────────────────────────────────────────

@router.get("/plugins")
async def get_plugins(_user: dict = Depends(get_current_user)):
    """Return all plugins (installed + available) for the openclaw_manager component."""
    plugins = await list_plugins()
    return {"plugins": plugins}


@router.post("/plugins/{name}/install")
async def install_plugin_endpoint(name: str, _user: dict = Depends(require_admin)):
    """Install an OpenClaw plugin."""
    try:
        result = await install_plugin(name)
        return result
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.delete("/plugins/{name}")
async def remove_plugin_endpoint(name: str, _user: dict = Depends(require_admin)):
    """Remove an OpenClaw plugin."""
    try:
        result = await remove_plugin(name)
        return result
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── Skill endpoints (static routes BEFORE parameterised) ─────────────────────

@router.get("/skills")
async def get_skills(_user: dict = Depends(get_current_user)):
    """Return workspace-installed + eligible bundled skills for the skills_manager component."""
    skills = await list_skills()
    return {"skills": skills}


@router.get("/skills/search")
async def search_skills(q: str = Query(""), _user: dict = Depends(get_current_user)):
    """Search ClawHub registry. Returns offline:true gracefully when network unavailable."""
    return await search_clawhub_skills(q)


@router.get("/skills/{name}/preview")
async def preview_skill_endpoint(name: str, _user: dict = Depends(get_current_user)):
    """Read SKILL.md and run automated security scan. Returns content + security verdict."""
    return await preview_skill(name)


@router.post("/skills/{name}/install")
async def install_skill_endpoint(
    name: str,
    version: Optional[str] = None,
    force: bool = False,
    source: str = "clawhub",
    _user: dict = Depends(require_admin),
):
    """Install a skill (allowlist-gated). Returns 403 if not in allowlist."""
    try:
        return await install_skill(name, version=version, force=force, source=source)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/skills/{name}/update")
async def update_skill_endpoint(name: str, _user: dict = Depends(require_admin)):
    """Update a workspace skill from ClawHub."""
    try:
        return await update_skill(name)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.delete("/skills/{name}")
async def remove_skill_endpoint(name: str, _user: dict = Depends(require_admin)):
    """Remove a workspace skill by deleting its directory."""
    try:
        return await remove_skill(name)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── Telegram setup ────────────────────────────────────────────────────────────

_TOKEN_RE = re.compile(r"^\d{8,12}:[A-Za-z0-9_-]{35,}$")


async def _validate_bot_token(token: str) -> dict:
    """Call Telegram Bot API to verify the token and return bot info."""
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(f"https://api.telegram.org/bot{token}/getMe")
            data = r.json()
            if data.get("ok"):
                return data["result"]
            raise ValueError(data.get("description", "Invalid token"))
    except httpx.TimeoutException:
        raise ValueError("Telegram API timed out — check internet connection")
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"Could not reach Telegram: {exc}")


def _save_telegram_token(token: str) -> None:
    """Write the bot token into openclaw.json and enable the Telegram channel."""
    with open(_OPENCLAW_JSON) as f:
        cfg = json.load(f)

    cfg.setdefault("channels", {})
    cfg["channels"]["telegram"] = {
        "enabled": True,
        "botToken": token,
        "dmPolicy": "pairing",
        "groups": {"*": {"requireMention": True}},
    }

    with open(_OPENCLAW_JSON, "w") as f:
        json.dump(cfg, f, indent=2)


async def _restart_openclaw_gateway() -> None:
    """Attempt to restart the OpenClaw gateway service (best effort)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "systemctl", "--user", "restart", "openclaw-gateway",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=10)
    except Exception as exc:
        logger.warning("openclaw gateway restart failed (non-fatal): %s", exc)


@router.post("/telegram/setup")
async def telegram_setup(request: Request, _user: dict = Depends(get_current_user)):
    """
    Validate a Telegram bot token, write it to openclaw.json, and restart the gateway.

    Body: {"bot_token": "123456:ABC..."}
    """
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass

    token = (body.get("bot_token") or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="bot_token is required")

    # Basic format check before hitting Telegram API
    if not _TOKEN_RE.match(token):
        raise HTTPException(
            status_code=400,
            detail="Token format looks wrong. It should look like: 123456789:ABCdefGHIjklMNOpqrSTUvwxYZ",
        )

    # Verify with Telegram
    try:
        bot_info = await _validate_bot_token(token)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    bot_name = bot_info.get("first_name", "your bot")
    bot_username = bot_info.get("username", "")

    # Persist to config
    try:
        _save_telegram_token(token)
    except Exception as exc:
        logger.error("Failed to save telegram token: %s", exc)
        raise HTTPException(status_code=500, detail=f"Could not save config: {exc}")

    # Restart gateway so it picks up the new channel
    asyncio.ensure_future(_restart_openclaw_gateway())

    return {
        "status": "connected",
        "bot_name": bot_name,
        "bot_username": bot_username,
        "message": (
            f"✅ Connected to @{bot_username} ({bot_name})! "
            f"Now open Telegram and send a message to @{bot_username} to start chatting with Zoe."
        ),
    }


@router.get("/telegram/status")
async def telegram_status(_user: dict = Depends(get_current_user)):
    """Return Telegram connection status."""
    try:
        with open(_OPENCLAW_JSON) as f:
            cfg = json.load(f)
        tg = cfg.get("channels", {}).get("telegram", {})
        enabled = tg.get("enabled", False)
        token = tg.get("botToken", "")
        if enabled and token:
            # Quick token check
            try:
                bot_info = await _validate_bot_token(token)
                return {
                    "connected": True,
                    "bot_name": bot_info.get("first_name"),
                    "bot_username": bot_info.get("username"),
                }
            except Exception:
                return {"connected": False, "reason": "Token invalid or Telegram unreachable"}
        return {"connected": False, "reason": "Not configured"}
    except Exception as exc:
        return {"connected": False, "reason": str(exc)}
