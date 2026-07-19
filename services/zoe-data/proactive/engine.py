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
_cleanup_loop_task: asyncio.Task | None = None

# Set once the missed/error job listeners are attached to the live scheduler.
_listeners_installed: bool = False
# Event loop the engine started on — used to marshal scheduler-thread job
# events back onto the loop safely.
_engine_loop: asyncio.AbstractEventLoop | None = None

# APScheduler job-id prefix for one-shot reminders (see schedule_reminder).
_REMINDER_JOB_PREFIX = "reminder-"
# Give up re-firing a reminder after this many failed attempts so a poison
# reminder can't re-fire on every restart forever.
_MAX_FIRE_ATTEMPTS = int(os.environ.get("ZOE_REMINDER_MAX_ATTEMPTS", "5"))


def register_trigger(trigger: ProactiveTrigger) -> None:
    """Register a Tier 2 slow-loop trigger.

    Idempotent by trigger_type: a lifespan restart / test reload re-runs the
    registration block, and without this guard each trigger would be appended
    again and fire duplicate notifications every slow-loop cycle.
    """
    if any(t.trigger_type == trigger.trigger_type for t in _slow_triggers):
        log.debug("Trigger %s already registered; skipping duplicate", trigger.trigger_type)
        return
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
            # Only mark the OLD scheduled row fired once the follow-up job is
            # actually registered. If schedule_reminder fails, leave this row
            # unfired (and still claimed) rather than losing the notification:
            # reconcile_scheduled_jobs() only re-registers unfired rows, and this
            # keeps the row visible to it once the claim goes stale, instead of
            # being permanently invisible to every recovery path (this is the
            # same stuck-claim residual documented in _fire_reminder's docstring,
            # not a new failure mode). This matters most for item_id='' one-shot
            # nudges (POST /api/proactive/schedule): reminder_scan's recovery only
            # covers item_id != '' rows sourced from the `reminders` table, so a
            # lost item_id='' row had no recovery path at all.
            if pending_id:
                async with _get_compat_db() as db:
                    await db.execute(
                        "UPDATE proactive_scheduled SET fired = 1 WHERE id = ?", (pending_id,)
                    )
                    await db.commit()
        except Exception as _qe:
            log.error(
                "quiet-hours reschedule failed for pending %s (user %s): row left unfired "
                "for stuck-claim recovery: %s",
                pending_id, user_id, _qe,
            )
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

    # P-W2.2 spoken delivery — ADDITIVE, never a replacement: the push below is
    # always sent regardless of what happens in here, and the adapter never
    # raises (any spoken-path failure must not block the push).
    await _maybe_speak_notification(user_id=user_id, message=message, trigger_type=trigger_type)

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
            await _broadcaster.broadcast("all", "notification_created", {"id": _new_id, "type": "reminder", "title": "Reminder", "message": message, "delivered": False}, user_id=user_id)
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


def _spoken_enabled() -> bool:
    """P-W2.2 flag: ZOE_PROACTIVE_SPOKEN, default OFF. Read at call time so an
    .env flip + restart (or a test) needs no module reload."""
    return os.environ.get("ZOE_PROACTIVE_SPOKEN", "").strip().lower() in ("1", "true", "yes", "on")


def _spoken_triggers() -> set[str]:
    """Comma-separated trigger allowlist (ZOE_PROACTIVE_SPOKEN_TRIGGERS),
    default morning_checkin only."""
    raw = os.environ.get("ZOE_PROACTIVE_SPOKEN_TRIGGERS", "morning_checkin")
    return {t.strip() for t in raw.split(",") if t.strip()}


async def _maybe_speak_notification(user_id: str, message: str, trigger_type: str) -> None:
    """P-W2.2 spoken-delivery adapter: if the flag is ON, the trigger is
    allowlisted, and the user has a fresh foreground panel session
    (proactive.presence.panel_presence), enqueue a ``panel_announce`` UI action
    so the kiosk speaks the composed message.

    Contract (binding, see services/zoe-data/AGENTS.md):
      * ADDITIVE — the push in fire_notification is always still sent; this
        never replaces it.
      * NEVER raises — any failure here is logged (the PROACTIVE_SPOKEN line)
        and swallowed so the mandatory push path is untouched.
      * Flag OFF is byte-identical behaviour: immediate return, no DB access,
        no imports.
    """
    try:
        if not _spoken_enabled():
            return
        if trigger_type not in _spoken_triggers():
            return
        # Deferred imports: keep flag-OFF free of them and avoid import cycles
        # (ui_orchestrator pulls in push.broadcaster, same reason _send_push
        # defers routers.push).
        import proactive.presence as _presence
        panel_id = await _presence.panel_presence(user_id)
        if not panel_id:
            log.info("PROACTIVE_SPOKEN trigger=%s user=%s panel=none outcome=absent",
                     trigger_type, user_id)
            return
        import ui_orchestrator as _ui_orchestrator
        async with _get_compat_db() as db:
            await _ui_orchestrator.enqueue_ui_action(
                db,
                user_id=user_id,
                action_type="panel_announce",
                payload={"message": message},
                requested_by="proactive",
                panel_id=panel_id,
            )
        log.info("PROACTIVE_SPOKEN trigger=%s user=%s panel=%s outcome=enqueued",
                 trigger_type, user_id, panel_id)
    except Exception as exc:
        log.warning("PROACTIVE_SPOKEN trigger=%s user=%s outcome=error err=%s",
                    trigger_type, user_id, exc)


