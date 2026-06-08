"""
multica_autopilot_sync.py — Syncs Multica autopilot schedules to APScheduler.

On startup, fetches all autopilots from the Multica workspace and registers
each as an APScheduler CronTrigger job. When a job fires, it creates a
Multica issue (if mode=create_issue) and calls the matching Zoe task function.

Hot-reload: call sync_autopilots_from_multica() from the agent-sync endpoint
to pick up schedule changes made in the Multica UI without restarting.
"""
from __future__ import annotations

import datetime as _dt
import logging
import os
from typing import Callable

import httpx

logger = logging.getLogger(__name__)

_MULTICA_BASE_URL = os.environ.get("MULTICA_BASE_URL", "").rstrip("/")
_MULTICA_API_TOKEN = os.environ.get("MULTICA_API_TOKEN", "")
_MULTICA_WORKSPACE_ID = os.environ.get("MULTICA_WORKSPACE_ID", "")
_TIMEOUT = 10.0
_HEALTH_CHECK_TIMEOUT_S = float(os.environ.get("ZOE_HEALTH_CHECK_TIMEOUT_S", "120"))
def _hermes_agent_id() -> str:
    from multica_client import get_engineering_multica_agent_id  # type: ignore[import]

    return get_engineering_multica_agent_id()
_TZ = os.environ.get("ZOE_TIMEZONE", "Australia/Perth")

