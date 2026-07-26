import pytest

pytestmark = pytest.mark.ci_safe  # slim-dep write-path guards -> GitHub blocking lane (#960/#993 suites)
from fastapi import HTTPException

from intent_router import Intent, _load_direct_execution_user, execute_intent
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


@pytest.mark.asyncio
async def test_load_direct_execution_user_defaults_null_role_to_user():
    class FakeUserDB:
        async def execute(self, _sql, _params):
            return _Cursor({"id": "zoe-user", "role": None, "name": "Zoe User"})

    user = await _load_direct_execution_user(FakeUserDB(), "zoe-user")

    assert user == {"user_id": "zoe-user", "role": "user", "username": "Zoe User"}


@pytest.mark.asyncio
async def test_execute_reminder_create_falls_back_to_mcporter_when_direct_unavailable(monkeypatch):
    calls = []

    async def fake_direct(intent, user_id):
        calls.append(("direct", intent.name, user_id))
        return None

    def fake_build_command(intent, user_id):
        calls.append(("build", intent.name, user_id))
        return "mcporter-safe call zoe-data.reminder_create"

    async def fake_mcporter(cmd):
        calls.append(("mcporter", cmd))
        return "{}"

    monkeypatch.setattr("intent_router._execute_reminder_create_direct", fake_direct)
    monkeypatch.setattr("intent_router._build_command", fake_build_command)
    monkeypatch.setattr("intent_router._run_mcporter", fake_mcporter)

    result = await execute_intent(
        Intent("reminder_create", {"title": "check the oven", "date": "2026-06-15", "time": "23:00"}),
        "family-admin",
    )

    assert result == "Reminder set: check the oven for 2026-06-15 at 23:00."
    assert calls == [
        ("direct", "reminder_create", "family-admin"),
        ("build", "reminder_create", "family-admin"),
        ("mcporter", "mcporter-safe call zoe-data.reminder_create"),
    ]


class _ListCursor:
    def __init__(self, rows):
        self._rows = rows

    async def fetchall(self):
        return self._rows


def _fake_reminder_db_ctx(rows):
    """Async context manager factory mimicking database.get_db_ctx with fixed rows."""
    import contextlib

    class _ListDB:
        async def execute(self, _sql, _params=()):
            return _ListCursor(rows)

    @contextlib.asynccontextmanager
    async def ctx():
        yield _ListDB()

    return ctx


@pytest.mark.asyncio
async def test_execute_reminder_list_empty_returns_no_reminders_message(monkeypatch):
    """Empty reminders must produce the 'No reminders set.' message (ok:true at
    the intent-dispatch endpoint), not None (which maps to ok:false)."""

    async def fail_mcporter(_cmd):
        raise AssertionError("reminder_list direct path should avoid mcporter")

    monkeypatch.setattr("database.get_db_ctx", _fake_reminder_db_ctx([]))
    monkeypatch.setattr("intent_router._run_mcporter", fail_mcporter)

    result = await execute_intent(Intent("reminder_list", {}), "family-admin")

    assert result == "No reminders set."


@pytest.mark.asyncio
async def test_execute_reminder_list_nonempty_formats_reminders(monkeypatch):
    rows = [
        {"id": "rem-1", "title": "check the oven", "due_date": "2026-06-15",
         "due_time": "23:00", "priority": "normal", "category": "general"},
        {"id": "rem-2", "title": "water plants", "due_date": None,
         "due_time": None, "priority": "normal", "category": "general"},
    ]

    async def fail_mcporter(_cmd):
        raise AssertionError("reminder_list direct path should avoid mcporter")

    monkeypatch.setattr("database.get_db_ctx", _fake_reminder_db_ctx(rows))
    monkeypatch.setattr("intent_router._run_mcporter", fail_mcporter)

    result = await execute_intent(Intent("reminder_list", {}), "family-admin")

    assert result == (
        "Your reminders:\n"
        "  - check the oven (due: 2026-06-15)\n"
        "  - water plants (due: TBD)"
    )


@pytest.mark.asyncio
async def test_execute_reminder_list_real_failure_still_returns_none(monkeypatch):
    """DB down AND mcporter down is a real failure: execute_intent returns None,
    which the intent-dispatch endpoint maps to ok:false."""
    import contextlib

    @contextlib.asynccontextmanager
    async def broken_ctx():
        raise RuntimeError("db unavailable")
        yield  # pragma: no cover

    async def fake_mcporter(_cmd):
        return None

    monkeypatch.setattr("database.get_db_ctx", broken_ctx)
    monkeypatch.setattr("intent_router._run_mcporter", fake_mcporter)

    result = await execute_intent(Intent("reminder_list", {}), "family-admin")

    assert result is None


