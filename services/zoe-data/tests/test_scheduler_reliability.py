"""Regression tests for the scheduler/reminder reliability cluster.

Covers the original four bugs AND the three cross-review race conditions, plus
the three required behaviours:

  (a) A reminder whose one-shot APScheduler job was dropped while the service was
      down (>misfire_grace) still fires after restart — missed-job listener
      catch-up and startup reconciliation.
  (b) An updated / snoozed reminder fires at the NEW time and never at the old
      one — update/snooze cancel the stale job BEFORE mutating, and reschedule.
  (c) A lifespan restart / reload does not duplicate triggers or background
      loops.

Cross-review races, all gated by the atomic single-row claim:
  B1 listener+reconcile double-fire → exactly one delivery.
  B2 router commit→cancel window → no stale/after-delete fire (cancel-before-
     mutate + in-job obligation re-read).
  B3 crash-after-deliver-before-mark → reconcile does NOT re-deliver (recent
     claim), but a genuinely stuck claim recovers after the stuck timeout.

The fakes model the proactive_scheduled claim semantics so the races are
exercised for real, not just happy-path sequencing.
"""
import asyncio
import contextlib
from datetime import datetime, timedelta, timezone

import pytest

import proactive.engine as engine
import proactive.scheduler as scheduler
import proactive.triggers.reminder_scan as scan
import proactive.triggers.reminders as reminders

pytestmark = pytest.mark.ci_safe


# --------------------------------------------------------------------------- #
# Dual-mode cursor/exec (supports `await db.execute()` and `async with ...`).
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
    """Returns ``select_rows`` for any SELECT/RETURNING, [] otherwise; records SQL.

    Dumb fake for tests that don't depend on claim semantics.
    """

    def __init__(self, select_rows=None):
        self.select_rows = list(select_rows or [])
        self.executed = []

    def execute(self, sql, params=()):
        norm = " ".join(sql.split())
        self.executed.append((norm, params))
        upper = norm.strip().upper()
        rows = self.select_rows if (upper.startswith("SELECT") or "RETURNING" in upper) else []
        return _Exec(lambda: _async_cursor(rows))

    async def commit(self):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


