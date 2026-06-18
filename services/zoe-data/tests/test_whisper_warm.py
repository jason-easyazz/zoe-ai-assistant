"""Tests for /api/system/whisper-warm (on-demand persistent faster-whisper warm).

Startup warmup (ZOE_WHISPER_WARMUP) destabilized the service, so warming is
triggered post-startup via this internal endpoint instead. A warm failure must
be best-effort (ok:false, HTTP 200) and never 500.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

import auth
from routers.system import router as system_router


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(system_router)
    return app


def _patch_voice_helpers(monkeypatch, *, worker):
    """Stub the voice_tts helpers so no real model/GPU/disk work happens."""
    import routers.voice_tts as voice_tts

    def fake_create_path():
        return "/tmp/whisper-warm-test.wav"

    def fake_write_silence(path, **kwargs):
        return None

    monkeypatch.setattr(voice_tts, "_create_warmup_wav_path", fake_create_path)
    monkeypatch.setattr(voice_tts, "_write_warmup_silence_wav", fake_write_silence)
    monkeypatch.setattr(voice_tts, "_run_faster_whisper_worker", worker)


def test_requires_internal_token(monkeypatch):
    monkeypatch.setattr(auth, "_ZOE_INTERNAL_TOKEN", "tok")
    resp = TestClient(_app()).post("/api/system/whisper-warm")
    assert resp.status_code == 403


def test_happy_path_returns_warmed(monkeypatch):
    monkeypatch.setattr(auth, "_ZOE_INTERNAL_TOKEN", "tok")
    captured = {}

    async def fake_worker(wav_path):
        captured["wav_path"] = wav_path
        return ""  # silent transcription -> empty text

    _patch_voice_helpers(monkeypatch, worker=fake_worker)

    resp = TestClient(_app()).post(
        "/api/system/whisper-warm",
        headers={"X-Internal-Token": "tok"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["warmed"] is True
    assert isinstance(body["latency_ms"], int)
    assert body["latency_ms"] >= 0
    assert captured["wav_path"] == "/tmp/whisper-warm-test.wav"


def test_worker_failure_returns_ok_false_not_500(monkeypatch):
    """A warm failure is best-effort: ok:false with HTTP 200, never a 500."""
    monkeypatch.setattr(auth, "_ZOE_INTERNAL_TOKEN", "tok")

    async def boom(wav_path):
        raise RuntimeError("worker subprocess died")

    _patch_voice_helpers(monkeypatch, worker=boom)

    resp = TestClient(_app()).post(
        "/api/system/whisper-warm",
        headers={"X-Internal-Token": "tok"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["warmed"] is False
    assert body["error"] == "RuntimeError"
