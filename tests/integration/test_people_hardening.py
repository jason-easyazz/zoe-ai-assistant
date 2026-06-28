"""
Hardening tests for the people router (services/zoe-data/routers/people.py).

Covers two conservative hardening fixes:
1. add_relationship no longer re-raises raw DB exceptions to the client; a
   non-unique DB failure is logged server-side and surfaced as a generic 500,
   while the unique-key path still returns a clean 409.
2. add_important_date rejects out-of-range month/day/year/reminder_days_before
   with a 422, while legitimate values still succeed.

These import the live router module, so (per tests/AGENTS.md) they run on the
self-hosted runner, not GitHub-hosted runners.

Run: python3 -m pytest tests/integration/test_people_hardening.py -v
"""

import logging
import os
import sys

import pytest

# Router imports (auth, database, ...) are rooted at services/zoe-data.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "services", "zoe-data"))

import routers.people as people  # noqa: E402
from fastapi import HTTPException  # noqa: E402


# ── Fake DB plumbing ──────────────────────────────────────────────────────────

class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    async def fetchone(self):
        return self._rows[0] if self._rows else None

    async def fetchall(self):
        return list(self._rows)


class _ExecResult:
    """Mimics aiosqlite execute(): awaitable AND async context manager."""

    def __init__(self, cursor=None, error=None):
        self._cursor = cursor if cursor is not None else _FakeCursor([])
        self._error = error

    def __await__(self):
        async def _run():
            if self._error:
                raise self._error
            return self._cursor
        return _run().__await__()

    async def __aenter__(self):
        if self._error:
            raise self._error
        return self._cursor

    async def __aexit__(self, *exc):
        return False


class _FakeDB:
    def __init__(self, *, other_person_user_id=None, insert_error=None):
        self.other_person_user_id = other_person_user_id
        self.insert_error = insert_error
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append(sql)
        s = " ".join(sql.split()).upper()
        if s.startswith("SELECT USER_ID, VISIBILITY"):
            return _ExecResult(_FakeCursor([
                {"user_id": self.other_person_user_id, "visibility": "private"}
            ]))
        if s.startswith("INSERT INTO PERSON_RELATIONSHIPS") and self.insert_error:
            return _ExecResult(error=self.insert_error)
        return _ExecResult()

    async def commit(self):
        pass


@pytest.fixture
def patched(monkeypatch):
    """Bypass auth / person-lookup / side effects so tests target the new logic."""
    async def _noop(*a, **k):
        return None

    async def _person(*a, **k):
        return {"id": "p1", "user_id": "u1"}

    monkeypatch.setattr(people, "require_feature_access", _noop)
    monkeypatch.setattr(people, "_get_person_or_404", _person)
    monkeypatch.setattr(people, "_recalc_health", _noop)
    monkeypatch.setattr(people.broadcaster, "broadcast", _noop)


USER = {"user_id": "u1"}


# ── Finding #1: exception leak in add_relationship ────────────────────────────

async def test_relationship_unique_violation_returns_409(patched):
    db = _FakeDB(other_person_user_id="u1",
                 insert_error=Exception("UNIQUE constraint failed: person_relationships.id"))
    with pytest.raises(HTTPException) as ei:
        await people.add_relationship("p1", {"rel_type": "friend", "other_person_id": "p2"},
                                      user=USER, db=db)
    assert ei.value.status_code == 409
    assert ei.value.detail == "Relationship already exists"


async def test_relationship_db_error_does_not_leak(patched, caplog):
    secret = "FOREIGN KEY constraint failed: people.internal_secret_column"
    db = _FakeDB(other_person_user_id="u1", insert_error=Exception(secret))
    with caplog.at_level(logging.ERROR):
        with pytest.raises(HTTPException) as ei:
            await people.add_relationship("p1", {"rel_type": "friend", "other_person_id": "p2"},
                                          user=USER, db=db)
    # Clean, generic error to the client — no raw DB internals.
    assert ei.value.status_code == 500
    assert ei.value.detail == "Failed to create relationship"
    assert secret not in str(ei.value.detail)
    # And the real failure is logged server-side.
    assert "failed to create relationship" in caplog.text.lower()
    assert secret in caplog.text


# ── Finding #2: unbounded date fields in add_important_date ───────────────────

@pytest.mark.parametrize("body", [
    {"label": "Bday", "month": 13},
    {"label": "Bday", "month": 0},
    {"label": "Bday", "day": 32},
    {"label": "Bday", "day": 0},
    {"label": "Bday", "year": 999999},
    {"label": "Bday", "year": 0},
    {"label": "Bday", "reminder_days_before": -1},
    {"label": "Bday", "reminder_days_before": 100000},
    {"label": "Bday", "month": "13"},
])
async def test_important_date_invalid_rejected(patched, body):
    db = _FakeDB()
    with pytest.raises(HTTPException) as ei:
        await people.add_important_date("p1", body, user=USER, db=db)
    assert ei.value.status_code == 422
    # Rejected before touching the DB.
    assert not any("INSERT" in s.upper() for s in db.executed)


@pytest.mark.parametrize("body", [
    {"label": "Bday", "month": 1, "day": 1, "year": 1},
    {"label": "Bday", "month": 12, "day": 31, "year": 9999, "reminder_days_before": 366},
    {"label": "Anniversary", "month": 2, "day": 29, "year": 2024, "reminder_days_before": 0},
    {"label": "Bday"},  # all optional fields omitted -> reminder defaults to 7
    {"label": "Bday", "month": 6, "day": 28, "year": 2026},
])
async def test_important_date_valid_passes(patched, body):
    db = _FakeDB()
    result = await people.add_important_date("p1", body, user=USER, db=db)
    assert result["ok"] is True
    assert "id" in result
    assert any("INSERT INTO PERSON_IMPORTANT_DATES" in s.upper() for s in db.executed)
