import json
import sys
import types

import pytest

from routers.voice_tts import (
    _broadcast_weather_ui,
    _should_supersede_voice_weather_action,
)


def _row(action_type: str, payload: dict, key: str = "old-key") -> dict:
    return {
        "action_type": action_type,
        "payload": json.dumps(payload),
        "idempotency_key": key,
    }


def test_supersedes_old_voice_weather_actions() -> None:
    assert _should_supersede_voice_weather_action(
        _row("panel_navigate", {"url": "/touch/weather.html"}),
        "new-nav",
        "new-card",
    )
    assert _should_supersede_voice_weather_action(
        _row("show_card", {"type": "weather"}),
        "new-nav",
        "new-card",
    )


def test_preserves_current_weather_actions() -> None:
    assert not _should_supersede_voice_weather_action(
        _row("panel_navigate", {"url": "/touch/weather.html"}, key="new-nav"),
        "new-nav",
        "new-card",
    )
    assert not _should_supersede_voice_weather_action(
        _row("show_card", {"type": "weather"}, key="new-card"),
        "new-nav",
        "new-card",
    )


def test_ignores_non_weather_actions() -> None:
    assert not _should_supersede_voice_weather_action(
        _row("panel_navigate", {"url": "/touch/calendar.html"}),
        "new-nav",
        "new-card",
    )
    assert not _should_supersede_voice_weather_action(
        _row("show_card", {"type": "calendar"}),
        "new-nav",
        "new-card",
    )


class _Cursor:
    def __init__(self, *, row: dict | None = None, rows: list[dict] | None = None):
        self._row = row
        self._rows = rows or []

    async def fetchone(self):
        return self._row

    async def fetchall(self):
        return self._rows


class _Transaction:
    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        self._db.events.append("transaction_enter")
        return self._db

    async def __aexit__(self, exc_type, exc, tb):
        self._db.events.append("transaction_exit")
        return False


class _Db:
    def __init__(self):
        self.events: list[str] = []
        self.updated_ids: list[str] = []
        self.voice_rows = [
            {
                "id": "old-nav-null-key",
                "action_type": "panel_navigate",
                "payload": json.dumps({"url": "/touch/weather.html"}),
                "idempotency_key": None,
            },
            {
                "id": "old-card",
                "action_type": "show_card",
                "payload": json.dumps({"type": "weather"}),
                "idempotency_key": "old-card-key",
            },
            {
                "id": "current-nav",
                "action_type": "panel_navigate",
                "payload": json.dumps({"url": "/touch/weather.html"}),
                "idempotency_key": "voice_weather_nav_panel-1_turn-1",
            },
            {
                "id": "current-card",
                "action_type": "show_card",
                "payload": json.dumps({"type": "weather"}),
                "idempotency_key": "voice_weather_card_panel-1_turn-1",
            },
            {
                "id": "calendar",
                "action_type": "panel_navigate",
                "payload": json.dumps({"url": "/touch/calendar.html"}),
                "idempotency_key": "calendar-key",
            },
        ]

    def transaction(self):
        return _Transaction(self)

    async def execute(self, sql: str, params: tuple = ()):
        if "FROM ui_panel_sessions" in sql:
            return _Cursor(row={"user_id": "panel-user"})
        if "FROM ui_actions" in sql:
            return _Cursor(rows=self.voice_rows)
        if "UPDATE ui_actions" in sql:
            self.updated_ids.append(params[1])
            self.events.append(f"updated:{params[1]}")
            return _Cursor()
        raise AssertionError(f"unexpected SQL: {sql}")


@pytest.mark.asyncio
async def test_broadcast_weather_ui_supersedes_stale_rows_after_deduped_enqueue(
    monkeypatch,
) -> None:
    db = _Db()
    enqueue_calls = []
    broadcasts = []

    async def get_db():
        yield db

    async def enqueue_ui_action(_db, **kwargs):
        enqueue_calls.append(kwargs)
        db.events.append(f"enqueue:{kwargs['idempotency_key']}")
        return {
            "id": kwargs["idempotency_key"],
            "status": "queued",
            "deduped": True,
            "panel_id": kwargs["panel_id"],
        }

    class Broadcaster:
        async def broadcast(self, channel, event, payload):
            broadcasts.append((channel, event, payload))
            db.events.append(f"broadcast:{payload['action']['id']}")

    monkeypatch.setitem(sys.modules, "database", types.SimpleNamespace(get_db=get_db))
    monkeypatch.setitem(
        sys.modules,
        "ui_orchestrator",
        types.SimpleNamespace(enqueue_ui_action=enqueue_ui_action),
    )
    monkeypatch.setitem(
        sys.modules,
        "push",
        types.SimpleNamespace(broadcaster=Broadcaster()),
    )

    await _broadcast_weather_ui("panel-1", summary="Rain later", turn_key="turn-1")

    assert [call["idempotency_key"] for call in enqueue_calls] == [
        "voice_weather_nav_panel-1_turn-1",
        "voice_weather_card_panel-1_turn-1",
    ]
    assert all(call["commit"] is False for call in enqueue_calls)
    assert all(call["broadcast"] is False for call in enqueue_calls)
    assert db.updated_ids == ["old-nav-null-key", "old-card"]
    assert [item[2]["action"]["id"] for item in broadcasts] == [
        "voice_weather_nav_panel-1_turn-1",
        "voice_weather_card_panel-1_turn-1",
    ]
    assert db.events.index("transaction_exit") < db.events.index(
        "broadcast:voice_weather_nav_panel-1_turn-1"
    )
