"""Voice /transcribe endpoint: auth gate and whisper stub."""

import base64
import asyncio
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _clear_voice_sessions():
    from routers import voice_tts

    voice_tts._VOICE_SESSIONS.clear()
    yield
    voice_tts._VOICE_SESSIONS.clear()


@pytest.fixture
def client():
    from routers import voice_tts

    def _fake_auth():
        return {"source": "test", "panel_id": "test-panel", "user_id": "u1"}

    app = FastAPI()
    app.include_router(voice_tts.router)
    app.dependency_overrides[voice_tts._require_voice_auth] = _fake_auth
    with TestClient(app) as c:
        yield c


def test_transcribe_ok_with_mock_whisper(client, monkeypatch):
    from routers import voice_tts

    async def _fake_run(path: str) -> str:
        return "hello world"

    # Patch the waterfall entry point — the endpoint picks between
    # whisper.cpp CLI and faster-whisper based on runtime config, and on CI
    # neither may be available. Patching `_transcribe_audio` short-circuits
    # both branches so the test is deterministic.
    monkeypatch.setattr(voice_tts, "_transcribe_audio", _fake_run)
    wav = b"RIFF" + b"\x00" * 12  # minimal header-ish payload for temp file
    body = {"audio_base64": base64.b64encode(wav).decode(), "panel_id": "p1"}
    r = client.post("/api/voice/transcribe", json=body)
    assert r.status_code == 200
    data = r.json()
    assert data.get("ok") is True
    assert data.get("text") == "hello world"


def test_transcribe_writes_stt_audit_log(client, monkeypatch, tmp_path):
    from routers import voice_tts

    async def _fake_run(path: str) -> str:
        return "hello audit"

    log_path = tmp_path / "voice_stt.jsonl"
    monkeypatch.setenv("ZOE_VOICE_STT_LOG", str(log_path))
    monkeypatch.setenv("ZOE_WHISPER_MODEL", "small.en")
    monkeypatch.setattr(voice_tts, "_transcribe_audio", _fake_run)
    wav = b"RIFF" + b"\x00" * 12

    r = client.post(
        "/api/voice/transcribe",
        json={"audio_base64": base64.b64encode(wav).decode(), "panel_id": "p-audit"},
    )

    assert r.status_code == 200
    record = json.loads(log_path.read_text().strip())
    assert record["route"] == "transcribe"
    assert record["panel_id"] == "p-audit"
    assert record["transcript"] == "hello audit"
    assert record["model"] == "small.en"
    assert record["audio_bytes"] == len(wav)
    assert isinstance(record["vad_threshold"], float)
    assert isinstance(record["min_speech_ms"], int)
    assert isinstance(record["min_silence_ms"], int)
    assert isinstance(record["speech_pad_ms"], int)


def test_transcribe_missing_body_400(client):
    r = client.post("/api/voice/transcribe", json={})
    assert r.status_code == 400


def test_transcribe_503_when_whisper_missing(client, monkeypatch, tmp_path):
    from routers import voice_tts

    async def _boom(path: str) -> str:
        raise RuntimeError("whisper.cpp binary not found")

    log_path = tmp_path / "voice_stt.jsonl"
    monkeypatch.setenv("ZOE_VOICE_STT_LOG", str(log_path))
    monkeypatch.setattr(voice_tts, "_transcribe_audio", _boom)
    wav = b"RIFF" + b"\x01" * 20
    r = client.post(
        "/api/voice/transcribe",
        json={"audio_base64": base64.b64encode(wav).decode()},
    )
    assert r.status_code == 503
    record = json.loads(log_path.read_text().strip())
    assert record["route"] == "transcribe"
    assert record["audio_bytes"] == len(wav)
    assert record["transcript"] == ""
    assert "whisper.cpp binary not found" in record["error"]


def test_faster_whisper_subprocess_signal_does_not_exit_worker(monkeypatch):
    from routers import voice_tts

    class _Proc:
        returncode = -11

        async def communicate(self):
            return b"", b"native crash"

    async def _fake_exec(*args, **kwargs):
        return _Proc()

    monkeypatch.setattr(voice_tts.asyncio, "create_subprocess_exec", _fake_exec)
    monkeypatch.setenv("ZOE_WHISPER_TIMEOUT_S", "5")

    with pytest.raises(RuntimeError, match="signal 11"):
        asyncio.run(voice_tts._run_faster_whisper_subprocess("/tmp/example.wav"))


