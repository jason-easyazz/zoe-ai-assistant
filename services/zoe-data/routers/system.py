import asyncio
import hmac
import json
import logging
import os
import re
import time
from typing import Any, Literal, Optional
import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from agent_safety import SSRFBlocked, assert_panel_host, is_allowed_panel_host
from auth import (
    get_current_user,
    require_admin,
    get_a2a_caller,
    require_intent_dispatch_auth,
    require_internal_token,
)
from database import get_db
from hermes_http import hermes_auth_headers
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


def _pi_hybrid_production_public_status() -> dict[str, Any]:
    """Minimal non-admin summary for the live Pi hybrid production lane."""
    status = _pi_hybrid_production_status()
    return {
        "report_kind": "zoe_pi_hybrid_production_summary",
        "ok": bool(status.get("ok")),
        "status": status.get("status") or "unknown",
        "details_endpoint": "/api/system/pi-intent/production-status",
    }


def _pi_hybrid_production_status() -> dict[str, Any]:
    """Read-only status for the live Pi hybrid production lane."""
    try:
        from pi_hybrid_production import PiHybridProductionConfig

        config = PiHybridProductionConfig.from_env()
        config_dict = config.to_dict()
    except ValueError as exc:
        return {
            "report_kind": "zoe_pi_hybrid_production_status",
            "ok": False,
            "status": "invalid_config",
            "error": str(exc),
        }
    except Exception as exc:
        return {
            "report_kind": "zoe_pi_hybrid_production_status",
            "ok": False,
            "status": "unavailable",
            "error": exc.__class__.__name__,
        }

    enabled_groups = config_dict.get("groups") or []
    return {
        "report_kind": "zoe_pi_hybrid_production_status",
        "ok": bool(config.enabled and enabled_groups),
        "status": "enabled_no_groups" if config.enabled and not enabled_groups else "enabled" if config.enabled else "disabled",
        "route": "pi_intent_buffer_plus_zoe_safe_fulfillment",
        "surfaces": ["chat_non_stream", "chat_stream", "voice_non_stream"],
        "config": config_dict,
        "notes": [
            "Production Pi hybrid is separate from shadow/promotion evidence status.",
            "Only allowlisted low-risk groups can be enabled here.",
        ],
    }


