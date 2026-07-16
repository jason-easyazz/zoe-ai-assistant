import pytest

import db_pool
import user_portrait

pytestmark = pytest.mark.ci_safe


class _Cursor:
    def __init__(self, rows):
        self._rows = rows

    async def fetchall(self):
        return self._rows


class _FakeDb:
    def __init__(self, rows):
        self.rows = rows
        self.sql = []

    async def execute(self, sql, params=()):
        self.sql.append(sql)
        return _Cursor(self.rows)


class _FakeCtx:
    """Async-context-manager stand-in for db_pool.get_db_ctx()."""

    def __init__(self, db):
        self.db = db
        self.entered = 0
        self.exited = 0

    async def __aenter__(self):
        self.entered += 1
        return self.db

    async def __aexit__(self, *exc):
        self.exited += 1
        return False


@pytest.mark.asyncio
async def test_run_portrait_synthesis_for_all_fallback_db_none_uses_get_db_ctx(monkeypatch):
    """The chat_sessions fallback (MemoryService has no list_users) must acquire
    via get_db_ctx, not the suspended-generator `async for db in get_db(): break`
    form that closed the connection mid-query ('portrait: could not list users')."""
    import memory_service

    class _SvcNoListUsers:
        pass  # no list_users attr → AttributeError → fallback path

    monkeypatch.setattr(memory_service, "get_memory_service", lambda: _SvcNoListUsers())

    db = _FakeDb([("alice",), ("bob",), (None,)])  # None filtered out
    ctx = _FakeCtx(db)
    monkeypatch.setattr(db_pool, "get_db_ctx", lambda: ctx)

    seen = []

    async def fake_run_portrait_synthesis(user_id, db=None):
        seen.append((user_id, db))
        return {"user_id": user_id}

    monkeypatch.setattr(user_portrait, "run_portrait_synthesis", fake_run_portrait_synthesis)

    results = await user_portrait.run_portrait_synthesis_for_all()  # db=None

    assert ctx.entered == 1 and ctx.exited == 1  # pooled connection acquired + released
    assert [r["user_id"] for r in results] == ["alice", "bob"]
    # per-user synthesis self-acquires (db=None passed through).
    assert seen == [("alice", None), ("bob", None)]
    assert "SELECT DISTINCT user_id FROM chat_sessions" in db.sql[0]
