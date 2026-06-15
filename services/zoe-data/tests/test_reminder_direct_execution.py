import pytest

from intent_router import Intent, execute_intent
from models import ReminderCreate
from reminder_service import create_reminder_record


class _Cursor:
    def __init__(self, row=None):
        self._row = row

    async def fetchone(self):
        return self._row


class _FakeDB:
    def __init__(self):
        self.calls = []
        self.committed = False
        self.row = {
            "id": "rem-1",
            "user_id": "family-admin",
            "title": "check the oven",
            "due_date": "2026-06-15",
            "due_time": "23:00",
            "is_active": 1,
            "acknowledged": 0,
            "deleted": 0,
        }

    async def execute(self, sql, params=()):
        self.calls.append((sql, tuple(params)))
        if sql.strip().upper().startswith("SELECT"):
            return _Cursor(self.row)
        return _Cursor()

    async def commit(self):
        self.committed = True


@pytest.mark.asyncio
async def test_create_reminder_record_preserves_policy_write_notification_and_broadcast(monkeypatch):
    db = _FakeDB()
    policy_calls = []
    broadcasts = []

    async def fake_require_feature_access(db_arg, user, *, feature, action, **kwargs):
        assert "surface" not in kwargs
        policy_calls.append((db_arg, user, feature, action))

    async def fake_broadcast(channel, event, payload, *, user_id=None):
        broadcasts.append((channel, event, payload, user_id))

    monkeypatch.setattr("reminder_service.require_feature_access", fake_require_feature_access)
    monkeypatch.setattr("reminder_service.broadcaster.broadcast", fake_broadcast)

    reminder = await create_reminder_record(
        ReminderCreate(title="check the oven", due_date="2026-06-15", due_time="23:00"),
        user={"user_id": "family-admin", "role": "admin"},
        db=db,
    )

    assert policy_calls == [(db, {"user_id": "family-admin", "role": "admin"}, "reminders", "create")]
    assert db.committed is True
    assert any("INSERT INTO reminders" in sql for sql, _ in db.calls)
    assert any("INSERT INTO notifications" in sql for sql, _ in db.calls)
    assert reminder["is_active"] is True
    assert reminder["acknowledged"] is False
    assert reminder["deleted"] is False
    assert broadcasts == [("reminders", "reminder_created", reminder, "family-admin")]


@pytest.mark.asyncio
async def test_execute_reminder_create_uses_direct_path_before_mcporter(monkeypatch):
    calls = []

    async def fake_direct(intent, user_id):
        calls.append((intent.name, dict(intent.slots), user_id))
        return "Reminder set: check the oven."

    async def fail_mcporter(_cmd):
        raise AssertionError("reminder direct path should avoid mcporter")

    monkeypatch.setattr("intent_router._execute_reminder_create_direct", fake_direct)
    monkeypatch.setattr("intent_router._run_mcporter", fail_mcporter)

    result = await execute_intent(
        Intent("reminder_create", {"title": "check the oven", "date": "2026-06-15", "time": "23:00"}),
        "family-admin",
    )

    assert result == "Reminder set: check the oven."
    assert calls == [("reminder_create", {"title": "check the oven", "date": "2026-06-15", "time": "23:00"}, "family-admin")]
