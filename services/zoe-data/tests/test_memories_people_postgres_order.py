"""`GET /api/memories/people` must order case-insensitively with PORTABLE SQL.

2026-09-25 audit §2.1: the handler used `ORDER BY name COLLATE NOCASE` — a
SQLite collation. On Postgres asyncpg raises `UndefinedObjectError: collation
"nocase" ... does not exist`, so the endpoint 500ed in production while the
SQLite-backed unit lane stayed green.

Two guards:
1. The handler's SQL, run through `db_pool._adapt_params` (the last rewrite
   before asyncpg sees it), carries no `COLLATE NOCASE` and does order by
   `lower(name)`.
2. A tree sweep: no runtime module under services/zoe-data uses
   `COLLATE NOCASE` at all — the CLASS, not the instance.

Negative control: `_adapt_params` on the OLD ORDER BY clause still contains
`COLLATE NOCASE` — the translator does not silently fix it, so the query fix
is what carries the guarantee.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import db_pool
from routers import memories

pytestmark = pytest.mark.ci_safe

_ZOE_DATA = Path(__file__).resolve().parents[1]


class _Cursor:
    def __init__(self, rows):
        self._rows = rows

    async def fetchall(self):
        return list(self._rows)

    async def fetchone(self):
        return self._rows[0] if self._rows else None


class _RecordingDb:
    def __init__(self, rows):
        self.rows = rows
        self.calls: list[tuple[str, list]] = []

    async def execute(self, sql, params=()):
        self.calls.append((sql, list(params)))
        return _Cursor(self.rows)

    async def commit(self):
        return None


def _user():
    return {"user_id": "U1", "role": "user"}


async def _allow(*_args, **_kwargs):
    return None


def _people_app(db):
    app = FastAPI()
    app.include_router(memories.router)
    app.dependency_overrides[memories.get_current_user] = _user
    app.dependency_overrides[memories.get_db] = lambda: db
    return app


_ROWS = [
    {"id": "p1", "name": "alice", "relationship": "friend", "visibility": "family",
     "user_id": "U1", "preferences": '{"avatar_url": "/a.png"}'},
    {"id": "p2", "name": "Bob", "relationship": None, "visibility": "personal",
     "user_id": "U1", "preferences": None},
]


def test_people_endpoint_returns_200_and_consumer_shape(monkeypatch):
    monkeypatch.setattr(memories, "require_feature_access", _allow)
    db = _RecordingDb(_ROWS)
    client = TestClient(_people_app(db))

    resp = client.get("/api/memories/people", params={"limit": 10, "q": "b"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 2
    assert body["people"][0] == {
        "id": "p1", "name": "alice", "relationship": "friend",
        "avatar_url": "/a.png", "visibility": "family",
    }
    sql, params = next(c for c in db.calls if "FROM people" in c[0])
    assert params == ["U1", "%b%", 10]
    assert "COLLATE" not in sql.upper()


def test_people_query_reaching_postgres_orders_by_lower_name_not_nocase(monkeypatch):
    monkeypatch.setattr(memories, "require_feature_access", _allow)
    db = _RecordingDb(_ROWS)
    TestClient(_people_app(db)).get("/api/memories/people")

    sql, params = next(c for c in db.calls if "FROM people" in c[0])
    pg_sql, pg_params = db_pool._adapt_params(sql, params)

    assert "COLLATE" not in pg_sql.upper()
    assert re.search(r"ORDER BY\s+lower\(name\)", pg_sql)
    assert "$1" in pg_sql and "?" not in pg_sql
    assert pg_params == ["U1", 100]


def test_negative_control_translator_does_not_strip_nocase():
    """The compat layer is NOT what protects us — the query text is."""
    pg_sql, _ = db_pool._adapt_params(
        "SELECT id FROM people WHERE user_id = ? ORDER BY name COLLATE NOCASE LIMIT ?",
        ["U1", 5],
    )
    assert "COLLATE NOCASE" in pg_sql


def test_no_runtime_module_uses_collate_nocase():
    """Sweep the class: Postgres has no NOCASE collation, so no runtime SQL may
    use it. Tests and this file are excluded (they quote it on purpose)."""
    offenders = []
    for path in _ZOE_DATA.rglob("*.py"):
        rel = path.relative_to(_ZOE_DATA)
        if "tests" in rel.parts:
            continue
        if "COLLATE NOCASE" in path.read_text(errors="ignore"):
            offenders.append(str(rel))
    assert offenders == [], f"SQLite-only COLLATE NOCASE in Postgres-bound code: {offenders}"
