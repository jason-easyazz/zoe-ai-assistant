import json
import sys
import types

import pytest

import routers.voice_tts as voice_tts
from routers.voice_tts import (
    _broadcast_skybridge_ui,
    _broadcast_weather_ui,
    _should_supersede_voice_weather_action,
    voice_command,
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
        self.executed: list[tuple[str, tuple]] = []
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

    async def commit(self):
        self.events.append("commit")

    async def execute(self, sql: str, params: tuple = ()):
        self.executed.append((sql, params))
        if "FROM ui_panel_sessions" in sql:
            return _Cursor(row={"user_id": "panel-user"})
        if "FROM ui_actions" in sql:
            return _Cursor(rows=self.voice_rows)
        if "UPDATE ui_actions" in sql:
            updated_id = params[1] if len(params) > 1 else params[0]
            self.updated_ids.append(updated_id)
            self.events.append(f"updated:{updated_id}")
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


@pytest.mark.asyncio
async def test_broadcast_skybridge_ui_opens_skybridge_with_card_payload(monkeypatch) -> None:
    db = _Db()
    enqueue_calls = []
    broadcasts = []

    async def get_db():
        yield db

    async def enqueue_ui_action(_db, **kwargs):
        enqueue_calls.append(kwargs)
        return {
            "action_id": f"db-{kwargs['action_type']}",
            "status": "queued",
            "panel_id": kwargs["panel_id"],
            "action_type": kwargs["action_type"],
            "payload": kwargs["payload"],
        }

    class Broadcaster:
        async def broadcast_to_panel(self, panel_id, event, payload):
            assert panel_id == "panel-1"
            broadcasts.append((panel_id, event, payload))
            return 1

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

    card = {
        "schema_version": "1.0.0",
        "card_type": "generic",
        "card_id": "11111111-1111-1111-1111-111111111111",
        "content": {"title": "Weather", "source": "weather_current"},
        "producer": "test",
        "producer_version": "1",
        "created_at": "2026-06-14T00:00:00Z",
    }
    result = {
        "handled": True,
        "spoken_summary": "Here is the weather.",
        "intent": {"domain": "weather", "action": "current"},
        "cards": [card],
    }

    await _broadcast_skybridge_ui("panel-1", result, utterance="show weather", turn_key="turn-1")

    assert [call["action_type"] for call in enqueue_calls] == ["panel_navigate", "show_card"]
    assert enqueue_calls[0]["payload"]["url"] == "/touch/skybridge.html?q=show+weather"
    assert enqueue_calls[0]["broadcast"] is False
    assert enqueue_calls[1]["payload"]["type"] == "skybridge"
    assert enqueue_calls[1]["payload"]["card"] == card
    assert enqueue_calls[1]["payload"]["result"] == result
    assert enqueue_calls[1]["broadcast"] is False
    assert [item[1] for item in broadcasts] == ["ui_action"]
    assert [item[2]["action_id"] for item in broadcasts] == ["db-panel_navigate"]
    assert [item[2]["action_type"] for item in broadcasts] == ["panel_navigate"]
    assert db.updated_ids == ["panel-1", "db-panel_navigate"]
    assert "commit" in db.events
    cleanup_sql = next(sql for sql, _params in db.executed if "payload::jsonb" in sql)
    assert "payload::jsonb->>'source' = 'voice:skybridge'" in cleanup_sql


@pytest.mark.asyncio
async def test_broadcast_skybridge_ui_skips_supersession_when_card_id_missing(monkeypatch) -> None:
    db = _Db()
    enqueue_calls = []
    broadcasts = []

    async def get_db():
        yield db

    async def enqueue_ui_action(_db, **kwargs):
        enqueue_calls.append(kwargs)
        if kwargs["action_type"] == "show_card":
            return {
                "status": "queued",
                "panel_id": kwargs["panel_id"],
                "action_type": kwargs["action_type"],
                "payload": kwargs["payload"],
            }
        return {
            "action_id": "db-panel_navigate",
            "status": "queued",
            "panel_id": kwargs["panel_id"],
            "action_type": kwargs["action_type"],
            "payload": kwargs["payload"],
        }

    class Broadcaster:
        async def broadcast_to_panel(self, panel_id, event, payload):
            broadcasts.append((panel_id, event, payload))
            return 1

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

    await _broadcast_skybridge_ui(
        "panel-1",
        {"handled": True, "spoken_summary": "Showing it.", "cards": []},
        utterance="show weather",
        turn_key="turn-no-card-id",
    )

    assert [call["action_type"] for call in enqueue_calls] == ["panel_navigate", "show_card"]
    assert [item[1] for item in broadcasts] == ["ui_action"]
    assert not any("payload::jsonb" in sql for sql, _params in db.executed)


@pytest.mark.asyncio
async def test_voice_command_uses_skybridge_fast_path(monkeypatch) -> None:
    calls: dict[str, object] = {"broadcast": [], "events": []}

    async def resolve_skybridge_request(text, user_id, *, context=None, db=None):
        calls["resolver"] = {
            "text": text,
            "user_id": user_id,
            "context": context,
            "db": db,
        }
        return {
            "handled": True,
            "spoken_summary": "It is sunny in Perth.",
            "intent": {"domain": "weather", "action": "current"},
            "skybridge_context": {"domain": "weather"},
            "cards": [
                {
                    "schema_version": "1.0.0",
                    "card_type": "generic",
                    "card_id": "22222222-2222-2222-2222-222222222222",
                    "content": {"title": "Weather", "source": "weather_current"},
                    "producer": "test",
                    "producer_version": "1",
                    "created_at": "2026-06-14T00:00:00Z",
                }
            ],
        }

    async def broadcast_skybridge_ui(panel_id, result, *, utterance="", turn_key=None):
        calls["broadcast_skybridge_ui"] = {
            "panel_id": panel_id,
            "result": result,
            "utterance": utterance,
            "turn_key": turn_key,
        }

    class Broadcaster:
        async def broadcast(self, channel, event, payload):
            calls["broadcast"].append((channel, event, payload))
            calls["events"].append(event)

    class Audio:
        body = b"RIFF-test"
        media_type = "audio/wav"

    async def synthesize(payload, caller=None):
        calls["synthesize"] = payload
        calls["events"].append("synthesize")
        return Audio()

    async def no_user(*_args, **_kwargs):
        return None

    async def save_chat(*args, **_kwargs):
        calls["save_chat"] = args

    async def memory_passes(*args, **_kwargs):
        calls["memory_passes"] = args

    skybridge_module = types.SimpleNamespace(resolve_skybridge_request=resolve_skybridge_request)
    monkeypatch.setitem(sys.modules, "skybridge_service", skybridge_module)
    monkeypatch.setitem(sys.modules, "push", types.SimpleNamespace(broadcaster=Broadcaster()))
    monkeypatch.setattr(voice_tts, "_broadcast_skybridge_ui", broadcast_skybridge_ui)
    monkeypatch.setattr(voice_tts, "synthesize", synthesize)
    monkeypatch.setattr(voice_tts, "_resolve_recent_panel_session_user", no_user)
    monkeypatch.setattr(voice_tts, "_resolve_panel_default_user", no_user)
    monkeypatch.setattr(voice_tts, "_schedule_voice_chat_save", save_chat)
    monkeypatch.setattr(voice_tts, "_run_voice_memory_passes", memory_passes)
    monkeypatch.setattr(voice_tts.asyncio, "ensure_future", lambda coro: coro.close() if hasattr(coro, "close") else None)
    monkeypatch.setattr(voice_tts, "_VOICE_SESSIONS", {})

    response = await voice_command(
        {"text": "show weather", "panel_id": "panel-sky", "session_id": "session-sky"},
        caller={"user_id": "guest", "panel_id": "panel-sky"},
        db=object(),
    )

    assert response["ok"] is True
    assert response["panel_id"] == "panel-sky"
    assert response["reply"] == "It is sunny in Perth."
    assert response["intent"] == "skybridge:weather"
    assert response["skybridge"] is True
    assert response["audio_base64"]
    assert calls["resolver"]["text"] == "show weather"
    assert calls["resolver"]["user_id"] == "guest"
    assert calls["broadcast_skybridge_ui"]["panel_id"] == "panel-sky"
    assert calls["broadcast_skybridge_ui"]["utterance"] == "show weather"
    assert calls["synthesize"] == {"text": "It is sunny in Perth."}
    assert ("all", "voice:responding", {"panel_id": "panel-sky", "text": "It is sunny in Perth."}) in calls["broadcast"]
    assert ("all", "voice:done", {"panel_id": "panel-sky"}) in calls["broadcast"]
    assert calls["events"].index("synthesize") < calls["events"].index("voice:done")
    assert voice_tts._VOICE_SESSIONS["panel-sky"]["skybridge_context"] == {"domain": "weather"}
