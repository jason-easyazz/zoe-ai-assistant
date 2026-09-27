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

The OpenClaw Telegram bot-token endpoints (/telegram/setup, /telegram/status)
were removed 2026-09-27 (auth audit): OpenClaw is retired and nothing consumed
the token. Zoe's live Telegram bot keeps its token in its own unit env.

Note on route ordering: static paths (/skills, /skills/search) are registered BEFORE
parameterised paths (/skills/{name}/...) so FastAPI doesn't swallow "search" as a name.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Depends, Query
from auth import get_current_user, require_admin
from openclaw_manager import (
    install_plugin, list_plugins, remove_plugin,
    list_skills, install_skill, update_skill, remove_skill,
    preview_skill, search_clawhub_skills,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/openclaw", tags=["openclaw"])

_OPENCLAW_GATEWAY = os.environ.get("OPENCLAW_GATEWAY_URL", "http://127.0.0.1:18789").rstrip("/")


@router.get("/health")
async def openclaw_health(_user: dict = Depends(get_current_user)):
    """Probe OpenClaw gateway beyond a bare TCP check (ZOE-4325)."""
    status = {"gateway_url": _OPENCLAW_GATEWAY, "reachable": False, "detail": ""}
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            for path in ("/health", "/v1/health", "/"):
                try:
                    resp = await client.get(f"{_OPENCLAW_GATEWAY}{path}")
                    status["reachable"] = resp.status_code < 500
                    status["detail"] = f"{path} -> {resp.status_code}"
                    if status["reachable"]:
                        break
                except Exception:
                    continue
    except Exception as exc:
        status["detail"] = str(exc)
    return status


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
    try:
        return await preview_skill(name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


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
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/skills/{name}/update")
async def update_skill_endpoint(name: str, _user: dict = Depends(require_admin)):
    """Update a workspace skill from ClawHub."""
    try:
        return await update_skill(name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.delete("/skills/{name}")
async def remove_skill_endpoint(name: str, _user: dict = Depends(require_admin)):
    """Remove a workspace skill by deleting its directory."""
    try:
        return await remove_skill(name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
