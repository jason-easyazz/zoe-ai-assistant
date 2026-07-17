from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from models import ListItemCreate, ListItemUpdate
from routers import lists, notifications, openclaw, stubs

pytestmark = pytest.mark.ci_safe


class FakeCursor:
    def __init__(self, rows: list[dict[str, Any]] | None = None, rowcount: int | None = None):
        self._rows = rows or []
        self.rowcount = len(self._rows) if rowcount is None else rowcount

    async def fetchone(self):
        return self._rows[0] if self._rows else None

    async def fetchall(self):
        return self._rows


class ListDB:
    def __init__(self, list_owner: str, list_visibility: str = "family"):
        self.list_owner = list_owner
        self.list_visibility = list_visibility
        self.calls: list[tuple[str, list[Any]]] = []
        self.commits = 0
        self.item_text = "milk"

    async def execute(self, sql: str, params=()):
        params_list = list(params or [])
        self.calls.append((sql, params_list))
        normalized = " ".join(sql.split())

        if normalized.startswith("SELECT id, user_id"):
            actor_id = params_list[2]
            if self.list_visibility != "family" and actor_id != self.list_owner:
                return FakeCursor()
            return FakeCursor(
                [
                    {
                        "id": params_list[0],
                        "user_id": self.list_owner,
                        "visibility": self.list_visibility,
                    }
                ]
            )
        if normalized.startswith("SELECT id FROM list_items"):
            return FakeCursor([{"id": params_list[0]}])
        if normalized.startswith("INSERT INTO list_items"):
            return FakeCursor()
        if normalized.startswith("UPDATE list_items"):
            if params_list and isinstance(params_list[0], str):
                self.item_text = params_list[0]
            return FakeCursor(rowcount=1)
        if normalized.startswith("SELECT id, list_id, text"):
            return FakeCursor(
                [
                    {
                        "id": params_list[0],
                        "list_id": "list-1",
                        "text": self.item_text,
                        "completed": 0,
                        "priority": "normal",
                        "category": "",
                        "quantity": "",
                        "sort_order": 0,
                        "parent_id": None,
                        "assigned_to": None,
                        "created_at": None,
                        "updated_at": None,
                    }
                ]
            )
        return FakeCursor()

    async def commit(self):
        self.commits += 1


class NotificationDB:
    def __init__(self):
        self.calls: list[tuple[str, list[Any]]] = []
        self.commits = 0

    async def execute(self, sql: str, params=()):
        params_list = list(params or [])
        self.calls.append((sql, params_list))
        normalized = " ".join(sql.split())
        if normalized.startswith("SELECT id FROM notifications"):
            return FakeCursor([{"id": params_list[0]}])
        return FakeCursor(rowcount=1)

    async def commit(self):
        self.commits += 1


class BrokenSettingsDB:
    async def execute_fetchall(self, sql: str, params=()):
        raise RuntimeError("database unavailable")


async def _allow_feature(*args, **kwargs):
    return None


@pytest.mark.parametrize("operation", ["add", "update", "delete"])
@pytest.mark.asyncio
async def test_guest_cannot_mutate_family_visible_list_items(monkeypatch, operation):
    monkeypatch.setattr(lists, "require_feature_access", _allow_feature)
    db = ListDB(list_owner="owner")
    user = {"user_id": "guest", "role": "guest"}

    with pytest.raises(HTTPException) as excinfo:
        if operation == "add":
            await lists.add_item(
                "shopping",
                "list-1",
                ListItemCreate(text="milk"),
                user=user,
                db=db,
            )
        elif operation == "update":
            await lists.update_item(
                "shopping",
                "list-1",
                "item-1",
                ListItemUpdate(text="eggs"),
                user=user,
                db=db,
            )
        else:
            await lists.delete_item(
                "shopping",
                "list-1",
                "item-1",
                user=user,
                db=db,
            )

    assert excinfo.value.status_code == 403
    assert not any("INSERT INTO list_items" in sql for sql, _ in db.calls)
    assert not any("UPDATE list_items" in sql for sql, _ in db.calls)
    assert db.commits == 0


