"""
background_runner.py — Background task queue for Zoe.

Enables "fire and forget" tasks:
  1. User says "go find hotel prices, let me know when done"
  2. Pi Agent calls escalate_to_openclaw(background=True)
  3. chat.py calls enqueue_background_task()
  4. This module runs the task via OpenClaw ACP, stores result
  5. Next chat load: /api/chat/tasks/pending returns results
  6. Frontend injects them as proactive Zoe messages

Database schema (created on first use):
  CREATE TABLE background_tasks (
      id         INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id    TEXT NOT NULL,
      session_id TEXT,
      task       TEXT NOT NULL,
      status     TEXT DEFAULT 'pending',   -- pending|running|done|error
      result     TEXT,
      seen       INTEGER DEFAULT 0,
      created_at TEXT,
      completed_at TEXT
  )
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Running task futures keyed by task id (to avoid duplicate runs)
_running: dict[int, asyncio.Task] = {}


async def _ensure_table(db) -> None:
    await db.execute("""
        CREATE TABLE IF NOT EXISTS background_tasks (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      TEXT NOT NULL,
            session_id   TEXT,
            task         TEXT NOT NULL,
            status       TEXT DEFAULT 'pending',
            result       TEXT,
            seen         INTEGER DEFAULT 0,
            created_at   TEXT,
            completed_at TEXT
        )
    """)
    await db.commit()


async def _get_db():
    """Open the Zoe database directly (mirrors database.py pattern)."""
    import aiosqlite
    import os
    db_path = os.environ.get("ZOE_DATA_DB", os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "zoe.db"
    ))
    db = await aiosqlite.connect(db_path)
    await db.execute("PRAGMA journal_mode=WAL")
    return db


async def enqueue_background_task(task: str, user_id: str, session_id: str | None = None) -> int:
    """Insert a task row and kick off the async runner. Returns the new task id."""
    db = await _get_db()
    try:
        await _ensure_table(db)
        now = datetime.now(timezone.utc).isoformat()
        cur = await db.execute(
            "INSERT INTO background_tasks (user_id, session_id, task, status, created_at) VALUES (?,?,?,?,?)",
            (user_id, session_id, task, "pending", now),
        )
        await db.commit()
        task_id: int = cur.lastrowid  # type: ignore[assignment]
    finally:
        await db.close()

    # Fire off the runner coroutine
    fut = asyncio.ensure_future(_run_task(task_id, task, user_id, session_id))
    _running[task_id] = fut
    logger.info("background_runner: enqueued task #%d for user=%s", task_id, user_id)
    return task_id


async def _run_task(task_id: int, task: str, user_id: str, session_id: str | None) -> None:
    """Execute the task via OpenClaw ACP and store the result."""
    from zoe_acp_client import openclaw_acp_stream as _acp_stream

    async def _set_status(status: str, result: str | None = None) -> None:
        db = await _get_db()
        try:
            now = datetime.now(timezone.utc).isoformat()
            await db.execute(
                "UPDATE background_tasks SET status=?, result=?, completed_at=? WHERE id=?",
                (status, result, now if status in ("done", "error") else None, task_id),
            )
            await db.commit()
        finally:
            await db.close()

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
    except Exception as exc:
        logger.warning("background_runner: task #%d failed: %s", task_id, exc)
        await _set_status("error", f"Task failed: {exc}")
    finally:
        _running.pop(task_id, None)


async def get_pending_tasks(user_id: str) -> list[dict]:
    """Return completed-but-unseen tasks for a user, then mark them seen."""
    db = await _get_db()
    try:
        await _ensure_table(db)
        rows = await db.execute(
            "SELECT id, task, result, created_at, completed_at FROM background_tasks "
            "WHERE user_id=? AND status='done' AND seen=0 ORDER BY completed_at ASC",
            (user_id,),
        )
        tasks = await rows.fetchall()
        if tasks:
            ids = [str(r[0]) for r in tasks]
            await db.execute(
                f"UPDATE background_tasks SET seen=1 WHERE id IN ({','.join(ids)})"
            )
            await db.commit()
    finally:
        await db.close()

    return [
        {
            "id": r[0],
            "task": r[1],
            "result": r[2] or "",
            "created_at": r[3],
            "completed_at": r[4],
        }
        for r in tasks
    ]