@pytest.mark.asyncio
async def test_execute_reminder_list_falls_back_to_mcporter_when_direct_unavailable(monkeypatch):
    import contextlib

    @contextlib.asynccontextmanager
    async def broken_ctx():
        raise RuntimeError("db unavailable")
        yield  # pragma: no cover

    async def fake_mcporter(_cmd):
        return '{"reminders": [{"title": "check the oven", "due_date": "2026-06-15"}]}'

    monkeypatch.setattr("database.get_db_ctx", broken_ctx)
    monkeypatch.setattr("intent_router._run_mcporter", fake_mcporter)

    result = await execute_intent(Intent("reminder_list", {}), "family-admin")

    assert result == "Your reminders:\n  - check the oven (due: 2026-06-15)"


@pytest.mark.asyncio
async def test_execute_reminder_create_policy_denial_does_not_fall_back_to_mcporter(monkeypatch):
    async def fake_direct(_intent, _user_id):
        raise HTTPException(status_code=403, detail="Authentication required for this action.")

    async def fail_mcporter(_cmd):
        raise AssertionError("policy denial must not fall back to mcporter")

    monkeypatch.setattr("intent_router._execute_reminder_create_direct", fake_direct)
    monkeypatch.setattr("intent_router._run_mcporter", fail_mcporter)

    # A signed-in caller: guests are short-circuited by the chat sign-in gate
    # before reaching the direct executor (2026-07-13) — this test is about a
    # POLICY denial from the executor itself never falling through to mcporter.
    with pytest.raises(HTTPException) as exc_info:
        await execute_intent(Intent("reminder_create", {"title": "check the oven"}), "jason")

    assert exc_info.value.status_code == 403


# ---------------------------------------------------------------------------
# MCP tool path: reminder_create must route through reminder_service (#6098)
# ---------------------------------------------------------------------------

def _mcp_reminder_service_harness(monkeypatch):
    """Wire fakes around mcp_server's reminder_create and return the probes."""
    import mcp_server

    db = _FakeDB()
    service_calls = []
    notify_calls = []

    async def fake_create_reminder_record(payload, *, user, db):
        service_calls.append((payload, user, db))
        return {
            "id": "rem-1",
            "title": payload.title,
            "due_date": payload.due_date,
            "due_time": payload.due_time,
            "priority": payload.priority,
        }

    async def fake_notify_ui(channel, event_type, data):
        notify_calls.append((channel, event_type, data))

    # The handler imports create_reminder_record at call time, so patching the
    # reminder_service attribute intercepts it.
    monkeypatch.setattr("reminder_service.create_reminder_record", fake_create_reminder_record)
    monkeypatch.setattr(mcp_server, "_notify_ui", fake_notify_ui)
    return mcp_server, db, service_calls, notify_calls


_MCP_CREATE_ARGS = {
    "_user_id": "family-admin",
    "title": "check the oven",
    "due_date": "2026-06-15",
    "due_time": "23:00",
}


@pytest.mark.asyncio
async def test_mcp_reminder_create_routes_through_reminder_service(monkeypatch):
    mcp_server, db, service_calls, notify_calls = _mcp_reminder_service_harness(monkeypatch)

    result = await mcp_server._execute_tool(db, "reminder_create", dict(_MCP_CREATE_ARGS))

    # Exactly one service call, carrying the faithfully-mapped MCP args plus
    # the defaults the raw INSERT used to hard-code.
    assert len(service_calls) == 1
    payload, user, service_db = service_calls[0]
    assert service_db is db
    assert payload.title == "check the oven"
    assert payload.due_date == "2026-06-15"
    assert payload.due_time == "23:00"
    assert payload.priority == "normal"
    assert payload.category == "general"
    assert payload.visibility == "personal"
    assert user["user_id"] == "family-admin"
    assert "role" in user  # actor mapping satisfies require_feature_access

    # No raw INSERT bypass left behind.
    assert not any("INSERT INTO reminders" in sql for sql, _ in db.calls)

    # External result shape is unchanged.
    assert result == {
        "id": "rem-1",
        "title": "check the oven",
        "due_date": "2026-06-15",
        "due_time": "23:00",
        "priority": "normal",
        "status": "created",
    }

    # In-process callers (zoe_agent): the service's broadcaster.broadcast is
    # the one and only fan-out — no HTTP relay, no double notification.
    assert notify_calls == []


@pytest.mark.asyncio
async def test_mcp_reminder_create_stdio_worker_relays_exactly_one_ui_update(monkeypatch):
    mcp_server, db, service_calls, notify_calls = _mcp_reminder_service_harness(monkeypatch)
    # In the spawned stdio worker the in-process broadcaster has no UI clients,
    # so the HTTP relay must fire — exactly once.
    monkeypatch.setattr(mcp_server, "_STDIO_WORKER", True)

    result = await mcp_server._execute_tool(db, "reminder_create", dict(_MCP_CREATE_ARGS))

    assert len(service_calls) == 1
    assert result["status"] == "created"
    assert notify_calls == [(
        "reminders",
        "reminder_created",
        {"id": "rem-1", "title": "check the oven", "due_date": "2026-06-15",
         "due_time": "23:00", "priority": "normal"},
    )]
