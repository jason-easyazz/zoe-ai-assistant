"""
background_runner.py — Background task queue for Zoe.

Enables "fire and forget" tasks:
  1. User says "go find hotel prices, let me know when done"
  2. Pi Agent calls escalate_to_openclaw(background=True)
  3. chat.py calls enqueue_background_task()
  4. This module runs the task via OpenClaw ACP, stores result
  5. Next chat load: /api/chat/tasks/pending returns results
  6. Frontend injects them as proactive Zoe messages
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Running task futures keyed by task id (to avoid duplicate runs)
_running: dict[int, asyncio.Task] = {}


async def enqueue_background_task(
    task: str,
    user_id: str,
    session_id: str | None = None,
    panel_id: str | None = None,
) -> int:
    """Insert a task row and kick off the async runner. Returns the new task id."""
    from db_pool import get_db, get_db_ctx
    now = datetime.now(timezone.utc).isoformat()
    async with get_db_ctx() as db:
        row = await db.fetchrow(
            """INSERT INTO background_tasks (user_id, session_id, panel_id, task, status, created_at)
               VALUES ($1, $2, $3, $4, $5, $6) RETURNING id""",
            user_id, session_id, panel_id, task, "pending", now,
        )
        task_id: int = row["id"]

    # Fire off the runner coroutine
    fut = asyncio.ensure_future(_run_task(task_id, task, user_id, session_id, panel_id=panel_id))
    _running[task_id] = fut
    logger.info("background_runner: enqueued task #%d for user=%s panel=%s", task_id, user_id, panel_id)
    return task_id


async def _run_task(
    task_id: int,
    task: str,
    user_id: str,
    session_id: str | None,
    panel_id: str | None = None,
) -> None:
    """Execute the task via OpenClaw ACP and store the result."""
    from zoe_acp_client import openclaw_acp_stream as _acp_stream
    from db_pool import get_db, get_db_ctx

    async def _set_status(status: str, result: str | None = None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        async with get_db_ctx() as db:
            await db.execute(
                "UPDATE background_tasks SET status=$1, result=$2, completed_at=$3 WHERE id=$4",
                status, result, now if status in ("done", "error") else None, task_id,
            )

    await _set_status("running")
    try:
        gateway_key = f"agent:main:zoe_bg_{user_id}_{task_id}"
        chunks: list[str] = []
        async for chunk in _acp_stream(task, gateway_key):
            chunks.append(chunk)
        result = "".join(chunks).strip()
        if not result:
            result = "(No result returned)"
        await _set_status("done", result)
        logger.info("background_runner: task #%d completed (%d chars)", task_id, len(result))
        try:
            from push import broadcaster
            await broadcaster.broadcast(user_id, "background_task_done", {
                "task_id": task_id,
                "result": result[:500],
                "session_id": session_id,
                "panel_id": panel_id,
            })
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
            await broadcaster.broadcast(user_id, "background_task_error", {
                "task_id": task_id,
                "error": str(exc),
                "session_id": session_id,
            })
        except Exception:
            pass
    finally:
        _running.pop(task_id, None)


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
