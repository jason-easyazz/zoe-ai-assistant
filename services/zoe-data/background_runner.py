"""
background_runner.py - the background_tasks table: read side and housekeeping only.

There is NO executor here any more. This module used to launch ``hermes --accept-hooks`` with
``HERMES_YOLO_MODE=1`` as the zoe user for any text a chat / voice / A2A escalation enqueued; Hermes
is retired and that path was a latent root-equivalent execution route reachable from a guest
(deep dive S0-7), so it was removed, not gated. Nothing in the repo may launch ``hermes`` or set
``HERMES_YOLO_MODE`` (pinned by ``tests/test_no_hermes_exec.py``).

What remains is genuinely live:
  * ``get_pending_tasks`` - rows already marked done and not yet shown (``/api/chat/tasks/pending``);
  * ``_watchdog_loop`` - reaps rows stuck in 'running' and purges old done/error rows weekly.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from async_subprocess import env_float_failsafe

logger = logging.getLogger(__name__)


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


def _watchdog_timeout_s() -> float:
    """When the watchdog may declare a 'running' row stuck (``ZOE_TASK_TIMEOUT_S``, default 1 h).

    No runner creates 'running' rows any more, so this only reaps legacy rows. The hardened parser,
    not bare float(): NaN defeats the cutoff comparison and inf disables it; garbage or a
    non-finite value falls back to the default.
    """
    return env_float_failsafe("ZOE_TASK_TIMEOUT_S", 3600.0)


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

    timeout_s = _watchdog_timeout_s()
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
