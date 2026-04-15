import asyncio
import json
import logging
import os
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


@router.post("/memories/digest")
async def trigger_memory_digest(
    user_id: str = Query(None),
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