def test_faster_whisper_defaults_to_persistent_worker(monkeypatch):
    from routers import voice_tts

    async def _fake_subprocess(path: str) -> str:
        return f"child:{path}"

    async def _fake_worker(path: str) -> str:
        return f"persistent:{path}"

    async def _fake_in_process(path: str) -> str:
        return f"in-process:{path}"

    monkeypatch.delenv("ZOE_WHISPER_IN_PROCESS", raising=False)
    monkeypatch.delenv("ZOE_WHISPER_PERSISTENT_WORKER", raising=False)
    monkeypatch.setattr(voice_tts, "_run_faster_whisper_subprocess", _fake_subprocess)
    monkeypatch.setattr(voice_tts, "_run_faster_whisper_worker", _fake_worker)
    monkeypatch.setattr(voice_tts, "_run_faster_whisper_in_process", _fake_in_process)

    assert asyncio.run(voice_tts._run_faster_whisper("/tmp/audio.wav")) == "persistent:/tmp/audio.wav"


def test_faster_whisper_subprocess_fallback_opt_out(monkeypatch):
    from routers import voice_tts

    async def _fake_subprocess(path: str) -> str:
        return f"child:{path}"

    async def _fake_worker(path: str) -> str:
        return f"persistent:{path}"

    async def _fake_in_process(path: str) -> str:
        return f"in-process:{path}"

    monkeypatch.delenv("ZOE_WHISPER_IN_PROCESS", raising=False)
    monkeypatch.setenv("ZOE_WHISPER_PERSISTENT_WORKER", "false")
    monkeypatch.setattr(voice_tts, "_run_faster_whisper_subprocess", _fake_subprocess)
    monkeypatch.setattr(voice_tts, "_run_faster_whisper_worker", _fake_worker)
    monkeypatch.setattr(voice_tts, "_run_faster_whisper_in_process", _fake_in_process)

    assert asyncio.run(voice_tts._run_faster_whisper("/tmp/audio.wav")) == "child:/tmp/audio.wav"


def test_faster_whisper_in_process_opt_in(monkeypatch):
    from routers import voice_tts

    async def _fake_subprocess(path: str) -> str:
        return f"child:{path}"

    async def _fake_worker(path: str) -> str:
        return f"persistent:{path}"

    async def _fake_in_process(path: str) -> str:
        return f"in-process:{path}"

    monkeypatch.setenv("ZOE_WHISPER_IN_PROCESS", "true")
    monkeypatch.setattr(voice_tts, "_run_faster_whisper_subprocess", _fake_subprocess)
    monkeypatch.setattr(voice_tts, "_run_faster_whisper_worker", _fake_worker)
    monkeypatch.setattr(voice_tts, "_run_faster_whisper_in_process", _fake_in_process)

    assert asyncio.run(voice_tts._run_faster_whisper("/tmp/audio.wav")) == "in-process:/tmp/audio.wav"


def test_faster_whisper_worker_reuses_process(monkeypatch):
    from routers import voice_tts

    worker = voice_tts._FasterWhisperWorker()
    starts = []

    class _Stream:
        def __init__(self, lines=None):
            self.lines = list(lines or [])

        async def readline(self):
            return self.lines.pop(0)

    class _Stdin:
        def __init__(self):
            self.writes = []

        def write(self, data):
            self.writes.append(data)

        async def drain(self):
            return None

    class _Proc:
        returncode = None

        def __init__(self):
            self.stdin = _Stdin()
            self.stdout = _Stream([
                b'{"ready": true}\n',
                b'{"text": "first"}\n',
                b'{"text": "second"}\n',
            ])

        def terminate(self):
            self.returncode = -15

        async def wait(self):
            return self.returncode

    async def _fake_exec(*_args, **_kwargs):
        starts.append(1)
        return _Proc()

    monkeypatch.setattr(voice_tts.asyncio, "create_subprocess_exec", _fake_exec)
    monkeypatch.setenv("ZOE_WHISPER_TIMEOUT_S", "2")

    assert asyncio.run(worker.transcribe("/tmp/one.wav")) == "first"
    assert asyncio.run(worker.transcribe("/tmp/two.wav")) == "second"
    assert len(starts) == 1


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [
        ("show whether", "show weather"),
        ("open the whether", "open weather"),
        ("what's the whether", "what is the weather"),
        ("how is the whether", "how is the weather"),
        ("whether tomorrow", "weather tomorrow"),
        ("whether or not it rains, remind me", "whether or not it rains, remind me"),
    ],
)
def test_normalize_voice_command_text_weather_homophones(raw, normalized):
    from routers import voice_tts

    assert voice_tts._normalize_voice_command_text(raw) == normalized


