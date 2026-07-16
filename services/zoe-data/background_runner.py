"""
background_runner.py — Background task queue for Zoe.

Enables "fire and forget" tasks:
  1. User says "go find hotel prices, let me know when done"
  2. Zoe Agent queues the task for Hermes
  3. chat.py calls enqueue_background_task()
  4. This module runs the task via Hermes, stores result
  5. Next chat load: /api/chat/tasks/pending returns results
  6. Frontend injects them as proactive Zoe messages
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from datetime import datetime, timezone

from hermes_http import hermes_auth_headers, hermes_bin, zoe_repo_root

logger = logging.getLogger(__name__)


def _hermes_headers(*, session_id: str | None = None) -> dict[str, str]:
    return hermes_auth_headers(session_id=session_id)

# Running task futures keyed by task id (to avoid duplicate runs)
_running: dict[int, asyncio.Task] = {}

# Max A2A delegation depth to prevent infinite loops
_MAX_REQUEST_DEPTH = 3

# Background tasks run through a Kanban worker profile (OpenRouter), not the main
# Codex gateway. Override with HERMES_BACKGROUND_PROFILE or HERMES_BACKGROUND_MODEL.
_DEFAULT_BACKGROUND_PROFILE = "zoe-coder"


def _background_profile() -> str:
    for key in ("HERMES_BACKGROUND_PROFILE", "HERMES_BACKGROUND_MODEL"):
        val = os.environ.get(key, "").strip()
        if val:
            return val
    # Legacy HERMES_MODEL only when explicitly set to a worker profile / OpenRouter id.
    legacy = os.environ.get("HERMES_MODEL", "").strip()
    if legacy and legacy not in {"hermes-agent", "hermes"}:
        return legacy
    return _DEFAULT_BACKGROUND_PROFILE


async def _record_cost_event(
    agent_name: str,
    model: str,
    task_id: int | None,
    user_id: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    estimated_cost_usd: float = 0.0,
) -> None:
    """Record an agent cost event to agent_cost_events table."""
    try:
        from db_pool import get_db_ctx
        import uuid as _uuid
        event_id = _uuid.uuid4().hex
        async with get_db_ctx() as db:
            await db.execute(
                """INSERT INTO agent_cost_events
                   (id, agent_name, model, task_id, user_id, input_tokens, output_tokens, estimated_cost_usd, ts)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)""",
                event_id, agent_name, model, str(task_id) if task_id else None,
                user_id, input_tokens, output_tokens, estimated_cost_usd, time.time(),
            )
    except Exception as exc:
        logger.warning(
            "background_runner: failed to record cost event for agent=%s model=%s "
            "— spend under-reported: %s", agent_name, model, exc)


async def enqueue_background_task(
    task: str,
    user_id: str,
    session_id: str | None = None,
    panel_id: str | None = None,
    request_depth: int = 0,
    multica_issue_id: str | None = None,
) -> int:
    """Insert a task row and kick off the async runner. Returns the new task id."""
    if request_depth > _MAX_REQUEST_DEPTH:
        raise ValueError(f"A2A delegation depth {request_depth} exceeds max {_MAX_REQUEST_DEPTH}")

    from db_pool import get_db_ctx
    now = datetime.now(timezone.utc).isoformat()
    run_id = uuid.uuid4().hex
    async with get_db_ctx() as db:
        row = await db.fetchrow(
            """INSERT INTO background_tasks
               (user_id, session_id, panel_id, task, status, created_at,
                checkout_run_id, request_depth, multica_issue_id)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9) RETURNING id""",
            user_id, session_id, panel_id, task, "pending", now,
            run_id, request_depth, multica_issue_id,
        )
        task_id: int = row["id"]

    # Fire off the runner coroutine
    fut = asyncio.ensure_future(_run_task(task_id, task, user_id, session_id, panel_id=panel_id))
    _running[task_id] = fut
    logger.info(
        "background_runner: enqueued task #%d for user=%s depth=%d multica=%s",
        task_id, user_id, request_depth, multica_issue_id,
    )
    return task_id


async def _run_task(
    task_id: int,
    task: str,
    user_id: str,
    session_id: str | None,
    panel_id: str | None = None,
) -> None:
    """Execute the task via Hermes and store the result."""
    from db_pool import get_db_ctx

    async def _set_status(status: str, result: str | None = None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        async with get_db_ctx() as db:
            await db.execute(
                "UPDATE background_tasks SET status=$1, result=$2, completed_at=$3 WHERE id=$4",
                status, result, now if status in ("done", "error") else None, task_id,
            )

    await _set_status("running")
    try:
        result = await _run_hermes_background_task(task, user_id=user_id, task_id=task_id)
        if not result:
            result = "(No result returned)"
        await _set_status("done", result)
        logger.info("background_runner: task #%d completed (%d chars)", task_id, len(result))

        # Auto-deploy the linked evolution proposal when the task completes.
        # Task descriptions for approved proposals follow the pattern:
        #   "Implement evolution proposal <UUID>: <title>..."
        import re as _re
        _proposal_match = _re.match(
            r"Implement evolution proposal ([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
            task,
        )
        if _proposal_match:
            _proposal_id = _proposal_match.group(1)
            try:
                async with get_db_ctx() as _db:
                    rows = await _db.fetch(
                        "SELECT multica_issue_id FROM evolution_proposals WHERE id=$1",
                        _proposal_id,
                    )
                    await _db.execute(
                        """UPDATE evolution_proposals
                           SET status='deployed', deployed_at=$1
                           WHERE id=$2 AND status='approved'""",
                        time.time(), _proposal_id,
                    )
                logger.info("background_runner: task #%d → proposal %s status=deployed", task_id, _proposal_id)
                # Sync deployed status to Multica board
                _multica_id = rows[0]["multica_issue_id"] if rows else None
                if _multica_id:
                    try:
                        from multica_client import update_multica_issue_on_proposal_status_change  # type: ignore[import]
                        await update_multica_issue_on_proposal_status_change(_multica_id, "deployed")
                    except Exception as _me:
                        logger.debug("background_runner: Multica sync failed: %s", _me)
            except Exception as _pe:
                logger.warning(
                    "background_runner: could not mark proposal deployed — "
                    "proposal status stays stale: %s", _pe)

        # Estimate output tokens from output length (rough: ~4 chars/token)
        _est_tokens = len(result) // 4
        await _record_cost_event(
            agent_name="hermes",
            model=_background_profile(),
            task_id=task_id,
            user_id=user_id,
            output_tokens=_est_tokens,
            estimated_cost_usd=_est_tokens * 0.000002,  # rough OpenRouter worker output rate
        )
        try:
            from push import broadcaster
            await broadcaster.broadcast(
                "all",
                "background_task_done",
                {
                    "task_id": task_id,
                    "result": result[:500],
                    "session_id": session_id,
                    "panel_id": panel_id,
                },
                user_id=user_id,
            )
            if panel_id:
                await broadcaster.broadcast("all", "panel:announce", {
                    "panel_id": panel_id,
                    "text": f"Background task complete: {result[:200]}",
                    "task_id": task_id,
                })
        except Exception:
            pass  # polling fallback still works
    except Exception as exc:
        logger.warning("background_runner: task #%d failed: %s", task_id, exc)
        await _set_status("error", f"Task failed: {exc}")
        try:
            from push import broadcaster
            await broadcaster.broadcast(
                "all",
                "background_task_error",
                {
                    "task_id": task_id,
                    "error": str(exc),
                    "session_id": session_id,
                },
                user_id=user_id,
            )
        except Exception:
            pass
    finally:
        _running.pop(task_id, None)


async def _run_hermes_background_task(task: str, *, user_id: str, task_id: int) -> str:
    """Run a background task via ``hermes -p <worker-profile> -z`` (OpenRouter path).

    The main gateway API ignores per-request model overrides and always uses the
    Codex default, so background work is routed through a Kanban worker profile
    (default ``zoe-coder`` / DeepSeek on OpenRouter) instead.
    """
    profile = _background_profile()
    timeout_s = float(os.environ.get("HERMES_BACKGROUND_TIMEOUT_S", "900"))
    repo_root = zoe_repo_root()
    prompt = (
        "You are Hermes running a Zoe background task. "
        "Use Zoe tools and CloakBrowser MCP tools when needed. "
        "Do not use OpenClaw. Do not print secrets. "
        "For engineering tasks, use zoe-engineering: split large work, verify, and report blockers. "
        f"user_id={user_id}, task_id={task_id}.\n\n"
        f"Task:\n{task}"
    )
    cmd = [
        hermes_bin(),
        "-p",
        profile,
        "--accept-hooks",
        "-z",
        prompt,
    ]
    env = dict(os.environ)
    env.setdefault("HERMES_YOLO_MODE", "1")
    env["HERMES_SESSION_ID"] = f"background-task-{task_id}"
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=repo_root,
        env=env,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise TimeoutError(f"Background Hermes task timed out after {timeout_s:.0f}s")
    stdout = (out or b"").decode("utf-8", errors="replace").strip()
    stderr = (err or b"").decode("utf-8", errors="replace").strip()
    if proc.returncode != 0:
        raise RuntimeError(
            f"hermes -p {profile} -z exited {proc.returncode}: {stderr or stdout or 'no output'}"
        )
    return stdout or "(No result returned)"


async def get_pending_tasks(user_id: str) -> list[dict]:
    """Return completed-but-unseen tasks for a user, then mark them seen."""
    from db_pool import get_db, get_db_ctx
    async with get_db_ctx() as db:
        rows = await db.fetch(
            """SELECT id, task, result, created_at, completed_at
               FROM background_tasks
               WHERE user_id=$1 AND status='done' AND seen=0
               ORDER BY completed_at ASC""",
            user_id,
        )
        if rows:
            ids = [r["id"] for r in rows]
            await db.execute(
                f"UPDATE background_tasks SET seen=1 WHERE id = ANY($1::int[])",
                ids,
            )

    return [
        {
            "id": r["id"],
            "task": r["task"],
            "result": r["result"] or "",
            "created_at": r["created_at"],
            "completed_at": r["completed_at"],
        }
        for r in rows
    ]


async def _watchdog_loop() -> None:
    """Scan for tasks stuck in 'running' state beyond ZOE_TASK_TIMEOUT_S.

    Every 60 seconds, any task that has been running longer than the timeout
    is transitioned to 'blocked' with blocker_reason='watchdog_timeout'.
    A WebSocket push is sent to the owning user.

    Every ~7 days, old done/error rows older than 30 days are deleted to
    prevent unbounded table growth.
    """
    from db_pool import get_db_ctx
    from datetime import timezone, timedelta

    timeout_s = float(os.environ.get("ZOE_TASK_TIMEOUT_S", "900"))
    _CLEANUP_INTERVAL_S = 7 * 86400  # weekly
    _RETENTION_DAYS = 30
    _last_cleanup = 0.0

    while True:
        await asyncio.sleep(60)
        try:
            cutoff = (
                datetime.now(timezone.utc) - timedelta(seconds=timeout_s)
            ).isoformat()
            async with get_db_ctx() as db:
                stuck = await db.fetch(
                    """SELECT id, user_id, task, session_id FROM background_tasks
                       WHERE status='running' AND created_at < $1""",
                    cutoff,
                )
                if stuck:
                    ids = [r["id"] for r in stuck]
                    await db.execute(
                        """UPDATE background_tasks
                           SET status='blocked', blocker_reason='watchdog_timeout',
                               completed_at=$1
                           WHERE id = ANY($2::int[])""",
                        datetime.now(timezone.utc).isoformat(), ids,
                    )
                    for row in stuck:
                        logger.warning(
                            "Watchdog: task #%d (user=%s) timed out after %.0fs — blocked",
                            row["id"], row["user_id"], timeout_s,
                        )
                        try:
                            from push import broadcaster
                            await broadcaster.broadcast(row["user_id"], "background_task_error", {
                                "task_id": row["id"],
                                "error": "Task timed out",
                                "session_id": row["session_id"],
                            })
                        except Exception:
                            pass

                # Weekly cleanup: delete old done/error rows
                _now = time.time()
                if _now - _last_cleanup >= _CLEANUP_INTERVAL_S:
                    purge_before = (
                        datetime.now(timezone.utc) - timedelta(days=_RETENTION_DAYS)
                    ).isoformat()
                    await db.execute(
                        """DELETE FROM background_tasks
                           WHERE status IN ('done', 'error', 'blocked')
                             AND created_at < $1""",
                        purge_before,
                    )
                    _last_cleanup = _now
                    logger.info("Watchdog: purged old background_tasks rows (>%dd)", _RETENTION_DAYS)

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Watchdog loop error (non-fatal): %s", exc)
