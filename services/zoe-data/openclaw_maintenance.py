"""
OpenClaw installed/latest version detection, upgrade runner, and update notifications.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import uuid

import httpx

from openclaw_ws import NODE_BIN, OPENCLAW_CMD
from push import broadcaster

logger = logging.getLogger(__name__)

OPENCLAW_PKG_JSON = os.path.join(os.path.dirname(NODE_BIN), "lib", "node_modules", "openclaw", "package.json")
GATEWAY_HEALTH_URL = os.environ.get("OPENCLAW_GATEWAY_HEALTH_URL", "http://127.0.0.1:18789/health")
NPM_BIN = os.path.join(NODE_BIN, "npm")


def _openclaw_env() -> dict:
    env = os.environ.copy()
    env["PATH"] = f"{NODE_BIN}:{env.get('PATH', '')}"
    env["HOME"] = os.path.expanduser("~")
    env["CI"] = "true"
    return env


def read_installed_version_sync() -> str | None:
    """Read version from global npm package.json (fast, no subprocess)."""
    try:
        with open(OPENCLAW_PKG_JSON, encoding="utf-8") as f:
            data = json.load(f)
        v = data.get("version")
        return str(v).strip() if v else None
    except OSError:
        return None


async def fetch_gateway_status() -> tuple[str, str | None]:
    """Returns (gateway_status, gateway_model)."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(GATEWAY_HEALTH_URL)
            if r.status_code == 200:
                data = r.json()
                return "connected", data.get("model")
            return "error", None
    except Exception:
        return "offline", None


