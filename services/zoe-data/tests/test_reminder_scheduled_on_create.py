"""A reminder is scheduled when it is CREATED, not only when the slow loop next sees it.

Found 2026-10-12: ``create_reminder_record`` only inserted the row. The slow loop that
schedules reminders (``ReminderScanTrigger``) does nothing inside quiet hours
(22:00-07:00), and at 07:00 an explicit time earlier that night is "past due" and skipped
-- so "remind me at 22:30" said at 22:15 was never scheduled and never fired. The FIRE is
still deferred out of quiet hours by ``fire_notification`` (by design, unchanged).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import proactive.triggers.reminder_scan as scan
from models import ReminderCreate
from reminder_service import create_reminder_record

pytestmark = pytest.mark.ci_safe


class _Cur:
    """db_compat shape: awaitable AND an async context manager."""

    def __init__(self, row=None):
        self._row = row

    async def fetchone(self):
        return self._row

    async def fetchall(self):
        return [] if self._row is None else [self._row]

    def __await__(self):
        async def _self():
            return self
        return _self().__await__()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False


class _Db:
    def __init__(self, scheduled: list):
        self.scheduled = scheduled          # the fake proactive_scheduled table (unfired rows)
        self.stored: dict = {}

    def execute(self, sql, params=()):
        if "INSERT INTO reminders" in sql:
            self.stored = {"id": params[0], "user_id": params[1], "title": params[2],
                           "due_date": params[7], "due_time": params[8], "recurring_pattern": params[9],
                           "is_active": 1, "acknowledged": 0, "deleted": 0}
        if "FROM proactive_scheduled" in sql:
            return _Cur((1,) if self.scheduled else None)
        if sql.strip().upper().startswith("SELECT"):
            return _Cur(self.stored)
        return _Cur()

    async def commit(self):
        return None


@pytest.fixture
def wired(monkeypatch):
    scheduled: list[dict] = []

    async def _allow(*_a, **_k):
        return None

    async def fake_schedule_reminder(*, user_id, message, send_at, item_id=""):
        scheduled.append({"user_id": user_id, "send_at": send_at, "item_id": item_id})
        return "row"

    monkeypatch.setattr("reminder_service.require_feature_access", _allow)
    monkeypatch.setattr("reminder_service.broadcaster.broadcast", _allow)
    monkeypatch.setattr("proactive.triggers.reminders.schedule_reminder", fake_schedule_reminder)
    return scheduled


def _in_an_hour():
    when = scan.zoe_now() + timedelta(hours=1)
    return when.date().isoformat(), when.strftime("%H:%M")


@pytest.mark.asyncio
async def test_create_schedules_the_reminder_immediately(wired):
    due_date, due_time = _in_an_hour()
    db = _Db(wired)
    reminder = await create_reminder_record(
        ReminderCreate(title="check the oven", due_date=due_date, due_time=due_time),
        user={"user_id": "jason", "role": "admin"}, db=db)
    assert [s["item_id"] for s in wired] == [reminder["id"]], "created but never scheduled"
    assert wired[0]["send_at"] > datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_the_slow_loop_scan_after_create_does_not_schedule_a_second_job(wired):
    due_date, due_time = _in_an_hour()
    db = _Db(wired)
    reminder = await create_reminder_record(
        ReminderCreate(title="check the oven", due_date=due_date, due_time=due_time),
        user={"user_id": "jason", "role": "admin"}, db=db)
    assert len(wired) == 1
    assert await scan.schedule_due_reminder(db, db.stored) is None     # idempotent: an unfired job exists
    assert len(wired) == 1 and wired[0]["item_id"] == reminder["id"]


@pytest.mark.asyncio
async def test_a_scheduling_failure_never_fails_the_create(wired, monkeypatch):
    async def boom(*_a, **_k):
        raise RuntimeError("scheduler down")

    monkeypatch.setattr("proactive.triggers.reminders.schedule_reminder", boom)
    due_date, due_time = _in_an_hour()
    reminder = await create_reminder_record(
        ReminderCreate(title="check the oven", due_date=due_date, due_time=due_time),
        user={"user_id": "jason", "role": "admin"}, db=_Db(wired))
    assert reminder["title"] == "check the oven"      # the row is still created; the slow loop retries later
