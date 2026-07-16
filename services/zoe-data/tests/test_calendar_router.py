from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from routers import calendar as calendar_router

pytestmark = pytest.mark.ci_safe


class FakeCursor:
    def __init__(self, rows: list[dict]):
        self._rows = rows
        self._index = 0

    def __aiter__(self):
        self._index = 0
        return self

    async def __anext__(self):
        if self._index >= len(self._rows):
            raise StopAsyncIteration
        row = self._rows[self._index]
        self._index += 1
        return row

    async def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeDB:
    def __init__(self, rows: list[dict]):
        self._rows = rows
        self.calls: list[tuple[str, list[str]]] = []

    async def execute(self, sql: str, params: list[str]):
        self.calls.append((sql, params))
        return FakeCursor(self._rows)


@pytest.mark.asyncio
async def test_list_today_events_applies_calendar_read_gate_and_policy(monkeypatch):
    user = {"user_id": "u-1", "role": "member"}
    access_calls = []

    async def fake_require_feature_access(db, user, feature, action):
        access_calls.append((db, user, feature, action))

    monkeypatch.setattr(calendar_router, "require_feature_access", fake_require_feature_access)

    db = FakeDB(
        [
            {
                "id": "evt-1",
                "user_id": "u-1",
                "title": "Standup",
                "start_date": date.today().isoformat(),
                "start_time": "09:00",
                "metadata": None,
                "all_day": 0,
                "deleted": 0,
            }
        ]
    )

    result = await calendar_router.list_today_events(user=user, db=db)

    assert access_calls == [(db, user, "calendar", "read")]
    assert result["events"][0]["id"] == "evt-1"
    assert db.calls and db.calls[0][1] == ["u-1", date.today().isoformat()]


@pytest.mark.asyncio
async def test_get_event_applies_calendar_read_gate_and_policy(monkeypatch):
    user = {"user_id": "u-2", "role": "member"}
    access_calls = []

    async def fake_require_feature_access(db, user, feature, action):
        access_calls.append((db, user, feature, action))

    monkeypatch.setattr(calendar_router, "require_feature_access", fake_require_feature_access)

    db = FakeDB(
        [
            {
                "id": "evt-2",
                "user_id": "u-2",
                "title": "Doctor",
                "start_date": "2026-05-28",
                "start_time": "15:00",
                "metadata": None,
                "all_day": 0,
                "deleted": 0,
            }
        ]
    )

    result = await calendar_router.get_event("evt-2", user=user, db=db)

    assert access_calls == [(db, user, "calendar", "read")]
    assert result["id"] == "evt-2"
    assert db.calls and db.calls[0][1] == ["u-2", "evt-2"]


@pytest.mark.asyncio
async def test_list_today_events_allows_guest_when_policy_allows(monkeypatch):
    user = {"user_id": "guest-1", "role": "guest"}
    access_calls = []

    async def fake_require_feature_access(db, user, feature, action):
        access_calls.append((db, user, feature, action))

    monkeypatch.setattr(calendar_router, "require_feature_access", fake_require_feature_access)

    db = FakeDB(
        [
            {
                "id": "evt-guest",
                "user_id": "guest-1",
                "title": "Public reminder",
                "start_date": date.today().isoformat(),
                "start_time": "10:00",
                "metadata": None,
                "all_day": 0,
                "deleted": 0,
            }
        ]
    )

    result = await calendar_router.list_today_events(user=user, db=db)

    assert access_calls == [(db, user, "calendar", "read")]
    assert result["events"][0]["id"] == "evt-guest"
    assert db.calls and db.calls[0][1] == ["guest-1", date.today().isoformat()]


@pytest.mark.asyncio
async def test_get_event_propagates_calendar_read_denial(monkeypatch):
    user = {"user_id": "u-denied", "role": "member"}

    async def fake_require_feature_access(db, user, feature, action):
        raise HTTPException(status_code=403, detail="denied")

    monkeypatch.setattr(calendar_router, "require_feature_access", fake_require_feature_access)

    db = FakeDB(
        [
            {
                "id": "evt-denied",
                "user_id": "u-denied",
                "title": "Private",
                "start_date": "2026-05-28",
                "start_time": "15:00",
                "metadata": None,
                "all_day": 0,
                "deleted": 0,
            }
        ]
    )

    with pytest.raises(HTTPException) as excinfo:
        await calendar_router.get_event("evt-denied", user=user, db=db)

    assert excinfo.value.status_code == 403
    assert db.calls == []