class FakeReminderDB:
    """Models proactive_scheduled + reminders with REAL atomic-claim semantics.

    Implements the conditional claim UPDATE, fired-marking, failure/release, the
    obligation read, and the reconcile/load selects — so the races are exercised
    rather than mocked away.
    """

    def __init__(self, scheduled=None, reminders=None):
        self.scheduled = scheduled or {}
        self.reminders = reminders or {}
        self.executed = []

    def execute(self, sql, params=()):
        norm = " ".join(sql.split())
        p = list(params) if isinstance(params, (list, tuple)) else [params]
        self.executed.append((norm, p))
        return _Exec(lambda: self._do(norm.upper(), p))

    async def _do(self, u, p):
        # Atomic delivery claim.
        if u.startswith("UPDATE PROACTIVE_SCHEDULED SET CLAIMED_AT = ? WHERE ID = ? AND FIRED = 0"):
            now_iso, sid, stuck = p[0], p[1], p[2]
            row = self.scheduled.get(sid)
            if row and row.get("fired", 0) == 0 and (
                row.get("claimed_at") is None or row["claimed_at"] < stuck
            ):
                row["claimed_at"] = now_iso
                return _Cursor([{"id": sid, "item_id": row.get("item_id", ""),
                                 "schedule_generation": row.get("schedule_generation", 0)}])
            return _Cursor([])
        # Consume (void obligation): mark fired without delivering.
        if u.startswith("UPDATE PROACTIVE_SCHEDULED SET FIRED = 1 WHERE ID = ?"):
            row = self.scheduled.get(p[0])
            if row:
                row["fired"] = 1
            return _Cursor([])
        # Failure: bump attempts, store last_error, RELEASE claim.
        if u.startswith("UPDATE PROACTIVE_SCHEDULED SET ATTEMPTS"):
            row = self.scheduled.get(p[-1])
            if row:
                row["attempts"] = row.get("attempts", 0) + 1
                row["last_error"] = p[0]
                row["claimed_at"] = None
            return _Cursor([])
        # Obligation + generation read (is_active/acknowledged/deleted/generation).
        if "FROM REMINDERS" in u and "IS_ACTIVE" in u:
            r = self.reminders.get(p[0])
            if not r:
                return _Cursor([])
            out = dict(r)
            out.setdefault("schedule_generation", 0)
            return _Cursor([out])
        # Generation-only read (schedule_reminder stamping).
        if "SELECT SCHEDULE_GENERATION FROM REMINDERS" in u:
            r = self.reminders.get(p[0])
            return _Cursor([{"schedule_generation": (r or {}).get("schedule_generation", 0)}] if r else [])
        # cancel_reminder_jobs select (by item_id).
        if "FROM PROACTIVE_SCHEDULED" in u and "ITEM_ID = ?" in u:
            item = p[0]
            return _Cursor([
                {"id": sid} for sid, row in self.scheduled.items()
                if row.get("item_id") == item and row.get("fired", 0) == 0
            ])
        # reconcile select (fired=0 AND unclaimed/stuck).
        if "FROM PROACTIVE_SCHEDULED" in u and "WHERE FIRED = 0" in u:
            cutoff = p[0] if p else None
            out = [
                self._row_view(sid, row)
                for sid, row in self.scheduled.items()
                if row.get("fired", 0) == 0
                and (row.get("claimed_at") is None or (cutoff and row["claimed_at"] < cutoff))
            ]
            return _Cursor(out)
        # _load_scheduled_row (by id).
        if "FROM PROACTIVE_SCHEDULED" in u and "WHERE ID = ?" in u:
            row = self.scheduled.get(p[0])
            return _Cursor([self._row_view(p[0], row)] if row else [])
        return _Cursor([])

    @staticmethod
    def _row_view(sid, row):
        return {
            "id": sid, "user_id": row.get("user_id"), "message": row.get("message"),
            "item_id": row.get("item_id", ""), "send_at": row.get("send_at"),
            "apscheduler_job_id": row.get("apscheduler_job_id") or f"reminder-{sid}",
            "fired": row.get("fired", 0), "attempts": row.get("attempts", 0),
        }

    async def commit(self):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


async def _async_cursor(rows):
    return _Cursor(rows)


def _patch_compat(monkeypatch, module, db):
    @contextlib.asynccontextmanager
    async def fake_compat_db():
        yield db

    monkeypatch.setattr(module, "_get_compat_db", fake_compat_db)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _fake_fire(store, deliveries):
    async def fire(**kw):
        deliveries.append(kw["pending_id"])
        row = store.scheduled.get(kw["pending_id"])
        if row:
            row["fired"] = 1  # mirror fire_notification marking fired=1

    return fire


# =========================================================================== #
# Atomic claim — exactly-once delivery (B1 / B3 core)
# =========================================================================== #
@pytest.mark.asyncio
async def test_concurrent_fire_delivers_exactly_once(monkeypatch):
    """Two _fire_reminder calls on the same row (listener + reconcile catch-up
    jobs both registered) → exactly ONE delivery via the atomic claim."""
    store = FakeReminderDB(
        scheduled={"r1": {"fired": 0, "claimed_at": None, "item_id": "", "attempts": 0}},
    )
    _patch_compat(monkeypatch, reminders, store)
    deliveries = []
    monkeypatch.setattr("proactive.engine.fire_notification", _fake_fire(store, deliveries))

    await asyncio.gather(
        reminders._fire_reminder("r1", "u1", "hi"),
        reminders._fire_reminder("r1", "u1", "hi"),
    )

    assert deliveries == ["r1"], "claim must collapse concurrent fires to one delivery"
    assert store.scheduled["r1"]["fired"] == 1


@pytest.mark.asyncio
async def test_fire_skips_row_claimed_by_a_live_delivery(monkeypatch):
    """A row claimed moments ago (delivery in flight elsewhere) is not re-fired."""
    store = FakeReminderDB(
        scheduled={"r1": {"fired": 0, "claimed_at": _iso(datetime.now(timezone.utc)),
                          "item_id": "", "attempts": 0}},
    )
    _patch_compat(monkeypatch, reminders, store)
    deliveries = []
    monkeypatch.setattr("proactive.engine.fire_notification", _fake_fire(store, deliveries))

    await reminders._fire_reminder("r1", "u1", "hi")

    assert deliveries == [], "must not fire a row another path already claimed"


