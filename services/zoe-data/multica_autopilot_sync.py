"""
multica_autopilot_sync.py — Syncs Multica autopilot schedules to APScheduler.

On startup, fetches all autopilots from the Multica workspace and registers
each as an APScheduler CronTrigger job. When a job fires, it creates a
Multica issue (if mode=create_issue) and calls the matching Zoe task function.

Hot-reload: call sync_autopilots_from_multica() from the agent-sync endpoint
to pick up schedule changes made in the Multica UI without restarting.
"""
from __future__ import annotations

import logging
import os
from typing import Callable

import httpx

logger = logging.getLogger(__name__)

_MULTICA_BASE_URL = os.environ.get("MULTICA_BASE_URL", "").rstrip("/")
_MULTICA_API_TOKEN = os.environ.get("MULTICA_API_TOKEN", "")
_MULTICA_WORKSPACE_ID = os.environ.get("MULTICA_WORKSPACE_ID", "")
_TIMEOUT = 10.0
_TZ = os.environ.get("ZOE_TIMEZONE", "Australia/Perth")


def _is_configured() -> bool:
    return bool(_MULTICA_BASE_URL and _MULTICA_API_TOKEN and _MULTICA_WORKSPACE_ID)


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_MULTICA_API_TOKEN}",
        "Content-Type": "application/json",
        "X-Workspace-ID": _MULTICA_WORKSPACE_ID,
    }


# ── Multica API helpers ───────────────────────────────────────────────────────

async def get_multica_autopilots() -> list[dict]:
    """Fetch all autopilots from the Multica workspace. Returns [] on any error."""
    if not _is_configured():
        return []
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                f"{_MULTICA_BASE_URL}/api/autopilots",
                params={"workspace_id": _MULTICA_WORKSPACE_ID},
                headers=_headers(),
            )
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, list) else data.get("autopilots", [])
    except Exception as exc:
        logger.debug("get_multica_autopilots: %s", exc)
        return []


async def get_autopilot_triggers(autopilot_id: str) -> list[dict]:
    """Fetch cron triggers for a specific autopilot.

    Triggers are returned under the ``triggers`` key in the single-autopilot
    detail endpoint (``GET /api/autopilots/{id}``). Each trigger object has a
    ``cron_expression`` field.  Returns [] on any error.
    """
    if not _is_configured():
        return []
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                f"{_MULTICA_BASE_URL}/api/autopilots/{autopilot_id}",
                params={"workspace_id": _MULTICA_WORKSPACE_ID},
                headers=_headers(),
            )
            resp.raise_for_status()
            data = resp.json()
            # Response shape: {"autopilot": {...}, "triggers": [...]}
            triggers = data.get("triggers", [])
            if not isinstance(triggers, list):
                return []
            # Normalise: map cron_expression → cron so the rest of the code
            # works with either key name.
            normalised = []
            for t in triggers:
                if not isinstance(t, dict):
                    continue
                if t.get("enabled") is False:
                    continue  # skip disabled triggers
                entry = dict(t)
                if "cron_expression" in entry and "cron" not in entry:
                    entry["cron"] = entry["cron_expression"]
                normalised.append(entry)
            return normalised
    except Exception as exc:
        logger.debug("get_autopilot_triggers(%s): %s", autopilot_id, exc)
        return []


# ── Zoe task functions ────────────────────────────────────────────────────────
# These are the actual work functions called when an autopilot cron fires.
# They must be top-level async functions (importable by APScheduler pickle).

async def _run_morning_checkin() -> None:
    """Run the morning check-in trigger for all active users."""
    try:
        from db_compat import get_compat_db  # type: ignore[import]
        from proactive.triggers.morning_checkin import MorningCheckInTrigger
        from proactive.engine import fire_notification
        trigger = MorningCheckInTrigger()
        async with get_compat_db() as db:
            results = await trigger.check(db)
        for r in results:
            await fire_notification(
                user_id=r.user_id,
                message=r.message,
                trigger_type=r.trigger_type,
                item_id=r.item_id,
                context=getattr(r, "context", None),
            )
        logger.info("autopilot: morning_checkin fired for %d user(s)", len(results))
    except Exception as exc:
        logger.warning("_run_morning_checkin: %s", exc)


