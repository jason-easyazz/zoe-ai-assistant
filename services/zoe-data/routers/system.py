import asyncio
import json
import logging
import os
from typing import Optional
import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from auth import get_current_user, require_admin
from database import get_db
from openclaw_maintenance import (
    fetch_gateway_status,
    fetch_npm_latest_version,
    maybe_create_update_notification,
    notify_users_with_notify_preference,
    process_auto_update_users,
    read_installed_version_sync,
    run_npm_upgrade_openclaw,
    version_newer,
)
from system_updates import (
    build_updates_snapshot,
    install_component,
    installable_components,
    invalidate_cache as invalidate_updates_cache,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/platform")
async def get_platform():
    return {
        "platform": "zoe-data",
        "version": "1.0.0",
        "engine": "openclaw",
        "architecture": "aarch64",
    }


@router.get("/health")
async def health_check(db=Depends(get_db)):
    try:
        cursor = await db.execute("SELECT 1")
        await cursor.fetchone()
        return {"status": "healthy", "database": "ok"}
    except Exception as e:
        return {"status": "unhealthy", "database": str(e)}


@router.get("/modules/enabled")
async def get_enabled_modules():
    return {
        "modules": [
            {"id": "calendar", "name": "Calendar", "enabled": True},
            {"id": "lists", "name": "Lists", "enabled": True},
            {"id": "reminders", "name": "Reminders", "enabled": True},
            {"id": "notes", "name": "Notes", "enabled": True},
            {"id": "journal", "name": "Journal", "enabled": True},
            {"id": "weather", "name": "Weather", "enabled": True},
            {"id": "people", "name": "People", "enabled": True},
            {"id": "transactions", "name": "Transactions", "enabled": True},
        ]
    }


@router.get("/status")
async def get_system_status(
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """System status including OpenClaw gateway health."""
    gateway_status = "unknown"
    gateway_model = None
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get("http://127.0.0.1:18789/health")
            if r.status_code == 200:
                gateway_status = "connected"
                data = r.json()
                gateway_model = data.get("model")
            else:
                gateway_status = "error"
    except Exception:
        gateway_status = "offline"

    db_status = "ok"
    try:
        cursor = await db.execute("SELECT 1")
        await cursor.fetchone()
    except Exception:
        db_status = "error"

    ui_actions_pending = 0
    panels_online = 0
    try:
        cursor = await db.execute("SELECT COUNT(*) FROM ui_actions WHERE status IN ('queued', 'running')")
        row = await cursor.fetchone()
        ui_actions_pending = row[0] if row else 0
        cursor = await db.execute(
            """SELECT COUNT(*) FROM ui_panel_sessions
               WHERE last_seen_at >= datetime('now', '-30 seconds')"""
        )
        row = await cursor.fetchone()
        panels_online = row[0] if row else 0
    except Exception:
        pass

    # llama-server health probe (local model on port 11434)
    llama_status = "unconfigured"
    llama_model = None
    llama_url = os.environ.get("ZOE_LLAMA_URL", "http://127.0.0.1:11434")
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            lr = await client.get(f"{llama_url}/health")
            if lr.status_code == 200:
                llama_status = "ok"
                ld = lr.json()
                llama_model = ld.get("model") or ld.get("status")
            else:
                llama_status = f"http_{lr.status_code}"
    except httpx.ConnectError:
        llama_status = "offline"
    except Exception as exc:
        llama_status = f"error:{type(exc).__name__}"

    # homeassistant-mcp-bridge health (port 8007)
    ha_bridge_status = "unconfigured"
    ha_bridge_url = os.environ.get("ZOE_HA_BRIDGE_URL", "http://127.0.0.1:8007")
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            hr = await client.get(f"{ha_bridge_url}/entities")
            if hr.status_code == 200:
                hd = hr.json()
                entity_count = hd.get("count", len(hd) if isinstance(hd, list) else 0)
                ha_bridge_status = f"ok:{entity_count}_entities"
            else:
                ha_bridge_status = f"http_{hr.status_code}"
    except httpx.ConnectError:
        ha_bridge_status = "offline"
    except Exception as exc:
        ha_bridge_status = f"error:{type(exc).__name__}"

    return {
        "database": db_status,
        "openclaw_gateway": gateway_status,
        "openclaw_model": gateway_model,
        "llama_server": llama_status,
        "llama_model": llama_model,
        "ha_bridge": ha_bridge_status,
        "platform": "aarch64",
        "engine": "openclaw",
        "ui_orchestrator": {
            "pending_actions": ui_actions_pending,
            "online_panels_30s": panels_online,
        },
    }


def _load_skills_and_cron():
    skills = []
    skills_dir = os.path.expanduser("~/.openclaw/workspace/skills")
    if os.path.isdir(skills_dir):
        for entry in sorted(os.listdir(skills_dir)):
            skill_path = os.path.join(skills_dir, entry)
            if os.path.isdir(skill_path):
                skill_file = None
                for name in ("SKILL.md", "skill.md"):
                    p = os.path.join(skill_path, name)
                    if os.path.exists(p):
                        skill_file = p
                        break
                desc = ""
                if skill_file:
                    with open(skill_file, "r", encoding="utf-8", errors="replace") as f:
                        lines = f.readlines()
                        for line in lines[1:6]:
                            line = line.strip()
                            if line and not line.startswith("#"):
                                desc = line
                                break
                skills.append({"id": entry, "description": desc})

    cron_jobs = []
    cron_path = os.path.expanduser("~/.openclaw/cron/jobs.json")
    if os.path.exists(cron_path):
        with open(cron_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            for job in data.get("jobs", []):
                state = job.get("state", {})
                cron_jobs.append(
                    {
                        "name": job.get("name"),
                        "enabled": job.get("enabled", False),
                        "schedule": job.get("schedule", {}).get("expr"),
                        "last_status": state.get("lastRunStatus"),
                        "last_error": state.get("lastError"),
                        "consecutive_errors": state.get("consecutiveErrors", 0),
                    }
                )
    return skills, cron_jobs


_DEFAULT_OPENCLAW_PREFS = {"openclaw_auto_update": "notify"}


@router.get("/openclaw/preferences")
async def get_openclaw_preferences(
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    user_id = user["user_id"]
    cursor = await db.execute(
        "SELECT prefs FROM user_preferences WHERE user_id = ?",
        (user_id,),
    )
    row = await cursor.fetchone()
    merged = dict(_DEFAULT_OPENCLAW_PREFS)
    if row:
        try:
            raw = row["prefs"]
            parsed = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(parsed, dict):
                merged.update(parsed)
        except (json.JSONDecodeError, TypeError):
            pass
    return {"prefs": merged}


@router.put("/openclaw/preferences")
async def put_openclaw_preferences(
    request: Request,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    body = await request.json()
    incoming = body.get("prefs") if isinstance(body.get("prefs"), dict) else body
    if not isinstance(incoming, dict):
        raise HTTPException(status_code=400, detail="Expected JSON object with prefs")

    user_id = user["user_id"]
    cursor = await db.execute(
        "SELECT prefs FROM user_preferences WHERE user_id = ?",
        (user_id,),
    )
    row = await cursor.fetchone()
    merged = dict(_DEFAULT_OPENCLAW_PREFS)
    if row:
        try:
            raw = row["prefs"]
            parsed = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(parsed, dict):
                merged.update(parsed)
        except (json.JSONDecodeError, TypeError):
            pass

    if "openclaw_auto_update" in incoming:
        mode = incoming["openclaw_auto_update"]
        if mode not in ("off", "notify", "auto"):
            raise HTTPException(status_code=400, detail="openclaw_auto_update must be off, notify, or auto")
        merged["openclaw_auto_update"] = mode

    await db.execute(
        """INSERT INTO user_preferences (user_id, prefs, updated_at)
           VALUES (?, ?, datetime('now'))
           ON CONFLICT(user_id) DO UPDATE SET prefs = excluded.prefs, updated_at = datetime('now')""",
        (user_id, json.dumps(merged)),
    )
    await db.commit()
    return {"prefs": merged}


@router.post("/openclaw/upgrade")
async def post_openclaw_upgrade(
    request: Request,
    user: dict = Depends(require_admin),
):
    if os.environ.get("OPENCLAW_UPGRADE_ENABLED", "true").lower() != "true":
        raise HTTPException(status_code=403, detail="OpenClaw upgrades are disabled on this server")

    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    if not body.get("confirm"):
        raise HTTPException(status_code=400, detail='Set {"confirm": true} to run the upgrade')

    ok, log_tail = await run_npm_upgrade_openclaw()
    new_ver = read_installed_version_sync()
    if not ok:
        raise HTTPException(status_code=500, detail={"message": "Upgrade failed", "log": log_tail[-12000:]})
    return {"ok": True, "installed_version": new_ver, "log": log_tail[-12000:]}


@router.get("/openclaw")
async def get_openclaw_info(
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
    check_latest: bool = Query(False),
):
    """OpenClaw brain info: skills, cron, gateway, versions."""
    skills, cron_jobs = _load_skills_and_cron()
    gateway_status, gateway_model = await fetch_gateway_status()
    installed = read_installed_version_sync()

    latest_version = None
    update_available = False
    if check_latest:
        latest_version = await fetch_npm_latest_version()
        if latest_version and installed:
            update_available = version_newer(latest_version, installed)
        elif latest_version and not installed:
            update_available = True

        if check_latest and latest_version and update_available:
            await maybe_create_update_notification(db, user["user_id"], installed, latest_version)

    return {
        "skills": skills,
        "cron_jobs": cron_jobs,
        "gateway_status": gateway_status,
        "model": gateway_model,
        "gateway_model": gateway_model,
        "installed_version": installed,
        "latest_version": latest_version if check_latest else None,
        "update_available": update_available if check_latest else None,
    }


async def run_scheduled_openclaw_version_check():
    """Background: daily npm check, notify users, optional auto-upgrade."""
    try:
        async for db in get_db():
            latest = await fetch_npm_latest_version()
            if latest:
                await notify_users_with_notify_preference(db, latest)
                await process_auto_update_users(db, latest)
            break
    except Exception:
        logger.exception("run_scheduled_openclaw_version_check failed")


async def openclaw_background_loop():
    """First check after 3 minutes, then every 24 hours."""
    await asyncio.sleep(180)
    while True:
        await run_scheduled_openclaw_version_check()
        await asyncio.sleep(86400)


def start_openclaw_background_tasks():
    if os.environ.get("OPENCLAW_BACKGROUND_VERSION_CHECK", "true").lower() != "true":
        return None
    return asyncio.create_task(openclaw_background_loop(), name="openclaw_version_check")


# ── Nightly memory digest background loop ────────────────────────────────────

async def _memory_digest_loop():
    """Wait until 3am, then run LLM digest for all active users daily."""
    import datetime
    # Initial delay: sleep until next 3am
    while True:
        now = datetime.datetime.now()
        next_run = now.replace(hour=3, minute=0, second=0, microsecond=0)
        if next_run <= now:
            next_run += datetime.timedelta(days=1)
        delay_s = (next_run - now).total_seconds()
        logger.info("memory_digest: next run in %.0f minutes", delay_s / 60)
        await asyncio.sleep(delay_s)
        try:
            from memory_digest import run_digest_for_all_active_users  # type: ignore[import]
            results = await run_digest_for_all_active_users()
            logger.info("memory_digest: nightly run complete — %d users processed", len(results))
        except Exception as exc:
            logger.error("memory_digest: nightly loop error: %s", exc, exc_info=True)


def start_memory_digest_background():
    """Start the nightly memory digest loop (if not disabled)."""
    if os.environ.get("MEMORY_DIGEST_ENABLED", "true").lower() != "true":
        return None
    return asyncio.create_task(_memory_digest_loop(), name="memory_digest_nightly")


# ── Weekly memory consolidation (Sunday 04:00) ───────────────────────────────

async def _memory_consolidation_loop():
    """Sleep until the next Sunday 04:00, then run weekly consolidation.

    The consolidation pass merges near-duplicate memories, asks Bonsai to
    resolve high-similarity contradictions, and soft-archives low-score
    stale rows. See ``memory_digest.run_weekly_consolidation`` for the
    full algorithm.
    """
    import datetime
    while True:
        now = datetime.datetime.now()
        # weekday(): Monday=0 … Sunday=6
        days_ahead = (6 - now.weekday()) % 7
        next_run = (now + datetime.timedelta(days=days_ahead)).replace(
            hour=4, minute=0, second=0, microsecond=0
        )
        if next_run <= now:
            next_run += datetime.timedelta(days=7)
        delay_s = (next_run - now).total_seconds()
        logger.info(
            "memory_consolidation: next run %s (%.1f h)",
            next_run.isoformat(), delay_s / 3600,
        )
        await asyncio.sleep(delay_s)
        try:
            from memory_digest import run_weekly_consolidation_for_all  # type: ignore[import]
            results = await run_weekly_consolidation_for_all()
            logger.info(
                "memory_consolidation: weekly run complete — %d users processed",
                len(results),
            )
        except Exception as exc:
            logger.error("memory_consolidation: loop error: %s", exc, exc_info=True)


def start_memory_consolidation_background():
    """Start the Sunday 04:00 consolidation loop (if not disabled)."""
    if os.environ.get("MEMORY_CONSOLIDATION_ENABLED", "true").lower() != "true":
        return None
    return asyncio.create_task(
        _memory_consolidation_loop(), name="memory_consolidation_weekly"
    )


@router.post("/memories/consolidate")
async def trigger_memory_consolidation(
    user_id: Optional[str] = None,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Manually trigger the weekly consolidation pass.

    Admin-only when ``user_id`` differs from the caller or is ``all``.
    """
    from memory_digest import (
        run_weekly_consolidation,
        run_weekly_consolidation_for_all,
    )  # type: ignore[import]
    target_user = user_id or user["user_id"]
    if target_user != user["user_id"] and user.get("role") != "admin":
        raise HTTPException(
            status_code=403,
            detail="Admin role required to consolidate another user",
        )
    if user_id == "all" and user.get("role") == "admin":
        return {"results": await run_weekly_consolidation_for_all(db=db)}
    return await run_weekly_consolidation(target_user)


@router.post("/memories/digest")
async def trigger_memory_digest(
    user_id: Optional[str] = None,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Manually trigger the LLM memory digest for a user (or the requesting user)."""
    from memory_digest import run_memory_digest, run_digest_for_all_active_users  # type: ignore[import]
    target_user = user_id or user["user_id"]
    # Only admins can trigger digest for other users
    if target_user != user["user_id"] and user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin role required to digest another user")
    if user_id == "all" and user.get("role") == "admin":
        results = await run_digest_for_all_active_users(db=db)
        return {"results": results}
    result = await run_memory_digest(target_user, db=db)
    return result


# ── Updates Hub ──────────────────────────────────────────────────────────────

@router.get("/updates")
async def get_updates(
    force: bool = Query(False),
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Aggregated version + health for every component Zoe tracks."""
    return await build_updates_snapshot(db=db, force=force)


@router.get("/updates/history")
async def get_update_history(
    limit: int = Query(50, ge=1, le=500),
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    cursor = await db.execute(
        """SELECT id, component, version_before, version_after, ok,
                  log_excerpt, initiated_by, created_at
           FROM update_history
           ORDER BY id DESC
           LIMIT ?""",
        (limit,),
    )
    rows = await cursor.fetchall()
    return {"history": [dict(r) for r in rows]}


@router.post("/updates/install/{component}")
async def post_install_component(
    component: str,
    request: Request,
    user: dict = Depends(require_admin),
    db=Depends(get_db),
):
    body = {}
    try:
        if request.headers.get("content-type", "").startswith("application/json"):
            body = await request.json()
    except Exception:
        body = {}
    if component not in installable_components():
        raise HTTPException(
            status_code=400,
            detail=f"Component '{component}' is not installable. "
                   f"Installable: {', '.join(installable_components())}",
        )
    if component == "openclaw" and os.environ.get("OPENCLAW_UPGRADE_ENABLED", "true").lower() != "true":
        raise HTTPException(status_code=403, detail="OpenClaw upgrades are disabled on this server")
    if not body.get("confirm"):
        raise HTTPException(status_code=400, detail='Set {"confirm": true} to run the install')
    try:
        result = await install_component(db, user, component)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not result["ok"]:
        raise HTTPException(status_code=500, detail={"message": "Install failed", "log": result["log"]})
    return result


# ── Display Preferences (touch-panel dim + screen-off) ──────────────────────

_DEFAULT_DISPLAY_PREFS = {
    "enabled": True,
    "day_brightness": 100,
    "night_enabled": True,
    "night_start": "22:00",
    "night_end": "06:30",
    "night_brightness": 15,
    "idle_enabled": True,
    "idle_seconds": 120,
    "idle_brightness": 30,
    "off_enabled": True,
    "off_seconds": 900,
}

_DEFAULT_PI_HOST = os.environ.get("ZOE_PI_HOST", "192.168.1.61")
_PANEL_AGENT_PORT = int(os.environ.get("ZOE_PANEL_AGENT_PORT", "8765"))


def _row_to_prefs(row) -> dict:
    if not row:
        return dict(_DEFAULT_DISPLAY_PREFS)
    prefs = dict(_DEFAULT_DISPLAY_PREFS)
    for key in _DEFAULT_DISPLAY_PREFS:
        if key in row.keys() and row[key] is not None:
            val = row[key]
            if isinstance(_DEFAULT_DISPLAY_PREFS[key], bool):
                prefs[key] = bool(val)
            else:
                prefs[key] = val
    return prefs


@router.get("/display/preferences")
async def get_display_preferences(
    device_id: str = Query("default"),
    db=Depends(get_db),
):
    """Fetch the display preferences for a specific panel device.

    Deliberately unauthenticated so the Pi panel agent can poll this without a
    session token. No sensitive data is exposed.
    """
    cursor = await db.execute(
        "SELECT * FROM display_preferences WHERE device_id = ?",
        (device_id,),
    )
    row = await cursor.fetchone()
    prefs = _row_to_prefs(row)
    pi_host = (row["pi_host"] if row and "pi_host" in row.keys() and row["pi_host"] else _DEFAULT_PI_HOST)
    return {
        "device_id": device_id,
        "pi_host": pi_host,
        "preferences": prefs,
    }


async def _proxy_reload_to_pi(pi_host: str) -> None:
    """Fire-and-forget POST /reload so settings apply within ~1s."""
    if not pi_host:
        return
    url = f"http://{pi_host}:{_PANEL_AGENT_PORT}/reload"
    try:
        async with httpx.AsyncClient(timeout=2.5) as c:
            await c.post(url)
    except Exception as exc:
        logger.debug("panel agent reload failed: %s", exc)


@router.put("/display/preferences")
async def put_display_preferences(
    request: Request,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Expected JSON object")
    device_id = body.get("device_id") or "default"
    incoming = body.get("preferences") if isinstance(body.get("preferences"), dict) else body
    pi_host_override = body.get("pi_host")

    cursor = await db.execute(
        "SELECT * FROM display_preferences WHERE device_id = ?",
        (device_id,),
    )
    row = await cursor.fetchone()
    merged = _row_to_prefs(row)
    for key, default in _DEFAULT_DISPLAY_PREFS.items():
        if key in incoming and incoming[key] is not None:
            val = incoming[key]
            if isinstance(default, bool):
                merged[key] = bool(val)
            elif isinstance(default, int):
                try:
                    merged[key] = int(val)
                except (TypeError, ValueError):
                    pass
            else:
                merged[key] = val
    pi_host = pi_host_override or (row["pi_host"] if row and "pi_host" in row.keys() and row["pi_host"] else _DEFAULT_PI_HOST)

    await db.execute(
        """INSERT INTO display_preferences (
                device_id, enabled, day_brightness,
                night_enabled, night_start, night_end, night_brightness,
                idle_enabled, idle_seconds, idle_brightness,
                off_enabled, off_seconds, pi_host, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
           ON CONFLICT(device_id) DO UPDATE SET
                enabled=excluded.enabled,
                day_brightness=excluded.day_brightness,
                night_enabled=excluded.night_enabled,
                night_start=excluded.night_start,
                night_end=excluded.night_end,
                night_brightness=excluded.night_brightness,
                idle_enabled=excluded.idle_enabled,
                idle_seconds=excluded.idle_seconds,
                idle_brightness=excluded.idle_brightness,
                off_enabled=excluded.off_enabled,
                off_seconds=excluded.off_seconds,
                pi_host=excluded.pi_host,
                updated_at=datetime('now')""",
        (
            device_id,
            1 if merged["enabled"] else 0,
            int(merged["day_brightness"]),
            1 if merged["night_enabled"] else 0,
            str(merged["night_start"]),
            str(merged["night_end"]),
            int(merged["night_brightness"]),
            1 if merged["idle_enabled"] else 0,
            int(merged["idle_seconds"]),
            int(merged["idle_brightness"]),
            1 if merged["off_enabled"] else 0,
            int(merged["off_seconds"]),
            pi_host,
        ),
    )
    await db.commit()

    # Best-effort: poke the Pi panel agent so the new prefs apply immediately.
    asyncio.create_task(_proxy_reload_to_pi(pi_host))

    return {
        "device_id": device_id,
        "pi_host": pi_host,
        "preferences": merged,
    }


@router.post("/display/brightness")
async def post_display_brightness(
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Immediate brightness preview — forwards to the Pi panel agent."""
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    try:
        pct = int(body.get("value", 100))
    except Exception:
        raise HTTPException(status_code=400, detail="value must be an integer 0-100")
    pi_host = body.get("pi_host") or _DEFAULT_PI_HOST
    url = f"http://{pi_host}:{_PANEL_AGENT_PORT}/brightness"
    try:
        async with httpx.AsyncClient(timeout=3.0) as c:
            r = await c.post(url, json={"value": max(0, min(100, pct))})
            if r.status_code != 200:
                raise HTTPException(status_code=502, detail=f"panel agent: HTTP {r.status_code}")
            return r.json()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"panel agent unreachable: {exc}")
