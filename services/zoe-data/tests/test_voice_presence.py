import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from voice_presence import is_wake_payload, is_wake_text, wake_ack_phrase, wake_presence_events


def test_is_wake_text_matches_only_wake_phrase():
    assert is_wake_text("hey zoe") is True
    assert is_wake_text("Hi Zoe!") is True
    assert is_wake_text("hello zoe ") is True
    assert is_wake_text("hey zoe do I need a jacket") is False
    assert is_wake_text("zoe") is False


def test_is_wake_payload_accepts_explicit_wake_and_text_phrase():
    assert is_wake_payload({"type": "wake"}) is True
    assert is_wake_payload({"type": "text", "message": "hey zoe"}) is True
    assert is_wake_payload({"type": "text", "message": "hey zoe set a timer"}) is False
    assert is_wake_payload({"type": "cancel"}) is False
    assert is_wake_payload(None) is False


def test_wake_presence_events_are_instant_and_non_reasoning():
    events = wake_presence_events()

    assert events == [
        {"type": "state", "state": "wake"},
        {"type": "state", "state": "listening"},
        {"type": "done"},
    ]


def test_wake_presence_events_can_include_short_ack_phrase():
    events = wake_presence_events(ack_phrase="Yeah?")

    assert events == [
        {"type": "state", "state": "wake"},
        {"type": "transcript", "role": "zoe", "text": "Yeah?"},
        {"type": "state", "state": "listening"},
        {"type": "done"},
    ]


def test_wake_ack_phrase_uses_env_mapping():
    assert wake_ack_phrase({}) == ""
    assert wake_ack_phrase({"ZOE_WAKE_ACK_PHRASE": "  Yeah? "}) == "Yeah?"


def test_presence_event_builder_is_sub_millisecond_for_hot_path():
    import time

    started = time.perf_counter()
    for _ in range(1000):
        wake_presence_events(ack_phrase="Yeah?")
    elapsed_ms = (time.perf_counter() - started) * 1000

    assert elapsed_ms < 5.0


def test_websocket_wake_returns_presence_events_without_reasoning(monkeypatch):
    from fastapi.testclient import TestClient
    import main

    async def _unexpected_resolve(*_args, **_kwargs):
        raise AssertionError("wake must not enter Skybridge/card resolution")

    monkeypatch.setattr(main, "_resolve_voice_cards", _unexpected_resolve)
    monkeypatch.delenv("ZOE_WAKE_ACK_PHRASE", raising=False)

    with TestClient(main.app) as client:
        with client.websocket_connect("/ws/voice/") as ws:
            assert ws.receive_json() == {"type": "state", "state": "ambient"}
            ws.send_json({"type": "wake"})

            assert ws.receive_json() == {"type": "state", "state": "wake"}
            assert ws.receive_json() == {"type": "state", "state": "listening"}
            assert ws.receive_json() == {"type": "done"}


def test_websocket_hey_zoe_can_emit_configured_ack_phrase(monkeypatch):
    from fastapi.testclient import TestClient
    import main

    async def _unexpected_resolve(*_args, **_kwargs):
        raise AssertionError("wake phrase must not enter Skybridge/card resolution")

    monkeypatch.setattr(main, "_resolve_voice_cards", _unexpected_resolve)
    monkeypatch.setenv("ZOE_WAKE_ACK_PHRASE", "Yes Jason?")

    with TestClient(main.app) as client:
        with client.websocket_connect("/ws/voice/") as ws:
            assert ws.receive_json() == {"type": "state", "state": "ambient"}
            ws.send_json({"type": "text", "message": "hey zoe"})

            assert ws.receive_json() == {"type": "state", "state": "wake"}
            assert ws.receive_json() == {"type": "transcript", "role": "zoe", "text": "Yes Jason?"}
            assert ws.receive_json() == {"type": "state", "state": "listening"}
            assert ws.receive_json() == {"type": "done"}


def test_websocket_ignores_non_object_json_and_stays_alive(monkeypatch):
    from fastapi.testclient import TestClient
    import main

    async def _unexpected_resolve(*_args, **_kwargs):
        raise AssertionError("non-message payload must not enter Skybridge/card resolution")

    monkeypatch.setattr(main, "_resolve_voice_cards", _unexpected_resolve)

    with TestClient(main.app) as client:
        with client.websocket_connect("/ws/voice/") as ws:
            assert ws.receive_json() == {"type": "state", "state": "ambient"}
            ws.send_text("[]")
            ws.send_text("ping")

            assert ws.receive_json() == {"type": "pong"}