async def _send_push(user_id: str, message: str, extra: dict | None = None) -> int:
    """Internal: import push router and call send_push_to_user. Returns subscriber count reached."""
    try:
        from routers.push import send_push_to_user  # deferred to avoid circular imports
        return await send_push_to_user(user_id=user_id, message=message, extra=extra or {}) or 0
    except Exception as exc:
        log.error("_send_push failed for user %s: %s", user_id, exc)
        return 0


async def _slow_loop() -> None:
    """Tier 2 loop: poll registered triggers every SLOW_LOOP_INTERVAL seconds.

    POOL DISCIPLINE: a pooled connection is held ONLY for the discrete
    trigger.check() DB reads, never across fire_notification() — which can run
    an LLM compose_message call plus push delivery, each potentially tens of
    seconds. Firing multiple Tier-2 results in one cycle while pinning a pool
    slot for the whole cycle risks pool exhaustion (same class of bug fixed in
    450f45c3 for idle-consolidation).

    The whole iteration body is wrapped in try/except so a transient failure
    (including the connection acquisition itself) is logged and the loop keeps
    running, rather than the acquisition exception escaping the while-True and
    silently killing this background task for the rest of the process
    lifetime.
    """
    log.info("Proactive slow loop started (interval=%ss)", SLOW_LOOP_INTERVAL)
    while True:
        await asyncio.sleep(SLOW_LOOP_INTERVAL)
        try:
            if _is_in_quiet_hours():
                continue

            # Step 1: short-lived conn — run every trigger.check(), then release
            # before any fire_notification() (LLM + push) work happens.
            pending: list[Any] = []
            async with _get_compat_db() as db:
                for trigger in _slow_triggers:
                    try:
                        results = await trigger.check(db)
                        pending.extend(results)
                    except Exception as exc:
                        log.error("Trigger %s raised: %s", trigger.trigger_type, exc)

            # Step 2: NO pooled connection held across compose_message/push.
            for r in pending:
                try:
                    await fire_notification(
                        user_id=r.user_id,
                        message=r.message,
                        trigger_type=r.trigger_type,
                        item_id=r.item_id,
                        context=r.context,
                    )
                except Exception as exc:
                    log.error("fire_notification failed for trigger result (user=%s, type=%s): %s",
                              getattr(r, "user_id", "?"), getattr(r, "trigger_type", "?"), exc)
        except Exception as exc:
            # Covers connection-acquisition failures (e.g. transient pool
            # exhaustion / DB restart) that would otherwise escape this loop
            # and permanently kill the Tier-2 background task.
            log.error("_slow_loop iteration failed: %s", exc)
            await asyncio.sleep(min(SLOW_LOOP_INTERVAL, 30))


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


# Columns migration 0012 must have added before the claim/generation logic works.
_REQUIRED_PROACTIVE_COLUMNS = {
    "proactive_scheduled": ["attempts", "last_error", "claimed_at", "schedule_generation"],
    "reminders": ["schedule_generation"],
}


async def verify_proactive_schema() -> list[str]:
    """Return the list of required columns that are MISSING (empty list = OK).

    The claim/reconcile/generation logic reads attempts/last_error/claimed_at/
    schedule_generation; if migration 0012 hasn't run those queries error and
    reminders would silently fail to deliver. Startup uses this to make the
    dependency explicit and non-silent (see main.py) rather than failing quietly.
    """
    missing: list[str] = []
    async with _get_compat_db() as db:
        for table, cols in _REQUIRED_PROACTIVE_COLUMNS.items():
            async with db.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = ?",
                (table,),
            ) as cur:
                have = {r["column_name"] for r in await cur.fetchall()}
            missing.extend(f"{table}.{c}" for c in cols if c not in have)
    return missing


