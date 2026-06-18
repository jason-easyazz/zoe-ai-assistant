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
    """Stub the voice_tts helpers so no real model/GPU/disk work happens.
    Returns a list that records _reset_faster_whisper_worker calls."""
    import routers.voice_tts as voice_tts

    def fake_create_path():
        return "/tmp/whisper-warm-test.wav"

    def fake_write_silence(path, **kwargs):
        return None

    reset_calls: list[int] = []

    async def fake_reset():
        reset_calls.append(1)

    monkeypatch.setattr(voice_tts, "_create_warmup_wav_path", fake_create_path)
    monkeypatch.setattr(voice_tts, "_write_warmup_silence_wav", fake_write_silence)
    monkeypatch.setattr(voice_tts, "_run_faster_whisper_worker", worker)
    monkeypatch.setattr(voice_tts, "_reset_faster_whisper_worker", fake_reset)
    return reset_calls


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

    reset_calls = _patch_voice_helpers(monkeypatch, worker=boom)

    resp = TestClient(_app()).post(
        "/api/system/whisper-warm",
        headers={"X-Internal-Token": "tok"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["warmed"] is False
    assert body["error"] == "RuntimeError"
    # The broken worker must be torn down so the next ping starts fresh.
    assert reset_calls == [1], "failed warm must reset the persistent worker"


def test_hung_worker_times_out_and_resets(monkeypatch):
    """A hung worker is bounded by the timeout (not hung forever) and reset."""
    import asyncio as _aio

    monkeypatch.setattr(auth, "_ZOE_INTERNAL_TOKEN", "tok")
    monkeypatch.setenv("ZOE_WHISPER_WARMUP_TIMEOUT_S", "0.2")

    async def hang(wav_path):
        await _aio.sleep(10)

    reset_calls = _patch_voice_helpers(monkeypatch, worker=hang)

    resp = TestClient(_app()).post(
        "/api/system/whisper-warm",
        headers={"X-Internal-Token": "tok"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["error"] in ("TimeoutError", "CancelledError")
    assert reset_calls == [1], "timed-out warm must reset the worker"
