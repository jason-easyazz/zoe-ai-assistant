"""Regression tests for the scheduler/reminder reliability cluster.

Covers four bugs and the three required behaviours:

  (a) A reminder whose one-shot APScheduler job was dropped while the service was
      down (>misfire_grace) still fires after restart — via the missed-job
      listener catch-up and startup reconciliation.
  (b) An updated / snoozed reminder fires at the NEW time and never at the old
      one — update/snooze cancel the stale job and reschedule.
  (c) A lifespan restart / reload does not duplicate triggers or background
      loops.

Plus P2: _fire_reminder records failure state and RE-RAISES (so APScheduler
marks the job errored and reconciliation can retry) instead of swallowing.

All fakes avoid a real DB / scheduler so the suite runs without Postgres.
"""
import asyncio
import contextlib
from datetime import datetime, timedelta, timezone

import pytest

import proactive.engine as engine
import proactive.scheduler as scheduler
import proactive.triggers.reminder_scan as scan
import proactive.triggers.reminders as reminders


# --------------------------------------------------------------------------- #
# Dual-mode fake compat DB (supports both `await db.execute()` and
# `async with db.execute() as cur`).
# --------------------------------------------------------------------------- #
class _Cursor:
    def __init__(self, rows):
        self._rows = rows

    async def fetchall(self):
        return self._rows

    async def fetchone(self):
        return self._rows[0] if self._rows else None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


class _Exec:
    def __init__(self, factory):
        self._factory = factory

    def __await__(self):
        return self._factory().__await__()

    async def __aenter__(self):
        return await self._factory()

    async def __aexit__(self, *_):
        return False


class FakeCompatDB:
    """Returns ``select_rows`` for any SELECT/RETURNING, [] otherwise; records SQL."""

    def __init__(self, select_rows=None):
        self.select_rows = list(select_rows or [])
        self.executed = []

    def execute(self, sql, params=()):
        norm = " ".join(sql.split())
        self.executed.append((norm, params))
        upper = norm.strip().upper()
        rows = self.select_rows if (upper.startswith("SELECT") or "RETURNING" in upper) else []
        return _Exec(lambda: _make_cursor(rows))

    async def commit(self):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


async def _make_cursor(rows):
    return _Cursor(rows)


def _patch_compat(monkeypatch, module, db):
    @contextlib.asynccontextmanager
    async def fake_compat_db():
        yield db

    monkeypatch.setattr(module, "_get_compat_db", fake_compat_db)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# =========================================================================== #
# (a) Missed reminder fires after restart
# =========================================================================== #
@pytest.mark.asyncio
async def test_reconcile_reregisters_reminder_with_missing_job(monkeypatch):
    """fired=0 row whose live job is gone → re-registered at its send_at."""
    send_at = datetime.now(timezone.utc) + timedelta(hours=2)
    row = {
        "id": "r1", "user_id": "u1", "message": "ping", "item_id": "rem1",
        "send_at": _iso(send_at), "apscheduler_job_id": "reminder-r1",
        "fired": 0, "attempts": 0,
    }
    _patch_compat(monkeypatch, engine, FakeCompatDB(select_rows=[row]))
    monkeypatch.setattr(scheduler, "job_exists", lambda jid: False)
    registered = []
    monkeypatch.setattr(scheduler, "register_job", lambda **kw: registered.append(kw))

    recovered = await engine.reconcile_scheduled_jobs()

    assert recovered == 1
    assert registered[0]["job_id"] == "reminder-r1"
    # Future reminder keeps its original fire time (no double-fire / no shifting).
    assert registered[0]["run_at"] == datetime.fromisoformat(row["send_at"].replace("Z", "+00:00"))
    assert registered[0]["kwargs"]["pending_id"] == "r1"


@pytest.mark.asyncio
async def test_reconcile_skips_reminder_with_live_job(monkeypatch):
    """A reminder that still has a live job is left untouched (no double-fire)."""
    row = {
        "id": "r1", "user_id": "u1", "message": "ping", "item_id": "rem1",
        "send_at": _iso(datetime.now(timezone.utc) + timedelta(hours=2)),
        "apscheduler_job_id": "reminder-r1", "fired": 0, "attempts": 0,
    }
    _patch_compat(monkeypatch, engine, FakeCompatDB(select_rows=[row]))
    monkeypatch.setattr(scheduler, "job_exists", lambda jid: True)
    registered = []
    monkeypatch.setattr(scheduler, "register_job", lambda **kw: registered.append(kw))

    recovered = await engine.reconcile_scheduled_jobs()

    assert recovered == 0
    assert registered == []