def start_proactive_engine() -> None:
    """Start APScheduler (Tier 1) and the slow loop (Tier 2).

    Idempotent: a second call (lifespan restart / test reload) reuses the
    existing scheduler, listeners, and background loops instead of spawning
    duplicates that would each fire notifications.
    """
    global _slow_loop_task, _cleanup_loop_task, _engine_loop
    try:
        loop = asyncio.get_running_loop()
        loop.set_exception_handler(_suppress_scheduler_not_running)
        _engine_loop = loop
    except RuntimeError:
        _engine_loop = None
    start_scheduler()
    _install_job_listeners()
    # Only (re)create a background task if one isn't already alive — a leaked
    # second loop would double-fire every Tier-2 trigger.
    if _slow_loop_task is None or _slow_loop_task.done():
        _slow_loop_task = asyncio.ensure_future(_slow_loop())
    if _cleanup_loop_task is None or _cleanup_loop_task.done():
        _cleanup_loop_task = asyncio.ensure_future(_cleanup_expired_pending())
    log.info("Proactive engine started")


def stop_proactive_engine() -> None:
    """Shut down APScheduler and the slow loop."""
    global _slow_loop_task, _cleanup_loop_task, _listeners_installed
    stop_scheduler()
    # Listeners live on the scheduler instance, which stop_scheduler() discards;
    # clear the flag so a later start re-attaches them to the fresh scheduler.
    _listeners_installed = False
    if _slow_loop_task is not None:
        _slow_loop_task.cancel()
        _slow_loop_task = None
    if _cleanup_loop_task is not None:
        _cleanup_loop_task.cancel()
        _cleanup_loop_task = None
    log.info("Proactive engine stopped")


# --------------------------------------------------------------------------- #
# Missed/error job recovery (Tier 1 reliability)
# --------------------------------------------------------------------------- #
def _install_job_listeners() -> None:
    """Attach missed/error listeners to the live scheduler (once per scheduler).

    A one-shot reminder job that misfires (service down longer than
    misfire_grace_time) is otherwise dropped permanently; the missed listener
    re-fires it. The error listener surfaces a reminder that raised so it isn't
    silently lost (the DB row stays unfired and reconciliation retries it).
    """
    global _listeners_installed
    if _listeners_installed:
        return
    try:
        from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_MISSED
        from proactive.scheduler import get_scheduler
        scheduler = get_scheduler()
        scheduler.add_listener(_on_job_missed, EVENT_JOB_MISSED)
        scheduler.add_listener(_on_job_error, EVENT_JOB_ERROR)
        _listeners_installed = True
        log.info("Proactive job listeners installed (missed/error)")
    except Exception as exc:
        log.warning("Failed to install proactive job listeners: %s", exc)


def _run_on_engine_loop(coro) -> None:
    """Schedule a coroutine on the engine loop from a scheduler-event callback.

    APScheduler dispatches events from the loop thread for AsyncIOScheduler, but
    run_coroutine_threadsafe is correct either way and keeps the task referenced
    until completion (no GC-cancellation).
    """
    loop = _engine_loop
    if loop is None or loop.is_closed():
        log.warning("proactive job event dropped: no live engine loop")
        coro.close()
        return
    try:
        asyncio.run_coroutine_threadsafe(coro, loop)
    except Exception as exc:
        log.warning("failed to schedule proactive job-event handler: %s", exc)
        coro.close()


def _on_job_missed(event) -> None:
    job_id = getattr(event, "job_id", "") or ""
    if not job_id.startswith(_REMINDER_JOB_PREFIX):
        return
    log.warning("reminder job %s MISSED — scheduling catch-up fire", job_id)
    _run_on_engine_loop(_recover_missed_reminder(job_id))


def _on_job_error(event) -> None:
    job_id = getattr(event, "job_id", "") or ""
    if not job_id.startswith(_REMINDER_JOB_PREFIX):
        return
    # _fire_reminder already recorded attempts/last_error and RELEASED the claim.
    # Re-register a bounded, backed-off retry now so a transient failure doesn't
    # leave the reminder stuck until the next restart.
    log.error("reminder job %s raised: %s — scheduling bounded retry",
              job_id, getattr(event, "exception", None))
    _run_on_engine_loop(_retry_errored_reminder(job_id))


async def _retry_errored_reminder(job_id: str) -> None:
    scheduled_id = job_id[len(_REMINDER_JOB_PREFIX):]
    try:
        row = await _load_scheduled_row(scheduled_id)
        if row is None or row["fired"]:
            return
        attempts = row["attempts"] or 0
        if attempts >= _MAX_FIRE_ATTEMPTS:
            log.error("reminder %s exhausted %d attempts; not retrying", scheduled_id, _MAX_FIRE_ATTEMPTS)
            return
        # Linear backoff capped at 5 min; attempts was already bumped by the
        # failed fire, so the first retry waits ~attempts*60s.
        backoff = timedelta(seconds=min(attempts, 5) * 60)
        await _ensure_reminder_job(row, catch_up=True, delay=backoff)
    except Exception as exc:
        log.warning("failed to schedule retry for reminder %s: %s", scheduled_id, exc)


