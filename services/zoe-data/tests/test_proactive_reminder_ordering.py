"""Regression tests for P2 reminder create/cancel ordering atomicity.

Old behaviour:
- schedule_reminder INSERTed the DB row then register_job()'d — a registration
  failure left a stale "scheduled" row that could never fire.
- cancel_reminder DELETEd the row BEFORE cancel_job(), and treated cancel_job()
  == False as "job absent" — but the old cancel_job returned False for ANY
  remove_job failure (transient jobstore error too), so a real cancel failure
  silently deleted the row and orphaned a live job.

Fixed behaviour:
- schedule_reminder compensates by deleting the orphan row if register_job fails.
- cancel_job returns a tri-state (REMOVED / ABSENT) and RAISES on a real failure.
- cancel_reminder cancels the job FIRST and deletes the row only on a confirmed
  REMOVED/ABSENT outcome; a real failure propagates and the row is kept.
"""
import contextlib
from datetime import datetime, timedelta, timezone

import pytest
from apscheduler.jobstores.base import JobLookupError

import proactive.scheduler as scheduler
import proactive.triggers.reminders as reminders
from proactive.scheduler import CancelResult

pytestmark = pytest.mark.ci_safe


# --------------------------------------------------------------------------- #
# cancel_job tri-state (scheduler.py)
# --------------------------------------------------------------------------- #
class _FakeScheduler:
    def __init__(self, exc=None):
        self._exc = exc
        self.removed = []

    def remove_job(self, job_id):
        if self._exc is not None:
            raise self._exc
        self.removed.append(job_id)


def test_cancel_job_removed(monkeypatch):
    fs = _FakeScheduler()
    monkeypatch.setattr(scheduler, "get_scheduler", lambda: fs)
    assert scheduler.cancel_job("j1") is CancelResult.REMOVED
    assert fs.removed == ["j1"]


def test_cancel_job_absent_on_joblookuperror(monkeypatch):
    fs = _FakeScheduler(exc=JobLookupError("j1"))
    monkeypatch.setattr(scheduler, "get_scheduler", lambda: fs)
    assert scheduler.cancel_job("j1") is CancelResult.ABSENT


def test_cancel_job_reraises_real_failure(monkeypatch):
    fs = _FakeScheduler(exc=RuntimeError("jobstore down"))
    monkeypatch.setattr(scheduler, "get_scheduler", lambda: fs)
    with pytest.raises(RuntimeError):
        scheduler.cancel_job("j1")


# --------------------------------------------------------------------------- #
# Fake compat DB for reminders
# --------------------------------------------------------------------------- #
class _Cursor:
    def __init__(self, rows):
        self._rows = rows

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


class RecordingDB:
    def __init__(self, ops, rows):
        self.ops = ops
        self.rows = rows

    def execute(self, sql, params=()):
        return _Exec(lambda: self._do(sql, params))

    async def _do(self, sql, params):
        u = " ".join(sql.split()).upper()
        if u.startswith("INSERT INTO PROACTIVE_SCHEDULED"):
            self.ops.append(("insert", params[0]))
            self.rows[params[0]] = {"apscheduler_job_id": params[4]}
            return _Cursor([])
        if u.startswith("SELECT APSCHEDULER_JOB_ID"):
            r = self.rows.get(params[0])
            return _Cursor([r] if r else [])
        if u.startswith("DELETE FROM PROACTIVE_SCHEDULED"):
            self.ops.append(("delete", params[0]))
            self.rows.pop(params[0], None)
            return _Cursor([])
        return _Cursor([])

    async def commit(self):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


def _patch(monkeypatch, ops, rows):
    @contextlib.asynccontextmanager
    async def fake_compat_db():
        yield RecordingDB(ops, rows)

    monkeypatch.setattr(reminders, "_get_compat_db", fake_compat_db)


# --------------------------------------------------------------------------- #
# schedule_reminder
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_schedule_happy_path_registers_and_keeps_row(monkeypatch):
    ops, rows = [], {}
    _patch(monkeypatch, ops, rows)
    registered = []
    monkeypatch.setattr(reminders, "register_job", lambda **kw: registered.append(kw["job_id"]))

    send_at = datetime.now(timezone.utc) + timedelta(hours=1)
    row_id = await reminders.schedule_reminder("u1", "ping", send_at)

    assert row_id in rows, "row persists after successful registration"
    assert registered, "register_job was called"
    assert not any(op[0] == "delete" for op in ops), "no compensating delete on success"


@pytest.mark.asyncio
async def test_schedule_compensates_on_register_failure(monkeypatch):
    ops, rows = [], {}
    _patch(monkeypatch, ops, rows)

    def boom(**_kw):
        raise RuntimeError("scheduler down")

    monkeypatch.setattr(reminders, "register_job", boom)

    send_at = datetime.now(timezone.utc) + timedelta(hours=1)
    with pytest.raises(RuntimeError):
        await reminders.schedule_reminder("u1", "ping", send_at)

    row_id = ops[0][1]  # from the recorded INSERT
    assert ("delete", row_id) in ops, "orphan row must be compensated/deleted"
    assert row_id not in rows


# --------------------------------------------------------------------------- #
# cancel_reminder
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_cancel_cancels_job_before_deleting_row(monkeypatch):
    ops = []
    rows = {"sid": {"apscheduler_job_id": "reminder-sid"}}
    _patch(monkeypatch, ops, rows)

    def fake_cancel(job_id):
        ops.append(("cancel", job_id))
        return CancelResult.REMOVED

    monkeypatch.setattr(reminders, "cancel_job", fake_cancel)

    result = await reminders.cancel_reminder("sid")

    assert result is True
    kinds = [op[0] for op in ops]
    assert kinds.index("cancel") < kinds.index("delete"), "cancel must precede delete"
    assert "sid" not in rows


@pytest.mark.asyncio
async def test_cancel_absent_job_still_removes_stale_row(monkeypatch):
    ops = []
    rows = {"sid": {"apscheduler_job_id": "reminder-sid"}}
    _patch(monkeypatch, ops, rows)

    def fake_cancel(job_id):
        ops.append(("cancel", job_id))
        return CancelResult.ABSENT

    monkeypatch.setattr(reminders, "cancel_job", fake_cancel)

    result = await reminders.cancel_reminder("sid")

    assert result is False, "no live job was removed"
    assert "sid" not in rows, "stale row removed even when job already absent"
    assert [op[0] for op in ops] == ["cancel", "delete"]


@pytest.mark.asyncio
async def test_cancel_real_failure_keeps_row_and_propagates(monkeypatch):
    ops = []
    rows = {"sid": {"apscheduler_job_id": "reminder-sid"}}
    _patch(monkeypatch, ops, rows)

    def boom(job_id):
        ops.append(("cancel", job_id))
        raise RuntimeError("transient jobstore error")

    monkeypatch.setattr(reminders, "cancel_job", boom)

    with pytest.raises(RuntimeError):
        await reminders.cancel_reminder("sid")

    assert "sid" in rows, "row must be KEPT on a real cancel failure (no orphan)"
    assert not any(op[0] == "delete" for op in ops), "row must not be deleted"


@pytest.mark.asyncio
async def test_cancel_unknown_id_returns_false(monkeypatch):
    ops = []
    _patch(monkeypatch, ops, {})
    monkeypatch.setattr(reminders, "cancel_job", lambda job_id: ops.append(("cancel", job_id)))

    assert await reminders.cancel_reminder("missing") is False
    assert ops == [], "no cancel/delete attempted for unknown id"