@pytest.mark.asyncio
async def test_reconcile_gives_up_after_max_attempts(monkeypatch):
    """A poison reminder past the attempt cap is not re-registered (no spin)."""
    row = {
        "id": "r1", "user_id": "u1", "message": "ping", "item_id": "rem1",
        "send_at": _iso(datetime.now(timezone.utc) - timedelta(hours=2)),
        "apscheduler_job_id": "reminder-r1", "fired": 0,
        "attempts": engine._MAX_FIRE_ATTEMPTS,
    }
    _patch_compat(monkeypatch, engine, FakeCompatDB(select_rows=[row]))
    monkeypatch.setattr(scheduler, "job_exists", lambda jid: False)
    registered = []
    monkeypatch.setattr(scheduler, "register_job", lambda **kw: registered.append(kw))

    assert await engine.reconcile_scheduled_jobs() == 0
    assert registered == []


@pytest.mark.asyncio
async def test_missed_listener_schedules_immediate_catchup(monkeypatch):
    """EVENT_JOB_MISSED handler re-registers a past-due reminder to fire now."""
    before = datetime.now(timezone.utc)
    row = {
        "id": "r1", "user_id": "u1", "message": "ping", "item_id": "rem1",
        "send_at": _iso(before - timedelta(hours=6)),  # missed during downtime
        "apscheduler_job_id": "reminder-r1", "fired": 0, "attempts": 0,
    }
    _patch_compat(monkeypatch, engine, FakeCompatDB(select_rows=[row]))
    registered = []
    monkeypatch.setattr(scheduler, "register_job", lambda **kw: registered.append(kw))

    await engine._recover_missed_reminder("reminder-r1")

    assert registered, "missed reminder must be re-registered for catch-up"
    assert registered[0]["job_id"] == "reminder-r1"
    # Catch-up fires immediately (not at the long-past original time).
    assert registered[0]["run_at"] >= before


@pytest.mark.asyncio
async def test_missed_listener_ignores_non_reminder_jobs(monkeypatch):
    fired = []
    monkeypatch.setattr(engine, "_run_on_engine_loop", lambda coro: (fired.append(1), coro.close()))

    class _Evt:
        job_id = "multica-autopilot-7"

    engine._on_job_missed(_Evt())
    assert fired == [], "only reminder-* jobs trigger catch-up"


# =========================================================================== #
# P2: _fire_reminder records failure and re-raises (no silent death)
# =========================================================================== #
@pytest.mark.asyncio
async def test_fire_reminder_records_failure_and_reraises(monkeypatch):
    async def boom(**_kw):
        raise RuntimeError("delivery down")

    monkeypatch.setattr("proactive.engine.fire_notification", boom)
    db = FakeCompatDB()
    _patch_compat(monkeypatch, reminders, db)

    with pytest.raises(RuntimeError):
        await reminders._fire_reminder("p1", "u1", "hi")

    # Failure state recorded so reconciliation/listeners can act.
    assert any(
        sql.upper().startswith("UPDATE PROACTIVE_SCHEDULED") and "ATTEMPTS" in sql.upper()
        for sql, _ in db.executed
    ), "attempts/last_error must be recorded on failure"


@pytest.mark.asyncio
async def test_fire_reminder_success_does_not_record_failure(monkeypatch):
    async def ok(**_kw):
        return None

    monkeypatch.setattr("proactive.engine.fire_notification", ok)
    db = FakeCompatDB()
    _patch_compat(monkeypatch, reminders, db)

    await reminders._fire_reminder("p1", "u1", "hi")  # must not raise

    assert db.executed == [], "a successful fire writes no failure state"


