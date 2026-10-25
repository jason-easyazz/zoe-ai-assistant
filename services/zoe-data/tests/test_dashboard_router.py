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
            and params[0] == json.dumps([{"uid": "t1", "type": "tasks", "x": 0, "y": 0, "w": 2, "h": 3}])
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
    assert [widget["type"] for widget in db.layout] == ["events"]
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
    assert {widget["type"] for widget in db.layout} == {"tasks", "events"}
    assert "weather" not in {widget["type"] for widget in db.layout}


@pytest.mark.asyncio
async def test_concurrent_save_and_add_serialize_on_layout_lock(monkeypatch):
    db = _SlowSaveDb(layout=[{"id": "weather", "x": 0, "y": 0, "w": 2, "h": 2}])
    _patch_db(monkeypatch, db)

    save_task = asyncio.create_task(
        dashboard.save_layout(
            _Request({"layout": [{"uid": "t1", "type": "tasks", "x": 0, "y": 0, "w": 2, "h": 3}]}),
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
    assert {widget["type"] for widget in db.layout} == {"tasks", "events"}
    assert db.max_active_transactions == 1
    assert sum("FOR UPDATE" in sql for sql in db.sql) >= 2


# ── Wave 4b (2026-10-19): layout entries are widget INSTANCES ─────────────────
# The duplicate-widget flaw had three layers; these pin the two server ones: the
# layout schema carries an instance key (uid) apart from the type, and nothing
# collapses two same-type cards.


class _GetDb:
    def __init__(self, layout):
        self.layout = layout

    async def execute(self, sql, params):
        layout = self.layout

        class _Cur:
            async def fetchone(self_inner):
                return {"layout": json.dumps(layout), "updated_at": None}

        return _Cur()


@pytest.mark.asyncio
async def test_put_layout_keeps_two_cards_of_the_same_type(monkeypatch):
    db = _FakeDashboardDb(layout=[])
    _patch_db(monkeypatch, db)
    two_weather = [
        {"uid": "w1", "type": "weather", "x": 0, "y": 0, "w": 4, "h": 3},
        {"uid": "w2", "type": "weather", "x": 4, "y": 0, "w": 4, "h": 3, "options": {"city": "Perth"}},
    ]

    assert (await dashboard.save_layout(_Request({"layout": two_weather}), {"user_id": "u1"}))["status"] == "ok"

    assert db.layout == two_weather
    assert [w["uid"] for w in db.layout] == ["w1", "w2"]


@pytest.mark.parametrize(
    "bad",
    [
        {"layout": "weather"},
        {"layout": [{"type": "weather", "x": 0, "y": 0, "w": 4, "h": 3}]},  # no uid: the old shape
        {"layout": [{"uid": "a", "type": "weather", "x": 0, "y": 0, "w": 4, "h": 3},
                    {"uid": "a", "type": "weather", "x": 4, "y": 0, "w": 4, "h": 3}]},  # repeated uid
        {"layout": [{"uid": "a", "type": "<script>", "x": 0, "y": 0, "w": 4, "h": 3}]},
        {"layout": [{"uid": "a", "type": "weather", "x": "0", "y": 0, "w": 4, "h": 3}]},
        {"layout": [{"uid": "a", "type": "weather", "x": 0, "y": 0, "w": 4, "h": 3, "options": "x" * 10}]},
        {"layout": [{"uid": f"u{i}", "type": "time", "x": 0, "y": 0, "w": 1, "h": 1}
                    for i in range(dashboard.MAX_LAYOUT_ENTRIES + 1)]},
    ],
)
@pytest.mark.asyncio
async def test_put_layout_refuses_a_malformed_layout_instead_of_dropping_cards(monkeypatch, bad):
    db = _FakeDashboardDb(layout=[{"uid": "keep", "type": "time", "x": 0, "y": 0, "w": 4, "h": 3}])
    _patch_db(monkeypatch, db)

    with pytest.raises(HTTPException) as exc:
        await dashboard.save_layout(_Request(bad), {"user_id": "u1"})

    assert exc.value.status_code == 400
    assert db.layout == [{"uid": "keep", "type": "time", "x": 0, "y": 0, "w": 4, "h": 3}]


@pytest.mark.asyncio
async def test_get_layout_reads_legacy_entries_as_instances(monkeypatch):
    from contextlib import asynccontextmanager

    legacy = [
        {"id": "weather", "x": 0, "y": 0, "w": 2, "h": 2},
        {"id": "weather", "x": 2, "y": 0, "w": 2, "h": 2},
        {"uid": "n1", "type": "notes", "x": 0, "y": 2, "w": 4, "h": 4},
    ]

    @asynccontextmanager
    async def fake_ctx():
        yield _GetDb(legacy)

    monkeypatch.setattr(dashboard, "get_db_ctx", fake_ctx)
    result = await dashboard.get_layout({"user_id": "u1"})

    layout = result["layout"]
    assert [w["type"] for w in layout] == ["weather", "weather", "notes"]
    assert all("id" not in w for w in layout)
    uids = [w["uid"] for w in layout]
    assert len(set(uids)) == 3, "two legacy same-type cards must come back as two instances"
    assert uids[2] == "n1"


@pytest.mark.asyncio
async def test_post_widgets_is_ensure_present_and_writes_instances(monkeypatch):
    db = _FakeDashboardDb(layout=[
        {"uid": "w1", "type": "weather", "x": 0, "y": 0, "w": 4, "h": 3},
        {"uid": "w2", "type": "weather", "x": 4, "y": 0, "w": 4, "h": 3},
    ])
    _patch_db(monkeypatch, db)

    result = await dashboard.add_widgets(_Request({"widgets": ["weather", "notes"]}), {"user_id": "u1"})

    assert result == {"status": "ok", "added": ["notes"]}
    assert [w["type"] for w in db.layout] == ["weather", "weather", "notes"]
    assert [w["uid"] for w in db.layout][:2] == ["w1", "w2"], "existing instances keep their uid"
    assert db.layout[2]["uid"] and "id" not in db.layout[2]


@pytest.mark.asyncio
async def test_delete_by_uid_removes_one_card_by_type_removes_all(monkeypatch):
    db = _FakeDashboardDb(layout=[
        {"uid": "w1", "type": "weather", "x": 0, "y": 0, "w": 4, "h": 3},
        {"uid": "w2", "type": "weather", "x": 4, "y": 0, "w": 4, "h": 3},
        {"uid": "n1", "type": "notes", "x": 0, "y": 3, "w": 4, "h": 4},
    ])
    _patch_db(monkeypatch, db)

    await dashboard.remove_widget("w2", {"user_id": "u1"})
    assert [w["uid"] for w in db.layout] == ["w1", "n1"]

    await dashboard.remove_widget("notes", {"user_id": "u1"})
    assert [w["uid"] for w in db.layout] == ["w1"]


@pytest.mark.asyncio
async def test_delete_removes_a_legacy_card_by_the_key_get_returned(monkeypatch):
    """GET and DELETE each normalize the stored row; a legacy card's uid must agree between them."""
    from contextlib import asynccontextmanager

    legacy = [
        {"id": "weather", "x": 0, "y": 0, "w": 2, "h": 2},
        {"id": "weather", "x": 2, "y": 0, "w": 2, "h": 2},
    ]

    @asynccontextmanager
    async def fake_ctx():
        yield _GetDb(legacy)

    monkeypatch.setattr(dashboard, "get_db_ctx", fake_ctx)
    keys = [w["uid"] for w in (await dashboard.get_layout({"user_id": "u1"}))["layout"]]

    db = _FakeDashboardDb(layout=legacy)
    _patch_db(monkeypatch, db)
    await dashboard.remove_widget(keys[1], {"user_id": "u1"})

    assert len(db.layout) == 1, "only the card whose key was deleted goes"
    assert db.layout[0]["x"] == 0


@pytest.mark.asyncio
async def test_put_layout_with_a_stale_base_version_conflicts_and_returns_the_current_layout(monkeypatch):
    chat_added = [
        {"uid": "w1", "type": "weather", "x": 0, "y": 0, "w": 4, "h": 3},
        {"uid": "c1", "type": "notes", "x": 0, "y": 3, "w": 4, "h": 3},
    ]
    db = _FakeDashboardDb(layout=chat_added)
    _patch_db(monkeypatch, db)
    version = dashboard._layout_version(chat_added)
    board = [{"uid": "w1", "type": "weather", "x": 4, "y": 0, "w": 4, "h": 3}]

    with pytest.raises(HTTPException) as exc:
        await dashboard.save_layout(_Request({"layout": board, "base_version": "stale"}), {"user_id": "u1"})
    assert exc.value.status_code == 409
    assert exc.value.detail["version"] == version
    assert [w["uid"] for w in exc.value.detail["layout"]] == ["w1", "c1"]
    assert db.layout == chat_added, "a conflicting save writes nothing"

    result = await dashboard.save_layout(_Request({"layout": board, "base_version": version}), {"user_id": "u1"})
    assert result["version"] == dashboard._layout_version(board)
    assert db.layout == board
