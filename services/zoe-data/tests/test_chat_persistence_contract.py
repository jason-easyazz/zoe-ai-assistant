import pytest

from routers import chat as chat_router


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


def _fake_get_db(db):
    async def _gen():
        yield db

    return _gen


@pytest.mark.asyncio
async def test_save_chat_message_updates_weak_session_title(monkeypatch):
    db = _FakeDb(title="New Chat")
    monkeypatch.setattr(chat_router, "get_db", _fake_get_db(db))

    await chat_router._save_chat_message("session-1", "user", "  plan the lake trip  ")

    insert = db.executed[0]
    assert "INSERT INTO chat_messages" in insert[0]
    assert insert[1][1:] == ("session-1", "user", "plan the lake trip")

    title_updates = [params for sql, params in db.executed if "title = ?" in sql]
    assert title_updates
    assert title_updates[0][0] != "New Chat"
    assert title_updates[0][1] == "session-1"


@pytest.mark.asyncio
async def test_save_chat_message_only_touches_existing_strong_title(monkeypatch):
    db = _FakeDb(title="Trip Planning")
    monkeypatch.setattr(chat_router, "get_db", _fake_get_db(db))

    await chat_router._save_chat_message("session-1", "assistant", "Sounds good")

    assert any("INSERT INTO chat_messages" in sql for sql, _ in db.executed)
    assert not any("title = ?" in sql for sql, _ in db.executed)
    assert any("UPDATE chat_sessions SET updated_at" in sql for sql, _ in db.executed)