@pytest.mark.asyncio
async def test_family_member_can_add_item_to_family_visible_list(monkeypatch):
    monkeypatch.setattr(lists, "require_feature_access", _allow_feature)

    async def fake_broadcast(*args, **kwargs):
        return 1

    monkeypatch.setattr(lists.broadcaster, "broadcast", fake_broadcast)
    db = ListDB(list_owner="owner")
    user = {"user_id": "household-member", "role": "member"}

    result = await lists.add_item(
        "shopping",
        "list-1",
        ListItemCreate(text="milk"),
        user=user,
        db=db,
    )

    assert result["text"] == "milk"
    assert any("INSERT INTO list_items" in sql for sql, _ in db.calls)
    assert db.commits == 1


@pytest.mark.asyncio
async def test_owner_can_still_update_item_in_list(monkeypatch):
    monkeypatch.setattr(lists, "require_feature_access", _allow_feature)

    async def fake_broadcast(*args, **kwargs):
        return 1

    monkeypatch.setattr(lists.broadcaster, "broadcast", fake_broadcast)
    db = ListDB(list_owner="owner")
    user = {"user_id": "owner", "role": "member"}

    result = await lists.update_item(
        "shopping",
        "list-1",
        "item-1",
        ListItemUpdate(text="eggs"),
        user=user,
        db=db,
    )

    assert result["text"] == "eggs"
    assert any("UPDATE list_items" in sql for sql, _ in db.calls)
    assert db.commits == 1


@pytest.mark.asyncio
async def test_non_owner_cannot_mutate_personal_list_items(monkeypatch):
    monkeypatch.setattr(lists, "require_feature_access", _allow_feature)
    db = ListDB(list_owner="owner", list_visibility="personal")
    user = {"user_id": "household-member", "role": "member"}

    with pytest.raises(HTTPException) as excinfo:
        await lists.add_item(
            "shopping",
            "list-1",
            ListItemCreate(text="milk"),
            user=user,
            db=db,
        )

    assert excinfo.value.status_code == 404
    assert not any("INSERT INTO list_items" in sql for sql, _ in db.calls)
    assert db.commits == 0


def _notification_client(db: NotificationDB) -> TestClient:
    app = FastAPI()
    app.include_router(notifications.router)

    async def fake_get_db():
        yield db

    app.dependency_overrides[notifications.get_current_user] = lambda: {
        "user_id": "u-1",
        "role": "member",
    }
    app.dependency_overrides[notifications.get_db] = fake_get_db
    return TestClient(app)


@pytest.mark.asyncio
async def test_notification_create_broadcasts_only_to_target_user(monkeypatch):
    monkeypatch.setattr(notifications, "require_feature_access", _allow_feature)
    broadcasts = []

    async def fake_broadcast(channel, event_type, payload, **kwargs):
        broadcasts.append((channel, event_type, payload, kwargs))
        return 1

    monkeypatch.setattr(notifications.broadcaster, "broadcast", fake_broadcast)
    db = NotificationDB()
    user = {"user_id": "u-target", "role": "member"}

    await notifications.create_notification(
        {"type": "info", "title": "Private", "message": "For one user"},
        user=user,
        db=db,
    )

    assert broadcasts == [
        (
            "all",
            "notification_created",
            {
                "id": broadcasts[0][2]["id"],
                "type": "info",
                "title": "Private",
                "message": "For one user",
                "delivered": False,
            },
            {"user_id": "u-target"},
        )
    ]