@pytest.mark.asyncio
async def test_fire_reclaims_stuck_claim_then_delivers(monkeypatch):
    """A claim older than the stuck timeout (crash mid-delivery) is reclaimable."""
    old = _iso(datetime.now(timezone.utc) - timedelta(seconds=reminders._STUCK_CLAIM_SECONDS + 60))
    store = FakeReminderDB(
        scheduled={"r1": {"fired": 0, "claimed_at": old, "item_id": "", "attempts": 0}},
    )
    _patch_compat(monkeypatch, reminders, store)
    deliveries = []
    monkeypatch.setattr("proactive.engine.fire_notification", _fake_fire(store, deliveries))

    await reminders._fire_reminder("r1", "u1", "hi")

    assert deliveries == ["r1"], "a stuck claim must recover and deliver"


@pytest.mark.asyncio
async def test_fire_failure_releases_claim_and_reraises(monkeypatch):
    """On delivery failure: attempts bumped, claim released, exception re-raised."""
    store = FakeReminderDB(
        scheduled={"r1": {"fired": 0, "claimed_at": None, "item_id": "", "attempts": 0}},
    )
    _patch_compat(monkeypatch, reminders, store)

    async def boom(**_kw):
        raise RuntimeError("delivery down")

    monkeypatch.setattr("proactive.engine.fire_notification", boom)

    with pytest.raises(RuntimeError):
        await reminders._fire_reminder("r1", "u1", "hi")

    row = store.scheduled["r1"]
    assert row["attempts"] == 1, "attempts recorded"
    assert row["claimed_at"] is None, "claim released so a retry can re-claim"
    assert row["fired"] == 0, "failed delivery stays unfired"


@pytest.mark.asyncio
async def test_fire_reminder_passes_item_id_to_notification(monkeypatch):
    """The claimed item_id must flow into fire_notification so a quiet-hours
    reschedule keeps the reminder link (generation/supersede backstop)."""
    store = FakeReminderDB(
        scheduled={"r1": {"fired": 0, "claimed_at": None, "item_id": "rem1",
                          "schedule_generation": 0, "attempts": 0}},
        reminders={"rem1": {"is_active": 1, "acknowledged": 0, "deleted": 0,
                            "schedule_generation": 0}},
    )
    _patch_compat(monkeypatch, reminders, store)
    captured = {}

    async def fake_fire(**kw):
        captured.update(kw)
        store.scheduled[kw["pending_id"]]["fired"] = 1

    monkeypatch.setattr("proactive.engine.fire_notification", fake_fire)

    await reminders._fire_reminder("r1", "u1", "hi")

    assert captured.get("item_id") == "rem1"


@pytest.mark.asyncio
async def test_fire_aborts_when_reminder_obligation_void(monkeypatch):
    """B2: an in-flight job re-reads state and does NOT deliver a deleted reminder."""
    store = FakeReminderDB(
        scheduled={"r1": {"fired": 0, "claimed_at": None, "item_id": "rem1", "attempts": 0}},
        reminders={"rem1": {"is_active": 1, "acknowledged": 0, "deleted": 1}},
    )
    _patch_compat(monkeypatch, reminders, store)
    deliveries = []
    monkeypatch.setattr("proactive.engine.fire_notification", _fake_fire(store, deliveries))

    await reminders._fire_reminder("r1", "u1", "hi")

    assert deliveries == [], "a deleted reminder must not fire"
    assert store.scheduled["r1"]["fired"] == 1, "void obligation consumed (won't retry)"


