import builtins
import logging
import os
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from models import NoteCreate
from routers import memories, notes

pytestmark = pytest.mark.ci_safe


class _Cursor:
    def __init__(self, rows):
        self._rows = rows

    async def fetchall(self):
        return list(self._rows)

    async def fetchone(self):
        return self._rows[0] if self._rows else None


class _RecordingDb:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.calls = []
        self.commits = 0

    async def execute(self, sql, params=()):
        self.calls.append((sql, list(params)))
        if "COUNT(*)" in sql:
            return _Cursor([(len(self.rows),)])
        if "SELECT * FROM notes WHERE id = ?" in sql:
            return _Cursor(
                [
                    {
                        "id": "note-1",
                        "user_id": "U1",
                        "title": "T",
                        "content": "remember Sarah likes tea",
                        "category": "general",
                        "tags": None,
                        "visibility": "personal",
                        "deleted": 0,
                    }
                ]
            )
        return _Cursor(self.rows)

    async def commit(self):
        self.commits += 1


class _Broadcaster:
    async def broadcast(self, *_args, **_kwargs):
        return None


async def _allow_feature(*_args, **_kwargs):
    return None


def _user():
    return {"user_id": "U1", "role": "user"}


def _notes_app(db):
    app = FastAPI()
    app.include_router(notes.router)
    app.dependency_overrides[notes.get_current_user] = _user
    app.dependency_overrides[notes.get_db] = lambda: db
    return app


def _memories_app(db):
    app = FastAPI()
    app.include_router(memories.router)
    app.dependency_overrides[memories.get_current_user] = _user
    app.dependency_overrides[memories.get_db] = lambda: db
    return app


@pytest.mark.asyncio
async def test_note_person_extraction_failure_is_logged_and_nonfatal(monkeypatch, caplog):
    monkeypatch.setattr(notes, "require_feature_access", _allow_feature)
    monkeypatch.setattr(notes, "broadcaster", _Broadcaster())

    async def _skip_memory(*_args, **_kwargs):
        return None

    monkeypatch.setattr(notes, "_store_note_memory", _skip_memory)
    original_import = builtins.__import__

    def _fail_person_import(name, *args, **kwargs):
        if name in {"person_extractor", "person_extractor_llm"}:
            raise RuntimeError("extractor unavailable")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fail_person_import)
    caplog.set_level(logging.ERROR, logger=notes.__name__)

    result = await notes.create_note(
        NoteCreate(title="T", content="remember Sarah likes tea"),
        user=_user(),
        db=_RecordingDb(),
    )

    assert result["id"] == "note-1"
    assert "failed to schedule person extraction" in caplog.text


@pytest.mark.asyncio
async def test_memory_prompt_search_failure_is_logged_and_nonfatal(monkeypatch, caplog):
    class _Svc:
        async def load_for_prompt(self, user_id, *, limit):
            return []

        async def search(self, *_args, **_kwargs):
            raise RuntimeError("semantic index down")

    monkeypatch.setattr(memories, "_svc", lambda: _Svc())
    monkeypatch.setattr(memories, "_message_needs_memory", lambda _message: True)
    caplog.set_level(logging.ERROR, logger=memories.__name__)

    # Direct call bypasses FastAPI, so pass limit explicitly: the Query(12)
    # default is a sentinel object and #1005's `all_rows[:limit]` slice needs
    # a real int (FastAPI resolves it in production).
    result = await memories.memory_for_prompt(
        user_id="U1",
        message="what do you remember",
        limit=memories._PROMPT_PACKET_MAX_FACTS,
    )

    assert result == {"packet": "", "refs": [], "count": 0, "user_scoped": True}
    assert "semantic prompt search failed" in caplog.text


def test_notes_category_filter_accepts_long_stored_values(monkeypatch):
    monkeypatch.setattr(notes, "require_feature_access", _allow_feature)
    db = _RecordingDb()
    client = TestClient(_notes_app(db))

    stored_category = "c" * 700
    plausible = client.get("/api/notes/", params={"category": stored_category})
    assert plausible.status_code == 200
    assert db.calls[0][1] == ["U1", stored_category, 100]
    assert db.calls[1][1] == ["U1", stored_category]

    normal = client.get("/api/notes/", params={"category": "work", "limit": 5})
    assert normal.status_code == 200
    assert db.calls[2][1] == ["U1", "work", 5]
    assert db.calls[3][1] == ["U1", "work"]


def test_memories_like_search_rejects_or_caps_overlong_inputs(monkeypatch):
    monkeypatch.setattr(memories, "require_feature_access", _allow_feature)
    db = _RecordingDb()
    client = TestClient(_memories_app(db))

    people = client.get(
        "/api/memories/people",
        params={"q": "x" * (memories._MAX_LIKE_QUERY_LENGTH + 1)},
    )
    assert people.status_code == 422
    assert db.calls == []

    preview = client.post(
        "/api/memories/link-preview",
        json={"query": "y" * (memories._MAX_LIKE_QUERY_LENGTH + 1)},
    )
    assert preview.status_code == 422
    assert db.calls == []


def test_memories_normal_like_search_inputs_are_unchanged(monkeypatch):
    monkeypatch.setattr(memories, "require_feature_access", _allow_feature)
    db = _RecordingDb()
    client = TestClient(_memories_app(db))

    people = client.get("/api/memories/people", params={"q": "sarah", "limit": 7})
    assert people.status_code == 200
    assert db.calls[0][1] == ["U1", "%sarah%", 7]

    preview = client.post("/api/memories/link-preview", json={"query": "tea"})
    assert preview.status_code == 200
    assert db.calls[-1][1][1:] == ["%tea%", "%tea%"]

    long_url = "https://example.com/" + ("p" * 350)
    preview = client.post("/api/memories/link-preview", json={"url": long_url})
    assert preview.status_code == 200
    assert db.calls[-1][1][1:] == [f"%{long_url}%", f"%{long_url}%"]
