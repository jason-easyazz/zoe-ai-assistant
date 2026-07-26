"""Tests for GET /api/agent/tasks list endpoint."""
from __future__ import annotations

import sys
import types

import pytest

sys.path.insert(0, __file__.rsplit("/tests/", 1)[0])

from routers import system

pytestmark = pytest.mark.ci_safe


class _FakeDB:
    def __init__(self):
        self.last_fetch: tuple | None = None

    async def fetch(self, sql, *args):
        self.last_fetch = (sql, args)
        return [
            {
                "id": 7,
                "task": "audit validators",
                "status": "done",
                "created_at": None,
                "completed_at": None,
                "multica_issue_id": None,
            }
        ]


class _FakeCtx:
    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, *_):
        return None


@pytest.mark.asyncio
async def test_list_agent_tasks_scopes_to_authenticated_user(monkeypatch):
    db = _FakeDB()
    monkeypatch.setitem(
        sys.modules,
        "db_pool",
        types.SimpleNamespace(get_db_ctx=lambda: _FakeCtx(db)),
    )

    result = await system.list_agent_tasks(
        limit=10,
        status=None,
        user={"user_id": "user-a"},
    )

    assert result["tasks"][0]["task_id"] == "7"
    assert result["tasks"][0]["task"] == "audit validators"
    assert "result" not in result["tasks"][0]
    assert db.last_fetch is not None
    assert db.last_fetch[1][0] == "user-a"


@pytest.mark.asyncio
async def test_list_agent_tasks_status_filter(monkeypatch):
    db = _FakeDB()
    monkeypatch.setitem(
        sys.modules,
        "db_pool",
        types.SimpleNamespace(get_db_ctx=lambda: _FakeCtx(db)),
    )

    await system.list_agent_tasks(
        limit=5,
        status="running",
        user={"user_id": "user-b"},
    )

    sql, args = db.last_fetch
    assert "status=$2" in sql
    assert args[1] == "running"
