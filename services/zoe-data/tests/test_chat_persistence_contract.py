import pytest

from routers import chat as chat_router

pytestmark = pytest.mark.ci_safe


class _FakeDb:
    def __init__(self, title="New Chat"):
        self.title = title
        self.executed = []

    async def execute(self, sql, params=()):
        self.executed.append((sql, params))

    async def execute_fetchall(self, sql, params=()):
        self.executed.append((sql, params))
        if "SELECT title FROM chat_sessions" in sql:
            return [{"title": self.title}]
        return []

    async def commit(self):
        self.executed.append(("COMMIT", ()))


def _patch_db_ctx(monkeypatch, db):
    """_save_chat_message acquires via db_pool.get_db_ctx() — patch that.

    Uses monkeypatch.setitem/setattr so both the sys.modules entry and the
    get_db_ctx attribute are restored on teardown (no cross-test leak).
    """
    import sys
    import types

    class _Ctx:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *a):
            return False

    fake_db_pool = sys.modules.get("db_pool") or types.ModuleType("db_pool")
    monkeypatch.setattr(fake_db_pool, "get_db_ctx", lambda: _Ctx(), raising=False)
    monkeypatch.setitem(sys.modules, "db_pool", fake_db_pool)


@pytest.mark.asyncio
async def test_save_chat_message_updates_weak_session_title(monkeypatch):
    db = _FakeDb(title="New Chat")
    _patch_db_ctx(monkeypatch, db)

    await chat_router._save_chat_message("session-1", "user", "  plan the lake trip  ")

    insert = db.executed[0]
    assert "INSERT INTO chat_messages" in insert[0]
    # (id, session_id, role, content, metadata) — metadata NULL when no user given
    assert insert[1][1:4] == ("session-1", "user", "plan the lake trip")
    assert insert[1][4] is None

    title_updates = [params for sql, params in db.executed if "title = ?" in sql]
    assert title_updates
    assert title_updates[0][0] != "New Chat"
    assert title_updates[0][1] == "session-1"


@pytest.mark.asyncio
async def test_save_chat_message_only_touches_existing_strong_title(monkeypatch):
    db = _FakeDb(title="Trip Planning")
    _patch_db_ctx(monkeypatch, db)

    await chat_router._save_chat_message("session-1", "assistant", "Sounds good")

    assert any("INSERT INTO chat_messages" in sql for sql, _ in db.executed)
    assert not any("title = ?" in sql for sql, _ in db.executed)
    assert any("UPDATE chat_sessions SET updated_at" in sql for sql, _ in db.executed)


@pytest.mark.asyncio
async def test_save_chat_message_stamps_user_id_metadata(monkeypatch):
    import json as _json

    db = _FakeDb(title="Trip Planning")
    _patch_db_ctx(monkeypatch, db)

    await chat_router._save_chat_message("session-1", "user", "remember this", user_id="jason")

    insert = db.executed[0]
    assert "INSERT INTO chat_messages" in insert[0]
    assert _json.loads(insert[1][4]) == {"user_id": "jason"}