@router.get("/platform")
async def get_platform():
    return {
        "platform": "zoe-data",
        "version": "1.0.0",
        "engine": "hermes",
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
    """System status for Zoe's active runtime services."""
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
               WHERE last_seen_at::timestamptz >= CURRENT_TIMESTAMP - INTERVAL '30 seconds'"""
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
        "openclaw_default_route": False,
        "openclaw_manual_fallback": gateway_status == "connected",
        "openclaw_model": gateway_model,
        "llama_server": llama_status,
        "llama_model": llama_model,
        "ha_bridge": ha_bridge_status,
        "platform": "aarch64",
        "engine": "hermes",
        "ui_orchestrator": {
            "pending_actions": ui_actions_pending,
            "online_panels_30s": panels_online,
        },
        "pi_hybrid_production": _pi_hybrid_production_public_status(),
    }


@router.get("/memory-router/status")
async def get_memory_router_status(user: dict = Depends(require_admin)):
    """Read-only status for Zoe's disabled-by-default memory router runtime."""
    from zoe_memory_router_runtime import memory_router_runtime_status

    return memory_router_runtime_status()


@router.get("/pi-intent/status")
async def get_pi_intent_status(user: dict = Depends(require_admin)):
    """Read-only status for Zoe's Pi/Gemma ambiguous-intent governor."""
    from pi_intent_classifier import pi_intent_status

    return pi_intent_status()


@router.get("/pi-intent/shadow-status")
async def get_pi_intent_shadow_status(user: dict = Depends(require_admin)):
    """Read-only status for Zoe's disabled-by-default Pi-vs-Zoe shadow evidence."""
    from pi_intent_shadow import pi_intent_shadow_status

    return pi_intent_shadow_status()


@router.get("/pi-intent/hybrid-buffer-status")
async def get_pi_hybrid_buffer_status(user: dict = Depends(require_admin)):
    """Read-only readiness for Zoe's instant-buffer + Pi evidence mode."""
    from pi_hybrid_buffer import pi_hybrid_buffer_status

    return pi_hybrid_buffer_status()


@router.get("/pi-intent/production-status")
async def get_pi_hybrid_production_status(user: dict = Depends(require_admin)):
    """Read-only status for Zoe's live Pi hybrid production lane."""

    return _pi_hybrid_production_status()


@router.get("/pi-intent/readiness-report")
async def get_pi_readiness_report(user: dict = Depends(require_admin)):
    """Read-only operator report for Pi hybrid promotion readiness."""
    from pi_readiness_report import pi_readiness_report

    return pi_readiness_report()


class PiIntentShadowLabelRequest(BaseModel):
    text_hash: str
    outcome_label: Optional[str] = None
    negative: bool = False
    source: Literal["admin_review", "operator_override"] = "admin_review"


@router.post("/pi-intent/shadow-labels")
async def post_pi_intent_shadow_label(payload: PiIntentShadowLabelRequest, user: dict = Depends(require_admin)):
    """Append one trusted admin label for an existing Pi shadow record."""
    from pi_intent_shadow import append_pi_intent_shadow_label

    try:
        return append_pi_intent_shadow_label(
            text_hash=payload.text_hash,
            outcome_label=payload.outcome_label,
            negative=payload.negative,
            source=payload.source,
            reviewed_by=str(user.get("user_id") or "admin"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class PiHybridProductionLabelRequest(BaseModel):
    text_hash: str
    outcome_label: Optional[str] = None
    negative: bool = False
    source: Literal["admin_review", "operator_override"] = "admin_review"
    route_class: Optional[Literal["deterministic", "fallback", "extraction_failed"]] = None
    baseline_kind: Optional[
        Literal[
            "operator_extraction_failed_override",
            "operator_fallback_override",
            "router",
            "router_extraction_failed_not_comparable",
            "router_only_not_comparable",
            "zoe_agent_extraction_failed_baseline",
            "zoe_agent_extraction_failed_error",
            "zoe_agent_extraction_failed_timeout",
            "zoe_agent_fallback_baseline",
            "zoe_agent_fallback_error",
            "zoe_agent_fallback_timeout",
        ]
    ] = None
    baseline_comparable: Optional[bool] = None
    zoe_latency_ms: Optional[float] = None


@router.get("/pi-intent/production-label-queue")
async def get_pi_hybrid_production_label_queue(
    group: list[str] | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    include_labeled: bool = Query(default=False),
    include_rejected: bool = Query(default=False),
    user: dict = Depends(require_admin),
):
    """Read-only queue of sanitized production Pi records awaiting labels."""
    from pi_intent_evidence import (
        apply_pi_hybrid_production_labels,
        build_pi_hybrid_production_label_queue,
        load_pi_hybrid_production_labels,
        load_pi_hybrid_production_records,
    )

    evidence_path = os.environ.get("ZOE_PI_HYBRID_PRODUCTION_EVIDENCE_PATH") or "~/.zoe/data/pi-hybrid-production-evidence.jsonl"
    labels_path = os.environ.get("ZOE_PI_HYBRID_PRODUCTION_LABELS_PATH") or "~/.zoe/data/pi-hybrid-production-labels.jsonl"
    try:
        records = load_pi_hybrid_production_records(evidence_path, limit=max(limit * 10, 500))
        labels = load_pi_hybrid_production_labels(labels_path)
        labeled_records = apply_pi_hybrid_production_labels(records, labels)
        payload = build_pi_hybrid_production_label_queue(
            labeled_records,
            groups=group,
            include_labeled=include_labeled,
            include_rejected=include_rejected,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    payload["summary"]["path"] = os.path.expanduser(evidence_path)
    payload["summary"]["labels_path"] = os.path.expanduser(labels_path)
    return payload


@router.post("/pi-intent/production-labels")
async def post_pi_hybrid_production_label(
    payload: PiHybridProductionLabelRequest,
    user: dict = Depends(require_admin),
):
    """Append one trusted admin label for an existing Pi hybrid production record."""
    from pi_intent_evidence import append_pi_hybrid_production_label

    try:
        return append_pi_hybrid_production_label(
            text_hash=payload.text_hash,
            outcome_label=payload.outcome_label,
            negative=payload.negative,
            source=payload.source,
            reviewed_by=str(user.get("user_id") or "admin"),
            route_class=payload.route_class,
            baseline_kind=payload.baseline_kind,
            baseline_comparable=payload.baseline_comparable,
            zoe_latency_ms=payload.zoe_latency_ms,
            evidence_path=os.environ.get("ZOE_PI_HYBRID_PRODUCTION_EVIDENCE_PATH")
            or "~/.zoe/data/pi-hybrid-production-evidence.jsonl",
            labels_path=os.environ.get("ZOE_PI_HYBRID_PRODUCTION_LABELS_PATH")
            or "~/.zoe/data/pi-hybrid-production-labels.jsonl",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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


class HermesProfilesRequest(BaseModel):
    profiles: list[dict[str, Any]]
    confirm_paid_auto: bool = False


class HermesProfilesApplyRequest(BaseModel):
    profiles: list[dict[str, Any]] | None = None
    confirm_paid_auto: bool = False
    restart: bool = False
    force_restart: bool = False


class HermesProfilesRollbackRequest(BaseModel):
    backup_dir: str | None = None


def _hermes_profile_error(exc: Exception) -> HTTPException:
    if isinstance(exc, RuntimeError):
        status = 409
    elif isinstance(exc, OSError):
        status = 503
    else:
        status = 400
    return HTTPException(status_code=status, detail=str(exc))


@router.get("/hermes/model-profiles/status")
async def get_hermes_model_profiles_status(user: dict = Depends(require_admin)):
    from hermes_model_profiles import count_running_workers, draft_path

    try:
        return {
            "ok": True,
            "running_workers": count_running_workers(),
            "draft_exists": draft_path().exists(),
        }
    except OSError as exc:
        raise _hermes_profile_error(exc) from exc


@router.get("/hermes/model-profiles")
async def get_hermes_model_profiles(user: dict = Depends(require_admin)):
    from hermes_model_profiles import count_running_workers, list_profiles, load_draft

    try:
        draft = load_draft()
        profiles = list_profiles()
        running_workers = count_running_workers()
    except (ValueError, TypeError, OSError) as exc:
        raise _hermes_profile_error(exc) from exc
    return {
        "profiles": profiles,
        "draft": draft,
        "running_workers": running_workers,
    }


@router.put("/hermes/model-profiles/draft")
async def put_hermes_model_profiles_draft(
    body: HermesProfilesRequest,
    user: dict = Depends(require_admin),
):
    from hermes_model_profiles import save_draft

    try:
        return save_draft(body.profiles, confirm_paid_auto=body.confirm_paid_auto)
    except (ValueError, TypeError, OSError) as exc:
        raise _hermes_profile_error(exc) from exc


@router.post("/hermes/model-profiles/validate")
async def post_hermes_model_profiles_validate(
    body: HermesProfilesRequest,
    user: dict = Depends(require_admin),
):
    from hermes_model_profiles import build_diff

    try:
        return build_diff(body.profiles, confirm_paid_auto=body.confirm_paid_auto)
    except (ValueError, TypeError, OSError) as exc:
        raise _hermes_profile_error(exc) from exc


@router.post("/hermes/model-profiles/apply")
async def post_hermes_model_profiles_apply(
    body: HermesProfilesApplyRequest,
    user: dict = Depends(require_admin),
):
    from hermes_model_profiles import apply_profiles

    actor = str(user.get("user_id") or user.get("username") or "unknown")
    try:
        return apply_profiles(
            body.profiles,
            actor=actor,
            confirm_paid_auto=body.confirm_paid_auto,
            restart=body.restart,
            force_restart=body.force_restart,
        )
    except (RuntimeError, ValueError, TypeError, OSError) as exc:
        raise _hermes_profile_error(exc) from exc


@router.post("/hermes/model-profiles/rollback")
async def post_hermes_model_profiles_rollback(
    body: HermesProfilesRollbackRequest,
    user: dict = Depends(require_admin),
):
    from hermes_model_profiles import rollback_profiles

    actor = str(user.get("user_id") or user.get("username") or "unknown")
    try:
        return rollback_profiles(body.backup_dir, actor=actor)
    except (ValueError, TypeError, OSError) as exc:
        raise _hermes_profile_error(exc) from exc


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
           VALUES (?, ?, NOW())
           ON CONFLICT(user_id) DO UPDATE SET prefs = excluded.prefs, updated_at = NOW()""",
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
        # get_db_ctx, not `async for db in get_db()`: the `break` leaked the
        # pooled connection (#953 / the 2026-07-03 pool drain).
        from db_pool import get_db_ctx

        async with get_db_ctx() as db:
            latest = await fetch_npm_latest_version()
            if latest:
                await notify_users_with_notify_preference(db, latest)
                await process_auto_update_users(db, latest)
    except Exception:
        logger.exception("run_scheduled_openclaw_version_check failed")


async def openclaw_background_loop():
    """First check after 3 minutes, then every 24 hours."""
    await asyncio.sleep(180)
    while True:
        await run_scheduled_openclaw_version_check()
        await asyncio.sleep(86400)


def start_openclaw_background_tasks():
    if os.environ.get("OPENCLAW_BACKGROUND_VERSION_CHECK", "false").lower() != "true":
        return None
    return asyncio.create_task(openclaw_background_loop(), name="openclaw_version_check")


# ── Nightly memory digest background loop ────────────────────────────────────

def _attach_memory_loop_log_handler() -> None:
    """Attach a durable rotating file handler to this module's logger.

    The zoe-data systemd unit does not reliably capture app stdout, so the
    memory-loop run-status lines (next-run, run-complete, errors) emitted here
    would otherwise be invisible after the fact — which is exactly how the
    nightly digest broke silently for weeks. Mirror the multica poll-loop
    pattern in main.py: handler-scoped level (never mutate the shared logger
    level), idempotent via a marker attr, and never let logging setup break
    the loop.
    """
    try:
        from logging.handlers import RotatingFileHandler

        if any(getattr(h, "_zoe_memory_loop_log", False) for h in logger.handlers):
            return
        path = os.path.expanduser(
            os.environ.get("ZOE_MEMORY_LOOP_LOG_PATH", "~/.zoe/zoe-data-memory-loops.log")
        )
        log_dir = os.path.dirname(path)
        if log_dir:  # a bare filename → dirname "" → os.makedirs("") would raise
            os.makedirs(log_dir, exist_ok=True)
        fh = RotatingFileHandler(path, maxBytes=2_000_000, backupCount=3)
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        fh.setLevel(logging.INFO)  # handler-scoped; do NOT mutate the shared logger level
        fh._zoe_memory_loop_log = True  # type: ignore[attr-defined]
        logger.addHandler(fh)
        logger.info("memory_loops: diagnostics logging to %s", path)
    except Exception as _log_exc:  # pragma: no cover - logging setup must never break the loop
        logger.warning("memory_loops: could not attach file log: %s", _log_exc)


def _record_memory_loop(loop: str, results) -> dict:
    """Record a completed loop run for observability, never raising.

    Persists last-run timestamp + aggregate effect counts (Prometheus gauges +
    the queryable ``memory_loop_status`` state). A metrics failure must never
    break the maintenance loop, so it degrades to a plain user count.
    """
    try:
        from memory_metrics import record_consolidation_run, record_digest_run

        recorder = record_digest_run if loop == "digest" else record_consolidation_run
        return recorder(results)
    except Exception as _rec_exc:  # pragma: no cover - metrics must never break the loop
        logger.warning("memory_%s: metrics record failed (non-fatal): %s", loop, _rec_exc)
        return {"users": len(results) if results is not None else 0, "effects": {}}


async def _memory_digest_loop():
    """Wait until 3am, then run LLM digest for all active users daily."""
    import datetime
    _attach_memory_loop_log_handler()
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
            summary = _record_memory_loop("digest", results)
            logger.info(
                "memory_digest: nightly run complete — %d users processed, effects=%s",
                summary["users"], summary["effects"],
            )
        except Exception as exc:
            logger.error("memory_digest: nightly loop error: %s", exc, exc_info=True)
        # Phase 6: Evolution NOTICE pass
        try:
            from evolution_notice import run_evolution_notice  # type: ignore[import]
            ev_result = await run_evolution_notice()
            logger.info("evolution_notice: phase 6 complete — %s", ev_result)
        except Exception as exc:
            logger.warning("evolution_notice: phase 6 error (non-fatal): %s", exc)
        # Phase 6b: Evolution MEASURE pass (close 48h monitoring windows)
        try:
            from evolution_notice import run_measure_phase  # type: ignore[import]
            measure_result = await run_measure_phase()
            logger.info("evolution_measure: phase 6b complete — %s", measure_result)
        except Exception as exc:
            logger.warning("evolution_measure: phase 6b error (non-fatal): %s", exc)


def start_memory_digest_background():
    """Start the nightly memory digest loop (if not disabled)."""
    if os.environ.get("MEMORY_DIGEST_ENABLED", "true").lower() != "true":
        return None
    return asyncio.create_task(_memory_digest_loop(), name="memory_digest_nightly")


# ── Weekly memory consolidation (Sunday 04:00) ───────────────────────────────

async def _memory_consolidation_loop():
    """Sleep until the next Sunday 04:00, then run weekly consolidation.

    The consolidation pass merges near-duplicate memories, uses the LLM to
    resolve high-similarity contradictions, and soft-archives low-score
    stale rows. See ``memory_digest.run_weekly_consolidation`` for the
    full algorithm.
    """
    import datetime
    _attach_memory_loop_log_handler()
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
            summary = _record_memory_loop("consolidation", results)
            logger.info(
                "memory_consolidation: weekly run complete — %d users processed, effects=%s",
                summary["users"], summary["effects"],
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


@router.get("/memory-loops/status")
async def get_memory_loops_status(user: dict = Depends(require_admin)):
    """Last-run + staleness for the nightly digest and weekly consolidation loops.

    ``stale: true`` (including a loop that never ran this process) is the signal
    a nightly/Sunday pass was missed — the gap that let the digest break
    silently for weeks. ``age_seconds`` is the age of the last successful run.
    """
    from memory_metrics import memory_loop_status

    return {"loops": memory_loop_status()}


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


@router.post("/run-digest")
async def trigger_run_digest(user: dict = Depends(get_current_user)):
    """Manually trigger full nightly dreaming cycle including evolution notice (admin only)."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")
    from memory_digest import run_digest_for_all_active_users  # type: ignore[import]
    digest_results = await run_digest_for_all_active_users()
    ev_result: dict = {}
    measure_result: dict = {}
    try:
        from evolution_notice import run_evolution_notice, run_measure_phase  # type: ignore[import]
        ev_result = await run_evolution_notice()
        measure_result = await run_measure_phase()
    except Exception as exc:
        ev_result = {"error": str(exc)}
    return {
        "digest": {"users_processed": len(digest_results)},
        "evolution_notice": ev_result,
        "evolution_measure": measure_result,
    }


@router.post("/agent-sync")
async def trigger_agent_sync(user: dict = Depends(get_current_user)):
    """Regenerate ZOE_SELF.md and distribute to all agents (OpenClaw, Hermes, compact).

    Admin-only. Runs the same sync as Phase 5 of the Sunday dreaming cycle.
    Also hot-reloads Multica autopilot schedules into APScheduler.
    """
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")
    from agent_sync import run_agent_sync  # type: ignore[import]
    result = await run_agent_sync()

    # Hot-reload Multica autopilot schedules so cron edits in Multica UI take
    # effect without a service restart.
    try:
        from multica_autopilot_sync import sync_autopilots_from_multica  # type: ignore[import]
        from proactive.scheduler import get_scheduler
        n = await sync_autopilots_from_multica(get_scheduler())
        if isinstance(result, dict):
            result["multica_autopilots"] = {"jobs_registered": n}
    except Exception as _aps_exc:
        logger.warning("agent-sync: Multica autopilot sync failed (non-fatal): %s", _aps_exc)
        if isinstance(result, dict):
            result["multica_autopilots"] = {"error": str(_aps_exc)}

    return result


# ── A2A Agent Card ────────────────────────────────────────────────────────────

# No auth required — the card is public identity info (A2A spec §2.1).
# Exposes only capability metadata, not any user data or secrets.

_agent_card_router = APIRouter(prefix="/api/agent", tags=["agent"])


def _build_agent_card() -> dict:
    """Build an A2A v1.0 compliant agent card for Zoe."""
    from pathlib import Path as _Path

    base_url = os.environ.get("ZOE_BASE_URL", "http://localhost:8000").rstrip("/")

    # Count only the tools registered with the MCP server (not all internal tools).
    mcp_tool_count = 0
    try:
        from mcp_server import TOOLS as _mcp_tools  # type: ignore[import]
        mcp_tool_count = len(_mcp_tools)
    except Exception:
        # Fall back to CAPABILITIES.md line count if mcp_server is unavailable
        capabilities_md = _Path("/home/zoe/assistant/CAPABILITIES.md")
        if capabilities_md.exists():
            try:
                content = capabilities_md.read_text()
                import re as _re
                tool_lines = [l for l in content.splitlines() if l.startswith("- `")]
                mcp_tool_count = len(tool_lines)
            except Exception:
                pass

    # Runtime health from module-level dict (populated at startup)
    from main import _RUNTIME_HEALTH  # type: ignore[import]

    skills = []
    try:
        from mcp_server import TOOLS as _mcp_tools  # type: ignore[import]

        for spec in _mcp_tools:
            tool_name = spec.get("name", "tool")
            skills.append(
                {
                    "id": tool_name,
                    "name": tool_name.replace("_", " ").title(),
                    "description": (spec.get("description") or "")[:500],
                    "inputModes": ["text", "data"],
                    "outputModes": ["text", "data"],
                }
            )
    except Exception:
        skills = []

    agent_tiers = [
        {"tier": 0, "name": "intent_router", "latency_ms": "<10", "model": "regex", "status": "online"},
        {
            "tier": 1,
            "name": "zoe_agent",
            "model": os.environ.get("ZOE_LOCAL_MODEL", "Gemma 4 E4B-QAT"),
            "status": "online" if _RUNTIME_HEALTH.get("local_llm") else "offline",
        },
        {
            "tier": 1.5,
            "name": "hermes",
            "model": "GPT-5.4/Codex",
            "status": "online" if _RUNTIME_HEALTH.get("hermes") else "offline",
        },
        {
            "tier": 2,
            "name": "openclaw",
            "model": "Gemma 4 E4B-QAT",
            "browser": True,
            "status": "online" if _RUNTIME_HEALTH.get("openclaw") else "offline",
        },
    ]

    return {
        "a2aVersion": "1.0",
        "name": "Zoe",
        "version": "2.0",
        "url": base_url,
        "description": (
            "Personal AI companion with persistent memory, voice, home automation, "
            "web capabilities, and a proactive open-loops engine for Zoe-level continuity."
        ),
        "provider": {
            "name": "Zoe",
            "url": base_url,
        },
        "authentication": {
            "schemes": ["Bearer", "ApiKey"],
            "apiKeyHeaders": ["X-Session-ID", "X-Device-Token"],
        },
        "defaultInputModes": ["text"],
        "defaultOutputModes": ["text", "data"],
        "skills": skills,
        "agent_tiers": agent_tiers,
        "mcp_tools": mcp_tool_count,
        "protocols": ["MCP", "ACP", "A2A"],
        "endpoints": {
            "chat": "/api/chat/",
            "health": "/api/system/health",
            "a2a_card": "/api/agent/card",
            "a2a_tasks": "/api/agent/tasks",
            "a2a_tasks_stream": "/api/agent/tasks/stream",
            "registry": "/api/agent/registry",
        },
        "squads": ["zoe"],
    }


@_agent_card_router.get("/card")
async def agent_card():
    """A2A v1.0 agent identity card — public capability advertisement."""
    return _build_agent_card()


class _A2ATaskRequest(BaseModel):
    task: str
    caller: str = "unknown"
    session_id: str | None = None
    context: dict | None = None
    stream: bool = False


class _EngineeringTaskRequest(BaseModel):
    task: str
    title: str | None = None


class _EngineeringGuardRunRequest(BaseModel):
    packet_only: bool = False


@_agent_card_router.post("/tasks")
async def a2a_task(body: _A2ATaskRequest, user: dict = Depends(get_a2a_caller)):
    """
    A2A task intake endpoint.

    Accepts a natural-language task from another agent and routes it through
    Zoe's standard pipeline (intent router -> Zoe Agent -> Hermes escalation).

    Returns a task_id immediately. Results available via GET /api/agent/tasks/{task_id}.
    For streaming, POST to /api/agent/tasks/stream instead.

    Auth: ``Authorization: Bearer <ZOE_A2A_TOKEN>`` (A2A agents) or
    ``X-Session-ID`` / ``X-Device-Token`` (UI/voice paths).
    """
    user_id = user.get("user_id", "a2a-agent")
    session_id = body.session_id or f"a2a-{body.caller}-{__import__('uuid').uuid4().hex[:8]}"

    logger.info(
        "A2A task received: caller=%s user=%s task=%s",
        body.caller, user_id, body.task[:80],
    )

    from background_runner import enqueue_background_task  # type: ignore[import]
    task_id = await enqueue_background_task(body.task, user_id, session_id)
    return {
        "task_id": task_id,
        "session_id": session_id,
        "status": "queued",
        "caller": body.caller,
        "result_endpoint": f"/api/agent/tasks/{task_id}",
    }


@_agent_card_router.post("/tasks/stream")
async def a2a_task_stream(body: _A2ATaskRequest, user: dict = Depends(get_a2a_caller)):
    """A2A streaming task — returns AG-UI SSE events (same format as /api/chat/).

    Dedicated endpoint avoids the 307 redirect issue where some A2A clients
    lose Authorization headers when following redirects.
    """
    from fastapi.responses import StreamingResponse
    import urllib.parse as _up

    user_id = user.get("user_id", "a2a-agent")
    session_id = body.session_id or f"a2a-{body.caller}-{__import__('uuid').uuid4().hex[:8]}"

    async def _stream():
        from routers.chat import chat_stream_generator  # type: ignore[import]
        _a2a_user = {
            "user_id": user_id,
            "role": user.get("role", "agent"),
            "username": user.get("username", f"a2a:{body.caller}"),
        }
        async for chunk in chat_stream_generator(
            message=body.task,
            session_id=session_id,
            user=_a2a_user,
        ):
            yield chunk

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={
            "X-A2A-Session-ID": session_id,
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@_agent_card_router.get("/tasks")
async def list_agent_tasks(
    limit: int = 20,
    status: Literal["running", "pending", "done", "error", "blocked"] | None = None,
    user: dict = Depends(get_current_user),
):
    """List recent background tasks for the current user."""
    from db_pool import get_db_ctx as _get_pg_db

    user_id = user.get("user_id", "")
    if limit < 1 or limit > 100:
        limit = 20

    def _ts(val):
        return val.isoformat() if hasattr(val, "isoformat") else val

    try:
        async with _get_pg_db() as db:
            if status:
                rows = await db.fetch(
                    "SELECT id, task, status, created_at, completed_at, multica_issue_id "
                    "FROM background_tasks WHERE user_id=$1 AND status=$2 "
                    "ORDER BY created_at DESC LIMIT $3",
                    user_id, status, limit,
                )
            else:
                rows = await db.fetch(
                    "SELECT id, task, status, created_at, completed_at, multica_issue_id "
                    "FROM background_tasks WHERE user_id=$1 "
                    "ORDER BY created_at DESC LIMIT $2",
                    user_id, limit,
                )
    except Exception as exc:
        logger.error("list_agent_tasks DB error for user=%s: %s", user_id, exc)
        raise HTTPException(status_code=500, detail="Internal server error") from exc

    return {
        "tasks": [
            {
                "task_id": str(r["id"]),
                "task": r["task"],
                "status": r["status"],
                "created_at": _ts(r["created_at"]),
                "completed_at": _ts(r["completed_at"]),
                "multica_issue_id": r["multica_issue_id"],
            }
            for r in rows
        ]
    }


@_agent_card_router.get("/tasks/{task_id}")
async def a2a_task_result(task_id: str, user: dict = Depends(get_a2a_caller)):
    """Poll for the result of a previously submitted A2A task."""

    from db_pool import get_db_ctx as _get_pg_db

    # task IDs are SERIAL integers; reject non-numeric IDs immediately.
    try:
        task_id_int = int(task_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=404, detail="Task not found")

    try:
        async with _get_pg_db() as db:
            row = await db.fetchrow(
                "SELECT id, user_id, task, status, result, created_at, completed_at "
                "FROM background_tasks WHERE id=$1",
                task_id_int,
            )
    except Exception as exc:
        logger.error("a2a_task_result DB error for task_id=%s: %s", task_id, exc)
        raise HTTPException(status_code=500, detail="Internal server error") from exc

    if row is None:
        raise HTTPException(status_code=404, detail="Task not found")
    caller_user_id = user.get("user_id", "")
    # A2A agents (role=agent) can read any task; session users only see their own
    if user.get("role") not in ("admin", "agent") and row["user_id"] != caller_user_id:
        raise HTTPException(status_code=403, detail="Not your task")

    def _ts(val):
        return val.isoformat() if hasattr(val, "isoformat") else val

    return {
        "task_id": task_id,
        "status": row["status"],
        "task": row["task"],
        "result": row["result"],
        "created_at": _ts(row["created_at"]),
        "completed_at": _ts(row["completed_at"]),
    }


# ── Engineering board (Multica SoT + Kanban execution) ────────────────────────

@_agent_card_router.post("/engineering/tasks")
async def create_engineering_workflow_task(
    body: _EngineeringTaskRequest,
    user: dict = Depends(require_admin),
):
    """Add a Hermes-assigned Multica issue and dispatch it to the Kanban seam."""
    from executor_registry import dispatch_issue  # type: ignore[import]
    from multica_client import MULClient, get_engineering_multica_agent_id  # type: ignore[import]

    client = MULClient()
    if not client.is_configured():
        return {"ok": False, "reason": "Multica not configured"}
    issue = await client.create_issue(
        title=(body.title or body.task)[:120],
        description=body.task,
        priority="medium",
        assignee_id=get_engineering_multica_agent_id(),
        assignee_type="agent",
    )
    dispatch = await dispatch_issue(issue) if isinstance(issue, dict) and issue.get("id") else None
    return {"ok": True, "issue": issue, "dispatch": dispatch}


@_agent_card_router.get("/engineering/tasks")
async def list_engineering_workflow_tasks(
    limit: int = 20,
    status: str | None = None,
    user: dict = Depends(require_admin),
):
    """Status view: Hermes-assigned Multica issues with journaled driver state."""
    from executor_registry import poll_ref  # type: ignore[import]
    from multica_client import MULClient, get_engineering_multica_agent_id  # type: ignore[import]

    client = MULClient()
    if not client.is_configured():
        return {"tasks": []}
    hermes_id = str(get_engineering_multica_agent_id())
    statuses = [status] if status in ("todo", "in_progress", "in_review", "done") else ["in_progress", "todo"]
    tasks: list[dict] = []
    for st in statuses:
        for issue in await client.list_issues(status=st) or []:
            if str(issue.get("assignee_id") or "") != hermes_id:
                continue
            if (issue.get("title") or "").lower().startswith("autopilot:"):
                continue
            chain = await poll_ref(f"multica:{issue.get('id')}", issue=issue)
            tasks.append({
                "issue_id": issue.get("id"),
                "identifier": issue.get("identifier"),
                "title": issue.get("title"),
                "multica_status": st,
                "chain_status": chain.get("status") if chain.get("found") else None,
                "phases": chain.get("phases"),
                "pr_url": chain.get("pr_url"),
                "blocker": chain.get("blocker"),
            })
            if len(tasks) >= limit:
                return {"tasks": tasks}
    return {"tasks": tasks}


@_agent_card_router.get("/engineering/guard/{pr_number}")
async def get_engineering_guard(pr_number: int, user: dict = Depends(require_admin)):
    """PR-keyed Greptile grep-loop guard state."""
    from greploop_guard import read_observed_guard_state

    return {"ok": True, "pr_number": pr_number, "state": read_observed_guard_state(pr_number)}


@_agent_card_router.post("/engineering/guard/{pr_number}/once")
async def run_engineering_guard_once(
    pr_number: int,
    body: _EngineeringGuardRunRequest | None = None,
    user: dict = Depends(require_admin),
):
    from greploop_guard import GuardError, run_guard_once

    try:
        return await run_guard_once(pr_number, packet_only=bool(body.packet_only if body else False))
    except GuardError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@_agent_card_router.post("/engineering/guard/{pr_number}/packet")
async def build_engineering_guard_packet(pr_number: int, user: dict = Depends(require_admin)):
    from greploop_guard import GuardError, run_guard_once

    try:
        return await run_guard_once(pr_number, packet_only=True)
    except GuardError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ── A2A Registry + Peer Cards + Squad ────────────────────────────────────────

_AGENTS_REGISTRY_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "agents_registry.yml")
_registry_cache: dict = {}
_registry_loaded: bool = False


def _load_registry() -> dict:
    global _registry_cache, _registry_loaded
    if _registry_loaded:
        return _registry_cache
    return _reload_registry()


def _reload_registry() -> dict:
    global _registry_cache, _registry_loaded
    try:
        import yaml
        with open(_AGENTS_REGISTRY_PATH) as f:
            _registry_cache = yaml.safe_load(f) or {}
        _registry_loaded = True
        logger.info("Agent registry loaded from %s", _AGENTS_REGISTRY_PATH)
    except Exception as exc:
        logger.warning("Failed to load agents_registry.yml: %s", exc)
        _registry_cache = {"agents": {}, "squads": {}}
        _registry_loaded = True
    return _registry_cache


@_agent_card_router.get("/registry")
async def get_agent_registry():
    """List all registered peer agents with runtime health."""
    registry = _load_registry()
    from main import _RUNTIME_HEALTH  # type: ignore[import]

    agents_out = {}
    for name, info in registry.get("agents", {}).items():
        health_port = info.get("health_port")
        is_online = _RUNTIME_HEALTH.get(name, False)
        agents_out[name] = {
            "description": info.get("description", ""),
            "model": info.get("model", ""),
            "base_url": info.get("base_url", ""),
            "skills": info.get("skills", []),
            "status": "online" if is_online else "offline",
        }

    return {
        "agents": agents_out,
        "squads": registry.get("squads", {}),
    }


@_agent_card_router.post("/registry/reload")
async def reload_agent_registry(user: dict = Depends(require_admin)):
    """Admin-only: hot-reload agents_registry.yml from disk."""
    global _registry_loaded
    _registry_loaded = False
    registry = _reload_registry()
    return {"ok": True, "agents": list(registry.get("agents", {}).keys())}


@_agent_card_router.post("/delegate")
async def delegate_to_agent(
    body: dict,
    user: dict = Depends(get_a2a_caller),
):
    """Delegate a task to a named peer agent via A2A."""
    # Accept both {agent_name, task} and {agent, goal} parameter styles
    agent_name = body.get("agent_name") or body.get("agent", "")
    task = body.get("task") or body.get("goal", "")
    if not agent_name or not task:
        raise HTTPException(status_code=400, detail="agent_name (or agent) and task (or goal) are required")

    if agent_name == "openclaw" and not bool(body.get("allow_openclaw", False)):
        raise HTTPException(
            status_code=400,
            detail=(
                "OpenClaw is available only as an explicit fallback. "
                "Set allow_openclaw=true after the user/operator specifically asks for OpenClaw; "
                "otherwise delegate to Hermes."
            ),
        )

    if agent_name == "hermes":
        from background_runner import enqueue_background_task  # type: ignore[import]
        user_id = user.get("user_id", "unknown")
        session_id = body.get("session_id") or None
        task_id = await enqueue_background_task(
            task,
            user_id,
            session_id=session_id,
            request_depth=int(body.get("request_depth") or 0),
        )
        return {
            "agent": agent_name,
            "result": {
                "status": "queued",
                "task_id": task_id,
                "result_endpoint": f"/api/agent/tasks/{task_id}",
            },
        }

    registry = _load_registry()
    agent_info = registry.get("agents", {}).get(agent_name)
    if not agent_info:
        raise HTTPException(status_code=404, detail=f"Unknown agent: {agent_name}")

    from a2a_client import get_a2a_client  # type: ignore[import]
    client = get_a2a_client()
    result = await client.submit_task(
        base_url=agent_info["base_url"],
        task=task,
        caller=f"zoe-{user.get('user_id', 'unknown')}",
        token=agent_info.get("a2a_token", ""),
    )
    return {"agent": agent_name, "result": result}


@_agent_card_router.get("/squad")
async def get_squad():
    """Return the formalized agent squad topology."""
    registry = _load_registry()
    from main import _RUNTIME_HEALTH  # type: ignore[import]

    squads = registry.get("squads", {})
    agents = registry.get("agents", {})

    # Enrich members with live health
    for squad_name, squad in squads.items():
        enriched_members = []
        for member in squad.get("members", []):
            enriched_members.append({
                "name": member,
                "status": "online" if _RUNTIME_HEALTH.get(member) else "offline",
                "description": agents.get(member, {}).get("description", ""),
            })
        squad["members_detail"] = enriched_members

    return {"squads": squads}


@_agent_card_router.get("/runtimes")
async def get_agent_runtimes():
    """Return live runtime health for all agent endpoints with last-probe timestamp."""
    from main import _RUNTIME_HEALTH, _RUNTIME_LAST_PROBED  # type: ignore[import]
    return {
        "last_probed": _RUNTIME_LAST_PROBED or None,
        "refresh_interval_s": 300,
        "runtimes": {
            "local_llm": {
                "port": 11434,
                "description": "llama-server (Gemma 4 E4B-QAT, fast-path agent)",
                "online": _RUNTIME_HEALTH.get("local_llm", False),
            },
            "hermes": {
                "port": 8642,
                "description": "Hermes Agent (GPT-5.4/Codex)",
                "online": _RUNTIME_HEALTH.get("hermes", False),
            },
            "openclaw": {
                "port": 18789,
                "description": "OpenClaw Gateway (Gemma 4 E4B-QAT, browser/exec)",
                "online": _RUNTIME_HEALTH.get("openclaw", False),
            },
        },
    }


@_agent_card_router.get("/peers/{name}/card")
async def get_peer_agent_card(name: str):
    """Return a live A2A v1.0 proxy card for a peer agent with dynamic skills[]."""
    registry = _load_registry()
    agent_info = registry.get("agents", {}).get(name)
    if not agent_info:
        raise HTTPException(status_code=404, detail=f"Unknown peer agent: {name}")

    from main import _RUNTIME_HEALTH  # type: ignore[import]
    from skill_discovery import parse_openclaw_skills, parse_hermes_skills  # type: ignore[import]

    if name == "openclaw":
        skills = parse_openclaw_skills()
    elif name == "hermes":
        skills = parse_hermes_skills()
    else:
        skills = [{"id": s, "name": s, "description": s} for s in agent_info.get("skills", [])]

    return {
        "a2aVersion": "1.0",
        "name": name,
        "description": agent_info.get("description", ""),
        "model": agent_info.get("model", ""),
        "base_url": agent_info.get("base_url", ""),
        "status": "online" if _RUNTIME_HEALTH.get(name) else "offline",
        "skills": skills,
        "defaultInputModes": ["text"],
        "defaultOutputModes": ["text"],
    }


@_agent_card_router.post("/peers/{name}/skills/reload")
async def reload_peer_skills(name: str, user: dict = Depends(require_admin)):
    """Admin-only: force-flush the skill discovery cache for a peer agent."""
    from skill_discovery import invalidate_openclaw_cache, invalidate_hermes_cache  # type: ignore[import]

    if name == "openclaw":
        invalidate_openclaw_cache()
        from skill_discovery import parse_openclaw_skills  # type: ignore[import]
        skills = parse_openclaw_skills()
    elif name == "hermes":
        invalidate_hermes_cache()
        from skill_discovery import parse_hermes_skills  # type: ignore[import]
        skills = parse_hermes_skills()
    else:
        raise HTTPException(status_code=404, detail=f"Unknown peer agent: {name}")

    return {"ok": True, "agent": name, "skill_count": len(skills)}


# ── Cost tracking + LLM stats ────────────────────────────────────────────────

@_agent_card_router.get("/costs")
async def get_agent_costs(
    period: str = "30d",
    user: dict = Depends(require_admin),
):
    """Per-agent cost rollup for the given period (7d, 30d, 90d)."""
    import time as _time
    from db_pool import get_db_ctx as _get_pg_db

    days = {"7d": 7, "30d": 30, "90d": 90}.get(period, 30)
    cutoff = _time.time() - days * 86400

    async with _get_pg_db() as db:
        rows = await db.fetch(
            """SELECT agent_name, model,
                      SUM(input_tokens) as input_tokens,
                      SUM(output_tokens) as output_tokens,
                      SUM(estimated_cost_usd) as total_cost_usd,
                      COUNT(*) as events
               FROM agent_cost_events
               WHERE ts >= $1
               GROUP BY agent_name, model
               ORDER BY total_cost_usd DESC""",
            cutoff,
        )
    return {
        "period": period,
        "agents": [
            {
                "agent_name": r["agent_name"],
                "model": r["model"],
                "input_tokens": r["input_tokens"],
                "output_tokens": r["output_tokens"],
                "total_cost_usd": round(float(r["total_cost_usd"]), 6),
                "events": r["events"],
            }
            for r in rows
        ],
    }


@_agent_card_router.get("/llm-stats")
async def get_llm_stats(
    period: str = "7d",
    user: dict = Depends(require_admin),
):
    """Per-tier LLM call stats for the given period."""
    import time as _time
    from db_pool import get_db_ctx as _get_pg_db

    days = {"7d": 7, "30d": 30}.get(period, 7)
    cutoff = _time.time() - days * 86400

    async with _get_pg_db() as db:
        rows = await db.fetch(
            """SELECT agent_tier, model,
                      COUNT(*) as calls,
                      AVG(latency_ms) as avg_latency_ms,
                      SUM(prompt_tokens) as total_prompt_tokens,
                      SUM(completion_tokens) as total_completion_tokens,
                      SUM(estimated_cost_usd) as total_cost_usd
               FROM llm_call_log
               WHERE ts >= $1
               GROUP BY agent_tier, model
               ORDER BY calls DESC""",
            cutoff,
        )
    return {
        "period": period,
        "tiers": [
            {
                "agent_tier": r["agent_tier"],
                "model": r["model"],
                "calls": r["calls"],
                "avg_latency_ms": round(float(r["avg_latency_ms"] or 0), 1),
                "total_prompt_tokens": r["total_prompt_tokens"],
                "total_completion_tokens": r["total_completion_tokens"],
                "total_cost_usd": round(float(r["total_cost_usd"] or 0), 6),
            }
            for r in rows
        ],
    }


@_agent_card_router.get("/board")
async def get_agent_board(user: dict = Depends(get_current_user)):  # noqa: ARG001 - auth-only
    """Return Multica engineering ticket state for AG-UI display.

    Falls back gracefully if Multica is not configured or unavailable.
    """
    try:
        from executor_registry import poll_ref  # type: ignore[import]
        from multica_client import MULClient, get_engineering_multica_agent_id  # type: ignore[import]

        client = MULClient()
        if not client.is_configured():
            return {"active": [], "groups": {}, "available": False, "reason": "Multica not configured"}

        statuses = ("backlog", "todo", "in_progress", "blocked", "in_review")
        hermes_id = str(get_engineering_multica_agent_id())
        groups: dict[str, list[dict]] = {status: [] for status in statuses}
        active: list[dict] = []
        hermes_issues: list[dict] = []

        status_results = await asyncio.gather(
            *(client.list_issues(status=status) for status in statuses),
            return_exceptions=True,
        )
        for status, issues_or_exc in zip(statuses, status_results):
            if isinstance(issues_or_exc, Exception):
                continue
            for issue in issues_or_exc or []:
                enriched = dict(issue)
                try:
                    from multica_ticket_contract import parse_ticket_block

                    ticket = parse_ticket_block(issue.get("description") or "")
                except Exception:
                    ticket = {}
                enriched["phase"] = ticket.get("phase")
                enriched["blocker"] = ticket.get("blocked_reason")
                enriched["pr_url"] = ticket.get("pr_url")
                enriched["child_count"] = len(ticket.get("child_issue_ids") or [])
                if status in {"in_progress", "blocked", "in_review"} and str(issue.get("assignee_id") or "") == hermes_id:
                    hermes_issues.append(enriched)
                groups[status].append(enriched)
                if status in {"in_progress", "in_review"}:
                    active.append(enriched)

        async def enrich_chain(issue: dict) -> None:
            try:
                chain = await poll_ref(f"multica:{issue.get('id')}", issue=issue)
            except Exception as exc:
                issue["chain_error"] = str(exc)
                return
            issue["chain"] = chain
            pipeline = chain.get("pipeline") if isinstance(chain, dict) else None
            if isinstance(pipeline, dict):
                issue["phase"] = pipeline.get("phase")
                issue["needs_split"] = pipeline.get("needs_split")
                issue["split_packet"] = pipeline.get("split_packet")
            issue["blocker"] = chain.get("blocker") if isinstance(chain, dict) else None
            issue["pr_url"] = chain.get("pr_url") if isinstance(chain, dict) else None

        if hermes_issues:
            await asyncio.gather(*(enrich_chain(issue) for issue in hermes_issues))

        return {"active": active, "groups": groups, "available": True}
    except ImportError:
        return {"active": [], "groups": {}, "available": False, "reason": "Multica client not installed"}
    except Exception as exc:
        return {"active": [], "groups": {}, "available": False, "reason": str(exc)}


@_agent_card_router.post("/board/approve")
async def board_approve(task_id: str, user: dict = Depends(require_admin)):
    """Create a Hermes-assigned Multica issue and dispatch it via the executor seam."""
    try:
        from multica_client import MULClient, get_engineering_multica_agent_id  # type: ignore[import]
        from executor_registry import dispatch_issue  # type: ignore[import]

        client = MULClient()
        if not client.is_configured():
            return {"ok": False, "reason": "Multica not configured — route via Hermes manually"}

        issue = await client.create_issue(
            title=f"Task: {task_id}",
            description=f"Approved via Zoe chat. Task ID: {task_id}",
            priority="medium",
            assignee_id=get_engineering_multica_agent_id(),
            assignee_type="agent",
        )
        issue_id = issue.get("id") if isinstance(issue, dict) else None
        dispatch = None
        if issue_id:
            dispatch = await dispatch_issue(issue)
        return {"ok": True, "issue": issue, "dispatch": dispatch}
    except Exception as exc:
        return {"ok": False, "reason": str(exc)}


@_agent_card_router.post("/board/review")
async def board_review(task_id: str, user: dict = Depends(get_current_user)):
    """Create a Multica board issue unassigned for human review."""
    try:
        from multica_client import MULClient  # type: ignore[import]
        client = MULClient()
        if not client.is_configured():
            return {"ok": False, "reason": "Multica not configured"}
        issue = await client.create_issue(
            title=f"Task (review): {task_id}",
            description=f"Queued for review. Task ID: {task_id}",
            priority="low",
        )
        return {"ok": True, "issue": issue}
    except Exception as exc:
        return {"ok": False, "reason": str(exc)}


@_agent_card_router.post("/board/cancel")
async def board_cancel(task_id: str, user: dict = Depends(get_current_user)):
    """Cancel a pending board task."""
    return {"ok": True, "task_id": task_id, "status": "cancelled"}


def _multica_webhook_dispatch_allowed(request: Request) -> bool:
    """Return True when a webhook is allowed to start code-changing work."""
    secret = os.environ.get("MULTICA_WEBHOOK_SECRET", "").strip()
    if not secret:
        return False
    auth = request.headers.get("authorization", "")
    token = request.headers.get("x-multica-webhook-token", "")
    return (
        hmac.compare_digest(auth, f"Bearer {secret}")
        or hmac.compare_digest(token, secret)
    )


@_agent_card_router.post("/board/webhook")
async def multica_webhook(request: Request):
    """Receive Multica webhook events and update evolution proposal status."""
    try:
        payload = await request.json()
        event = payload.get("event", "")
        issue = payload.get("issue", {})

        if event == "issue.status_changed" and issue:
            logger.info(
                "Multica webhook: %s issue=%s status=%s",
                event, issue.get("identifier"), issue.get("status"),
            )
            # If the issue maps to an evolution proposal, sync status back
            multica_issue_id = issue.get("id")
            new_status = issue.get("status", "")
            if multica_issue_id and new_status:
                _status_map = {
                    "in_progress": "approved",
                    "done": "validated",
                    "cancelled": "failed",
                }
                proposal_status = _status_map.get(new_status)
                if proposal_status:
                    try:
                        from db_pool import get_db_ctx as _get_pg_db  # type: ignore[import]
                        async with _get_pg_db() as db:
                            await db.execute(
                                "UPDATE evolution_proposals SET status=$1 WHERE multica_issue_id=$2",
                                proposal_status, multica_issue_id,
                            )
                            logger.info(
                                "Multica webhook: synced proposal multica_id=%s → status=%s",
                                multica_issue_id, proposal_status,
                            )
                    except Exception as db_exc:
                        logger.warning("Multica webhook DB sync error: %s", db_exc)

        elif event == "issue.assigned" and issue:
            logger.info(
                "Multica webhook: %s issue=%s assignee=%s",
                event, issue.get("identifier"), issue.get("assignee_id"),
            )
            from multica_client import get_engineering_multica_agent_id  # type: ignore[import]

            if str(issue.get("assignee_id") or "") == get_engineering_multica_agent_id():
                if not _multica_webhook_dispatch_allowed(request):
                    logger.warning(
                        "Multica webhook: skipped Hermes dispatch for issue=%s; webhook dispatch auth missing",
                        issue.get("identifier") or issue.get("id"),
                    )
                    return {"ok": True, "dispatched": False, "reason": "dispatch auth required"}
                try:
                    from executor_registry import dispatch_issue  # type: ignore[import]

                    if str(issue.get("id") or ""):
                        result = await dispatch_issue(issue)
                        return {"ok": True, "dispatched": bool(result.get("ok")), "dispatch": result}
                except Exception as dispatch_exc:
                    logger.warning("Multica webhook: Hermes dispatch failed: %s", dispatch_exc)

        elif event == "issue.created" and issue:
            logger.info(
                "Multica webhook: new issue %s — %s",
                issue.get("identifier"), issue.get("title", "")[:80],
            )

        return {"ok": True}
    except Exception as exc:
        logger.warning("Multica webhook error: %s", exc)
        return {"ok": False, "error": str(exc)}


# ── Evolution proposals ───────────────────────────────────────────────────────

@_agent_card_router.get("/evolution/proposals")
async def get_evolution_proposals(
    status: str = "pending",
    limit: int = 20,
    user: dict = Depends(get_current_user),
):
    """List evolution proposals by status (pending|approved|rejected|deployed|validated|failed)."""
    from db_pool import get_db_ctx as _get_pg_db

    async with _get_pg_db() as db:
        rows = await db.fetch(
            """SELECT id, type, title, description, evidence, target_patterns,
                      status, multica_issue_id, proposed_at, reviewed_at, deployed_at,
                      validation_result, next_review_at
               FROM evolution_proposals
               WHERE status=$1
               ORDER BY proposed_at DESC
               LIMIT $2""",
            status, limit,
        )
    return {
        "proposals": [
            {
                "id": r["id"],
                "type": r["type"],
                "title": r["title"],
                "description": r["description"],
                "evidence": r["evidence"],
                "status": r["status"],
                "proposed_at": r["proposed_at"],
            }
            for r in rows
        ],
        "count": len(rows),
    }


async def _hermes_review_proposal(proposal: dict) -> tuple[bool, str]:
    """Ask Hermes to review an evolution proposal before implementation is queued.

    Returns (approved: bool, feedback: str).
    Fails open — caller must catch all exceptions and proceed if Hermes is unavailable.
    """
    hermes_url = os.environ.get("HERMES_API_URL", "http://127.0.0.1:8642")
    hermes_model = os.environ.get("HERMES_MODEL", "hermes-agent")
    prompt = (
        "Review this Zoe evolution proposal for implementation readiness.\n"
        "Reply with APPROVED on the first line if safe to proceed, or REJECT if not.\n"
        "If you have concerns, include a CONCERNS: section with concise actionable feedback.\n\n"
        f"Title: {proposal.get('title', '')}\n\n"
        f"Description: {proposal.get('description', '')}\n\n"
        f"Evidence: {proposal.get('evidence', '')}"
    )
    headers = {"Content-Type": "application/json", **hermes_auth_headers()}
    async with httpx.AsyncClient(timeout=90) as client:
        async with client.stream(
            "POST",
            f"{hermes_url}/v1/chat/completions",
            json={
                "model": hermes_model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": True,
            },
            headers=headers,
        ) as resp:
            resp.raise_for_status()
            content = ""
            async for line in resp.aiter_lines():
                if line.startswith("data: ") and "[DONE]" not in line:
                    try:
                        chunk = json.loads(line[6:])
                        content += chunk.get("choices", [{}])[0].get("delta", {}).get("content", "")
                    except Exception:
                        pass
    feedback = content.strip()
    upper = feedback.upper()
    approved = "APPROVED" in upper and "REJECT" not in upper and "CONCERNS:" not in upper
    return approved, feedback


@_agent_card_router.post("/evolution/proposals/{proposal_id}/action")
async def evolution_proposal_action(
    proposal_id: str,
    body: dict,
    user: dict = Depends(get_current_user),
):
    """Act on an evolution proposal: approve|reject|defer.

    On approve: creates a Multica board issue and queues Hermes implementation.
    On reject: archives the proposal.
    On defer: snoozes for 7 days.
    """
    import time as _time
    action = body.get("action", "")
    reason = body.get("reason", "")

    if action not in ("approve", "reject", "defer", "deploy"):
        raise HTTPException(status_code=400, detail="action must be approve|reject|defer|deploy")

    from db_pool import get_db_ctx as _get_pg_db

    async with _get_pg_db() as db:
        rows = await db.fetch(
            "SELECT * FROM evolution_proposals WHERE id=$1", proposal_id
        )
        if not rows:
            raise HTTPException(status_code=404, detail="Proposal not found")
        proposal = dict(rows[0])

        if action == "approve":
            existing_multica_id = proposal.get("multica_issue_id")
            multica_issue_id = existing_multica_id
            try:
                from multica_client import (  # type: ignore[import]
                    sync_evolution_proposal_to_multica,
                    update_multica_issue_on_proposal_status_change,
                )
                if existing_multica_id:
                    # Issue already created by run_evolution_notice — update status
                    await update_multica_issue_on_proposal_status_change(
                        existing_multica_id, "approved"
                    )
                else:
                    # Create issue now (proposal pre-dates the sync or created outside NOTICE)
                    new_id = await sync_evolution_proposal_to_multica(
                        proposal_id=proposal_id,
                        title=proposal["title"],
                        description=proposal["description"],
                        evidence=proposal.get("evidence", ""),
                        proposal_type=proposal.get("type", "intent_pattern"),
                        contract_snapshot=proposal.get("target_patterns"),
                    )
                    if new_id:
                        multica_issue_id = new_id
                        await update_multica_issue_on_proposal_status_change(new_id, "approved")
            except Exception as exc:
                logger.warning("Could not sync Multica for proposal %s: %s", proposal_id, exc)

            await db.execute(
                """UPDATE evolution_proposals
                   SET status='approved', reviewed_at=$1, multica_issue_id=$2
                   WHERE id=$3""",
                _time.time(), multica_issue_id, proposal_id,
            )
            # Hermes code-review gate before queuing implementation.
            # Fails open — if Hermes is unavailable we proceed normally.
            try:
                _h_approved, _h_feedback = await _hermes_review_proposal(proposal)
                if not _h_approved and re.search(r"CONCERNS:|REJECT", _h_feedback, re.IGNORECASE):
                    logger.info(
                        "evolution_approve: Hermes flagged concerns for proposal %s", proposal_id
                    )
                    if multica_issue_id:
                        try:
                            from multica_client import get_multica_client  # type: ignore[import]
                            _mc = get_multica_client()
                            if _mc.is_configured():
                                async with httpx.AsyncClient(timeout=30) as _hc:
                                    _cur_desc = proposal.get("description", "")
                                    await _hc.put(
                                        f"{_mc._base}/api/issues/{multica_issue_id}",
                                        json={"description": f"{_cur_desc}\n\nHermes review:\n{_h_feedback}"},
                                        headers=_mc._headers(),
                                    )
                        except Exception as _exc:
                            logger.warning(
                                "evolution_approve: could not append Hermes feedback to Multica issue: %s", _exc
                            )
                    await db.execute(
                        "UPDATE evolution_proposals SET status='pending' WHERE id=$1", proposal_id
                    )
                    return {"ok": True, "action": "hermes_review_required", "feedback": _h_feedback}
                logger.info("evolution_approve: Hermes cleared proposal %s", proposal_id)
            except Exception as exc:
                logger.warning(
                    "evolution_approve: Hermes review failed — proceeding without review for proposal %s: %s",
                    proposal_id, exc,
                )

            # Dispatch the approved proposal to the Kanban executor (via Multica issue).
            _dispatch = None
            try:
                from executor_registry import dispatch_issue  # type: ignore[import]
                from multica_client import get_engineering_multica_agent_id  # type: ignore[import]

                if multica_issue_id:
                    _issue = {
                        "id": multica_issue_id,
                        "identifier": proposal_id,
                        "title": proposal["title"],
                        "description": (
                            f"Implement evolution proposal {proposal_id}: "
                            f"{proposal['title']}.\n\n{proposal['description']}"
                        ),
                        "assignee_id": get_engineering_multica_agent_id(),
                    }
                    _dispatch = await dispatch_issue(_issue)
                    logger.info(
                        "evolution_approve: dispatched proposal %s -> %s",
                        proposal_id, _dispatch.get("chain") if _dispatch.get("ok") else _dispatch,
                    )
                else:
                    # No Multica issue to anchor the journaled engineering run (Multica
                    # unconfigured or the issue sync failed). Surface it so the
                    # approved proposal does not sit undispatched silently.
                    _dispatch = {"ok": False, "reason": "no multica_issue_id; proposal approved but not dispatched"}
                    logger.warning(
                        "evolution_approve: proposal %s approved but NOT dispatched — %s",
                        proposal_id, _dispatch["reason"],
                    )
            except Exception as exc:
                _dispatch = {"ok": False, "reason": str(exc)}
                logger.warning("evolution_approve: could not dispatch proposal to Kanban: %s", exc)
            return {
                "ok": True,
                "action": "approved",
                "multica_issue_id": multica_issue_id,
                "dispatch": _dispatch,
            }

        elif action == "deploy":
            # Called by background runner when Hermes completes implementation
            now = _time.time()
            await db.execute(
                """UPDATE evolution_proposals
                   SET status='deployed', deployed_at=$1
                   WHERE id=$2""",
                now, proposal_id,
            )
            # Sync deployed status to Multica
            existing_multica_id = proposal.get("multica_issue_id")
            if existing_multica_id:
                try:
                    from multica_client import update_multica_issue_on_proposal_status_change  # type: ignore[import]
                    await update_multica_issue_on_proposal_status_change(
                        existing_multica_id, "deployed"
                    )
                except Exception as exc:
                    logger.warning("Multica deploy sync failed for proposal %s: %s", proposal_id, exc)
            return {"ok": True, "action": "deployed", "deployed_at": now}

        elif action == "reject":
            await db.execute(
                "UPDATE evolution_proposals SET status='rejected', reviewed_at=$1 WHERE id=$2",
                _time.time(), proposal_id,
            )
            return {"ok": True, "action": "rejected"}

        elif action == "defer":
            next_review = _time.time() + 7 * 86400
            await db.execute(
                "UPDATE evolution_proposals SET status='pending', next_review_at=$1 WHERE id=$2",
                next_review, proposal_id,
            )
            return {"ok": True, "action": "deferred", "next_review_in_days": 7}


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
    if not is_allowed_panel_host(pi_host):
        # SSRF guard: never POST to loopback / link-local / metadata / public hosts.
        logger.warning("panel agent reload skipped: %r is not an allowed panel host", pi_host)
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
    if pi_host_override:
        # SSRF guard: reject (don't persist) a panel host that points at
        # loopback / link-local / metadata / public addresses.
        try:
            assert_panel_host(str(pi_host_override))
        except SSRFBlocked as exc:
            raise HTTPException(status_code=400, detail=str(exc))

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
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NOW())
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
                updated_at=NOW()""",
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
    try:
        assert_panel_host(pi_host)  # SSRF guard
    except SSRFBlocked as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    url = f"http://{pi_host}:{_PANEL_AGENT_PORT}/brightness"
    try:
        async with httpx.AsyncClient(timeout=3.0) as c:
            r = await c.post(url, json={"value": max(0, min(100, pct))})
            if r.status_code != 200:
                raise HTTPException(status_code=502, detail=f"panel agent: HTTP {r.status_code}")
            return r.json()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"panel agent unreachable: {exc}")


@router.get("/display/volume")
async def get_display_volume(
    user: dict = Depends(get_current_user),
):
    """Return the current ALSA master volume (0-100) via amixer on the local host."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "amixer", "sget", "Master",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
        text = stdout.decode(errors="replace")
        m = re.search(r"\[(\d+)%\]", text)
        vol = int(m.group(1)) if m else None
        return {"volume": vol}
    except Exception as exc:
        logger.warning("get_display_volume: amixer failed: %s", exc)
        return {"volume": None, "error": str(exc)}


@router.post("/display/volume")
async def post_display_volume(
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Set ALSA master volume (0-100) on the local host via amixer.

    Falls back to forwarding to the Pi panel agent when a pi_host is provided,
    using the /volume endpoint (add that handler to the panel agent if absent).
    """
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    try:
        pct = int(body.get("value", 80))
    except Exception:
        raise HTTPException(status_code=400, detail="value must be an integer 0-100")
    pct = max(0, min(100, pct))

    pi_host = body.get("pi_host") or None

    if pi_host:
        try:
            assert_panel_host(pi_host)  # SSRF guard
        except SSRFBlocked as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        # Forward to the Pi panel agent which controls the Pi's ALSA/PulseAudio volume
        url = f"http://{pi_host}:{_PANEL_AGENT_PORT}/volume"
        try:
            async with httpx.AsyncClient(timeout=3.0) as c:
                r = await c.post(url, json={"value": pct})
                if r.status_code == 200:
                    return r.json()
                # Non-fatal: fall through to local amixer attempt
                logger.warning("panel agent /volume returned %d — trying local amixer", r.status_code)
        except httpx.HTTPError as exc:
            logger.warning("panel agent /volume unreachable (%s) — trying local amixer", exc)

    # Local ALSA fallback (Jetson or any host with amixer)
    try:
        proc = await asyncio.create_subprocess_exec(
            "amixer", "sset", "Master", f"{pct}%",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
        text = stdout.decode(errors="replace")
        m = re.search(r"\[(\d+)%\]", text)
        confirmed = int(m.group(1)) if m else pct
        # Persist the new volume in the ALSA state file so it survives reboots and
        # USB reconnects (alsactl restore is called by alsa-restore.service at boot).
        asyncio.create_task(_persist_alsa_state())
        return {"volume": confirmed, "ok": True}
    except Exception as exc:
        logger.warning("post_display_volume: amixer failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"amixer error: {exc}")


async def _persist_alsa_state() -> None:
    """Fire-and-forget: persist ALSA mixer state so volume survives reboots."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "alsactl", "store",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(proc.communicate(), timeout=5.0)
    except Exception as exc:
        logger.debug("alsactl store failed (non-fatal): %s", exc)


# ─── Internal intent dispatch (zoe-core Pi brain → existing fulfillment) ──────

# Allowlist = the capabilities the zoe-core abilities registry exposes. The
# brain-side permission envelope gates writes before this is called; this
# allowlist is defense-in-depth so the endpoint can't run arbitrary intents.
_DISPATCHABLE_INTENTS = frozenset({
    "calendar_create", "calendar_show",
    "list_add", "list_remove", "list_show",
    "reminder_create", "reminder_list", "timer_create",
    "note_create", "note_search",
    "journal_create", "journal_prompt", "journal_streak",
    "people_create", "people_search",
    "music_play", "music_control", "music_volume", "music_setup", "set_volume",
    "smart_home", "weather", "daily_briefing", "time_query", "date_query", "calculate",
    # Wave 3 (cut-list record §3): the ONE new dispatchable intent — the explicit
    # model-callable memory write behind the Flue sidecar's remember_fact tool.
    # Fulfillment is intent_router.execute_intent → MemoryService.ingest.
    "memory_store",
})


class _IntentDispatchBody(BaseModel):
    user_id: str
    intent: str
    slots: dict[str, Any] = {}


@router.post("/intent-dispatch")
async def intent_dispatch(body: _IntentDispatchBody, _: None = Depends(require_intent_dispatch_auth)):
    """Run one allowlisted intent for a user via the existing fulfillment path.

    Internal/service endpoint (loopback or X-Internal-Token) — how the zoe-core
    Pi brain's tools execute capabilities, reusing intent_router.execute_intent
    (the same dispatch the live chat path uses). Fails closed on unknown user or
    non-allowlisted intent.
    """
    intent_name = (body.intent or "").strip()
    user_id = (body.user_id or "").strip()
    if not user_id:
        raise HTTPException(status_code=400, detail="user_id required")
    if intent_name not in _DISPATCHABLE_INTENTS:
        raise HTTPException(status_code=400, detail=f"intent not dispatchable: {intent_name}")
    try:
        from intent_router import Intent, execute_intent

        result = await execute_intent(
            Intent(name=intent_name, slots=dict(body.slots or {})), user_id=user_id
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("intent-dispatch failed intent=%s: %s", intent_name, exc)
        raise HTTPException(status_code=500, detail="intent execution failed") from exc
    return {"intent": intent_name, "ok": result is not None, "result": result or ""}


# Synchronous delegation targets the zoe-core brain may invoke in-turn. Hermes
# only: it owns web browsing / Telegram / the harness, and returns a completion
# we can fold into the chat answer. OpenClaw stays explicit-opt-in (see the
# async A2A /api/agent/delegate endpoint), so it is intentionally NOT here.
_SYNC_DELEGATE_TARGETS = frozenset({"hermes"})


class _DelegateSyncBody(BaseModel):
    user_id: str
    task: str
    target: str = "hermes"


@router.post("/delegate-sync")
async def delegate_sync(body: _DelegateSyncBody, _: None = Depends(require_intent_dispatch_auth)):
    """Synchronously delegate one task to a peer agent and return its completion.

    Internal/service endpoint — how the zoe-core Pi brain delegates work it can't
    do natively yet (web research, complex tasks) to Hermes and folds the answer
    into its turn, the synchronous parity of the legacy __ESCALATE_HERMES__ path.
    Fails closed on unknown user or non-allowlisted target.
    """
    user_id = (body.user_id or "").strip()
    task = (body.task or "").strip()
    target = (body.target or "hermes").strip().lower()
    if not user_id:
        raise HTTPException(status_code=400, detail="user_id required")
    if not task:
        raise HTTPException(status_code=400, detail="task required")
    if target not in _SYNC_DELEGATE_TARGETS:
        raise HTTPException(status_code=400, detail=f"target not sync-delegatable: {target}")
    try:
        # Lazy import: routers.chat owns the Hermes client; importing at module
        # load would be circular (chat imports system-side helpers too).
        from routers.chat import _hermes_completion, _mempalace_load_user_facts, _safe_load_portrait

        # Attach the same user context the legacy __ESCALATE_HERMES__ path sent,
        # so delegated answers stay personalized (preferences + memory), not generic.
        portrait, facts = await asyncio.gather(
            _safe_load_portrait(user_id),
            _mempalace_load_user_facts(user_id),
        )
        result = await _hermes_completion(
            task,
            session_id=f"delegate-{user_id}",
            user_id=user_id,
            portrait=portrait or "",
            facts=facts or "",
        )
    except Exception as exc:
        logger.warning("delegate-sync failed target=%s: %s", target, exc)
        raise HTTPException(status_code=502, detail="delegation failed") from exc
    return {"target": target, "ok": bool(result), "result": result or ""}


# ─── Telegram account linking (resolve a verified telegram_id → Zoe user) ─────
#
# The Telegram channel (labs/flue-zoe-telegram) forwards NO session, so every
# message would otherwise land as guest with no memory. A user links their
# account by storing their numeric telegram_id in their profile (via
# /api/user/profile, session-authed as themselves). This INTERNAL resolver maps
# a verified sender's telegram_id back to that Zoe user_id so the bot can act as
# them. Loopback / X-Internal-Token gated — it exposes an identity mapping and
# must never be reachable publicly.


@router.get("/resolve-telegram/{telegram_id}")
async def resolve_telegram(
    telegram_id: str,
    _: None = Depends(require_internal_token),
    db=Depends(get_db),
):
    """Return {"user_id": <id>} for the profile that registered telegram_id, else
    {"user_id": null}. Internal-only (loopback or X-Internal-Token).

    The telegram_id lives inside each user's user_preferences.prefs JSON under
    the "telegram_id" key (see routers/user_profile.py). Mapping is unique per
    telegram_id (the profile write enforces it), so at most one row matches; if
    a stale duplicate somehow exists we return the earliest-updated one
    deterministically.
    """
    tid = (telegram_id or "").strip()
    if not tid:
        return {"user_id": None}
    # prefs is a JSON blob; match on the stored telegram_id string. Ordering by
    # updated_at keeps the result deterministic even if a stale duplicate exists.
    cursor = await db.execute(
        """SELECT user_id, prefs FROM user_preferences
           WHERE prefs::jsonb ->> 'telegram_id' = ?
           ORDER BY updated_at ASC
           LIMIT 1""",
        (tid,),
    )
    row = await cursor.fetchone()
    if not row:
        return {"user_id": None}
    return {"user_id": row["user_id"]}


class _ConsumeLinkBody(BaseModel):
    token: str
    telegram_id: str
    telegram_username: Optional[str] = None


@router.post("/telegram/consume-link-token")
async def consume_telegram_link_token(
    body: _ConsumeLinkBody,
    _: None = Depends(require_internal_token),
    db=Depends(get_db),
):
    """Internal-only. The Telegram bot calls this after a user sends `/start
    <token>`. We verify the signed token, recover the user_id it was minted for,
    and link the VERIFIED sender's telegram_id to that profile. The sender id
    comes from Telegram (not the user), and this endpoint is loopback/token-gated,
    so a token can only ever link the redeemer's own Telegram account.
    """
    import telegram_link
    from routers.user_profile import _TELEGRAM_ID_RE, _read_prefs, _write_prefs

    token = (body.token or "").strip()
    # verify_link_token validates AND atomically RESERVES the token (single-use).
    # From here on, ANY exit path must either mark_token_consumed (success) or
    # release_token (failure) — otherwise the reservation would strand the token.
    user_id = telegram_link.verify_link_token(token)
    if not user_id:
        raise HTTPException(status_code=400, detail="invalid, expired, or already-used link token")

    try:
        tid = (body.telegram_id or "").strip()
        if not _TELEGRAM_ID_RE.match(tid):
            raise HTTPException(
                status_code=400,
                detail="telegram_id must be a positive numeric Telegram user id",
            )

        # The token's user must still exist (a since-deleted user would otherwise
        # 500 on the FK). Clean 400 instead, and the reservation is released below.
        cur = await db.execute("SELECT 1 FROM users WHERE id = ? LIMIT 1", (user_id,))
        if not await cur.fetchone():
            raise HTTPException(status_code=400, detail="link target user no longer exists")

        # Last-writer-wins uniqueness: a telegram_id maps to at most one Zoe user.
        cursor = await db.execute(
            """SELECT user_id, prefs FROM user_preferences
               WHERE prefs::jsonb ->> 'telegram_id' = ? AND user_id != ?""",
            (tid, user_id),
        )
        for row in await cursor.fetchall():
            other_prefs = await _read_prefs(db, row["user_id"])
            if other_prefs.get("telegram_id") == tid:
                other_prefs.pop("telegram_id", None)
                await _write_prefs(db, row["user_id"], other_prefs)

        prefs = await _read_prefs(db, user_id)
        prefs["telegram_id"] = tid
        await _write_prefs(db, user_id, prefs)
    except BaseException:
        # Failure before commit → free the reservation so the user can re-scan.
        # BaseException (not Exception) so asyncio.CancelledError — raised when
        # uvicorn cancels the handler on a client disconnect mid-await — ALSO
        # releases, instead of stranding the token in _pending_sigs for the TTL.
        # The bare `raise` re-raises CancelledError, preserving shutdown semantics.
        telegram_link.release_token(token)
        raise

    # Link is durably written — now burn the token so a replay/second scan fails.
    telegram_link.mark_token_consumed(token)
    logger.info("telegram self-service link: %s → user %s", tid, user_id)
    return {"ok": True, "user_id": user_id, "telegram_id": tid}


class _RegisterBotBody(BaseModel):
    username: str


@router.post("/telegram/register-bot")
async def register_telegram_bot(
    body: _RegisterBotBody, _: None = Depends(require_internal_token)
):
    """Internal-only. The Telegram bot self-registers its @username at startup so
    the settings UI can build `https://t.me/<bot>?start=<token>` deep links."""
    import telegram_link
    telegram_link.set_bot_username(body.username)
    return {"ok": True, "bot_username": telegram_link.get_bot_username()}