# =========================================================================== #
# (a) Missed reminder fires after restart  +  B3 reconcile claim filter
# =========================================================================== #
@pytest.mark.asyncio
async def test_reconcile_reregisters_reminder_with_missing_job(monkeypatch):
    send_at = datetime.now(timezone.utc) + timedelta(hours=2)
    store = FakeReminderDB(
        scheduled={"r1": {"fired": 0, "claimed_at": None, "item_id": "rem1",
                          "user_id": "u1", "message": "ping", "send_at": _iso(send_at),
                          "apscheduler_job_id": "reminder-r1", "attempts": 0}},
    )
    _patch_compat(monkeypatch, engine, store)
    monkeypatch.setattr(scheduler, "job_exists", lambda jid: False)
    registered = []
    monkeypatch.setattr(scheduler, "register_job", lambda **kw: registered.append(kw))

    assert await engine.reconcile_scheduled_jobs() == 1
    assert registered[0]["job_id"] == "reminder-r1"
    assert registered[0]["run_at"] == datetime.fromisoformat(_iso(send_at).replace("Z", "+00:00"))


@pytest.mark.asyncio
async def test_reconcile_skips_reminder_with_live_job(monkeypatch):
    store = FakeReminderDB(
        scheduled={"r1": {"fired": 0, "claimed_at": None, "item_id": "rem1",
                          "user_id": "u1", "message": "ping",
                          "send_at": _iso(datetime.now(timezone.utc) + timedelta(hours=2)),
                          "apscheduler_job_id": "reminder-r1", "attempts": 0}},
    )
    _patch_compat(monkeypatch, engine, store)
    monkeypatch.setattr(scheduler, "job_exists", lambda jid: True)
    registered = []
    monkeypatch.setattr(scheduler, "register_job", lambda **kw: registered.append(kw))

    assert await engine.reconcile_scheduled_jobs() == 0
    assert registered == []


@pytest.mark.asyncio
async def test_reconcile_skips_recently_claimed_row(monkeypatch):
    """B3: a row delivered-but-not-yet-marked (recent claim, fired=0) is NOT
    re-registered → no re-delivery after a crash within the stuck window."""
    store = FakeReminderDB(
        scheduled={"r1": {"fired": 0, "claimed_at": _iso(datetime.now(timezone.utc)),
                          "item_id": "rem1", "user_id": "u1", "message": "ping",
                          "send_at": _iso(datetime.now(timezone.utc) - timedelta(hours=1)),
                          "apscheduler_job_id": "reminder-r1", "attempts": 0}},
    )
    _patch_compat(monkeypatch, engine, store)
    monkeypatch.setattr(scheduler, "job_exists", lambda jid: False)
    registered = []
    monkeypatch.setattr(scheduler, "register_job", lambda **kw: registered.append(kw))

    assert await engine.reconcile_scheduled_jobs() == 0
    assert registered == [], "recently-claimed (in-flight/just-delivered) row must be left alone"


@pytest.mark.asyncio
async def test_reconcile_recovers_stuck_claim(monkeypatch):
    """A genuinely stuck claim (older than the timeout) is re-registered."""
    old = _iso(datetime.now(timezone.utc) - timedelta(seconds=reminders._STUCK_CLAIM_SECONDS + 60))
    store = FakeReminderDB(
        scheduled={"r1": {"fired": 0, "claimed_at": old, "item_id": "rem1",
                          "user_id": "u1", "message": "ping",
                          "send_at": _iso(datetime.now(timezone.utc) - timedelta(hours=1)),
                          "apscheduler_job_id": "reminder-r1", "attempts": 0}},
    )
    _patch_compat(monkeypatch, engine, store)
    monkeypatch.setattr(scheduler, "job_exists", lambda jid: False)
    registered = []
    monkeypatch.setattr(scheduler, "register_job", lambda **kw: registered.append(kw))

    assert await engine.reconcile_scheduled_jobs() == 1
    assert registered[0]["job_id"] == "reminder-r1"


@pytest.mark.asyncio
async def test_reconcile_gives_up_after_max_attempts(monkeypatch):
    store = FakeReminderDB(
        scheduled={"r1": {"fired": 0, "claimed_at": None, "item_id": "rem1",
                          "user_id": "u1", "message": "ping",
                          "send_at": _iso(datetime.now(timezone.utc) - timedelta(hours=2)),
                          "apscheduler_job_id": "reminder-r1",
                          "attempts": engine._MAX_FIRE_ATTEMPTS}},
    )
    _patch_compat(monkeypatch, engine, store)
    monkeypatch.setattr(scheduler, "job_exists", lambda jid: False)
    registered = []
    monkeypatch.setattr(scheduler, "register_job", lambda **kw: registered.append(kw))

    assert await engine.reconcile_scheduled_jobs() == 0
    assert registered == []