async def _run_evening_winddown() -> None:
    """Run the evening wind-down trigger for all active users."""
    try:
        from db_compat import get_compat_db  # type: ignore[import]
        from proactive.triggers.evening_windown import EveningWindDownTrigger
        from proactive.engine import fire_notification
        trigger = EveningWindDownTrigger()
        async with get_compat_db() as db:
            results = await trigger.check(db)
        for r in results:
            await fire_notification(
                user_id=r.user_id,
                message=r.message,
                trigger_type=r.trigger_type,
                item_id=r.item_id,
                context=getattr(r, "context", None),
            )
        logger.info("autopilot: evening_winddown fired for %d user(s)", len(results))
    except Exception as exc:
        logger.warning("_run_evening_winddown: %s", exc)


async def _run_evolution_notice() -> None:
    """Run the evolution notice (Phase 6 nightly dreaming cycle)."""
    try:
        from evolution_notice import run_evolution_notice  # type: ignore[import]
        result = await run_evolution_notice()
        logger.info("autopilot: evolution_notice complete — %s", result)
    except Exception as exc:
        logger.warning("_run_evolution_notice: %s", exc)


async def _run_evolution_weekly_digest() -> None:
    """Run the evolution weekly digest trigger for all active users."""
    try:
        from db_compat import get_compat_db  # type: ignore[import]
        from proactive.triggers.evolution_weekly_digest import EvolutionWeeklyDigestTrigger
        from proactive.engine import fire_notification
        trigger = EvolutionWeeklyDigestTrigger()
        async with get_compat_db() as db:
            results = await trigger.check(db)
        for r in results:
            await fire_notification(
                user_id=r.user_id,
                message=r.message,
                trigger_type=r.trigger_type,
                item_id=r.item_id,
                context=getattr(r, "context", None),
            )
        logger.info("autopilot: evolution_weekly_digest fired for %d user(s)", len(results))
    except Exception as exc:
        logger.warning("_run_evolution_weekly_digest: %s", exc)


async def _run_reminder_scan() -> None:
    """Run the reminder scan to auto-schedule upcoming reminders into APScheduler."""
    try:
        from db_compat import get_compat_db  # type: ignore[import]
        from proactive.triggers.reminder_scan import ReminderScanTrigger
        trigger = ReminderScanTrigger()
        async with get_compat_db() as db:
            await trigger.check(db)
        logger.info("autopilot: reminder_scan complete")
    except Exception as exc:
        logger.warning("_run_reminder_scan: %s", exc)


async def _run_platform_health_check() -> None:
    """Log a platform health check execution (extend to push a health card later)."""
    logger.info("autopilot: Platform Health Check fired")


# ── Title → task function mapping ────────────────────────────────────────────

_TITLE_TO_TASK: dict[str, Callable] = {
    "morning checkin": _run_morning_checkin,
    "morning check-in": _run_morning_checkin,
    "morning check in": _run_morning_checkin,
    "evening wind down": _run_evening_winddown,
    "evening winddown": _run_evening_winddown,
    "evening wind-down": _run_evening_winddown,
    "evolution nightly notice": _run_evolution_notice,
    "evolution notice": _run_evolution_notice,
    "evolution nightly": _run_evolution_notice,
    "evolution weekly digest": _run_evolution_weekly_digest,
    "weekly digest": _run_evolution_weekly_digest,
    "evolution digest": _run_evolution_weekly_digest,
    "reminder scan": _run_reminder_scan,
    "platform health check": _run_platform_health_check,
    "health check": _run_platform_health_check,
}


def _zoe_task_for_autopilot(autopilot_title: str) -> Callable | None:
    """Map autopilot title to the matching Zoe task function.

    Tries exact match first (case-insensitive), then partial substring match.
    Returns None if no mapping found.
    """
    title_lower = autopilot_title.lower().strip()
    if title_lower in _TITLE_TO_TASK:
        return _TITLE_TO_TASK[title_lower]
    for key, fn in _TITLE_TO_TASK.items():
        if key in title_lower or title_lower in key:
            return fn
    return None


# ── APScheduler job function ──────────────────────────────────────────────────

