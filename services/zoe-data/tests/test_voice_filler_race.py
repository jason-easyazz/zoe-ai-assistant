"""Thinking-filler race in /api/voice/turn_stream (perf fix for 5-11s TTFA).

voice_command does its brain work BEFORE returning, so the endpoint launches it
as a task and the stream begins immediately; the filler speaks when the task
outlives ZOE_VOICE_FILLER_AFTER_S. Covers: slow brain → filler then real reply;
fast brain → no filler; brain error → error frame (stream already started).
"""
import asyncio
import base64
import json

import pytest

pytestmark = pytest.mark.ci_safe  # pure-logic + TestClient; no models/DB

from fastapi import FastAPI
from fastapi.testclient import TestClient

import routers.voice_tts as vt


def _app(monkeypatch, brain, filler_after="0.15"):
    monkeypatch.setenv("ZOE_VOICE_FILLER_ENABLED", "1")
    monkeypatch.setenv("ZOE_VOICE_FILLER_AFTER_S", filler_after)

    async def _fake_stt(_path):
        return "tell me something interesting"
    monkeypatch.setattr(vt, "_transcribe_audio", _fake_stt)

    async def _fake_tts(_text):
        return b"RIFFfakewav"
    monkeypatch.setattr(vt, "_synthesize_kokoro_sidecar", _fake_tts)
    monkeypatch.setattr(vt, "voice_command", brain)

    app = FastAPI()
    app.include_router(vt.router)
    app.dependency_overrides[vt._require_voice_auth] = lambda: {
        "source": "device", "panel_id": "test-panel", "user_id": "voice-daemon",
    }
    from database import get_db as _real_get_db
    app.dependency_overrides[_real_get_db] = lambda: None  # brain is mocked; db unused
    return app


def _post(app):
    payload = {"audio_base64": base64.b64encode(b"\x00\x01" * 400).decode(), "panel_id": "test-panel"}
    with TestClient(app) as client:
        r = client.post("/api/voice/turn_stream", json=payload)
        assert r.status_code == 200
        frames = []
        for line in r.iter_lines():
            if not line:
                continue
            try:
                frames.append(json.loads(line))
            except Exception:
                frames.append({"_raw_audio": True})
        return frames


def test_slow_brain_speaks_filler_then_reply(monkeypatch):
    async def slow_brain(payload, caller=None, stream=True, db=None):
        await asyncio.sleep(0.6)  # >> filler_after
        return {"reply": "Here is the real answer.", "audio_base64": base64.b64encode(b"RIFFreal").decode()}
    frames = _post(_app(monkeypatch, slow_brain))
    kinds = [("filler" if f.get("provider") == "filler" else
              "full" if "full_audio" in f else
              "audio" if f.get("_raw_audio") else
              "done" if f.get("done") else "meta") for f in frames]
    assert "filler" in kinds, f"filler frame must be spoken while the brain works: {frames}"
    assert kinds.index("filler") < kinds.index("full"), "filler must precede the real reply"
    assert any(f.get("done") for f in frames)


def test_fast_brain_skips_filler(monkeypatch):
    async def fast_brain(payload, caller=None, stream=True, db=None):
        return {"reply": "Quick!", "audio_base64": base64.b64encode(b"RIFFfast").decode()}
    frames = _post(_app(monkeypatch, fast_brain))
    assert not any(f.get("provider") == "filler" for f in frames), "no filler when the brain is fast"
    assert any(f.get("done") for f in frames)


def test_brain_error_surfaces_error_frame(monkeypatch):
    async def broken_brain(payload, caller=None, stream=True, db=None):
        raise RuntimeError("brain exploded")
    frames = _post(_app(monkeypatch, broken_brain))
    assert any("brain exploded" in str(f.get("error", "")) for f in frames), frames


def _streaming_brain(first_chunk_delay):
    """voice_command's chat tier: returns a StreamingResponse INSTANTLY and does
    the brain work lazily inside the generator (the live failure mode: the
    task-race resolved in <filler_after, then TTFA sat at ~6s with no filler)."""
    from fastapi.responses import StreamingResponse

    async def brain(payload, caller=None, stream=True, db=None):
        async def _gen():
            await asyncio.sleep(first_chunk_delay)
            yield (json.dumps({"chunk": 0, "text": "Real answer."}) + "\n").encode()
            yield base64.b64encode(b"RIFFreal") + b"\n"
            yield (json.dumps({"done": True, "reply": "Real answer."}) + "\n").encode()
        return StreamingResponse(_gen(), media_type="application/x-zoe-audio-stream")
    return brain


def test_lazy_stream_slow_first_chunk_speaks_filler(monkeypatch):
    frames = _post(_app(monkeypatch, _streaming_brain(first_chunk_delay=0.6)))
    kinds = [("filler" if f.get("provider") == "filler" else
              "chunk0" if f.get("chunk") == 0 else
              "done" if f.get("done") else "other") for f in frames]
    assert "filler" in kinds, f"filler must race the first body chunk too: {frames}"
    assert kinds.index("filler") < kinds.index("chunk0"), "filler must precede the real first chunk"
    assert any(f.get("done") for f in frames)


def test_failed_filler_synthesis_not_retried_late(monkeypatch):
    """If the first race attempts the filler but TTS fails, the body-chunk race
    must NOT retry it on its 0.1s floor budget — a >1.6s filler is just noise."""
    async def slow_brain(payload, caller=None, stream=True, db=None):
        await asyncio.sleep(0.4)  # > filler_after: first race attempts the filler
        return await _streaming_brain(first_chunk_delay=0.4)(payload, caller=caller, stream=stream, db=db)
    app = _app(monkeypatch, slow_brain)

    calls = {"n": 0}
    async def _broken_tts(_text):
        calls["n"] += 1
        return None  # synthesis fails for filler AND fallback
    import routers.voice_tts as _vt
    monkeypatch.setattr(_vt, "_synthesize_kokoro_sidecar", _broken_tts)
    monkeypatch.setattr(_vt, "_synthesize_kokoro", _broken_tts)

    frames = _post(app)
    assert not any(f.get("provider") == "filler" for f in frames), frames
    assert calls["n"] == 2, f"one attempt (sidecar+fallback), no late retry: {calls}"
    assert any(f.get("done") for f in frames)


def test_lazy_stream_fast_first_chunk_skips_filler(monkeypatch):
    frames = _post(_app(monkeypatch, _streaming_brain(first_chunk_delay=0.0)))
    assert not any(f.get("provider") == "filler" for f in frames), frames
    assert any(f.get("chunk") == 0 for f in frames)
    assert any(f.get("done") for f in frames)