@pytest.mark.asyncio
async def test_missed_listener_schedules_immediate_catchup(monkeypatch):
    before = datetime.now(timezone.utc)
    store = FakeReminderDB(
        scheduled={"r1": {"fired": 0, "claimed_at": None, "item_id": "rem1",
                          "user_id": "u1", "message": "ping",
                          "send_at": _iso(before - timedelta(hours=6)),
                          "apscheduler_job_id": "reminder-r1", "attempts": 0}},
    )
    _patch_compat(monkeypatch, engine, store)
    registered = []
    monkeypatch.setattr(scheduler, "register_job", lambda **kw: registered.append(kw))

    await engine._recover_missed_reminder("reminder-r1")

    assert registered, "missed reminder must be re-registered for catch-up"
    assert registered[0]["job_id"] == "reminder-r1"
    assert registered[0]["run_at"] >= before  # fires now, not at the long-past time


@pytest.mark.asyncio
async def test_missed_listener_ignores_non_reminder_jobs(monkeypatch):
    fired = []
    monkeypatch.setattr(engine, "_run_on_engine_loop", lambda coro: (fired.append(1), coro.close()))

    class _Evt:
        job_id = "multica-autopilot-7"

    engine._on_job_missed(_Evt())
    assert fired == []


# =========================================================================== #
# (b) Updated / snoozed reminder fires at the NEW time, not the old (+B2)
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
    row = {"id": "rem1", "user_id": "u1", "title": "take meds", "due_date": None, "due_time": due_time}
    db = FakeCompatDB(select_rows=[])

    rid = await scan.schedule_due_reminder(db, row, now_utc=now)

    assert rid == "rem1"
    assert captured["item_id"] == "rem1"
    assert captured["send_at"] == scan.build_run_at(None, target_local.hour, target_local.minute, now)


@pytest.mark.asyncio
async def test_schedule_due_reminder_idempotent_when_already_scheduled(monkeypatch):
    called = []
    monkeypatch.setattr("proactive.triggers.reminders.schedule_reminder", lambda **kw: called.append(kw))
    db = FakeCompatDB(select_rows=[{"exists": 1}])
    row = {"id": "rem1", "user_id": "u1", "title": "t", "due_date": None, "due_time": "09:00"}

    assert await scan.schedule_due_reminder(db, row, now_utc=datetime.now(timezone.utc)) is None
    assert called == []


@pytest.mark.asyncio
async def test_cancel_reminder_jobs_cancels_every_unfired(monkeypatch):
    db = FakeCompatDB(select_rows=[{"id": "s1"}, {"id": "s2"}])
    _patch_compat(monkeypatch, reminders, db)
    cancelled = []

    async def fake_cancel(sid):
        cancelled.append(sid)
        return True

    monkeypatch.setattr(reminders, "cancel_reminder", fake_cancel)

    assert await reminders.cancel_reminder_jobs("rem1") == 2
    assert cancelled == ["s1", "s2"]


