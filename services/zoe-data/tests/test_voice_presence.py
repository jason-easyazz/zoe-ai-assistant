import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import voice_presence
from voice_presence import (
    is_wake_payload,
    is_wake_text,
    wake_ack_audio_payload,
    wake_ack_events,
    wake_ack_phrase,
    wake_ack_variant,
    wake_presence_events,
)

@pytest.fixture(autouse=True)
def _reset_voice_presence_state(monkeypatch):
    monkeypatch.setattr(voice_presence, "_AUDIO_CACHE", {})
    monkeypatch.setattr(voice_presence, "_VARIANT_CURSOR", 0)


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


def test_wake_ack_audio_payload_reads_and_caches_file(tmp_path):
    audio_path = tmp_path / "wake.wav"
    audio_path.write_bytes(b"RIFFwake")

    payload = wake_ack_audio_payload({"ZOE_WAKE_ACK_AUDIO_PATH": str(audio_path)})

    assert payload is not None
    assert payload["audio_base64"] == "UklGRndha2U="
    assert payload["content_type"].startswith("audio/")
    assert payload["source"] == "cached_wake_ack"

    cached = wake_ack_audio_payload({"ZOE_WAKE_ACK_AUDIO_PATH": str(audio_path)})
    assert cached == payload
    assert len(voice_presence._AUDIO_CACHE) == 1


def test_wake_presence_events_can_include_cached_audio():
    events = wake_presence_events(
        ack_phrase="Yeah?",
        ack_audio={"audio_base64": "UklGRg==", "content_type": "audio/wav"},
    )

    assert events == [
        {"type": "state", "state": "wake"},
        {"type": "transcript", "role": "zoe", "text": "Yeah?"},
        {
            "type": "audio",
            "audio_base64": "UklGRg==",
            "content_type": "audio/wav",
            "source": "cached_wake_ack",
        },
        {"type": "state", "state": "listening"},
        {"type": "done"},
    ]


def test_wake_ack_variant_selects_index_aligned_phrase_and_audio(tmp_path):
    first = tmp_path / "first.wav"
    second = tmp_path / "second.wav"
    first.write_bytes(b"RIFF1")
    second.write_bytes(b"RIFF2")

    variant = wake_ack_variant(
        {
            "ZOE_WAKE_ACK_PHRASES": "Yes Jason?|Good morning Jason.|Hi there, how can I help?",
            "ZOE_WAKE_ACK_AUDIO_PATHS": f"{first}|{second}",
        },
        index=1,
    )

    assert variant == {"phrase": "Good morning Jason.", "audio_path": str(second), "index": 1}


def test_wake_ack_events_uses_selected_cached_variant(tmp_path):
    audio_path = tmp_path / "wake.wav"
    audio_path.write_bytes(b"RIFF")

    events = wake_ack_events(
        {
            "ZOE_WAKE_ACK_PHRASES": "Hi there.|How can I help?",
            "ZOE_WAKE_ACK_AUDIO_PATHS": str(audio_path),
        }
    )

    assert events[0] == {"type": "state", "state": "wake"}
    assert events[1] == {"type": "transcript", "role": "zoe", "text": "Hi there."}
    assert events[2]["type"] == "audio"
    assert events[2]["audio_base64"] == "UklGRg=="
    assert events[-2:] == [
        {"type": "state", "state": "listening"},
        {"type": "done"},
    ]


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


def test_websocket_raw_text_hey_zoe_can_emit_configured_ack_phrase(monkeypatch):
    from fastapi.testclient import TestClient
    import main

    async def _unexpected_resolve(*_args, **_kwargs):
        raise AssertionError("raw wake phrase must not enter Skybridge/card resolution")

    monkeypatch.setattr(main, "_resolve_voice_cards", _unexpected_resolve)
    monkeypatch.setenv("ZOE_WAKE_ACK_PHRASE", "Yes Jason?")

    with TestClient(main.app) as client:
        with client.websocket_connect("/ws/voice/") as ws:
            assert ws.receive_json() == {"type": "state", "state": "ambient"}
            ws.send_text("hey zoe")

            assert ws.receive_json() == {"type": "state", "state": "wake"}
            assert ws.receive_json() == {"type": "transcript", "role": "zoe", "text": "Yes Jason?"}
            assert ws.receive_json() == {"type": "state", "state": "listening"}
            assert ws.receive_json() == {"type": "done"}


def test_websocket_wake_can_emit_cached_audio_without_reasoning(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    import main

    audio_path = tmp_path / "wake.wav"
    audio_path.write_bytes(b"RIFF")

    async def _unexpected_resolve(*_args, **_kwargs):
        raise AssertionError("wake audio must not enter Skybridge/card resolution")

    monkeypatch.setattr(main, "_resolve_voice_cards", _unexpected_resolve)
    monkeypatch.setenv("ZOE_WAKE_ACK_PHRASE", "Yes Jason?")
    monkeypatch.setenv("ZOE_WAKE_ACK_AUDIO_PATH", str(audio_path))

    with TestClient(main.app) as client:
        with client.websocket_connect("/ws/voice/") as ws:
            assert ws.receive_json() == {"type": "state", "state": "ambient"}
            ws.send_json({"type": "wake"})

            assert ws.receive_json() == {"type": "state", "state": "wake"}
            assert ws.receive_json() == {"type": "transcript", "role": "zoe", "text": "Yes Jason?"}
            audio = ws.receive_json()
            assert audio["type"] == "audio"
            assert audio["audio_base64"] == "UklGRg=="
            assert audio["content_type"].startswith("audio/")
            assert audio["source"] == "cached_wake_ack"
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


def test_voice_wake_endpoint_uses_cached_audio_before_live_tts(monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers import voice_tts

    audio_path = tmp_path / "wake.wav"
    audio_path.write_bytes(b"RIFF")

    def _fake_auth():
        return {"source": "test", "panel_id": "test-panel", "user_id": "u1"}

    async def _unexpected_synthesize(*_args, **_kwargs):
        raise AssertionError("cached wake audio should avoid live TTS")

    monkeypatch.setenv("ZOE_WAKE_ACK_PHRASE", "Yes Jason?")
    monkeypatch.setenv("ZOE_WAKE_ACK_AUDIO_PATH", str(audio_path))
    monkeypatch.setattr(voice_tts, "synthesize", _unexpected_synthesize)

    app = FastAPI()
    app.include_router(voice_tts.router)
    app.dependency_overrides[voice_tts._require_voice_auth] = _fake_auth

    with TestClient(app) as client:
        response = client.post("/api/voice/wake", json={"panel_id": "panel-1"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["panel_id"] == "panel-1"
    assert payload["ack_audio_base64"] == "UklGRg=="
    assert payload["content_type"].startswith("audio/")