def test_stt_audit_log_rotates_when_capped(monkeypatch, tmp_path):
    from routers import voice_tts

    log_path = tmp_path / "voice_stt.jsonl"
    log_path.write_text("x" * 50)
    monkeypatch.setenv("ZOE_VOICE_STT_LOG", str(log_path))
    monkeypatch.setenv("ZOE_VOICE_STT_LOG_MAX_BYTES", "10")

    voice_tts._log_voice_stt_sample(
        route="transcribe",
        panel_id="p-rotate",
        audio_bytes=1,
        suffix=".wav",
    )

    rotated = tmp_path / "voice_stt.jsonl.1"
    assert rotated.read_text() == "x" * 50
    record = json.loads(log_path.read_text().strip())
    assert record["panel_id"] == "p-rotate"


def test_recent_panel_session_user_is_trusted(monkeypatch):
    from routers import voice_tts

    monkeypatch.setenv("ZOE_PANEL_SESSION_TRUST_WINDOW_S", "900")

    class _Cursor:
        def __init__(self, row):
            self._row = row

        async def fetchone(self):
            return self._row

    class _Db:
        def __init__(self, row):
            self._row = row

        async def execute(self, _query, _params):
            return _Cursor(self._row)

    row = {
        "user_id": "alice",
        "last_seen_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
    }
    resolved = asyncio.run(
        voice_tts._resolve_recent_panel_session_user("panel-a", _Db(row))
    )
    assert resolved == "alice"


def test_recent_panel_session_user_stale_is_not_trusted(monkeypatch):
    from routers import voice_tts

    monkeypatch.setenv("ZOE_PANEL_SESSION_TRUST_WINDOW_S", "60")

    class _Cursor:
        def __init__(self, row):
            self._row = row

        async def fetchone(self):
            return self._row

    class _Db:
        def __init__(self, row):
            self._row = row

        async def execute(self, _query, _params):
            return _Cursor(self._row)

    row = {
        "user_id": "alice",
        "last_seen_at": (datetime.now(timezone.utc) - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S"),
    }
    resolved = asyncio.run(
        voice_tts._resolve_recent_panel_session_user("panel-a", _Db(row))
    )
    assert resolved is None


def test_voice_session_rollover_preserves_bound_user():
    from routers import voice_tts

    old_session = "voice-panel-p1-old"
    voice_tts._VOICE_SESSIONS["p1"] = {
        "session_id": old_session,
        "last_at": time.monotonic() - voice_tts._VOICE_SESSION_TTL_S - 5,
        "bound_user_id": "user-123",
    }
    try:
        new_session = voice_tts._get_or_create_voice_session("p1")
        assert new_session != old_session
        assert voice_tts._VOICE_SESSIONS["p1"].get("bound_user_id") == "user-123"
    finally:
        voice_tts._VOICE_SESSIONS.pop("p1", None)


def test_panel_session_trust_window_parsing(monkeypatch):
    from routers import voice_tts

    monkeypatch.setenv("ZOE_PANEL_SESSION_TRUST_WINDOW_S", "not-a-number")
    assert voice_tts._panel_session_trust_window_s() == 900

    monkeypatch.setenv("ZOE_PANEL_SESSION_TRUST_WINDOW_S", "-5")
    assert voice_tts._panel_session_trust_window_s() == 0

    monkeypatch.setenv("ZOE_PANEL_SESSION_TRUST_WINDOW_S", "99999999")
    assert voice_tts._panel_session_trust_window_s() == 24 * 60 * 60