# Default false: do not create a Multica tracker issue on every cron fire.
_CREATE_ISSUES = (
    os.environ.get("ZOE_MULTICA_AUTOPILOT_CREATE_ISSUES")
    or os.environ.get("ZOE_MULTICA_AUTOPIOT_CREATE_ISSUES", "false")
).lower() in ("1", "true", "yes")
# Default empty: no autopilot run creates a Multica tracker ("Autopilot: …")
# issue wrapper. These wrappers accumulated into thousands of done rows and
# added no signal (e.g. Platform Health Check already opens a dedicated
# "Platform health failures detected" issue on failure). Opt back in per
# autopilot title via this env var (comma-separated, case-insensitive).
_CREATE_ISSUES_FOR = {
    t.strip().lower()
    for t in (
        os.environ.get("ZOE_MULTICA_AUTOPILOT_CREATE_ISSUES_FOR")
        or os.environ.get("ZOE_MULTICA_AUTOPIOT_CREATE_ISSUES_FOR", "")
    ).split(",")
    if t.strip()
}
_STALE_AUTOPILOT_HOURS = float(
    os.environ.get("ZOE_MULTICA_AUTOPILOT_STALE_HOURS")
    or os.environ.get("ZOE_MULTICA_AUTOPIOT_STALE_HOURS", "2")
)
# Dedupe engineering dispatch: Zoe's poll bridge and compatibility sync command
# share the deterministic driver gate. The Multica "Board Review" autopilot
# would dispatch the same issue pool through the executor seam, so it remains
# disabled by default to avoid duplicate phase effects.
_BOARD_REVIEW_AUTOPILOT_ENABLED = (
    os.environ.get("ZOE_BOARD_REVIEW_AUTOPILOT_ENABLED", "false").lower()
    in ("1", "true", "yes")
)

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
    """Run the host health script and open a Hermes-assigned issue on failure."""
    import asyncio
    from pathlib import Path

    script = Path(
        os.environ.get(
            "ZOE_HEALTH_CHECK_SCRIPT",
            str(Path.home() / "assistant" / "scripts" / "maintenance" / "platform_health_check.sh"),
        )
    )
    if not script.exists():
        raise FileNotFoundError(f"Health check script missing: {script}")

    proc = await asyncio.create_subprocess_exec(
        "bash",
        str(script),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        stdout, _ = await asyncio.wait_for(
            proc.communicate(),
            timeout=_HEALTH_CHECK_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        raise RuntimeError(
            f"Platform health check timed out after {_HEALTH_CHECK_TIMEOUT_S:.0f}s"
        )
    output = (stdout or b"").decode("utf-8", errors="replace").strip()
    tail = "\n".join(output.splitlines()[-25:])

    if proc.returncode == 0:
        logger.info("autopilot: platform health check passed")
        return

    logger.warning("autopilot: platform health check failed (exit %s)", proc.returncode)
    if _is_configured():
        try:
            from multica_client import get_multica_client  # type: ignore[import]

            client = get_multica_client()
            title = "Platform health failures detected"
            description = (
                "The scheduled Platform Health Check found failing services.\n\n"
                f"```\n{tail}\n```"
            )
            existing = next(
                (
                    issue
                    for issue in await client.list_issues()
                    if issue.get("title") == title
                    and issue.get("status") not in {"done", "cancelled", "archived"}
                ),
                None,
            )
            if existing and existing.get("id"):
                await client.update_issue(
                    str(existing["id"]),
                    description=description,
                )
            else:
                await client.create_issue(
                    title=title,
                    description=description,
                    priority="high",
                    status="backlog",
                )
        except Exception as exc:
            logger.warning("autopilot: failed to create health failure issue: %s", exc)

    raise RuntimeError(f"Platform health check failed (exit {proc.returncode})")


async def _run_board_review() -> None:
    """Dispatch Hermes-assigned open Multica issues into engineering workflows.

    Disabled by default: the Hermes built-in hourly cron owns engineering
    dispatch (see _BOARD_REVIEW_AUTOPILOT_ENABLED). This stays as an opt-in
    fallback for when that cron is paused.
    """
    if not _BOARD_REVIEW_AUTOPILOT_ENABLED:
        logger.info(
            "autopilot: board review skipped — Hermes hourly cron owns engineering "
            "dispatch (set ZOE_BOARD_REVIEW_AUTOPILOT_ENABLED=true to re-enable)"
        )
        return
    try:
        from multica_client import get_multica_client  # type: ignore[import]
        from executor_registry import dispatch_issue, poll_ref  # type: ignore[import]

        client = get_multica_client()
        if not client.is_configured():
            return
        dispatched = 0
        for status in ("todo", "in_progress"):
            issues = await client.list_issues(status=status)
            for issue in issues or []:
                issue_id = str(issue.get("id") or "")
                if not issue_id:
                    continue
                if str(issue.get("assignee_id") or "") != _hermes_agent_id():
                    continue
                title = issue.get("title") or issue.get("identifier") or "Multica engineering task"
                if title.lower().startswith("autopilot:"):
                    logger.debug(
                        "autopilot: board review skipping wrapper issue %s (%r)",
                        issue_id,
                        title,
                    )
                    continue
                existing = await poll_ref(f"multica:{issue_id}")
                if existing.get("found") and existing.get("status") in ("running", "blocked"):
                    continue
                result = await dispatch_issue(issue)
                if result.get("ok"):
                    dispatched += 1
        logger.info("autopilot: board review dispatched %d issue(s)", dispatched)
    except Exception as exc:
        logger.warning("_run_board_review: %s", exc)


def _should_create_tracker_issue(autopilot_title: str, mode: str, task_fn) -> bool:
    if mode != "create_issue" or not _is_configured():
        return False
    title_lower = autopilot_title.lower().strip()
    if task_fn is _run_platform_health_check:
        return False
    if title_lower in _CREATE_ISSUES_FOR:
        return True
    if _CREATE_ISSUES:
        return True
    if task_fn is not None:
        return False
    return False


async def close_stale_autopilot_wrappers(
    *,
    min_age_hours: float | None = None,
    statuses: tuple[str, ...] = ("todo", "in_progress", "in_review"),
) -> int:
    if not _is_configured():
        return 0
    min_age_hours = _STALE_AUTOPILOT_HOURS if min_age_hours is None else min_age_hours
    closed = 0
    now = _dt.datetime.now(_dt.timezone.utc)
    for status in statuses:
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(
                    f"{_MULTICA_BASE_URL}/api/issues",
                    headers=_headers(),
                    params={"workspace_id": _MULTICA_WORKSPACE_ID, "status": status, "limit": 200},
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            logger.debug("close_stale_autopilot_wrappers: list %s failed: %s", status, exc)
            continue
        issues = data if isinstance(data, list) else data.get("issues", data.get("items", []))
        for issue in issues or []:
            title = issue.get("title") or ""
            issue_id = issue.get("id")
            if not issue_id or not title.startswith("Autopilot:"):
                continue
            created = issue.get("created_at", "")
            try:
                age_h = (
                    now - _dt.datetime.fromisoformat(created.replace("Z", "+00:00"))
                ).total_seconds() / 3600
            except Exception:
                age_h = min_age_hours
            if age_h < min_age_hours:
                continue
            try:
                from multica_client import get_multica_client  # type: ignore[import]
                client = get_multica_client()
                await client.update_issue(str(issue_id), status="done")
                closed += 1
            except Exception as exc:
                logger.debug("close_stale_autopilot_wrappers: close %s failed: %s", issue_id, exc)
    return closed


async def _run_stale_issue_cleanup() -> None:
    n = await close_stale_autopilot_wrappers()
    logger.info("autopilot: stale issue cleanup closed %d wrapper(s)", n)


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
    "board review": _run_board_review,
    "multica board review": _run_board_review,
    "hermes board review": _run_board_review,
    "stale issue cleanup": _run_stale_issue_cleanup,
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
    3. Marks the issue done on success, or resets it to todo on failure/no-op.
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

    task_fn = _zoe_task_for_autopilot(autopilot_title)

    if _should_create_tracker_issue(autopilot_title, mode, task_fn):
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
                logger.info("autopilot: created issue %s for %r", issue_id, autopilot_title)
        except Exception as exc:
            logger.warning("autopilot: failed to create issue for %r: %s", autopilot_title, exc)
    elif task_fn is not None:
        logger.debug("autopilot: ran %r (no tracker issue)", autopilot_title)

    if task_fn is not None:
        try:
            await task_fn()
            await _update_issue_status("done")
        except Exception as exc:
            await _update_issue_status("cancelled")
            logger.warning("autopilot: task function for %r raised: %s", autopilot_title, exc)
    else:
        await _update_issue_status("cancelled")
        logger.info("autopilot: no task function mapped for %r", autopilot_title)


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
