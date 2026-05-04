"""
Proactive Engine — coordinates all trigger tiers.

Tier 1: APScheduler fires _fire_reminder() directly (via scheduler.py).
Tier 2: Slow async loop polls OpenClawTrigger subclasses every interval.

Public API:
    start_proactive_engine()   — call from main.py lifespan
    stop_proactive_engine()    — call on shutdown
    fire_notification()        — called by Tier 1 jobs AND REST endpoint
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from database import DB_PATH
from proactive.composer import compose_message
from proactive.session_utils import create_pending
from proactive.scheduler import start_scheduler, stop_scheduler
from proactive.triggers.base import ProactiveTrigger

log = logging.getLogger(__name__)

# Slow-loop poll interval in seconds.  Override with env var.
SLOW_LOOP_INTERVAL = int(os.environ.get("ZOE_PROACTIVE_SLOW_LOOP_S", "300"))

# Quiet hours: no pushes between 22:00 and 07:00 local (server) time.
_QUIET_START = int(os.environ.get("ZOE_QUIET_START_HOUR", "22"))
_QUIET_END = int(os.environ.get("ZOE_QUIET_END_HOUR", "7"))

# Registered Tier 2 trigger instances.
_slow_triggers: list[ProactiveTrigger] = []
_slow_loop_task: asyncio.Task | None = None


def register_trigger(trigger: ProactiveTrigger) -> None:
    """Register a Tier 2 slow-loop trigger."""
    _slow_triggers.append(trigger)
    log.info("Registered trigger: %s", trigger.trigger_type)


def _is_in_quiet_hours() -> bool:
    hour = datetime.now().hour  # local server time
    if _QUIET_START > _QUIET_END:
        # Spans midnight, e.g. 22–7.
        return hour >= _QUIET_START or hour < _QUIET_END
    return _QUIET_START <= hour < _QUIET_END


async def fire_notification(
    user_id: str,
    message: str,
    trigger_type: str = "scheduled",
    pending_id: str | None = None,
    context: dict[str, Any] | None = None,
    item_id: str = "",
) -> None:
    """
    Core notification dispatch:
      1. Check quiet hours (skip if quiet, unless force_send in context).
      2. Optionally compose a richer message via the LLM.
      3. Create a proactive_pending row (lazy session, claimed on tap).
      4. Send a push notification with the deep-link URL.
      5. Mark proactive_scheduled as fired (if pending_id given).
    """
    ctx = context or {}
    force = ctx.get("force_send", False)

    if not force and _is_in_quiet_hours():
        log.info("Quiet hours active — deferring notification for user %s", user_id)
        return

    # LLM-compose only for non-reminder types (reminders already have a good message).
    if trigger_type not in ("reminder", "scheduled") and ctx:
        message = await compose_message(trigger_type, ctx, fallback=message)

    # Create a pending row so tap → session works.
    pid = pending_id or await create_pending(
        user_id=user_id,
        message=message,
        trigger_type=trigger_type,
        item_id=item_id,
        context=ctx,
    )

    deep_link = f"/chat.html?p={pid}"
    await _send_push(user_id=user_id, message=message, extra={"url": deep_link})

    # Mark scheduled row as fired.
    if pending_id:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE proactive_scheduled SET fired = 1 WHERE id = ?", (pending_id,)
            )
            await db.commit()


async def _send_push(user_id: str, message: str, extra: dict | None = None) -> None:
    """Internal: import push router and call send_push_to_user."""
    try:
        from routers.push import send_push_to_user  # deferred to avoid circular imports
        await send_push_to_user(user_id=user_id, message=message, extra=extra or {})
    except Exception as exc:
        log.error("_send_push failed for user %s: %s", user_id, exc)


async def _slow_loop() -> None:
    """Tier 2 loop: poll registered triggers every SLOW_LOOP_INTERVAL seconds."""
    log.info("Proactive slow loop started (interval=%ss)", SLOW_LOOP_INTERVAL)
    while True:
        await asyncio.sleep(SLOW_LOOP_INTERVAL)
        if _is_in_quiet_hours():
            continue
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            for trigger in _slow_triggers:
                try:
                    results = await trigger.check(db)
                    for r in results:
                        await fire_notification(
                            user_id=r.user_id,
                            message=r.message,
                            trigger_type=r.trigger_type,
                            item_id=r.item_id,
                            context=r.context,
                        )
                except Exception as exc:
                    log.error("Trigger %s raised: %s", trigger.trigger_type, exc)


async def _cleanup_expired_pending() -> None:
    """Periodically prune expired, unclaimed proactive_pending rows."""
    while True:
        await asyncio.sleep(3600)
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                async with db.execute(
                    "DELETE FROM proactive_pending WHERE claimed = 0 AND expires_at < ?",
                    (datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),),
                ) as cur:
                    deleted = cur.rowcount
                await db.commit()
            if deleted:
                log.info("Pruned %d expired proactive_pending rows", deleted)
        except Exception as exc:
            log.warning("cleanup_expired_pending: %s", exc)


def _suppress_scheduler_not_running(loop: asyncio.AbstractEventLoop, context: dict) -> None:
    """Swallow APScheduler's double-shutdown error that occurs on rapid restarts."""
    exc = context.get("exception")
    if exc is not None and exc.__class__.__name__ == "SchedulerNotRunningError":
        return
    loop.default_exception_handler(context)


def start_proactive_engine() -> None:
    """Start APScheduler (Tier 1) and the slow loop (Tier 2)."""
    global _slow_loop_task
    try:
        loop = asyncio.get_running_loop()
        loop.set_exception_handler(_suppress_scheduler_not_running)
    except RuntimeError:
        pass
    start_scheduler()
    _slow_loop_task = asyncio.ensure_future(_slow_loop())
    asyncio.ensure_future(_cleanup_expired_pending())
    log.info("Proactive engine started")


def stop_proactive_engine() -> None:
    """Shut down APScheduler and the slow loop."""
    global _slow_loop_task
    stop_scheduler()
    if _slow_loop_task is not None:
        _slow_loop_task.cancel()
        _slow_loop_task = None
    log.info("Proactive engine stopped")