async def _fire_autopilot_job(
    autopilot_id: str,
    autopilot_title: str,
    mode: str,
    assignee_agent_id: str | None,
) -> None:
    """Called by APScheduler when an autopilot's cron fires.

    1. Creates a Multica issue (if mode == 'create_issue').
    2. Runs the matching Zoe task function if one is mapped.
    3. Marks the issue done on success or cancelled on failure.
    """
    logger.info(
        "autopilot fire: id=%s title=%r mode=%s",
        autopilot_id, autopilot_title, mode,
    )

    issue_id: str | None = None

    async def _update_issue_status(status: str) -> None:
        if not issue_id or not _is_configured():
            return
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.put(
                    f"{_MULTICA_BASE_URL}/api/issues/{issue_id}",
                    json={"status": status},
                    headers=_headers(),
                )
                resp.raise_for_status()
                logger.info(
                    "autopilot: updated issue %s to status=%s for %r",
                    issue_id, status, autopilot_title,
                )
        except Exception as exc:
            logger.warning(
                "autopilot: failed to update issue %s to status=%s for %r: %s",
                issue_id, status, autopilot_title, exc,
            )

    if mode == "create_issue" and _is_configured():
        try:
            payload: dict = {
                "title": f"Autopilot: {autopilot_title}",
                "description": f"Scheduled autopilot run for: {autopilot_title}",
                "status": "in_progress",
            }
            if assignee_agent_id:
                payload["assignee_id"] = assignee_agent_id
                payload["assignee_type"] = "agent"
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(
                    f"{_MULTICA_BASE_URL}/api/issues",
                    json=payload,
                    headers=_headers(),
                    params={"workspace_id": _MULTICA_WORKSPACE_ID},
                )
                resp.raise_for_status()
                issue = resp.json()
                issue_id = issue.get("id") or issue.get("identifier")
                logger.info(
                    "autopilot: created issue %s for %r",
                    issue_id, autopilot_title,
                )
        except Exception as exc:
            logger.warning(
                "autopilot: failed to create issue for %r: %s", autopilot_title, exc
            )

    task_fn = _zoe_task_for_autopilot(autopilot_title)
    if task_fn is not None:
        try:
            await task_fn()
            await _update_issue_status("done")
        except Exception as exc:
            await _update_issue_status("cancelled")
            logger.warning(
                "autopilot: task function for %r raised: %s", autopilot_title, exc
            )
    else:
        logger.info(
            "autopilot: no task function mapped for %r — issue created only",
            autopilot_title,
        )


# ── Main sync function ────────────────────────────────────────────────────────

async def sync_autopilots_from_multica(scheduler) -> int:
    """Fetch Multica autopilots and register/refresh their APScheduler cron jobs.

    For each autopilot:
      - Removes any existing ``multica_autopilot_{id}`` job so cron changes
        made in the Multica UI take effect immediately on next reload.
      - Adds a new CronTrigger job using the first trigger's cron expression.

    Returns the number of jobs successfully registered.
    Fails silently if Multica is unavailable; zoe-data starts regardless.
    """
    from apscheduler.triggers.cron import CronTrigger

    if not _is_configured():
        logger.debug("sync_autopilots_from_multica: Multica not configured — skipping")
        return 0

    autopilots = await get_multica_autopilots()
    if not autopilots:
        logger.info("sync_autopilots_from_multica: no autopilots returned from Multica")
        return 0

    registered = 0
    for ap in autopilots:
        ap_id: str = str(ap.get("id", "")).strip()
        ap_title: str = (ap.get("title") or ap.get("name") or "").strip()
        ap_mode: str = ap.get("mode") or "create_issue"
        ap_assignee: str | None = ap.get("assignee_agent_id") or ap.get("assignee_id")

        if not ap_id or not ap_title:
            continue

        job_id = f"multica_autopilot_{ap_id}"

        triggers = await get_autopilot_triggers(ap_id)
        if not triggers:
            logger.debug(
                "sync_autopilots: no triggers for autopilot %s (%r)", ap_id, ap_title
            )
            continue

        for trig in triggers:
            cron_expr: str = (
                trig.get("cron") or trig.get("cron_expression") or ""
            ).strip()
            if not cron_expr:
                continue

            # Remove old job so schedule edits made in Multica UI take effect.
            try:
                scheduler.remove_job(job_id)
                logger.debug("sync_autopilots: removed stale job %s", job_id)
            except Exception:
                pass

            try:
                cron_trigger = CronTrigger.from_crontab(cron_expr, timezone=_TZ)
                scheduler.add_job(
                    _fire_autopilot_job,
                    trigger=cron_trigger,
                    id=job_id,
                    replace_existing=True,
                    kwargs={
                        "autopilot_id": ap_id,
                        "autopilot_title": ap_title,
                        "mode": ap_mode,
                        "assignee_agent_id": ap_assignee,
                    },
                )
                registered += 1
                logger.info(
                    "sync_autopilots: registered job %s for %r cron=%r tz=%s",
                    job_id, ap_title, cron_expr, _TZ,
                )
                break  # Use only the first valid trigger per autopilot.
            except Exception as exc:
                logger.warning(
                    "sync_autopilots: failed to register job for %r (cron=%r): %s",
                    ap_title, cron_expr, exc,
                )

    logger.info(
        "sync_autopilots_from_multica: complete — %d/%d job(s) registered",
        registered, len(autopilots),
    )
    return registered
