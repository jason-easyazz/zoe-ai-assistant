"""Direct-execution tests for list_add / list_remove intents.

Mirrors test_reminder_direct_execution.py (from #960). These intents used to
fall through to the mcporter subprocess path in intent_router, which spawns a
second mcp_server that dies on DB-pool init and returns None → ok:false, so
"add milk to my shopping list" silently failed. The direct executors write
straight through get_db_ctx, mirroring mcp_server's list_add_item /
list_remove_item storage semantics.
"""
import contextlib

import pytest

pytestmark = pytest.mark.ci_safe  # slim-dep write-path guards -> GitHub blocking lane (#960/#993 suites)

from intent_router import (
    Intent,
    _execute_list_add_direct,
    _execute_list_remove_direct,
    execute_intent,
)


class _Cursor:
    def __init__(self, row=None):
        self._row = row

    async def fetchone(self):
        return self._row


class _FakeListDB:
    """Captures every SQL statement; returns a scripted row for each SELECT.

    ``select_rows`` is a list consumed in order — one entry per SELECT the code
    under test issues (None means "no match").
    """

    def __init__(self, select_rows):
        self.calls = []
        self._selects = list(select_rows)

    async def execute(self, sql, params=()):
        self.calls.append((sql, tuple(params)))
        if sql.strip().upper().startswith("SELECT"):
            row = self._selects.pop(0) if self._selects else None
            return _Cursor(row)
        return _Cursor()

    def sql_matching(self, needle):
        return [sql for sql, _ in self.calls if needle in sql]


def _fake_db_ctx(db):
    @contextlib.asynccontextmanager
    async def ctx():
        yield db

    return ctx


def _fail_mcporter(monkeypatch, msg="direct path should avoid mcporter"):
    async def fail(_cmd):
        raise AssertionError(msg)

    monkeypatch.setattr("intent_router._run_mcporter", fail)


def _silence_ui(monkeypatch):
    async def noop(_event, _data):
        return None

    monkeypatch.setattr("intent_router._notify_lists_ui", noop)


# --- list_add ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_add_existing_list_ok_message_and_item_written(monkeypatch):
    """Add to an existing list: ok:true message, and an INSERT into list_items
    actually happens (no new list created)."""
    db = _FakeListDB([{"id": "list-shopping"}, None])  # list lookup finds the list; dup check: none
    _silence_ui(monkeypatch)
    monkeypatch.setattr("database.get_db_ctx", _fake_db_ctx(db))
    _fail_mcporter(monkeypatch)

    result = await execute_intent(
        Intent("list_add", {"item": "milk", "list_type": "shopping"}), "family-admin"
    )

    assert result == "Added milk to your shopping list."
    inserts = db.sql_matching("INSERT INTO list_items")
    assert len(inserts) == 1
    # existing list found → no new list row inserted
    assert db.sql_matching("INSERT INTO lists") == []
    # item text is actually written into the params
    _sql, params = next((c for c in db.calls if "INSERT INTO list_items" in c[0]))
    assert "milk" in params


@pytest.mark.asyncio
async def test_list_add_fresh_list_creates_list_then_item(monkeypatch):
    """When the list doesn't exist yet, both a lists row and a list_items row
    are inserted (mirrors mcp_server.list_add_item)."""
    db = _FakeListDB([None])  # list lookup finds nothing
    _silence_ui(monkeypatch)
    monkeypatch.setattr("database.get_db_ctx", _fake_db_ctx(db))
    _fail_mcporter(monkeypatch)

    result = await _execute_list_add_direct(
        Intent("list_add", {"item": "eggs", "list_type": "shopping"}), "family-admin"
    )

    assert result == "Added eggs to your shopping list."
    assert len(db.sql_matching("INSERT INTO lists")) == 1
    assert len(db.sql_matching("INSERT INTO list_items")) == 1


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_list_add_replay_within_window_skips_insert(monkeypatch):
    """Retry idempotency: an identical item added seconds ago (voice re-POST /
    HTTP retry replay) must NOT insert a duplicate — reply stays ok:true."""
    db = _FakeListDB([{"id": "list-shopping"}, {"id": "existing-item"}])  # dup check hits
    _silence_ui(monkeypatch)
    monkeypatch.setattr("database.get_db_ctx", _fake_db_ctx(db))
    _fail_mcporter(monkeypatch)

    result = await execute_intent(
        Intent("list_add", {"item": "milk", "list_type": "shopping"}), "family-admin"
    )

    assert result == "Added milk to your shopping list."
    assert db.sql_matching("INSERT INTO list_items") == []
    assert db.sql_matching("INSERT INTO lists") == []


async def test_list_add_non_shopping_list_friendly_name(monkeypatch):
    db = _FakeListDB([{"id": "list-personal"}, None])
    _silence_ui(monkeypatch)
    monkeypatch.setattr("database.get_db_ctx", _fake_db_ctx(db))
    _fail_mcporter(monkeypatch)

    result = await execute_intent(
        Intent("list_add", {"item": "call dentist", "list_type": "personal"}), "family-admin"
    )

    assert result == "Added call dentist to your personal list."


