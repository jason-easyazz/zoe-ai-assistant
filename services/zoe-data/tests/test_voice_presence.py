from datetime import datetime
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import db_pool
import voice_presence
from voice_presence import (
    is_wake_payload,
    is_wake_text,
    processing_ack_audio_payload,
    processing_ack_event,
    processing_ack_phrases,
    processing_ack_variant,
    wake_ack_audio_payload,
    wake_ack_events,
    wake_ack_phrase,
    wake_ack_time_period,
    wake_ack_variant,
    wake_ack_variant_labels,
    wake_presence_events,
)

@pytest.fixture(autouse=True)
def _reset_voice_presence_state(monkeypatch):
    monkeypatch.setattr(voice_presence, "_AUDIO_CACHE", {})
    monkeypatch.setattr(voice_presence, "_VARIANT_CURSOR", 0)
    monkeypatch.setattr(voice_presence, "_PROCESSING_ACK_CURSOR", 0)


def _drop_cached_db_pool():
    """Best-effort discard of any asyncpg pool cached by a previous lifespan."""
    stale = db_pool._pool
    db_pool._pool = None
    db_pool._pool_loop = None
    if stale is not None:
        try:
            stale.terminate()
        except Exception:
            # The previous test's event loop is already closed; asyncpg's
            # terminate() can raise "Event loop is closed" from the transport
            # abort. Connections die with the pytest process either way.
            pass


@pytest.fixture(autouse=True)
def _fresh_db_pool_per_test():
    """Isolate db_pool state across `with TestClient(main.app)` lifespans.

    Each TestClient context runs main's lifespan on a fresh event loop, and the
    lifespan never calls close_pool on shutdown — so the asyncpg pool leaks,
    bound to a loop that is closed by the time the NEXT test starts. Since #880
    init_pool() discards such stale pools via pool.terminate(), which raises
    RuntimeError("Event loop is closed") out of app startup and failed every
    second lifespan-entering test in this file. Dropping the cached pool around
    each test makes every lifespan build a fresh pool on its own loop.
    """
    _drop_cached_db_pool()
    yield
    _drop_cached_db_pool()


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

    assert variant == {"phrase": "Good morning Jason.", "audio_path": str(second), "index": 1, "label": ""}


def test_wake_ack_variant_labels_are_pipe_aligned():
    assert wake_ack_variant_labels({}) == []
    assert wake_ack_variant_labels({"ZOE_WAKE_ACK_VARIANT_LABELS": " default | Morning | evening,night "}) == [
        "default",
        "morning",
        "evening,night",
    ]


def test_wake_ack_time_period_uses_local_hour_buckets():
    assert wake_ack_time_period(datetime(2026, 1, 1, 8, 0)) == "morning"
    assert wake_ack_time_period(datetime(2026, 1, 1, 13, 0)) == "afternoon"
    assert wake_ack_time_period(datetime(2026, 1, 1, 20, 0)) == "evening"
    assert wake_ack_time_period(datetime(2026, 1, 1, 23, 0)) == "evening"
    assert wake_ack_time_period(datetime(2026, 1, 1, 2, 0)) == "night"


def test_wake_ack_variant_prefers_matching_time_label():
    env = {
        "ZOE_WAKE_ACK_PHRASES": "Yes Jason?|Good morning Jason.|Good evening Jason.",
        "ZOE_WAKE_ACK_VARIANT_LABELS": "default|morning|evening",
    }

    assert wake_ack_variant(env, now=datetime(2026, 1, 1, 8, 0))["phrase"] == "Good morning Jason."
    assert wake_ack_variant(env, now=datetime(2026, 1, 1, 19, 0))["phrase"] == "Good evening Jason."


def test_wake_ack_variant_selects_comma_combined_label_for_period():
    env = {
        "ZOE_WAKE_ACK_PHRASES": "Yes Jason?|Good night, Jason.",
        "ZOE_WAKE_ACK_VARIANT_LABELS": "default|evening,night",
    }

    assert wake_ack_variant(env, now=datetime(2026, 1, 1, 23, 0))["phrase"] == "Good night, Jason."
    assert wake_ack_variant(env, now=datetime(2026, 1, 1, 2, 0))["phrase"] == "Good night, Jason."


def test_wake_ack_variant_ignores_extra_labels_without_content_slots():
    env = {
        "ZOE_WAKE_ACK_PHRASES": "Yes Jason?",
        "ZOE_WAKE_ACK_VARIANT_LABELS": "default|morning",
    }

    variant = wake_ack_variant(env, now=datetime(2026, 1, 1, 8, 0))

    assert variant == {"phrase": "Yes Jason?", "audio_path": "", "index": 0, "label": "default"}


def test_wake_ack_variant_falls_back_to_default_label_when_period_missing():
    env = {
        "ZOE_WAKE_ACK_PHRASES": "Yes Jason?|Good morning Jason.",
        "ZOE_WAKE_ACK_VARIANT_LABELS": "default|morning",
    }

    variant = wake_ack_variant(env, now=datetime(2026, 1, 1, 14, 0))

    assert variant == {"phrase": "Yes Jason?", "audio_path": "", "index": 0, "label": "default"}


def test_wake_ack_events_can_use_time_aware_variant():
    events = wake_ack_events(
        {
            "ZOE_WAKE_ACK_PHRASES": "Yes Jason?|Good morning Jason.",
            "ZOE_WAKE_ACK_VARIANT_LABELS": "default|morning",
        },
        now=datetime(2026, 1, 1, 7, 0),
    )

    assert events == [
        {"type": "state", "state": "wake"},
        {"type": "transcript", "role": "zoe", "text": "Good morning Jason."},
        {"type": "state", "state": "listening"},
        {"type": "done"},
    ]


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