async def _load_scheduled_row(scheduled_id: str):
    async with _get_compat_db() as db:
        async with db.execute(
            "SELECT id, user_id, message, item_id, send_at, apscheduler_job_id, "
            "fired, attempts FROM proactive_scheduled WHERE id = ?",
            (scheduled_id,),
        ) as cur:
            return await cur.fetchone()


async def _ensure_reminder_job(row, *, catch_up: bool, delay: timedelta | None = None) -> bool:
    """(Re)register the APScheduler job for an unfired reminder row.

    Idempotent: the job id is deterministic and register_job uses
    replace_existing, so concurrent listener + reconciliation calls converge to
    exactly ONE live job; the in-job atomic claim guarantees exactly-once even if
    more than one job runs. Returns True if a job was (re)registered.

    catch_up fires immediately (missed/error recovery); otherwise the row's
    send_at is honoured. delay adds a backoff (used for error retries).
    """
    if row is None or row["fired"]:
        return False
    attempts = row["attempts"] or 0
    if attempts >= _MAX_FIRE_ATTEMPTS:
        log.error("reminder %s exceeded %d attempts; giving up", row["id"], _MAX_FIRE_ATTEMPTS)
        return False

    from proactive.scheduler import register_job, run_blocking
    from proactive.triggers.reminders import _fire_reminder

    now = datetime.now(timezone.utc)
    run_at = now
    if not catch_up:
        try:
            send_at = datetime.fromisoformat(str(row["send_at"]).replace("Z", "+00:00"))
            if send_at.tzinfo is None:
                send_at = send_at.replace(tzinfo=timezone.utc)
            run_at = send_at if send_at > now else now
        except Exception:
            run_at = now
    if delay:
        run_at = now + delay

    job_id = row["apscheduler_job_id"] or f"{_REMINDER_JOB_PREFIX}{row['id']}"
    await run_blocking(
        register_job,
        func=_fire_reminder,
        run_at=run_at,
        job_id=job_id,
        kwargs={"pending_id": row["id"], "user_id": row["user_id"], "message": row["message"]},
    )
    log.info("reminder job %s (re)registered for %s (catch_up=%s)", job_id, run_at.isoformat(), catch_up)
    return True


async def _recover_missed_reminder(job_id: str) -> None:
    scheduled_id = job_id[len(_REMINDER_JOB_PREFIX):]
    try:
        row = await _load_scheduled_row(scheduled_id)
        await _ensure_reminder_job(row, catch_up=True)
    except Exception as exc:
        log.warning("failed to recover missed reminder %s: %s", scheduled_id, exc)


async def reconcile_scheduled_jobs() -> int:
    """Startup reconciliation: re-register a live APScheduler job for every
    unfired proactive_scheduled row whose job is missing.

    Covers the gap where a reminder's one-shot job was dropped while the service
    was down (misfire) or never persisted: reminder_scan won't reschedule it
    (an unfired row already exists), so it would be lost forever. Rows that still
    have a live job are left untouched, so a correctly-scheduled reminder is
    never double-fired. Returns the number of jobs recovered.

    Only acts on rows that are UNCLAIMED or whose claim is stuck (older than the
    stuck timeout): a row claimed recently is either being delivered right now or
    was just delivered but not yet marked fired (crash window) — re-registering
    it would risk a double delivery. The in-job atomic claim is the final
    backstop even if a job is registered here.
    """
    from proactive.scheduler import job_exists, run_blocking
    from proactive.triggers.reminders import _STUCK_CLAIM_SECONDS

    stuck_cutoff = (
        datetime.now(timezone.utc) - timedelta(seconds=_STUCK_CLAIM_SECONDS)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        async with _get_compat_db() as db:
            async with db.execute(
                "SELECT id, user_id, message, item_id, send_at, apscheduler_job_id, "
                "fired, attempts FROM proactive_scheduled "
                "WHERE fired = 0 AND (claimed_at IS NULL OR claimed_at < ?)",
                (stuck_cutoff,),
            ) as cur:
                rows = await cur.fetchall()
    except Exception as exc:
        log.warning("reconcile_scheduled_jobs: query failed: %s", exc)
        return 0

    recovered = 0
    for row in rows:
        job_id = row["apscheduler_job_id"] or f"{_REMINDER_JOB_PREFIX}{row['id']}"
        try:
            if await run_blocking(job_exists, job_id):
                continue
            if await _ensure_reminder_job(row, catch_up=False):
                recovered += 1
        except Exception as exc:
            log.warning("reconcile: failed to recover %s: %s", row["id"], exc)

    if recovered:
        log.info("reconcile_scheduled_jobs: recovered %d missed reminder job(s)", recovered)
    return recovered
