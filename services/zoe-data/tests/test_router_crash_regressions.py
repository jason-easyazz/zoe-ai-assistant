from __future__ import annotations

import pytest
import json
import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from routers import calendar as calendar_router
from routers import reminders as reminders_router
from routers import transactions as transactions_router

pytestmark = pytest.mark.ci_safe


class FakeCursor:
    def __init__(self, rows: list[dict[str, Any]] | None = None):
        self._rows = rows or []
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

    async def fetchall(self):
        return self._rows

    async def fetchone(self):
        return self._rows[0] if self._rows else None


class RecordingDB:
    def __init__(self):
        self.calls: list[tuple[str, list[Any]]] = []
        self.commits = 0
        self.transaction_rows = [
            {
                "id": "txn-1",
                "user_id": "u-1",
                "description": "Coffee",
                "amount": 4.5,
                "type": "expense",
                "transaction_date": "2026-06-27",
                "status": "completed",
                "metadata": '{"source":"card"}',
                "visibility": "personal",
                "deleted": 0,
            }
        ]
        self.notification_rows = [
            {
                "id": "notif-1",
                "user_id": "u-1",
                "type": "reminder_due",
                "title": "Take meds",
                "message": "Take meds",
                "data": '{"reminder_id":"rem-1","priority":"high"}',
                "delivered": 0,
            }
        ]
        self.event_row: dict[str, Any] | None = None

    async def execute(self, sql: str, params=()):
        params_list = list(params or [])
        self.calls.append((sql, params_list))
        normalized = " ".join(sql.split())

        if normalized.startswith("SELECT * FROM transactions"):
            return FakeCursor(self.transaction_rows)

        if normalized.startswith("INSERT INTO events"):
            self.event_row = {
                "id": params_list[0],
                "user_id": params_list[1],
                "title": params_list[2],
                "start_date": params_list[3],
                "start_time": params_list[4],
                "end_date": params_list[5],
                "end_time": params_list[6],
                "duration": params_list[7],
                "category": params_list[8],
                "location": params_list[9],
                "all_day": params_list[10],
                "recurring": params_list[11],
                "metadata": params_list[12],
                "visibility": params_list[13],
                "deleted": 0,
            }
            return FakeCursor()

        if normalized.startswith("SELECT * FROM events"):
            return FakeCursor([self.event_row] if self.event_row else [])

        if normalized.startswith("SELECT * FROM notifications"):
            return FakeCursor(self.notification_rows)

        return FakeCursor()

    async def commit(self):
        self.commits += 1


def _client(router_module, db: RecordingDB) -> TestClient:
    app = FastAPI()
    app.include_router(router_module.router)

    async def fake_get_db():
        yield db

    app.dependency_overrides[router_module.get_current_user] = lambda: {
        "user_id": "u-1",
        "role": "admin",
    }
    app.dependency_overrides[router_module.get_db] = fake_get_db
    return TestClient(app)


def _allow_access(monkeypatch, router_module):
    async def fake_require_feature_access(db, user, *, feature, action, **kwargs):
        return None

    monkeypatch.setattr(router_module, "require_feature_access", fake_require_feature_access)


def _mute_broadcast(monkeypatch, router_module):
    async def fake_broadcast(*args, **kwargs):
        return None

    monkeypatch.setattr(router_module.broadcaster, "broadcast", fake_broadcast)


def test_transactions_list_endpoint_builds_where_without_filters(monkeypatch):
    _allow_access(monkeypatch, transactions_router)
    db = RecordingDB()

    response = _client(transactions_router, db).get("/api/transactions/")

    assert response.status_code == 200
    assert response.json()["transactions"][0]["metadata"] == {"source": "card"}
    sql, params = db.calls[0]
    assert "WHERE ?" not in sql
    assert "WHERE (visibility = 'family' OR user_id = ?) AND deleted = 0" in sql
    assert params == ["u-1"]


def test_transactions_list_endpoint_builds_where_with_filters(monkeypatch):
    _allow_access(monkeypatch, transactions_router)
    db = RecordingDB()

    response = _client(transactions_router, db).get(
        "/api/transactions/",
        params={
            "start_date": "2026-06-01",
            "end_date": "2026-06-30",
            "type": "expense",
            "status": "completed",
        },
    )

    assert response.status_code == 200
    sql, params = db.calls[0]
    assert "transaction_date >= ?" in sql
    assert "transaction_date <= ?" in sql
    assert "type = ?" in sql
    assert "status = ?" in sql
    assert params == ["u-1", "2026-06-01", "2026-06-30", "expense", "completed"]


def test_calendar_create_endpoint_serializes_metadata(monkeypatch):
    _allow_access(monkeypatch, calendar_router)
    _mute_broadcast(monkeypatch, calendar_router)
    db = RecordingDB()

    response = _client(calendar_router, db).post(
        "/api/calendar/events",
        json={
            "title": "Dentist",
            "start_date": "2026-07-01",
            "start_time": "09:30",
            "category": "health",
            "metadata": {"source": "test", "nested": {"ok": True}},
            "visibility": "personal",
        },
    )

    assert response.status_code == 200
    assert response.json()["metadata"] == {"source": "test", "nested": {"ok": True}}
    insert_sql, insert_params = db.calls[0]
    assert "INSERT INTO events" in insert_sql
    assert json.loads(insert_params[12]) == {"source": "test", "nested": {"ok": True}}
    assert db.commits == 1


def test_reminders_pending_notifications_endpoint_parses_data_payload(monkeypatch):
    _allow_access(monkeypatch, reminders_router)
    db = RecordingDB()

    response = _client(reminders_router, db).get("/api/reminders/notifications/pending")

    assert response.status_code == 200
    notifications = response.json()["notifications"]
    assert notifications[0]["data"] == {"reminder_id": "rem-1", "priority": "high"}
    assert notifications[0]["delivered"] is False