def test_processing_ack_phrases_default_and_override():
    assert processing_ack_phrases({}) == ["Let me check.", "One moment.", "I will check that."]
    assert processing_ack_phrases({"ZOE_PROCESSING_ACK_DEFAULT_ENABLED": "false"}) == []
    assert processing_ack_phrases({"ZOE_PROCESSING_ACK_PHRASE": "  Checking now. "}) == ["Checking now."]
    assert processing_ack_phrases({"ZOE_PROCESSING_ACK_PHRASES": "Checking.|On it."}) == [
        "Checking.",
        "On it.",
    ]


def test_processing_ack_variant_rotates_without_reasoning():
    env = {"ZOE_PROCESSING_ACK_PHRASES": "Checking.|On it."}

    assert processing_ack_variant(env)["phrase"] == "Checking."
    assert processing_ack_variant(env)["phrase"] == "On it."
    assert processing_ack_variant(env)["phrase"] == "Checking."


def test_processing_ack_rotation_is_independent_from_wake_rotation():
    wake_env = {"ZOE_WAKE_ACK_PHRASES": "Yes.|Morning.|Evening."}
    processing_env = {"ZOE_PROCESSING_ACK_PHRASES": "Let me check.|One moment."}

    assert wake_ack_variant(wake_env)["phrase"] == "Yes."
    assert wake_ack_variant(wake_env)["phrase"] == "Morning."
    assert wake_ack_variant(wake_env)["phrase"] == "Evening."

    assert processing_ack_variant(processing_env)["phrase"] == "Let me check."


def test_processing_ack_event_can_include_cached_audio(tmp_path):
    audio_path = tmp_path / "processing.wav"
    audio_path.write_bytes(b"RIFFprocessing")

    event = processing_ack_event(
        {
            "ZOE_PROCESSING_ACK_PHRASE": "Let me check.",
            "ZOE_PROCESSING_ACK_AUDIO_PATH": str(audio_path),
        }
    )

    assert event is not None
    assert event["type"] == "voice:processing_ack"
    assert event["text"] == "Let me check."
    assert event["source"] == "intent_buffer"
    assert event["audio_base64"] == "UklGRnByb2Nlc3Npbmc="
    assert event["audio_source"] == "cached_processing_ack"
    assert processing_ack_audio_payload(audio_path=str(audio_path))["source"] == "cached_processing_ack"


def test_websocket_wake_returns_presence_events_without_reasoning(monkeypatch):
    from fastapi.testclient import TestClient
    import main

    async def _unexpected_resolve(*_args, **_kwargs):
        raise AssertionError("wake must not enter Skybridge/card resolution")

    monkeypatch.setattr(main, "_resolve_voice_cards", _unexpected_resolve)
    monkeypatch.delenv("ZOE_WAKE_ACK_PHRASE", raising=False)
    monkeypatch.delenv("ZOE_WAKE_ACK_PHRASES", raising=False)
    monkeypatch.delenv("ZOE_WAKE_ACK_AUDIO_PATH", raising=False)
    monkeypatch.delenv("ZOE_WAKE_ACK_AUDIO_PATHS", raising=False)

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
    monkeypatch.delenv("ZOE_WAKE_ACK_PHRASES", raising=False)
    monkeypatch.delenv("ZOE_WAKE_ACK_AUDIO_PATH", raising=False)
    monkeypatch.delenv("ZOE_WAKE_ACK_AUDIO_PATHS", raising=False)
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
    monkeypatch.delenv("ZOE_WAKE_ACK_PHRASES", raising=False)
    monkeypatch.delenv("ZOE_WAKE_ACK_AUDIO_PATH", raising=False)
    monkeypatch.delenv("ZOE_WAKE_ACK_AUDIO_PATHS", raising=False)
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
    monkeypatch.delenv("ZOE_WAKE_ACK_PHRASES", raising=False)
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

    monkeypatch.delenv("ZOE_WAKE_ACK_PHRASES", raising=False)
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
    assert payload["ack_text"] == "Yes Jason?"
    assert payload["ack_audio_base64"] == "UklGRg=="
    assert payload["content_type"].startswith("audio/")


def test_voice_wake_endpoint_reports_ack_text_when_using_live_tts_fallback(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers import voice_tts

    class Audio:
        body = b"RIFF-tts"
        media_type = "audio/wav"

    def _fake_auth():
        return {"source": "test", "panel_id": "test-panel", "user_id": "u1"}

    async def _fake_synthesize(payload, caller=None):
        assert payload == {"text": "Yes Jason?"}
        assert caller["source"] == "test"
        return Audio()

    monkeypatch.setattr(voice_presence, "_AUDIO_CACHE", {})
    monkeypatch.setattr(voice_presence, "_VARIANT_CURSOR", 0)
    monkeypatch.delenv("ZOE_WAKE_ACK_PHRASES", raising=False)
    monkeypatch.delenv("ZOE_WAKE_ACK_AUDIO_PATH", raising=False)
    monkeypatch.delenv("ZOE_WAKE_ACK_AUDIO_PATHS", raising=False)
    monkeypatch.setenv("ZOE_WAKE_ACK_PHRASE", "Yes Jason?")
    monkeypatch.setattr(voice_tts, "synthesize", _fake_synthesize)

    app = FastAPI()
    app.include_router(voice_tts.router)
    app.dependency_overrides[voice_tts._require_voice_auth] = _fake_auth

    with TestClient(app) as client:
        response = client.post("/api/voice/wake", json={"panel_id": "panel-1"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["panel_id"] == "panel-1"
    assert payload["ack_text"] == "Yes Jason?"
    assert payload["ack_audio_base64"] == "UklGRi10dHM="
    assert payload["content_type"] == "audio/wav"
