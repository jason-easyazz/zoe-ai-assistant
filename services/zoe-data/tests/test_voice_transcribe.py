"""Voice /transcribe endpoint: auth gate and whisper stub."""

import base64
import sys
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _clear_dependency_overrides():
    import main as main_mod

    main_mod.app.dependency_overrides.clear()
    yield
    main_mod.app.dependency_overrides.clear()


@pytest.fixture
def client():
    import main as main_mod
    from routers import voice_tts

    def _fake_auth():
        return {"source": "test", "panel_id": "test-panel", "user_id": "u1"}

    main_mod.app.dependency_overrides[voice_tts._require_voice_auth] = _fake_auth
    with TestClient(main_mod.app) as c:
        yield c
    main_mod.app.dependency_overrides.pop(voice_tts._require_voice_auth, None)


def test_transcribe_ok_with_mock_whisper(client, monkeypatch):
    from routers import voice_tts

    async def _fake_run(path: str) -> str:
        return "hello world"

    monkeypatch.setattr(voice_tts, "_run_whisper_cpp", _fake_run)
    wav = b"RIFF" + b"\x00" * 12  # minimal header-ish payload for temp file
    body = {"audio_base64": base64.b64encode(wav).decode(), "panel_id": "p1"}
    r = client.post("/api/voice/transcribe", json=body)
    assert r.status_code == 200
    data = r.json()
    assert data.get("ok") is True
    assert data.get("text") == "hello world"


def test_transcribe_missing_body_400(client):
    r = client.post("/api/voice/transcribe", json={})
    assert r.status_code == 400


def test_transcribe_503_when_whisper_missing(client, monkeypatch):
    from routers import voice_tts

    async def _boom(path: str) -> str:
        raise RuntimeError("whisper.cpp binary not found")

    monkeypatch.setattr(voice_tts, "_run_whisper_cpp", _boom)
    wav = b"RIFF" + b"\x01" * 20
    r = client.post(
        "/api/voice/transcribe",
        json={"audio_base64": base64.b64encode(wav).decode()},
    )
    assert r.status_code == 503