def test_track_interaction_accepts_query_action_without_body(monkeypatch):
    monkeypatch.setattr(notifications, "require_feature_access", _allow_feature)
    db = NotificationDB()

    response = _notification_client(db).post("/api/notifications/notif-1/interaction?action=click")

    assert response.status_code == 200
    update_calls = [call for call in db.calls if "UPDATE notifications SET delivered = 1" in call[0]]
    assert update_calls
    assert update_calls[0][1][0] == "click"


def test_track_interaction_accepts_json_body_action(monkeypatch):
    monkeypatch.setattr(notifications, "require_feature_access", _allow_feature)
    db = NotificationDB()

    response = _notification_client(db).post(
        "/api/notifications/notif-1/interaction",
        json={"action": "snooze"},
    )

    assert response.status_code == 200
    update_calls = [call for call in db.calls if "UPDATE notifications SET delivered = 1" in call[0]]
    assert update_calls
    assert update_calls[0][1][0] == "snooze"


def test_non_admin_rejected_from_telegram_setup(monkeypatch):
    app = FastAPI()
    app.include_router(openclaw.router)
    app.dependency_overrides[openclaw.get_current_user] = lambda: {
        "user_id": "u-member",
        "role": "member",
    }

    response = TestClient(app).post(
        "/api/openclaw/telegram/setup",
        json={"bot_token": "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Admin access required"


@pytest.mark.asyncio
async def test_track_interaction_records_body_action(monkeypatch):
    monkeypatch.setattr(notifications, "require_feature_access", _allow_feature)
    db = NotificationDB()
    user = {"user_id": "u-1", "role": "member"}

    await notifications.track_interaction(
        "notif-1",
        body=notifications.NotificationInteraction(action="click"),
        user=user,
        db=db,
    )

    update_calls = [call for call in db.calls if "UPDATE notifications SET delivered = 1" in call[0]]
    assert update_calls
    assert update_calls[0][1][0] == "click"


@pytest.mark.asyncio
async def test_settings_db_error_surfaces_flat_degraded_response(monkeypatch):
    monkeypatch.setenv("ZOE_HA_URL", "http://ha.local")
    result = await stubs.get_settings(
        user={"user_id": "u-1", "role": "member"},
        db=BrokenSettingsDB(),
    )

    assert result["_status"] == "degraded"
    assert result["_error"] == "settings_storage_unavailable"
    assert result["homeassistant_url"] == "http://ha.local"
    assert "settings" not in result


@pytest.mark.asyncio
async def test_intelligence_settings_db_error_surfaces_degraded_response():
    result = await stubs.get_intelligence_settings(
        user={"user_id": "u-1", "role": "member"},
        db=BrokenSettingsDB(),
    )

    assert result["status"] == "degraded"
    assert result["error"] == "settings_storage_unavailable"
    assert result["settings"] == dict(stubs._INTELLIGENCE_DEFAULTS)


class _BrokenWriteDB:
    async def execute(self, sql: str, params=()):
        raise RuntimeError("database unavailable: relation does not exist")

    async def commit(self):  # pragma: no cover — never reached
        pass


class _FakeJSONRequest:
    def __init__(self, payload: dict):
        self._payload = payload

    async def json(self):
        return self._payload


@pytest.mark.asyncio
async def test_intelligence_settings_write_failure_is_a_500_not_a_200():
    """A failed settings WRITE must never answer 200.

    The panel does `if (res.ok) alert('Saved')`, so the old
    200-with-{"status": "error"} body told the user their settings had saved
    when nothing was written. A write either persisted or it didn't — the read
    path's "degraded" shape has no honest meaning here.
    """
    with pytest.raises(HTTPException) as excinfo:
        await stubs.save_intelligence_settings(
            request=_FakeJSONRequest({"learning_enabled": True}),
            user={"user_id": "u-1", "role": "member"},
            db=_BrokenWriteDB(),
        )

    assert excinfo.value.status_code == 500
    # The raw exception must not reach the client: it can carry DB schema and
    # connection detail, and any signed-in user can call this endpoint.
    assert "relation does not exist" not in str(excinfo.value.detail)