# =========================================================================== #
# (b) Updated / snoozed reminder fires at the NEW time, not the old
# =========================================================================== #
@pytest.mark.asyncio
async def test_schedule_due_reminder_uses_current_due_time(monkeypatch):
    captured = {}

    async def fake_schedule_reminder(user_id, message, send_at, item_id=""):
        captured.update(user_id=user_id, message=message, send_at=send_at, item_id=item_id)
        return "row-x"

    monkeypatch.setattr("proactive.triggers.reminders.schedule_reminder", fake_schedule_reminder)

    now = datetime.now(timezone.utc)
    target_local = now.astimezone(scan._ZOE_TZ) + timedelta(hours=2)
    due_time = f"{target_local.hour:02d}:{target_local.minute:02d}"
    row = {"id": "rem1", "user_id": "u1", "title": "take meds",
           "due_date": None, "due_time": due_time}
    db = FakeCompatDB(select_rows=[])  # no existing unfired schedule

    rid = await scan.schedule_due_reminder(db, row, now_utc=now)

    assert rid == "rem1"
    assert captured["item_id"] == "rem1"
    # send_at is derived from the row's (new) due_time, not any stale value.
    assert captured["send_at"] == scan.build_run_at(
        None, target_local.hour, target_local.minute, now
    )


@pytest.mark.asyncio
async def test_schedule_due_reminder_idempotent_when_already_scheduled(monkeypatch):
    called = []
    monkeypatch.setattr(
        "proactive.triggers.reminders.schedule_reminder",
        lambda **kw: called.append(kw),
    )
    db = FakeCompatDB(select_rows=[{"exists": 1}])  # an unfired row already exists
    row = {"id": "rem1", "user_id": "u1", "title": "t", "due_date": None, "due_time": "09:00"}

    rid = await scan.schedule_due_reminder(db, row, now_utc=datetime.now(timezone.utc))

    assert rid is None
    assert called == [], "must not double-schedule an already-scheduled reminder"


@pytest.mark.asyncio
async def test_cancel_reminder_jobs_cancels_every_unfired(monkeypatch):
    db = FakeCompatDB(select_rows=[{"id": "s1"}, {"id": "s2"}])
    _patch_compat(monkeypatch, reminders, db)
    cancelled = []

    async def fake_cancel(sid):
        cancelled.append(sid)
        return True

    monkeypatch.setattr(reminders, "cancel_reminder", fake_cancel)

    n = await reminders.cancel_reminder_jobs("rem1")

    assert n == 2
    assert cancelled == ["s1", "s2"]


@pytest.mark.asyncio
async def test_update_resync_cancels_old_then_reschedules_new(monkeypatch):
    """Router resync: stale job cancelled, reminder rescheduled at the new due-time."""
    import routers.reminders as rr

    cancelled = []
    scheduled = []

    async def fake_cancel(reminder_id):
        cancelled.append(reminder_id)
        return 1

    async def fake_schedule_due(db, row, **_kw):
        scheduled.append(dict(row))
        return row["id"]

    monkeypatch.setattr("proactive.triggers.reminders.cancel_reminder_jobs", fake_cancel)
    monkeypatch.setattr("proactive.triggers.reminder_scan.schedule_due_reminder", fake_schedule_due)

    updated_row = {
        "id": "rem1", "user_id": "u1", "title": "t",
        "due_date": "2026-07-01", "due_time": "09:30",  # the NEW time
        "is_active": 1, "acknowledged": 0, "deleted": 0, "snoozed_until": None,
    }
    db = FakeCompatDB(select_rows=[updated_row])

    await rr._resync_reminder_schedule(db, "rem1", reschedule=True)

    assert cancelled == ["rem1"], "the old-time job must be cancelled"
    assert scheduled and scheduled[0]["due_time"] == "09:30", "rescheduled at the NEW time"


@pytest.mark.asyncio
async def test_resync_no_reschedule_only_cancels(monkeypatch):
    """acknowledge/delete: cancel only, never reschedule a done reminder."""
    import routers.reminders as rr

    cancelled = []
    scheduled = []
    monkeypatch.setattr(
        "proactive.triggers.reminders.cancel_reminder_jobs",
        lambda rid: _coro_append(cancelled, rid),
    )
    monkeypatch.setattr(
        "proactive.triggers.reminder_scan.schedule_due_reminder",
        lambda db, row, **k: _coro_append(scheduled, row),
    )

    await rr._resync_reminder_schedule(FakeCompatDB(), "rem1", reschedule=False)

    assert cancelled == ["rem1"]
    assert scheduled == [], "reschedule=False must not register a new job"


async def _coro_append(bucket, value):
    bucket.append(value)
    return 1


