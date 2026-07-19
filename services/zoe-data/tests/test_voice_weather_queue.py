import json
import sys
import types

import pytest

import voice_presence
import skybridge_service
import routers.voice_tts as voice_tts
from routers.voice_tts import (
    _broadcast_skybridge_ui,
    _broadcast_weather_ui,
    _should_supersede_voice_skybridge_action,
    _should_supersede_voice_weather_action,
    voice_command,
)


@pytest.fixture(autouse=True)
def _reset_voice_presence_state(monkeypatch):
    monkeypatch.setattr(voice_presence, "_AUDIO_CACHE", {})
    monkeypatch.setattr(voice_presence, "_VARIANT_CURSOR", 0)
    monkeypatch.setattr(voice_presence, "_PROCESSING_ACK_CURSOR", 0)


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
    # Estate-repointed nav (home.html?domain=weather) is superseded too.
    assert _should_supersede_voice_weather_action(
        _row("panel_navigate", {"url": "/touch/home.html?domain=weather&say=Rain"}),
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


def test_supersedes_stale_voice_actions_for_skybridge_surface() -> None:
    assert _should_supersede_voice_skybridge_action(
        _row("panel_navigate", {"url": "/touch/calendar.html"}),
        "new-nav",
        "new-card",
    )
    assert _should_supersede_voice_skybridge_action(
        _row("panel_navigate", {"url": "/touch/weather.html?panel_id=panel-1"}),
        "new-nav",
        "new-card",
    )
    assert _should_supersede_voice_skybridge_action(
        _row("show_card", {"type": "calendar"}),
        "new-nav",
        "new-card",
    )
    assert _should_supersede_voice_skybridge_action(
        _row("show_card", {"source": "voice:skybridge"}),
        "new-nav",
        "new-card",
    )
    # Estate-repointed per-domain navs (home.html?domain=<domain>) are superseded.
    assert _should_supersede_voice_skybridge_action(
        _row("panel_navigate", {"url": "/touch/home.html?domain=calendar&say=x"}),
        "new-nav",
        "new-card",
    )
    # A bare estate-home nav (no migrated domain) is left alone.
    assert not _should_supersede_voice_skybridge_action(
        _row("panel_navigate", {"url": "/touch/home.html"}),
        "new-nav",
        "new-card",
    )
    assert not _should_supersede_voice_skybridge_action(
        _row("panel_navigate", {"url": "/touch/home.html"}, key="new-nav"),
        "new-nav",
        "new-card",
    )


@pytest.mark.asyncio
async def test_request_auth_ui_includes_skybridge_card_metadata(monkeypatch):
    calls = {"broadcast": None, "enqueue": None}

    class Cursor:
        async def fetchone(self):
            return {"user_id": "family-admin"}

    class Db:
        async def execute(self, *_args, **_kwargs):
            return Cursor()

    async def get_db():
        yield Db()

    async def enqueue_ui_action(_db, **kwargs):
        calls["enqueue"] = kwargs
        return {"action_id": "auth-action"}

    async def broadcast(channel, event_type, payload):
        calls["broadcast"] = {"channel": channel, "event_type": event_type, "payload": payload}
        return True

    monkeypatch.setitem(sys.modules, "database", types.SimpleNamespace(get_db=get_db))
    monkeypatch.setitem(sys.modules, "ui_orchestrator", types.SimpleNamespace(enqueue_ui_action=enqueue_ui_action))
    monkeypatch.setitem(sys.modules, "push", types.SimpleNamespace(broadcaster=types.SimpleNamespace(broadcast=broadcast)))

    delivered = await voice_tts._request_auth_ui(
        panel_id="panel-auth",
        challenge_id="challenge-123",
        reason="Private list request",
    )

    assert delivered is True
    payload = calls["broadcast"]["payload"]["action"]["payload"]
    assert payload["panel_id"] == "panel-auth"
    assert payload["challenge_id"] == "challenge-123"
    assert payload["action_context"] == "Private list request"
    assert payload["title"] == "Confirm it is you"
    assert payload["message"] == "Zoe needs to know who is speaking before showing or changing personal data."
    assert payload["domain"] == "Private data"
    assert payload["intent_action"] == "continue"
    assert payload["cta"] == "Continue"
    assert payload["summary"] == "Please authenticate on the touch panel to continue."
    assert calls["enqueue"]["action_type"] == "panel_request_auth"
    assert calls["enqueue"]["payload"] == payload


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


class _FakeBgTask:
    """Task-shaped stub: voice_tts._bg_task calls add_done_callback on the
    ensure_future result, so the fake must not return None."""

    def add_done_callback(self, _cb):
        return None


def _close_coro_ensure_future(coro):
    if hasattr(coro, "close"):
        coro.close()
    return _FakeBgTask()


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
    # Voice lands on the ESTATE home with display-only params — the estate
    # shows heard/summary and opens the domain screen without re-executing.
    assert enqueue_calls[0]["payload"]["url"] == (
        "/touch/home.html?heard=show+weather&say=Here+is+the+weather.&domain=weather"
    )
    assert enqueue_calls[0]["broadcast"] is False
    assert {call["user_id"] for call in enqueue_calls} == {"panel-user"}
    cleanup_selects = [
        params
        for sql, params in db.executed
        if "FROM ui_actions" in sql and "user_id = ?" in sql
    ]
    assert cleanup_selects == [("panel-1", "panel-user")]
    assert enqueue_calls[1]["payload"]["type"] == "skybridge"
    assert enqueue_calls[1]["payload"]["card"] == card
    assert enqueue_calls[1]["payload"]["result"] == result
    assert enqueue_calls[1]["broadcast"] is False
    assert [item[1] for item in broadcasts] == ["ui_action"]
    assert [item[2]["action_id"] for item in broadcasts] == ["db-panel_navigate"]
    assert [item[2]["action_type"] for item in broadcasts] == ["panel_navigate"]
    assert db.updated_ids == [
        "old-nav-null-key",
        "old-card",
        "current-nav",
        "current-card",
        "calendar",
        "db-panel_navigate",
    ]
    assert "commit" in db.events
    assert not any("payload::jsonb" in sql for sql, _params in db.executed)


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
    assert "calendar" in db.updated_ids
    assert not any("payload::jsonb" in sql for sql, _params in db.executed)


@pytest.mark.parametrize(
    ("utterance", "resolver_text", "domain", "reply"),
    [
        ("show weather", "show weather", "weather", "It is sunny in Perth."),
        ("show whether", "show weather", "weather", "It is sunny in Perth."),
        ("show my calendar", "show my calendar", "calendar", "Here is your calendar."),
    ],
)
@pytest.mark.asyncio
async def test_voice_command_uses_skybridge_fast_path(monkeypatch, utterance, resolver_text, domain, reply) -> None:
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
            "spoken_summary": reply,
            "intent": {"domain": domain, "action": "current"},
            "skybridge_context": {"domain": domain},
            "cards": [
                {
                    "schema_version": "1.0.0",
                    "card_type": "generic",
                    "card_id": "22222222-2222-2222-2222-222222222222",
                    "content": {"title": domain.title(), "source": f"{domain}_current"},
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
    monkeypatch.setattr(skybridge_service, "resolve_skybridge_request", resolve_skybridge_request)
    monkeypatch.setitem(
        sys.modules,
        "guest_policy",
        types.SimpleNamespace(record_policy_decision=lambda *_args, **_kwargs: None),
    )
    monkeypatch.setitem(sys.modules, "push", types.SimpleNamespace(broadcaster=Broadcaster()))
    monkeypatch.setattr(voice_tts, "_broadcast_skybridge_ui", broadcast_skybridge_ui)
    monkeypatch.setattr(voice_tts, "synthesize", synthesize)
    monkeypatch.setattr(voice_tts, "_resolve_recent_panel_session_user", no_user)
    monkeypatch.setattr(voice_tts, "_resolve_panel_default_user", no_user)
    monkeypatch.setattr(voice_tts, "_schedule_voice_chat_save", save_chat)
    monkeypatch.setattr(voice_tts, "_run_voice_memory_passes", memory_passes)
    monkeypatch.setattr(voice_tts.asyncio, "ensure_future", _close_coro_ensure_future)
    monkeypatch.setattr(voice_tts, "_VOICE_SESSIONS", {})

    response = await voice_command(
        {"text": utterance, "panel_id": "panel-sky", "session_id": "session-sky"},
        caller={"user_id": "guest", "panel_id": "panel-sky"},
        db=object(),
        stream=False,
    )

    assert response["ok"] is True
    assert response["panel_id"] == "panel-sky"
    assert response["reply"] == reply
    assert response["intent"] == f"skybridge:{domain}"
    assert response["skybridge"] is True
    assert response["audio_base64"]
    assert calls["resolver"]["text"] == resolver_text
    assert calls["resolver"]["user_id"] == "guest"
    assert calls["broadcast_skybridge_ui"]["panel_id"] == "panel-sky"
    assert calls["broadcast_skybridge_ui"]["utterance"] == resolver_text
    assert calls["synthesize"] == {"text": reply}
    assert ("all", "voice:responding", {"panel_id": "panel-sky", "text": reply}) in calls["broadcast"]
    assert ("all", "voice:done", {"panel_id": "panel-sky"}) in calls["broadcast"]
    assert calls["events"].index("synthesize") < calls["events"].index("voice:done")
    assert voice_tts._VOICE_SESSIONS["panel-sky"]["skybridge_context"] == {"domain": domain}


@pytest.mark.asyncio
async def test_voice_command_emits_processing_cue_for_non_pi_fallback(monkeypatch) -> None:
    import pi_hybrid_production

    calls: dict[str, object] = {"broadcast": [], "events": []}

    async def resolve_skybridge_request(text, user_id, *, context=None, db=None):
        return {
            "handled": True,
            "spoken_summary": "Here is the calendar.",
            "intent": {"domain": "calendar", "action": "current"},
            "skybridge_context": {"domain": "calendar"},
            "cards": [],
        }

    async def broadcast_skybridge_ui(*_args, **_kwargs):
        return None

    class Broadcaster:
        async def broadcast(self, channel, event, payload):
            calls["broadcast"].append((channel, event, payload))
            calls["events"].append(event)

    class Audio:
        body = b"RIFF-test"
        media_type = "audio/wav"

    async def synthesize(payload, caller=None):
        calls["events"].append("synthesize")
        return Audio()

    async def no_user(*_args, **_kwargs):
        return None

    async def fake_processing_cue(**_kwargs):
        return {"available": True, "text": "One moment."}

    async def save_chat(*_args, **_kwargs):
        return None

    async def memory_passes(*_args, **_kwargs):
        return None

    ineligible_config = types.SimpleNamespace(enabled=True)
    monkeypatch.setattr(
        pi_hybrid_production.PiHybridProductionConfig,
        "from_env",
        classmethod(lambda cls: ineligible_config),
    )
    def fake_pi_eligible(text, config=None):
        assert config is ineligible_config
        return False, "not_low_risk"

    monkeypatch.setattr(pi_hybrid_production, "pi_hybrid_production_eligible", fake_pi_eligible)
    monkeypatch.setattr(pi_hybrid_production, "processing_cue_packet", fake_processing_cue)
    skybridge_module = types.SimpleNamespace(resolve_skybridge_request=resolve_skybridge_request)
    monkeypatch.setitem(sys.modules, "skybridge_service", skybridge_module)
    monkeypatch.setattr(skybridge_service, "resolve_skybridge_request", resolve_skybridge_request)
    monkeypatch.setitem(sys.modules, "push", types.SimpleNamespace(broadcaster=Broadcaster()))
    monkeypatch.setattr(voice_tts, "_broadcast_skybridge_ui", broadcast_skybridge_ui)
    monkeypatch.setattr(voice_tts, "synthesize", synthesize)
    monkeypatch.setattr(voice_tts, "_resolve_recent_panel_session_user", no_user)
    monkeypatch.setattr(voice_tts, "_resolve_panel_default_user", no_user)
    monkeypatch.setattr(voice_tts, "_schedule_voice_chat_save", save_chat)
    monkeypatch.setattr(voice_tts, "_run_voice_memory_passes", memory_passes)
    monkeypatch.setattr(voice_tts.asyncio, "ensure_future", _close_coro_ensure_future)
    monkeypatch.setattr(voice_tts, "_VOICE_SESSIONS", {})

    response = await voice_command(
        {"text": "show my calendar", "panel_id": "panel-sky", "session_id": "session-sky"},
        caller={"user_id": "guest", "panel_id": "panel-sky"},
        stream=False,
        db=object(),
    )

    assert response["ok"] is True
    assert response["reply"] == "Here is the calendar."
    assert (
        "all",
        "voice:responding",
        {
            "panel_id": "panel-sky",
            "text": "One moment.",
            "processing_ack": True,
            "source": "voice_command_fallback",
        },
    ) in calls["broadcast"]
    assert calls["events"].index("voice:responding") < calls["events"].index("synthesize")


@pytest.mark.asyncio
async def test_voice_skybridge_private_request_challenges_guest_before_cards(monkeypatch) -> None:
    calls: dict[str, object] = {"broadcast_called": False, "challenge_called": False}

    async def resolve_skybridge_request(text, user_id, *, context=None, db=None):
        calls["resolver"] = {"text": text, "user_id": user_id, "context": context, "db": db}
        return {
            "handled": True,
            "auth_required": True,
            "spoken_summary": "Please authenticate on the touch panel to continue.",
            "intent": {"domain": "lists", "action": "show", "list_type": "shopping"},
            "cards": [
                {
                    "component": "status",
                    "props": {"title": "Sign in to view lists", "status": "Authentication"},
                }
            ],
        }

    async def broadcast_skybridge_ui(*_args, **_kwargs):
        calls["broadcast_called"] = True

    async def request_voice_identity_challenge(**kwargs):
        calls["challenge_called"] = True
        calls["challenge"] = kwargs
        return {
            "ok": True,
            "panel_id": kwargs["panel_id"],
            "reply": "Please authenticate on the touch panel.",
            "status": "awaiting_pin",
            "audio_base64": "UklGRg==",
            "content_type": "audio/wav",
        }

    class Broadcaster:
        async def broadcast(self, *_args, **_kwargs):
            return True

    async def no_user(*_args, **_kwargs):
        return None

    async def default_user(*_args, **_kwargs):
        return "panel-default-user"

    skybridge_module = types.SimpleNamespace(resolve_skybridge_request=resolve_skybridge_request)
    monkeypatch.setitem(sys.modules, "skybridge_service", skybridge_module)
    monkeypatch.setattr(skybridge_service, "resolve_skybridge_request", resolve_skybridge_request)
    monkeypatch.setitem(sys.modules, "push", types.SimpleNamespace(broadcaster=Broadcaster()))
    monkeypatch.setattr(voice_tts, "_broadcast_skybridge_ui", broadcast_skybridge_ui)
    monkeypatch.setattr(voice_tts, "_request_voice_identity_challenge", request_voice_identity_challenge)
    monkeypatch.setattr(voice_tts, "_resolve_recent_panel_session_user", no_user)
    monkeypatch.setattr(voice_tts, "_resolve_panel_default_user", default_user)
    monkeypatch.setattr(voice_tts, "_VOICE_SESSIONS", {})

    response = await voice_command(
        {"text": "what's on my shopping list", "panel_id": "panel-auth", "session_id": "session-auth"},
        caller={"user_id": "guest", "panel_id": "panel-auth"},
        db=object(),
    )

    assert response["ok"] is True
    assert response["status"] == "awaiting_pin"
    assert calls["resolver"]["user_id"] == "guest"
    assert calls["challenge_called"] is True
    assert calls["challenge"]["text"] == "what's on my shopping list"
    assert calls["challenge"]["session_id"] == "session-auth"
    assert calls["broadcast_called"] is False


@pytest.mark.asyncio
async def test_voice_skybridge_auth_challenge_failure_does_not_fall_through(monkeypatch) -> None:
    calls: dict[str, object] = {"broadcast_called": False}

    async def resolve_skybridge_request(text, user_id, *, context=None, db=None):
        return {
            "handled": True,
            "auth_required": True,
            "spoken_summary": "Please authenticate on the touch panel to continue.",
            "intent": {"domain": "lists", "action": "show", "list_type": "shopping"},
            "cards": [],
        }

    async def broadcast_skybridge_ui(*_args, **_kwargs):
        calls["broadcast_called"] = True

    async def request_voice_identity_challenge(**_kwargs):
        raise RuntimeError("panel auth unavailable")

    class Broadcaster:
        async def broadcast(self, *_args, **_kwargs):
            return True

    class Audio:
        body = b"RIFF-test"
        media_type = "audio/wav"

    async def synthesize(payload, caller=None):
        calls["synthesize"] = payload
        return Audio()

    async def no_user(*_args, **_kwargs):
        return None

    skybridge_module = types.SimpleNamespace(resolve_skybridge_request=resolve_skybridge_request)
    monkeypatch.setitem(sys.modules, "skybridge_service", skybridge_module)
    monkeypatch.setattr(skybridge_service, "resolve_skybridge_request", resolve_skybridge_request)
    monkeypatch.setitem(
        sys.modules,
        "guest_policy",
        types.SimpleNamespace(record_policy_decision=lambda *_args, **_kwargs: None),
    )
    monkeypatch.setitem(sys.modules, "push", types.SimpleNamespace(broadcaster=Broadcaster()))
    monkeypatch.setattr(voice_tts, "_broadcast_skybridge_ui", broadcast_skybridge_ui)
    monkeypatch.setattr(voice_tts, "_request_voice_identity_challenge", request_voice_identity_challenge)
    monkeypatch.setattr(voice_tts, "synthesize", synthesize)
    monkeypatch.setattr(voice_tts, "_resolve_recent_panel_session_user", no_user)
    monkeypatch.setattr(voice_tts, "_resolve_panel_default_user", no_user)
    monkeypatch.setattr(voice_tts, "_VOICE_SESSIONS", {})

    response = await voice_command(
        {"text": "what's on my shopping list", "panel_id": "panel-auth", "session_id": "session-auth"},
        caller={"user_id": "guest", "panel_id": "panel-auth"},
        db=object(),
    )

    assert response["ok"] is True
    assert response["status"] == "auth_unavailable"
    assert "Authentication is required" in response["reply"]
    assert response["audio_base64"]
    assert calls["broadcast_called"] is False


@pytest.mark.asyncio
async def test_voice_identity_challenge_survives_tts_failure(monkeypatch) -> None:
    calls: dict[str, object] = {}

    async def synthesize(_payload, caller=None):
        raise RuntimeError("tts unavailable")

    async def create_pin_challenge_internal(**kwargs):
        calls["challenge"] = kwargs
        return {"challenge_id": "challenge-1"}

    async def get_db():
        yield object()

    async def request_auth_ui(**kwargs):
        calls["request_auth"] = kwargs
        return True

    class Metric:
        def labels(self, **_kwargs):
            return self

        def inc(self):
            calls["metric"] = True

    monkeypatch.setattr(voice_tts, "synthesize", synthesize)
    monkeypatch.setattr(voice_tts, "_request_auth_ui", request_auth_ui)
    monkeypatch.setitem(
        sys.modules,
        "routers.panel_auth",
        types.SimpleNamespace(create_pin_challenge_internal=create_pin_challenge_internal),
    )
    monkeypatch.setitem(sys.modules, "database", types.SimpleNamespace(get_db=get_db))
    monkeypatch.setitem(sys.modules, "voice_metrics", types.SimpleNamespace(voice_turn_count=Metric()))
    monkeypatch.setitem(
        sys.modules,
        "push",
        types.SimpleNamespace(broadcaster=types.SimpleNamespace(broadcast=lambda *_args, **_kwargs: None)),
    )
    monkeypatch.setattr(voice_tts, "_PENDING_VOICE_IDENT", {})

    response = await voice_tts._request_voice_identity_challenge(
        panel_id="panel-auth",
        text="show my calendar",
        session_id="session-auth",
        caller={"user_id": "guest"},
    )

    assert response["status"] == "awaiting_pin"
    assert response["audio_base64"] is None
    assert response["content_type"] == "audio/wav"
    assert calls["challenge"]["panel_id"] == "panel-auth"
    assert calls["request_auth"]["challenge_id"] == "challenge-1"
    assert voice_tts._PENDING_VOICE_IDENT["panel-auth"]["transcript"] == "show my calendar"


@pytest.mark.asyncio
async def test_voice_command_stream_emits_processing_ack_before_slow_response(monkeypatch) -> None:
    calls: dict[str, object] = {"broadcast": []}

    async def no_user(*_args, **_kwargs):
        return None

    async def panel_user(*_args, **_kwargs):
        return "panel-user"

    async def resolve_skybridge_request(*_args, **_kwargs):
        return None

    class Broadcaster:
        async def broadcast(self, channel, event, payload):
            calls["broadcast"].append((channel, event, payload))

    async def load_voice_history(*_args, **_kwargs):
        return []

    async def run_zoe_agent_streaming(*_args, **_kwargs):
        yield "The answer is ready."

    async def synthesize_sentence(*_args, **_kwargs):
        return b"RIFF-final"

    monkeypatch.setenv("ZOE_PROCESSING_ACK_PHRASE", "Let me check.")
    monkeypatch.setattr(voice_tts, "_resolve_recent_panel_session_user", panel_user)
    monkeypatch.setattr(voice_tts, "_resolve_panel_default_user", no_user)
    monkeypatch.setattr(voice_tts, "_load_voice_history", load_voice_history)
    monkeypatch.setattr(voice_tts, "_synthesize_kokoro_sidecar", synthesize_sentence)
    monkeypatch.setattr(voice_tts, "_VOICE_SESSIONS", {})
    monkeypatch.setitem(sys.modules, "push", types.SimpleNamespace(broadcaster=Broadcaster()))
    monkeypatch.setitem(
        sys.modules,
        "skybridge_service",
        types.SimpleNamespace(resolve_skybridge_request=resolve_skybridge_request),
    )
    # Pipeline behaviour is brain-agnostic; pin the legacy brain so the dispatch
    # routes to the mocked zoe_agent (default is now zoe-core).
    monkeypatch.setenv("ZOE_USE_CORE_BRAIN", "false")
    monkeypatch.setitem(sys.modules, "zoe_agent", types.SimpleNamespace(run_zoe_agent_streaming=run_zoe_agent_streaming))
    monkeypatch.setattr(skybridge_service, "resolve_skybridge_request", resolve_skybridge_request)

    response = await voice_command(
        {"text": "could you think about this for me", "panel_id": "panel-stream", "session_id": "session-stream"},
        caller={"user_id": "guest", "panel_id": "panel-stream"},
        stream=True,
        db=object(),
    )

    chunks = [chunk async for chunk in response.body_iterator]
    first_line = json.loads(chunks[0].decode())
    final_header = json.loads(chunks[1].decode())
    done = json.loads(chunks[-1].decode())

    assert first_line == {"processing_ack": True, "text": "Let me check.", "panel_id": "panel-stream"}
    assert (
        "all",
        "voice:responding",
        {"panel_id": "panel-stream", "text": "Let me check.", "processing_ack": True},
    ) in calls["broadcast"]
    assert final_header["text"] == "The answer is ready."
    assert done["done"] is True
    assert done["reply"] == "The answer is ready."


@pytest.mark.parametrize(
    ("utterance", "intent", "intent_group", "agreement_kind", "response_text"),
    [
        ("will it rain later", "weather", "weather", "intent_buffer_hint", "It is 18.5 C and clear."),
        (
            "what is my day looking like",
            "daily_briefing",
            "daily_briefing",
            "intent_buffer_hint",
            "Here's your day:\n\nNo events on the calendar today.",
        ),
    ],
)
@pytest.mark.asyncio
async def test_voice_command_uses_pi_hybrid_production_with_processing_cue(
    monkeypatch,
    utterance,
    intent,
    intent_group,
    agreement_kind,
    response_text,
) -> None:
    import pi_hybrid_production

    calls: dict[str, object] = {"broadcast": [], "tts": []}

    async def no_user(*_args, **_kwargs):
        return None

    async def panel_user(*_args, **_kwargs):
        return "panel-user"

    async def resolve_skybridge_request(*_args, **_kwargs):
        return None

    class Broadcaster:
        async def broadcast(self, channel, event, payload):
            calls["broadcast"].append((channel, event, payload))

    async def fake_synthesize(payload, caller=None):
        calls["tts"].append(payload)
        return types.SimpleNamespace(body=b"RIFF-pi-hybrid", media_type="audio/wav")

    async def fake_try_pi(text, **kwargs):
        assert text == utterance
        assert kwargs["user_id"] == "panel-user"
        return {
            "accepted": True,
            "reason": "accepted",
            "intent": intent,
            "intent_group": intent_group,
            "agreement_kind": agreement_kind,
            "response_text": response_text,
        }

    async def fake_processing_cue(**_kwargs):
        return {"available": True, "text": "Let me check.", "event": {"type": "voice:processing_ack", "text": "Let me check."}}

    monkeypatch.setattr(voice_tts, "_resolve_recent_panel_session_user", panel_user)
    monkeypatch.setattr(voice_tts, "_resolve_panel_default_user", no_user)
    monkeypatch.setattr(voice_tts, "_VOICE_SESSIONS", {})
    monkeypatch.setattr(voice_tts, "synthesize", fake_synthesize)
    monkeypatch.setattr(pi_hybrid_production.PiHybridProductionConfig, "from_env", classmethod(lambda cls: cls(enabled=True, resource_guard_enabled=False)))
    monkeypatch.setattr(pi_hybrid_production, "pi_hybrid_production_eligible", lambda text, config=None: (True, "eligible"))
    monkeypatch.setattr(pi_hybrid_production, "processing_cue_packet", fake_processing_cue)
    monkeypatch.setattr(pi_hybrid_production, "try_pi_hybrid_production", fake_try_pi)
    monkeypatch.setitem(sys.modules, "push", types.SimpleNamespace(broadcaster=Broadcaster()))
    monkeypatch.setitem(
        sys.modules,
        "skybridge_service",
        types.SimpleNamespace(resolve_skybridge_request=resolve_skybridge_request),
    )
    monkeypatch.setattr(skybridge_service, "resolve_skybridge_request", resolve_skybridge_request)

    response = await voice_command(
        {"text": utterance, "panel_id": "panel-pi", "session_id": "session-pi"},
        caller={"user_id": "guest", "panel_id": "panel-pi"},
        stream=False,
        db=object(),
    )

    assert response["ok"] is True
    assert response["reply"] == response_text
    assert response["intent"] == intent
    assert response["pi_hybrid"]["accepted"] is True
    assert response["pi_hybrid"]["intent_group"] == intent_group
    assert response["pi_hybrid"]["agreement_kind"] == agreement_kind
    assert response["pi_hybrid"]["processing_cue"] == {"available": True, "text": "Let me check."}
    assert response["audio_base64"]
    assert calls["tts"] == [{"text": response_text}]
    assert (
        "all",
        "voice:responding",
        {
            "panel_id": "panel-pi",
            "text": "Let me check.",
            "processing_ack": True,
            "source": "pi_hybrid_production",
        },
    ) in calls["broadcast"]
    assert any(
        event == "voice:responding" and payload.get("pi_hybrid") is True
        for _, event, payload in calls["broadcast"]
    )


@pytest.mark.asyncio
async def test_voice_command_caps_pi_hybrid_list_show_response(monkeypatch) -> None:
    import pi_hybrid_production

    long_reply = "Your shopping list:\n" + "\n".join(f"  - item {idx}" for idx in range(1, 8))
    capped_reply = "Your shopping list:\n  - item 1\n  - item 2\n  - item 3\n  - item 4\n  - item 5\nAnd 2 more."
    calls: dict[str, object] = {"broadcast": [], "tts": []}

    async def no_user(*_args, **_kwargs):
        return None

    async def panel_user(*_args, **_kwargs):
        return "panel-user"

    async def resolve_skybridge_request(*_args, **_kwargs):
        return None

    class Broadcaster:
        async def broadcast(self, channel, event, payload):
            calls["broadcast"].append((channel, event, payload))

    async def fake_synthesize(payload, caller=None):
        calls["tts"].append(payload)
        return types.SimpleNamespace(body=b"RIFF-pi-hybrid-list", media_type="audio/wav")

    async def fake_try_pi(text, **kwargs):
        assert text == "what is on my shopping list"
        assert kwargs["user_id"] == "panel-user"
        return {
            "accepted": True,
            "reason": "router_confirmed_fast_accept",
            "intent": "list_show",
            "intent_group": "lists",
            "agreement_kind": "zoe_router_fast",
            "response_text": long_reply,
        }

    async def fake_processing_cue(**_kwargs):
        return {"available": True, "text": "Let me check."}

    monkeypatch.setattr(voice_tts, "_resolve_recent_panel_session_user", panel_user)
    monkeypatch.setattr(voice_tts, "_resolve_panel_default_user", no_user)
    monkeypatch.setattr(voice_tts, "_VOICE_SESSIONS", {})
    monkeypatch.setattr(voice_tts, "synthesize", fake_synthesize)
    monkeypatch.setattr(
        pi_hybrid_production.PiHybridProductionConfig,
        "from_env",
        classmethod(lambda cls: cls(enabled=True, resource_guard_enabled=False)),
    )
    monkeypatch.setattr(pi_hybrid_production, "pi_hybrid_production_eligible", lambda text, config=None: (True, "eligible"))
    monkeypatch.setattr(pi_hybrid_production, "processing_cue_packet", fake_processing_cue)
    monkeypatch.setattr(pi_hybrid_production, "try_pi_hybrid_production", fake_try_pi)
    monkeypatch.setitem(sys.modules, "push", types.SimpleNamespace(broadcaster=Broadcaster()))
    monkeypatch.setitem(
        sys.modules,
        "skybridge_service",
        types.SimpleNamespace(resolve_skybridge_request=resolve_skybridge_request),
    )
    monkeypatch.setattr(skybridge_service, "resolve_skybridge_request", resolve_skybridge_request)

    response = await voice_command(
        {"text": "what is on my shopping list", "panel_id": "panel-pi", "session_id": "session-pi"},
        caller={"user_id": "guest", "panel_id": "panel-pi"},
        stream=False,
        db=object(),
    )

    assert response["ok"] is True
    assert response["reply"] == capped_reply
    assert response["intent"] == "list_show"
    assert response["pi_hybrid"]["accepted"] is True
    assert calls["tts"] == [{"text": capped_reply}]


def test_cap_voice_list_show_reply_resets_per_list_section() -> None:
    text = "\n".join(
        [
            "Your shopping lists:",
            "Pantry:",
            "  - pantry 1",
            "  - pantry 2",
            "Shopping:",
            "  - item 1",
            "  - item 2",
            "  - item 3",
            "  - item 4",
            "  - item 5",
            "  - item 6",
            "  - item 7",
        ]
    )

    assert voice_tts._cap_voice_list_show_reply(text) == "\n".join(
        [
            "Your shopping lists:",
            "Pantry:",
            "  - pantry 1",
            "  - pantry 2",
            "Shopping:",
            "  - item 1",
            "  - item 2",
            "  - item 3",
            "  - item 4",
            "  - item 5",
            "And 2 more.",
        ]
    )