@pytest.mark.asyncio
async def test_update_cancels_job_before_mutating_state(monkeypatch):
    """B2: update cancels the OLD-time job BEFORE the state change is persisted."""
    import routers.reminders as rr

    order = []

    async def fake_cancel(reminder_id):
        order.append(("cancel", reminder_id))

    async def fake_resched(db, reminder_id):
        order.append(("reschedule", reminder_id))

    async def noop(*a, **k):
        return None

    monkeypatch.setattr("proactive.triggers.reminders.cancel_reminder_jobs", fake_cancel)
    monkeypatch.setattr(rr, "_reschedule_reminder_due_safe", fake_resched)
    monkeypatch.setattr(rr, "require_feature_access", noop)
    monkeypatch.setattr(rr.broadcaster, "broadcast", noop)

    reminder = {"id": "rem1", "user_id": "u1", "title": "t", "due_time": "09:00",
                "is_active": 1, "acknowledged": 0, "deleted": 0, "snoozed_until": None}

    class OrderDB:
        def execute(self, sql, params=()):
            norm = " ".join(sql.split())
            if norm.upper().startswith("UPDATE REMINDERS"):
                order.append(("update", "rem1"))
            return _Exec(lambda: self._do(norm))

        async def _do(self, norm):
            if norm.upper().startswith("SELECT"):
                return _Cursor([reminder])
            return _Cursor([])

        async def commit(self):
            pass

    from models import ReminderUpdate

    await rr.update_reminder("rem1", ReminderUpdate(due_time="17:00"),
                             user={"user_id": "u1"}, db=OrderDB())

    assert ("cancel", "rem1") in order and ("update", "rem1") in order
    assert order.index(("cancel", "rem1")) < order.index(("update", "rem1")), \
        "B2: stale job must be cancelled BEFORE the state mutation commits"
    assert order.index(("update", "rem1")) < order.index(("reschedule", "rem1"))


@pytest.mark.asyncio
async def test_snooze_cancels_before_mutate_and_reschedules_at_snooze_time(monkeypatch):
    import routers.reminders as rr
    from models import SnoozeBody

    reminder_row = {"id": "rem1", "user_id": "u1", "title": "stretch", "due_time": "09:00",
                    "is_active": 1, "acknowledged": 0, "deleted": 0, "snoozed_until": None}

    order = []

    async def noop(*a, **k):
        return None

    monkeypatch.setattr(rr, "require_feature_access", noop)
    monkeypatch.setattr(rr, "_create_notification", noop)
    monkeypatch.setattr(rr.broadcaster, "broadcast", noop)

    async def fake_cancel(reminder_id):
        order.append("cancel")

    scheduled = []

    async def fake_schedule_reminder(user_id, message, send_at, item_id=""):
        order.append("schedule")
        scheduled.append({"user_id": user_id, "message": message, "send_at": send_at, "item_id": item_id})
        return "row-x"

    monkeypatch.setattr("proactive.triggers.reminders.cancel_reminder_jobs", fake_cancel)
    monkeypatch.setattr("proactive.triggers.reminders.schedule_reminder", fake_schedule_reminder)

    class SnoozeDB:
        def execute(self, sql, params=()):
            norm = " ".join(sql.split())
            if norm.upper().startswith("UPDATE REMINDERS"):
                order.append("update")
            return _Exec(lambda: self._do(norm))

        async def _do(self, norm):
            if norm.upper().startswith("SELECT"):
                return _Cursor([reminder_row])
            return _Cursor([])

        async def commit(self):
            pass

    before = datetime.now(timezone.utc)
    result = await rr.snooze_reminder("rem1", SnoozeBody(snooze_minutes=15),
                                      user={"user_id": "u1"}, db=SnoozeDB())

    assert result is not None
    assert order.index("cancel") < order.index("update"), "cancel old job before mutating snooze state"
    assert scheduled and scheduled[0]["item_id"] == "rem1"
    # Re-fires ~15 min out (the snooze), not at the original 09:00.
    assert before + timedelta(minutes=14) <= scheduled[0]["send_at"] <= before + timedelta(minutes=16)


# =========================================================================== #
# FIX 1 — generation marker voids a stale claimed job after a reschedule
# =========================================================================== #
@pytest.mark.asyncio
async def test_stale_generation_job_does_not_deliver_after_reschedule(monkeypatch):
    """A job that won its claim for the OLD time must NOT deliver once the
    reminder has been rescheduled/snoozed (generation advanced); the new-time
    job (matching generation) delivers."""
    store = FakeReminderDB(
        scheduled={
            "old": {"fired": 0, "claimed_at": None, "item_id": "rem1",
                    "schedule_generation": 1, "attempts": 0},
            "new": {"fired": 0, "claimed_at": None, "item_id": "rem1",
                    "schedule_generation": 2, "attempts": 0},
        },
        reminders={"rem1": {"is_active": 1, "acknowledged": 0, "deleted": 0,
                            "schedule_generation": 2}},  # rescheduled → gen bumped to 2
    )
    _patch_compat(monkeypatch, reminders, store)
    deliveries = []
    monkeypatch.setattr("proactive.engine.fire_notification", _fake_fire(store, deliveries))

    # Stale old-time job (gen 1) wins the claim but the reminder is now gen 2.
    await reminders._fire_reminder("old", "u1", "old-time msg")
    assert deliveries == [], "stale-generation job must not fire at the old time"
    assert store.scheduled["old"]["fired"] == 1, "stale job self-voided (consumed)"

    # Current new-time job (gen 2) matches → delivers.
    await reminders._fire_reminder("new", "u1", "new-time msg")
    assert deliveries == ["new"], "the new-time job delivers"