@pytest.mark.asyncio
async def test_snooze_route_reschedules_at_snooze_time(monkeypatch):
    """Snooze cancels the old-time job and schedules a one-shot at snoozed_until."""
    import routers.reminders as rr
    from models import SnoozeBody

    reminder_row = {"id": "rem1", "user_id": "u1", "title": "stretch",
                    "due_time": "09:00", "is_active": 1, "acknowledged": 0,
                    "deleted": 0, "snoozed_until": None}
    db = FakeCompatDB(select_rows=[reminder_row])

    async def noop(*a, **k):
        return None

    monkeypatch.setattr(rr, "require_feature_access", noop)
    monkeypatch.setattr(rr, "_create_notification", noop)
    monkeypatch.setattr(rr.broadcaster, "broadcast", noop)

    cancelled = []
    scheduled = []

    async def fake_cancel(reminder_id):
        cancelled.append(reminder_id)
        return 1

    async def fake_schedule_reminder(user_id, message, send_at, item_id=""):
        scheduled.append({"user_id": user_id, "message": message,
                          "send_at": send_at, "item_id": item_id})
        return "row-x"

    monkeypatch.setattr("proactive.triggers.reminders.cancel_reminder_jobs", fake_cancel)
    monkeypatch.setattr("proactive.triggers.reminders.schedule_reminder", fake_schedule_reminder)

    before = datetime.now(timezone.utc)
    result = await rr.snooze_reminder(
        "rem1", SnoozeBody(snooze_minutes=15), user={"user_id": "u1"}, db=db
    )

    assert result is not None
    assert cancelled == ["rem1"], "old-time job cancelled on snooze"
    assert scheduled, "snooze must schedule a fire at the new (snooze) time"
    sched = scheduled[0]
    assert sched["item_id"] == "rem1"
    # New fire time is ~15 min out (the snooze), not the original 09:00.
    assert before + timedelta(minutes=14) <= sched["send_at"] <= before + timedelta(minutes=16)


# =========================================================================== #
# (c) No duplicate triggers / loops on reload
# =========================================================================== #
@pytest.fixture
def trigger_registry():
    saved = list(engine._slow_triggers)
    engine._slow_triggers.clear()
    try:
        yield engine._slow_triggers
    finally:
        engine._slow_triggers.clear()
        engine._slow_triggers.extend(saved)


def test_register_trigger_is_idempotent(trigger_registry):
    class _T:
        def __init__(self, t):
            self.trigger_type = t

    engine.register_trigger(_T("reminder_scan"))
    engine.register_trigger(_T("reminder_scan"))  # reload re-registers
    engine.register_trigger(_T("morning_checkin"))

    types = [t.trigger_type for t in trigger_registry]
    assert types.count("reminder_scan") == 1, "duplicate trigger must be skipped"
    assert types.count("morning_checkin") == 1


@pytest.fixture
def engine_task_globals():
    previous_slow = engine._slow_loop_task
    previous_cleanup = engine._cleanup_loop_task
    previous_listeners = engine._listeners_installed
    engine._slow_loop_task = None
    engine._cleanup_loop_task = None
    engine._listeners_installed = False
    try:
        yield
    finally:
        for task in (engine._slow_loop_task, engine._cleanup_loop_task):
            if task is not None and not task.done():
                task.cancel()
        engine._slow_loop_task = previous_slow
        engine._cleanup_loop_task = previous_cleanup
        engine._listeners_installed = previous_listeners


@pytest.mark.asyncio
async def test_start_proactive_engine_does_not_duplicate_loops(monkeypatch, engine_task_globals):
    monkeypatch.setattr(engine, "start_scheduler", lambda: None)
    monkeypatch.setattr(engine, "_install_job_listeners", lambda: None)

    async def neverending():
        await asyncio.Event().wait()

    monkeypatch.setattr(engine, "_slow_loop", neverending)
    monkeypatch.setattr(engine, "_cleanup_expired_pending", neverending)

    engine.start_proactive_engine()
    slow1, cleanup1 = engine._slow_loop_task, engine._cleanup_loop_task

    engine.start_proactive_engine()  # simulate lifespan restart / reload

    assert engine._slow_loop_task is slow1, "live slow loop must not be replaced"
    assert engine._cleanup_loop_task is cleanup1, "live cleanup loop must not be replaced"

    engine.stop_proactive_engine()
    assert engine._slow_loop_task is None
    assert engine._cleanup_loop_task is None
    assert engine._listeners_installed is False
    await asyncio.gather(slow1, cleanup1, return_exceptions=True)
