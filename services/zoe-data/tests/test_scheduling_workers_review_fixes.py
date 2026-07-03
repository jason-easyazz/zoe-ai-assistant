"""Regression tests for the scheduling/background-worker review fixes:

  F1: engine.fire_notification's in-app fallback must scope the WS broadcast to
      the target user (broadcast('all', ..., user_id=user_id)), not fan it out
      to every connected client.
  F4: routers/push.send_push_to_user must release the pooled DB connection
      BEFORE making any outbound webpush() call, and must run webpush() off the
      event loop (via an executor) rather than blocking it directly.
  F5: routers/proactive.trigger_morning_brief must compute `today` from the
      user's local timezone (matching morning_checkin.py's _ZOE_TZ), not UTC.
  F6: engine.fire_notification's quiet-hours path must not mark the scheduled
      row fired=1 until the reschedule has actually succeeded.
"""
from __future__ import annotations

import contextlib
from datetime import datetime, timedelta, timezone

import pytest

import proactive.engine as engine
import routers.push as push_router


# --------------------------------------------------------------------------- #
# Fakes shared by the fire_notification tests
# --------------------------------------------------------------------------- #
class _Cursor:
    def __init__(self, rows=None):
        self._rows = rows or []

    async def fetchone(self):
        return self._rows[0] if self._rows else None

    async def fetchall(self):
        return self._rows

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
    """Minimal fake compat-db that just records every execute() call."""

    def __init__(self, ops):
        self.ops = ops

    def execute(self, sql, params=()):
        return _Exec(lambda: self._do(sql, params))

    async def _do(self, sql, params):
        self.ops.append((" ".join(sql.split()).upper(), params))
        return _Cursor([])

    async def commit(self):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


def _patch_compat_db(monkeypatch, ops):
    @contextlib.asynccontextmanager
    async def fake_compat_db():
        yield RecordingDB(ops)

    monkeypatch.setattr(engine, "_get_compat_db", fake_compat_db)


# --------------------------------------------------------------------------- #
# F1 — in-app fallback must scope the broadcast to the target user
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_in_app_fallback_broadcast_scoped_to_user(monkeypatch):
    ops = []
    _patch_compat_db(monkeypatch, ops)
    monkeypatch.setattr(engine, "create_pending", _fake_create_pending)
    monkeypatch.setattr(engine, "_send_push", _fake_send_push_zero)

    calls = []

    async def fake_broadcast(channel, event, data, user_id=None):
        calls.append({"channel": channel, "event": event, "user_id": user_id})
        return 0

    import push as push_module
    monkeypatch.setattr(push_module.broadcaster, "broadcast", fake_broadcast)

    await engine.fire_notification(
        user_id="user-a",
        message="take medication X",
        trigger_type="reminder",
        context={"force_send": True},
    )

    assert calls, "in-app fallback must broadcast when there are no WS subscribers"
    assert calls[0]["user_id"] == "user-a", (
        "reminder broadcast must be scoped to the target user, "
        "not fanned out to every connected client"
    )


async def _fake_create_pending(**_kw):
    return "pending-1"


async def _fake_send_push_zero(**_kw):
    return 0


# --------------------------------------------------------------------------- #
# F6 — quiet-hours: fired=1 only after a successful reschedule
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_quiet_hours_does_not_mark_fired_on_reschedule_failure(monkeypatch):
    ops = []
    _patch_compat_db(monkeypatch, ops)
    monkeypatch.setattr(engine, "_is_in_quiet_hours", lambda: True)

    async def boom(**_kw):
        raise RuntimeError("db down")

    import proactive.triggers.reminders as reminders
    monkeypatch.setattr(reminders, "schedule_reminder", boom)

    await engine.fire_notification(
        user_id="user-a",
        message="ping",
        trigger_type="reminder",
        pending_id="sched-1",
    )

    fired_updates = [op for op in ops if op[0].startswith("UPDATE PROACTIVE_SCHEDULED SET FIRED")]
    assert not fired_updates, (
        "a failed quiet-hours reschedule must NOT mark the row fired — "
        "doing so permanently loses the notification"
    )