# =========================================================================== #
# FIX 3 — startup schema guard for migration 0012
# =========================================================================== #
class _SchemaDB:
    def __init__(self, columns_by_table):
        self.columns_by_table = columns_by_table

    def execute(self, sql, params=()):
        p = list(params) if isinstance(params, (list, tuple)) else [params]
        table = p[0]
        cols = self.columns_by_table.get(table, set())
        return _Exec(lambda: _async_cursor([{"column_name": c} for c in cols]))

    async def commit(self):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


@pytest.mark.asyncio
async def test_verify_proactive_schema_detects_missing_columns(monkeypatch):
    _patch_compat(monkeypatch, engine, _SchemaDB({
        "proactive_scheduled": {"id", "attempts", "last_error"},  # missing claimed_at + generation
        "reminders": {"id"},  # missing schedule_generation
    }))
    missing = await engine.verify_proactive_schema()
    assert "proactive_scheduled.claimed_at" in missing
    assert "proactive_scheduled.schedule_generation" in missing
    assert "reminders.schedule_generation" in missing


@pytest.mark.asyncio
async def test_verify_proactive_schema_ok_when_present(monkeypatch):
    _patch_compat(monkeypatch, engine, _SchemaDB({
        "proactive_scheduled": {"attempts", "last_error", "claimed_at", "schedule_generation"},
        "reminders": {"schedule_generation"},
    }))
    assert await engine.verify_proactive_schema() == []


# =========================================================================== #
# Cross-review follow-ups (Greptile threads)
# =========================================================================== #
@pytest.mark.asyncio
async def test_cancel_reminder_jobs_neutralizes_row_on_cancel_failure(monkeypatch):
    """G4: if APScheduler removal fails, the row is consumed so the orphan job
    can't deliver (its claim finds fired=1)."""
    store = FakeReminderDB(
        scheduled={"s1": {"fired": 0, "claimed_at": None, "item_id": "rem1", "attempts": 0}},
    )
    _patch_compat(monkeypatch, reminders, store)

    async def boom(_sid):
        raise RuntimeError("jobstore down")

    monkeypatch.setattr(reminders, "cancel_reminder", boom)

    n = await reminders.cancel_reminder_jobs("rem1")

    assert n == 1
    assert store.scheduled["s1"]["fired"] == 1, "orphan row neutralized on cancel failure"


@pytest.mark.asyncio
async def test_error_listener_schedules_bounded_retry(monkeypatch):
    """G3: an errored fire is retried in-process (with backoff), not left until restart."""
    store = FakeReminderDB(
        scheduled={"r1": {"fired": 0, "claimed_at": None, "item_id": "", "attempts": 1,
                          "user_id": "u1", "message": "hi",
                          "send_at": _iso(datetime.now(timezone.utc) - timedelta(hours=1)),
                          "apscheduler_job_id": "reminder-r1"}},
    )
    _patch_compat(monkeypatch, engine, store)
    registered = []
    monkeypatch.setattr(scheduler, "register_job", lambda **kw: registered.append(kw))

    await engine._retry_errored_reminder("reminder-r1")

    assert registered and registered[0]["job_id"] == "reminder-r1"
    assert registered[0]["run_at"] > datetime.now(timezone.utc), "retry is backed off into the future"