@pytest.mark.asyncio
async def test_list_add_genuine_failure_returns_none(monkeypatch):
    """DB down AND mcporter down is a real failure → None → ok:false."""

    @contextlib.asynccontextmanager
    async def broken_ctx():
        raise RuntimeError("db unavailable")
        yield  # pragma: no cover

    async def dead_mcporter(_cmd):
        return None

    monkeypatch.setattr("database.get_db_ctx", broken_ctx)
    monkeypatch.setattr("intent_router._run_mcporter", dead_mcporter)

    result = await execute_intent(
        Intent("list_add", {"item": "milk", "list_type": "shopping"}), "family-admin"
    )

    assert result is None


# --- list_remove ------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_remove_present_item_ok_and_completed(monkeypatch):
    """Removing an item that's on the list: exact match hit, UPDATE completed=1,
    friendly confirmation."""
    db = _FakeListDB([{"id": "item-1", "list_id": "list-shopping"}])
    _silence_ui(monkeypatch)
    monkeypatch.setattr("database.get_db_ctx", _fake_db_ctx(db))
    _fail_mcporter(monkeypatch)

    result = await execute_intent(
        Intent("list_remove", {"item": "milk", "list_type": "shopping"}), "family-admin"
    )

    assert result == "Removed milk from your list."
    assert len(db.sql_matching("UPDATE list_items SET completed=1")) == 1


@pytest.mark.asyncio
async def test_list_remove_absent_item_is_clean_success(monkeypatch):
    """Item not on the list is ok:true with a clean message, never a failure —
    both the exact and the LIKE-fallback SELECT miss."""
    db = _FakeListDB([None, None])
    _silence_ui(monkeypatch)
    monkeypatch.setattr("database.get_db_ctx", _fake_db_ctx(db))
    _fail_mcporter(monkeypatch)

    result = await execute_intent(
        Intent("list_remove", {"item": "kale", "list_type": "shopping"}), "family-admin"
    )

    assert result == "kale wasn't on your shopping list."
    # nothing was completed
    assert db.sql_matching("UPDATE list_items SET completed=1") == []


@pytest.mark.asyncio
async def test_list_remove_substring_fallback_when_no_exact(monkeypatch):
    """Exact match misses, LIKE fallback hits → item is completed."""
    db = _FakeListDB([None, {"id": "item-2", "list_id": "list-shopping"}])
    _silence_ui(monkeypatch)
    monkeypatch.setattr("database.get_db_ctx", _fake_db_ctx(db))
    _fail_mcporter(monkeypatch)

    result = await _execute_list_remove_direct(
        Intent("list_remove", {"item": "milk", "list_type": "shopping"}), "family-admin"
    )

    assert result == "Removed milk from your list."
    assert len(db.sql_matching("UPDATE list_items SET completed=1")) == 1


@pytest.mark.asyncio
async def test_list_remove_genuine_failure_returns_none(monkeypatch):
    @contextlib.asynccontextmanager
    async def broken_ctx():
        raise RuntimeError("db unavailable")
        yield  # pragma: no cover

    async def dead_mcporter(_cmd):
        return None

    monkeypatch.setattr("database.get_db_ctx", broken_ctx)
    monkeypatch.setattr("intent_router._run_mcporter", dead_mcporter)

    result = await execute_intent(
        Intent("list_remove", {"item": "milk", "list_type": "shopping"}), "family-admin"
    )

    assert result is None


# --- wiring: direct path runs ahead of mcporter -----------------------------


@pytest.mark.asyncio
async def test_list_add_uses_direct_path_before_mcporter(monkeypatch):
    calls = []

    async def fake_direct(intent, user_id):
        calls.append((intent.name, dict(intent.slots), user_id))
        return "Added milk to your shopping list."

    async def fail_mcporter(_cmd):
        raise AssertionError("list_add direct path should avoid mcporter")

    monkeypatch.setattr("intent_router._execute_list_add_direct", fake_direct)
    monkeypatch.setattr("intent_router._run_mcporter", fail_mcporter)

    result = await execute_intent(
        Intent("list_add", {"item": "milk", "list_type": "shopping"}), "family-admin"
    )

    assert result == "Added milk to your shopping list."
    assert calls == [("list_add", {"item": "milk", "list_type": "shopping"}, "family-admin")]


@pytest.mark.asyncio
async def test_list_remove_uses_direct_path_before_mcporter(monkeypatch):
    calls = []

    async def fake_direct(intent, user_id):
        calls.append((intent.name, dict(intent.slots), user_id))
        return "Removed milk from your list."

    async def fail_mcporter(_cmd):
        raise AssertionError("list_remove direct path should avoid mcporter")

    monkeypatch.setattr("intent_router._execute_list_remove_direct", fake_direct)
    monkeypatch.setattr("intent_router._run_mcporter", fail_mcporter)

    result = await execute_intent(
        Intent("list_remove", {"item": "milk", "list_type": "shopping"}), "family-admin"
    )

    assert result == "Removed milk from your list."
    assert calls == [("list_remove", {"item": "milk", "list_type": "shopping"}, "family-admin")]
