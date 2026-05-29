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
import zoneinfo
from datetime import datetime, timedelta, timezone
from typing import Any


from db_compat import get_compat_db as _get_compat_db
from proactive.composer import compose_message
from proactive.session_utils import create_pending
from proactive.scheduler import start_scheduler, stop_scheduler
from proactive.triggers.base import ProactiveTrigger

log = logging.getLogger(__name__)

# Slow-loop poll interval in seconds.  Override with env var.
SLOW_LOOP_INTERVAL = int(os.environ.get("ZOE_PROACTIVE_SLOW_LOOP_S", "300"))

# Quiet hours: no pushes between 22:00 and 07:00 local time (ZOE_TIMEZONE).
_QUIET_START = int(os.environ.get("ZOE_QUIET_START_HOUR", "22"))
_QUIET_END = int(os.environ.get("ZOE_QUIET_END_HOUR", "7"))
_ZOE_TZ = zoneinfo.ZoneInfo(os.environ.get("ZOE_TIMEZONE", "Australia/Perth"))

# Registered Tier 2 trigger instances.
_slow_triggers: list[ProactiveTrigger] = []
_slow_loop_task: asyncio.Task | None = None


def register_trigger(trigger: ProactiveTrigger) -> None:
    """Register a Tier 2 slow-loop trigger."""
    _slow_triggers.append(trigger)
    log.info("Registered trigger: %s", trigger.trigger_type)


def _is_in_quiet_hours() -> bool:
    hour = datetime.now(_ZOE_TZ).hour
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
        # Mark the scheduled row as fired so reminder_scan can reschedule it.
        if pending_id:
            async with _get_compat_db() as db:
                await db.execute(
                    "UPDATE proactive_scheduled SET fired = 1 WHERE id = ?", (pending_id,)
                )
                await db.commit()
        # Reschedule for the start of the next quiet-end window.
        now_local = datetime.now(_ZOE_TZ)
        next_ok = now_local.replace(hour=_QUIET_END, minute=0, second=0, microsecond=0)
        if next_ok <= now_local:
            next_ok += timedelta(days=1)
        try:
            from proactive.triggers.reminders import schedule_reminder
            await schedule_reminder(
                user_id=user_id,
                message=message,
                send_at=next_ok.astimezone(timezone.utc),
                item_id=item_id,
            )
        except Exception as _qe:
            log.warning("quiet-hours reschedule failed: %s", _qe)
        return

    # LLM-compose only for non-reminder types (reminders already have a good message).
    if trigger_type not in ("reminder", "scheduled") and ctx:
        message = await compose_message(trigger_type, ctx, fallback=message)

    # Always create a proactive_pending row — this is the id used by claim_pending()
    # for tap-to-session.  The pending_id parameter is the proactive_scheduled.id and
    # is only used below to mark that row as fired; it must NOT be used as the URL id.
    pid = await create_pending(
        user_id=user_id,
        message=message,
        trigger_type=trigger_type,
        item_id=item_id,
        context=ctx,
    )

    deep_link = f"/chat.html?p={pid}"
    subscribers_reached = await _send_push(user_id=user_id, message=message, extra={"url": deep_link})
    in_app_fallback_ok = False

    # If no WebSocket subscribers received the push, fall back to an in-app
    # notification so the reminder appears in the notification centre when the
    # user next opens the app.
    if subscribers_reached == 0 and trigger_type == "reminder":
        try:
            import uuid as _uuid
            import datetime as _dt
            from push import broadcaster as _broadcaster  # deferred to avoid circular imports
            _new_id = str(_uuid.uuid4())
            async with _get_compat_db() as db:
                await db.execute(
                    """INSERT INTO notifications
                       (id, user_id, type, title, message, delivered, created_at)
                       VALUES (?, ?, 'reminder', ?, ?, 0, ?)""",
                    (
                        _new_id,
                        user_id,
                        "Reminder",
                        message,
                        _dt.datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    ),
                )
                await db.commit()
            await _broadcaster.broadcast("all", "notification_created", {"id": _new_id, "type": "reminder", "title": "Reminder", "message": message, "delivered": False})
            in_app_fallback_ok = True
            log.info("reminder fallback: in-app notification created for user %s (no WS subscribers)", user_id)
        except Exception as _fe:
            log.warning("reminder fallback: in-app insert failed: %s", _fe)

    delivered = subscribers_reached > 0 or in_app_fallback_ok
    if pending_id:
        async with _get_compat_db() as db:
            await db.execute(
                "UPDATE proactive_scheduled SET fired = 1 WHERE id = ?", (pending_id,)
            )
            await db.commit()
        if trigger_type == "reminder" and not delivered:
            log.warning(
                "reminder scheduled %s marked fired without push/in-app delivery for user %s",
                pending_id,
                user_id,
            )


async def _send_push(user_id: str, message: str, extra: dict | None = None) -> int:
    """Internal: import push router and call send_push_to_user. Returns subscriber count reached."""
    try:
        from routers.push import send_push_to_user  # deferred to avoid circular imports
        return await send_push_to_user(user_id=user_id, message=message, extra=extra or {}) or 0
    except Exception as exc:
        log.error("_send_push failed for user %s: %s", user_id, exc)
        return 0


async def _slow_loop() -> None:
    """Tier 2 loop: poll registered triggers every SLOW_LOOP_INTERVAL seconds."""
    log.info("Proactive slow loop started (interval=%ss)", SLOW_LOOP_INTERVAL)
    while True:
        await asyncio.sleep(SLOW_LOOP_INTERVAL)
        if _is_in_quiet_hours():
            continue
        async with _get_compat_db() as db:
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
            async with _get_compat_db() as db:
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
