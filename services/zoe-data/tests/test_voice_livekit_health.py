import asyncio
import json
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from routers import voice_livekit


@pytest.fixture(autouse=True)
def _reset_health():
    voice_livekit.reset_voice_health_for_tests()
    yield
    voice_livekit.reset_voice_health_for_tests()


class _LocalParticipant:
    def __init__(self):
        self.messages = []

    async def publish_data(self, payload: bytes, reliable: bool = True):
        self.messages.append(json.loads(payload.decode()))


def test_livekit_health_endpoint_returns_observable_contract():
    app = FastAPI()
    app.include_router(voice_livekit.router)

    with TestClient(app) as client:
        response = client.get("/api/voice/livekit-health")

    assert response.status_code == 200
    data = response.json()
    for key in {
        "status",
        "backend",
        "connected",
        "participant_identity",
        "connection_count",
        "reconnect_count",
        "audio_tracks",
        "pipeline_successes",
        "pipeline_failures",
        "playback_completions",
        "last_stage",
        "last_error",
        "stage_latency_ms",
    }:
        assert key in data
    assert data["participant_identity"] == "zoe-agent"


def test_record_voice_connected_counts_reconnects():
    voice_livekit._record_voice_connected()
    first = voice_livekit.get_voice_health()
    voice_livekit._record_voice_connected()
    second = voice_livekit.get_voice_health()

    assert first["connection_count"] == 1
    assert first["reconnect_count"] == 0
    assert second["connection_count"] == 2
    assert second["reconnect_count"] == 1
    assert second["connected"] is True
    assert second["last_connected_at"]


@pytest.mark.asyncio
async def test_pipeline_failure_updates_health(monkeypatch):
    from routers import voice_tts

    async def _boom(path: str) -> str:
        raise RuntimeError("stt unavailable")

    monkeypatch.setattr(voice_tts, "_transcribe_audio", _boom)
    local = _LocalParticipant()

    await voice_livekit._run_pipeline(local, [b"\x00\x00" * 160], "u1", "s1")

    health = voice_livekit.get_voice_health()
    assert health["pipeline_failures"] == 1
    assert health["last_stage"] == "stt_failed"
    assert "stt unavailable" in health["last_error"]
    assert local.messages[-1] == {"type": "state", "state": "ambient"}


@pytest.mark.asyncio
async def test_data_channel_text_message_runs_text_pipeline(monkeypatch):
    calls = []

    async def _fake_text_pipeline(local_participant, message, user_id, session_id):
        calls.append((message, user_id, session_id))

    monkeypatch.setattr(voice_livekit, "_run_text_pipeline", _fake_text_pipeline)

    class Room:
        def __init__(self):
            self.local_participant = _LocalParticipant()
            self.handlers = {}

        def on(self, event):
            def _decorator(func):
                self.handlers[event] = func
                return func
            return _decorator

    class Participant:
        sid = "participant-123456"
        identity = "browser"

    class Packet:
        participant = Participant()
        data = json.dumps({
            "type": "text",
            "message": "hello zoe",
            "session_id": "session-1",
        }).encode()

    room = Room()
    state = {}
    voice_livekit._build_room_handlers(room, state, {})
    room.handlers["data_received"](Packet())
    await asyncio.sleep(0)

    assert calls == [("hello zoe", "guest", "session-1")]
    assert state[Participant.sid]["state"] == voice_livekit._ParticipantState.PROCESSING