@pytest.mark.asyncio
async def test_error_listener_gives_up_at_max_attempts(monkeypatch):
    store = FakeReminderDB(
        scheduled={"r1": {"fired": 0, "claimed_at": None, "item_id": "",
                          "attempts": engine._MAX_FIRE_ATTEMPTS,
                          "user_id": "u1", "message": "hi",
                          "send_at": _iso(datetime.now(timezone.utc) - timedelta(hours=1)),
                          "apscheduler_job_id": "reminder-r1"}},
    )
    _patch_compat(monkeypatch, engine, store)
    registered = []
    monkeypatch.setattr(scheduler, "register_job", lambda **kw: registered.append(kw))

    await engine._retry_errored_reminder("reminder-r1")

    assert registered == [], "no retry past the attempt cap"


@pytest.mark.asyncio
async def test_editing_snoozed_reminder_reschedules_at_snooze_time(monkeypatch):
    """G2: editing a snoozed reminder reschedules at snoozed_until (not dropped)."""
    import routers.reminders as rr

    future = datetime.now(timezone.utc) + timedelta(minutes=30)
    snoozed_iso = future.strftime("%Y-%m-%dT%H:%M:%SZ")
    reminder = {"id": "rem1", "user_id": "u1", "title": "t", "due_time": "09:00",
                "is_active": 1, "acknowledged": 0, "deleted": 0, "snoozed_until": snoozed_iso}
    scheduled = []

    async def fake_schedule_reminder(user_id, message, send_at, item_id=""):
        scheduled.append({"send_at": send_at, "item_id": item_id})
        return "row-x"

    monkeypatch.setattr("proactive.triggers.reminders.schedule_reminder", fake_schedule_reminder)

    await rr._reschedule_reminder_due_safe(FakeCompatDB(select_rows=[reminder]), "rem1")

    assert scheduled and scheduled[0]["item_id"] == "rem1"
    assert scheduled[0]["send_at"] == datetime.fromisoformat(snoozed_iso.replace("Z", "+00:00")), \
        "snoozed reminder must keep firing at snoozed_until after an edit"


@pytest.mark.asyncio
async def test_snooze_rejects_cross_user(monkeypatch):
    """G1: a user who can VIEW a family reminder cannot snooze another's reminder."""
    import routers.reminders as rr
    from fastapi import HTTPException
    from models import SnoozeBody

    async def noop(*a, **k):
        return None

    monkeypatch.setattr(rr, "require_feature_access", noop)
    reminder = {"id": "rem1", "user_id": "owner", "title": "t", "due_time": "09:00",
                "is_active": 1, "acknowledged": 0, "deleted": 0, "snoozed_until": None}

    with pytest.raises(HTTPException) as ei:
        await rr.snooze_reminder("rem1", SnoozeBody(snooze_minutes=10),
                                 user={"user_id": "intruder"}, db=FakeCompatDB(select_rows=[reminder]))
    assert ei.value.status_code == 403


@pytest.mark.asyncio
async def test_acknowledge_rejects_cross_user(monkeypatch):
    import routers.reminders as rr
    from fastapi import HTTPException

    async def noop(*a, **k):
        return None

    monkeypatch.setattr(rr, "require_feature_access", noop)
    reminder = {"id": "rem1", "user_id": "owner", "title": "t",
                "is_active": 1, "acknowledged": 0, "deleted": 0}

    with pytest.raises(HTTPException) as ei:
        await rr.acknowledge_reminder("rem1", user={"user_id": "intruder"},
                                      db=FakeCompatDB(select_rows=[reminder]))
    assert ei.value.status_code == 403


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
    engine.register_trigger(_T("reminder_scan"))
    engine.register_trigger(_T("morning_checkin"))

    types = [t.trigger_type for t in trigger_registry]
    assert types.count("reminder_scan") == 1
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

    engine.start_proactive_engine()  # lifespan restart / reload

    assert engine._slow_loop_task is slow1
    assert engine._cleanup_loop_task is cleanup1

    engine.stop_proactive_engine()
    assert engine._slow_loop_task is None
    assert engine._cleanup_loop_task is None
    assert engine._listeners_installed is False
    await asyncio.gather(slow1, cleanup1, return_exceptions=True)