@pytest.mark.asyncio
async def test_quiet_hours_marks_fired_after_successful_reschedule(monkeypatch):
    ops = []
    _patch_compat_db(monkeypatch, ops)
    monkeypatch.setattr(engine, "_is_in_quiet_hours", lambda: True)

    async def ok(**_kw):
        return "new-row-id"

    import proactive.triggers.reminders as reminders
    monkeypatch.setattr(reminders, "schedule_reminder", ok)

    await engine.fire_notification(
        user_id="user-a",
        message="ping",
        trigger_type="reminder",
        pending_id="sched-1",
    )

    fired_updates = [op for op in ops if op[0].startswith("UPDATE PROACTIVE_SCHEDULED SET FIRED")]
    assert fired_updates and fired_updates[0][1] == ("sched-1",)


# --------------------------------------------------------------------------- #
# F4 — send_push_to_user releases the DB connection before webpush() runs,
# and dispatches webpush() through an executor (never directly on the loop).
# --------------------------------------------------------------------------- #
class _SubCursor:
    def __init__(self, rows):
        self._rows = rows

    async def fetchall(self):
        return self._rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


class _PushFakeDB:
    """Fake compat-db for push tests: tracks acquire/release ordering."""

    def __init__(self, events, rows, deleted):
        self._events = events
        self._rows = rows
        self._deleted = deleted

    def execute(self, sql, params=()):
        u = " ".join(sql.split()).upper()
        if u.startswith("SELECT ENDPOINT"):
            return _SubCursor(self._rows)
        if u.startswith("DELETE FROM PUSH_SUBSCRIPTIONS"):
            self._deleted.append(params[0])
            return _SubCursor([])
        return _SubCursor([])

    async def commit(self):
        pass


async def _fake_get_db(events, rows, deleted):
    events.append("acquire")
    try:
        yield _PushFakeDB(events, rows, deleted)
    finally:
        events.append("release")


@pytest.mark.asyncio
async def test_send_push_releases_db_before_webpush_and_uses_executor(monkeypatch):
    events = []
    rows = [{"endpoint": "https://push.example/ep1", "keys_p256dh": "p", "keys_auth": "a"}]
    deleted = []

    monkeypatch.setattr(push_router, "get_db", lambda: _fake_get_db(events, rows, deleted))
    monkeypatch.setattr(push_router, "_get_vapid_keys", lambda: {"public_key": "pub", "private_key": "priv"})

    executor_calls = []

    class _FakeLoop:
        async def run_in_executor(self, _pool, fn, *args):
            # webpush() must run through here, AFTER the DB connection has
            # already been released (proves no pooled connection is held
            # across the outbound HTTP call).
            executor_calls.append(1)
            assert events == ["acquire", "release"], (
                "webpush() must not run while the pooled DB connection is "
                "still checked out"
            )
            return fn(*args)

    monkeypatch.setattr(push_router.asyncio, "get_running_loop", lambda: _FakeLoop())

    def fake_webpush(**_kw):
        return "ok"

    import sys
    import types
    fake_pywebpush = types.ModuleType("pywebpush")
    fake_pywebpush.webpush = fake_webpush
    fake_pywebpush.WebPushException = Exception
    monkeypatch.setitem(sys.modules, "pywebpush", fake_pywebpush)

    sent = await push_router.send_push_to_user(user_id="user-a", message="hi")

    assert sent == 1
    assert executor_calls, "webpush() must be dispatched via loop.run_in_executor"
    assert events == ["acquire", "release"]
    assert not deleted


# --------------------------------------------------------------------------- #
# F5 — trigger_morning_brief computes `today`/day-string from local tz, not UTC
# --------------------------------------------------------------------------- #
def test_trigger_morning_brief_uses_local_tz_not_utc():
    import inspect
    import routers.proactive as proactive_router

    src = inspect.getsource(proactive_router.trigger_morning_brief)
    assert "datetime.now(timezone.utc).date()" not in src, (
        "trigger-morning must not derive `today` from UTC — during the actual "
        "Perth morning (00:00-08:00 local) that queries yesterday's calendar"
    )
    assert "_ZOE_TZ" in src, "must reuse morning_checkin.py's local-tz resolution"