async def fetch_npm_latest_version(timeout_s: float = 25.0) -> str | None:
    try:
        proc = await asyncio.create_subprocess_exec(
            NPM_BIN,
            "view",
            "openclaw",
            "version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_openclaw_env(),
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        if proc.returncode != 0:
            return None
        raw = (stdout or b"").decode().strip()
        return raw or None
    except Exception as e:
        logger.warning("npm view openclaw version failed: %s", e)
        return None


def version_newer(latest: str, installed: str | None) -> bool:
    if not latest or not installed:
        return bool(latest and not installed)
    if latest.strip() == installed.strip():
        return False
    # CalVer strings like 2026.3.1 — compare tuple of ints where possible
    def parts(v: str) -> tuple:
        out = []
        for p in re.split(r"[^\d]+", v):
            if p.isdigit():
                out.append(int(p))
        return tuple(out) if out else (0,)

    return parts(latest) > parts(installed)


async def run_npm_upgrade_openclaw(timeout_s: float = 600.0) -> tuple[bool, str]:
    """npm install -g openclaw@latest; returns (ok, combined_log_tail)."""
    logs: list[str] = []
    systemctl = shutil.which("systemctl") or "/usr/bin/systemctl"

    async def _run(cmd: list[str], label: str) -> int:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=_openclaw_env(),
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        chunk = (out or b"").decode(errors="replace")[-8000:]
        logs.append(f"=== {label} ===\n{chunk}")
        return proc.returncode or 0

    try:
        rc1 = await _run(
            [NPM_BIN, "install", "-g", "openclaw@latest"],
            "npm install -g openclaw@latest",
        )
        if rc1 != 0:
            return False, "\n".join(logs)

        rc2 = await _run(
            [systemctl, "--user", "restart", "openclaw-gateway.service"],
            "systemctl --user restart openclaw-gateway",
        )
        if rc2 != 0:
            return False, "\n".join(logs)

        new_ver = read_installed_version_sync()
        logs.append(f"Installed version after upgrade: {new_ver}")
        return True, "\n".join(logs)
    except asyncio.TimeoutError:
        return False, "Upgrade timed out. Try again or run manually on the server."
    except Exception as e:
        logger.exception("run_npm_upgrade_openclaw failed")
        return False, str(e)


async def maybe_create_update_notification(db, user_id: str, current: str | None, latest: str) -> bool:
    """
    Insert a deduped openclaw_update notification. Returns True if a new row was inserted.

    Also retires any prior openclaw_update rows for this user whose `latest` is older
    than the incoming one (or equal to the now-installed `current`), so the bell never
    stacks up one-per-release when upgrades go out.
    """
    cursor = await db.execute(
        """SELECT id, data FROM notifications
           WHERE user_id = ? AND delivered = 0 AND data IS NOT NULL""",
        (user_id,),
    )
    rows = await cursor.fetchall()
    superseded_ids: list[str] = []
    for row in rows:
        try:
            d = json.loads(row["data"] or "{}")
        except json.JSONDecodeError:
            continue
        if d.get("kind") != "openclaw_update":
            continue
        row_latest = str(d.get("latest") or "")
        if row_latest == latest:
            return False  # already have an active notif for this exact version
        # Row's target is older than the incoming one (or already installed) → retire.
        if not row_latest or version_newer(latest, row_latest) or (current and not version_newer(row_latest, current)):
            superseded_ids.append(str(row["id"]))

    if superseded_ids:
        placeholders = ",".join("?" * len(superseded_ids))
        await db.execute(
            f"UPDATE notifications SET delivered = 1, action_taken = 'superseded' "
            f"WHERE id IN ({placeholders})",
            superseded_ids,
        )
        try:
            await broadcaster.broadcast("all", "notifications_changed", {"reason": "superseded"})
        except Exception:
            logger.debug("supersede broadcast failed", exc_info=True)

    nid = str(uuid.uuid4())
    title = "OpenClaw update available"
    message = f"Version {latest} is available (you have {current or 'unknown'})."
    payload = json.dumps({"kind": "openclaw_update", "latest": latest, "current": current or ""})
    await db.execute(
        """INSERT INTO notifications (id, user_id, type, title, message, data, delivered, created_at)
           VALUES (?, ?, ?, ?, ?, ?, 0, datetime('now'))""",
        (nid, user_id, "info", title, message, payload),
    )
    await db.commit()
    try:
        await broadcaster.broadcast(
            "all",
            "notification_created",
            {"id": nid, "type": "info", "title": title, "message": message},
        )
    except Exception:
        logger.debug("notification broadcast failed", exc_info=True)
    return True


async def clear_satisfied_update_notifications(db, installed: str | None) -> int:
    """
    Mark as delivered any active `openclaw_update` notifications whose target version
    is now <= the installed version. Returns the count cleared.
    """
    if not installed:
        return 0
    try:
        cursor = await db.execute(
            "SELECT id, data FROM notifications WHERE delivered = 0 AND data IS NOT NULL"
        )
        rows = await cursor.fetchall()
    except Exception:
        return 0

    cleared: list[str] = []
    for row in rows:
        try:
            d = json.loads(row["data"] or "{}")
        except json.JSONDecodeError:
            continue
        if d.get("kind") != "openclaw_update":
            continue
        row_latest = str(d.get("latest") or "")
        if row_latest and not version_newer(row_latest, installed):
            cleared.append(str(row["id"]))

    if cleared:
        placeholders = ",".join("?" * len(cleared))
        await db.execute(
            f"UPDATE notifications SET delivered = 1, action_taken = 'auto-cleared' "
            f"WHERE id IN ({placeholders})",
            cleared,
        )
        await db.commit()
        try:
            await broadcaster.broadcast("all", "notifications_changed", {"reason": "auto-cleared"})
        except Exception:
            logger.debug("auto-clear broadcast failed", exc_info=True)
    return len(cleared)


async def process_auto_update_users(db, latest: str | None) -> None:
    """
    For users with prefs openclaw_auto_update == 'auto', run upgrade if env allows.
    """
    if not latest:
        return
    allow = os.environ.get("OPENCLAW_ALLOW_AUTO_UPDATE", "").lower() == "true"
    if not allow:
        return

    cursor = await db.execute("SELECT user_id, prefs FROM user_preferences")
    rows = await cursor.fetchall()
    for row in rows:
        try:
            prefs = json.loads(row["prefs"] if isinstance(row["prefs"], str) else row["prefs"])
        except (json.JSONDecodeError, TypeError, KeyError):
            prefs = {}
        if prefs.get("openclaw_auto_update") != "auto":
            continue
        uid = row["user_id"]
        cur = read_installed_version_sync()
        if not version_newer(latest, cur):
            continue
        ok, log = await run_npm_upgrade_openclaw()
        logger.info("OpenClaw auto-update for %s: ok=%s %s", uid, ok, log[-500:])
        break  # one upgrade per cycle is enough for the whole system


async def notify_users_with_notify_preference(db, latest: str | None) -> int:
    """Create notifications for users with openclaw_auto_update == 'notify' when update exists."""
    if not latest:
        return 0
    cur_installed = read_installed_version_sync()
    if not version_newer(latest, cur_installed):
        return 0

    cursor = await db.execute("SELECT user_id, prefs FROM user_preferences")
    rows = await cursor.fetchall()
    created = 0
    for row in rows:
        try:
            prefs = json.loads(row["prefs"] if isinstance(row["prefs"], str) else row["prefs"])
        except (json.JSONDecodeError, TypeError, KeyError):
            prefs = {}
        mode = prefs.get("openclaw_auto_update", "notify")
        if mode != "notify":
            continue
        uid = row["user_id"]
        if await maybe_create_update_notification(db, uid, cur_installed, latest):
            created += 1
    # Default users without a row: notify family-admin once
    cursor2 = await db.execute("SELECT id FROM users WHERE role = 'admin' OR id = 'family-admin'")
    admins = await cursor2.fetchall()
    for adm in admins:
        uid = adm["id"]
        cursor3 = await db.execute("SELECT 1 FROM user_preferences WHERE user_id = ?", (uid,))
        if await cursor3.fetchone():
            continue
        if await maybe_create_update_notification(db, uid, cur_installed, latest):
            created += 1
    return created
