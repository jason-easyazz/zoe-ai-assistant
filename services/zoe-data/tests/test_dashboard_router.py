import asyncio
import json
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from routers import dashboard  # noqa: E402


class _Request:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


class _Txn:
    def __init__(self, lock):
        self._lock = lock

    async def __aenter__(self):
        await self._lock.acquire()
        return self

    async def __aexit__(self, *_args):
        self._lock.release()


class _FakeDashboardDb:
    def __init__(self, layout=None):
        self.layout = layout
        self.lock = asyncio.Lock()
        self.sql = []

    def transaction(self):
        return _Txn(self.lock)

    async def fetchrow(self, sql, user_id):
        self.sql.append(sql)
        if self.layout is None:
            return None
        return {"layout": json.dumps(self.layout)}

    async def execute(self, sql, *params):
        self.sql.append(sql)
        if sql.startswith("INSERT INTO dashboard_layouts"):
            if self.layout is None:
                self.layout = json.loads(params[1])
            return "INSERT 0 1"
        if sql.startswith("UPDATE dashboard_layouts"):
            self.layout = json.loads(params[0])
            return "UPDATE 1"
        raise AssertionError(f"unexpected SQL: {sql}")


def _patch_db(monkeypatch, db):
    async def fake_get_db():
        yield db

    monkeypatch.setattr(dashboard, "get_db", fake_get_db)


@pytest.mark.asyncio
async def test_add_widgets_rejects_oversized_widget_ids():
    request = _Request({"widgets": ["weather"] * (dashboard.MAX_WIDGET_IDS_PER_REQUEST + 1)})

    with pytest.raises(HTTPException) as exc:
        await dashboard.add_widgets(request, {"user_id": "u1"})

    assert exc.value.status_code == 400
    assert "at most" in exc.value.detail


@pytest.mark.asyncio
async def test_add_and_remove_widgets_use_locked_transaction(monkeypatch):
    db = _FakeDashboardDb(layout=[{"id": "weather", "x": 0, "y": 0, "w": 2, "h": 2}])
    _patch_db(monkeypatch, db)

    add_result = await dashboard.add_widgets(_Request({"widgets": ["events", "events"]}), {"user_id": "u1"})
    remove_result = await dashboard.remove_widget("weather", {"user_id": "u1"})

    assert add_result == {"status": "ok", "added": ["events"]}
    assert remove_result == {"status": "ok", "removed": "weather"}
    assert [widget["id"] for widget in db.layout] == ["events"]
    assert any("FOR UPDATE" in sql for sql in db.sql)


@pytest.mark.asyncio
async def test_concurrent_add_and_remove_do_not_lose_updates(monkeypatch):
    db = _FakeDashboardDb(
        layout=[
            {"id": "weather", "x": 0, "y": 0, "w": 2, "h": 2},
            {"id": "tasks", "x": 0, "y": 2, "w": 2, "h": 3},
        ]
    )
    _patch_db(monkeypatch, db)

    add_result, remove_result = await asyncio.gather(
        dashboard.add_widgets(_Request({"widgets": ["events"]}), {"user_id": "u1"}),
        dashboard.remove_widget("weather", {"user_id": "u1"}),
    )

    assert add_result == {"status": "ok", "added": ["events"]}
    assert remove_result == {"status": "ok", "removed": "weather"}
    assert {widget["id"] for widget in db.layout} == {"tasks", "events"}
    assert "weather" not in {widget["id"] for widget in db.layout}
