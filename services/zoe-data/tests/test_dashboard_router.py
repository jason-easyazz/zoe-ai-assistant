import asyncio
import json
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from routers import dashboard  # noqa: E402

pytestmark = pytest.mark.ci_safe


class _Request:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


class _Txn:
    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        await self._db.lock.acquire()
        self._db.active_transactions += 1
        self._db.max_active_transactions = max(
            self._db.max_active_transactions,
            self._db.active_transactions,
        )
        return self

    async def __aexit__(self, *_args):
        self._db.active_transactions -= 1
        self._db.lock.release()


class _FakeDashboardDb:
    def __init__(self, layout=None):
        self.layout = layout
        self.lock = asyncio.Lock()
        self.sql = []
        self.active_transactions = 0
        self.max_active_transactions = 0

    def transaction(self):
        return _Txn(self)

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


class _SlowSaveDb(_FakeDashboardDb):
    def __init__(self, layout=None):
        super().__init__(layout=layout)
        self.save_lock_acquired = asyncio.Event()
        self.release_save = asyncio.Event()
        self._held_save_once = False

    async def execute(self, sql, *params):
        if (
            sql.startswith("UPDATE dashboard_layouts")
            and not self._held_save_once
            and params[0] == json.dumps([{"id": "tasks", "x": 0, "y": 0, "w": 2, "h": 3}])
        ):
            self._held_save_once = True
            self.save_lock_acquired.set()
            await self.release_save.wait()
        return await super().execute(sql, *params)


def _patch_db(monkeypatch, db):
    from contextlib import asynccontextmanager

    async def fake_get_db():
        yield db

    @asynccontextmanager
    async def fake_get_db_ctx():
        yield db

    monkeypatch.setattr(dashboard, "get_db", fake_get_db)
    # Endpoints with early returns use get_db_ctx (the #953 leak fix) — stub it
    # to the same fake db so both acquisition paths are covered.
    monkeypatch.setattr(dashboard, "get_db_ctx", fake_get_db_ctx)


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


@pytest.mark.asyncio
async def test_concurrent_save_and_add_serialize_on_layout_lock(monkeypatch):
    db = _SlowSaveDb(layout=[{"id": "weather", "x": 0, "y": 0, "w": 2, "h": 2}])
    _patch_db(monkeypatch, db)

    save_task = asyncio.create_task(
        dashboard.save_layout(
            _Request({"layout": [{"id": "tasks", "x": 0, "y": 0, "w": 2, "h": 3}]}),
            {"user_id": "u1"},
        )
    )
    await db.save_lock_acquired.wait()
    add_task = asyncio.create_task(
        dashboard.add_widgets(_Request({"widgets": ["events"]}), {"user_id": "u1"})
    )

    await asyncio.sleep(0)
    assert not add_task.done()

    db.release_save.set()
    save_result, add_result = await asyncio.gather(save_task, add_task)

    assert save_result == {"status": "ok"}
    assert add_result == {"status": "ok", "added": ["events"]}
    assert {widget["id"] for widget in db.layout} == {"tasks", "events"}
    assert db.max_active_transactions == 1
    assert sum("FOR UPDATE" in sql for sql in db.sql) >= 2
