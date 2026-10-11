"""STT under speech (ZOE_STT_STREAM_UNDER_SPEECH, default OFF), server half: flag off = 409 and no stream id is read; a
stream transcript is used ONLY when its sample count equals the WAV's and is non-empty; every other shape falls back to
batch STT; sessions are single-use and bounded; a negative control shows the sample-count check is what protects it.
Fakes only (no Moonshine, network or DB)."""
from __future__ import annotations

import asyncio
import base64
import io
import threading
import types
import wave

import pytest

pytestmark = pytest.mark.ci_safe

from fastapi import HTTPException

import routers.voice_tts as vt
import voice_stt_stream as ss


class FakeStream:
    def __init__(self, text="what is on my calendar", fail=False):
        self.audio_samples = 0
        self.text = text
        self.fail = fail
        self.closed = False

    def add_audio(self, audio, sr):
        if self.fail:
            raise RuntimeError("boom")
        self.audio_samples += len(audio)

    def stop(self):
        return types.SimpleNamespace(lines=[types.SimpleNamespace(text=self.text)] if self.text else [])

    def close(self):
        self.closed = True


def pcm(n_samples: int) -> bytes:
    return b"\x01\x00" * n_samples


def wav(n_samples: int, rate: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm(n_samples))
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    ss._sessions.clear()
    monkeypatch.setenv("ZOE_STT_STREAM_UNDER_SPEECH", "1")
    yield
    for s in list(ss._sessions.values()):
        s.abort()
    ss._sessions.clear()


def open_session(sid="a" * 16, stream=None):
    stream = stream or FakeStream()
    sess = ss.get_or_open(sid, 0, lambda: stream, threading.Lock())
    return sess, stream


def test_session_finishes_with_transcript_and_counts_samples():
    sess, stream = open_session()
    assert sess.feed(0, pcm(5120)) and sess.feed(1, pcm(5120))
    lines, reason = sess.finish(10240, 5.0)
    assert reason == "ok" and lines == ["what is on my calendar"]
    assert stream.audio_samples == 10240 and stream.closed


def test_seq_gap_breaks_the_session():
    sess, _ = open_session()
    assert sess.feed(0, pcm(100))
    assert not sess.feed(2, pcm(100))  # seq 1 missing
    assert sess.finish(200, 1.0) == (None, "bad_chunk")


def test_sample_mismatch_falls_back():
    sess, _ = open_session()
    sess.feed(0, pcm(1000))
    assert sess.finish(1001, 1.0) == (None, "sample_mismatch")


def test_engine_error_falls_back():
    sess, _ = open_session(stream=FakeStream(fail=True))
    sess.feed(0, pcm(100))
    lines, reason = sess.finish(100, 2.0)
    assert lines is None and reason.startswith("engine:")


def test_oversize_audio_breaks_the_session():
    sess, _ = open_session()
    assert not sess.feed(0, pcm(ss.MAX_SAMPLES + 2))


def test_registry_is_bounded_single_use_and_opens_only_at_seq0():
    lock = threading.Lock()
    assert ss.get_or_open("b" * 16, 1, FakeStream, lock) is None  # mid-stream id never opens
    a = ss.get_or_open("a" * 16, 0, FakeStream, lock)
    b = ss.get_or_open("b" * 16, 0, FakeStream, lock)
    assert a and b and ss.get_or_open("c" * 16, 0, FakeStream, lock) is None  # MAX_SESSIONS
    assert ss.take("a" * 16) is a and ss.take("a" * 16) is None  # single-use


def test_wav_sample_count_rejects_wrong_format():
    assert ss.wav_sample_count(wav(1234)) == 1234
    assert ss.wav_sample_count(wav(1234, rate=8000)) is None
    assert ss.wav_sample_count(b"not a wav") is None


def _take(payload, raw):
    return asyncio.run(vt._take_stt_stream_text(payload, raw, ".wav"))


def test_take_hit_when_samples_match():
    sess, _ = open_session("d" * 16)
    sess.feed(0, pcm(2000))
    assert _take({"stt_stream_id": "d" * 16, "stt_stream_samples": 2000}, wav(2000)).lower() == "what is on my calendar"


def test_take_falls_back_on_every_failure_shape():
    # unknown id
    assert _take({"stt_stream_id": "e" * 16, "stt_stream_samples": 10}, wav(10)) is None
    # wav/stream mismatch
    sess, _ = open_session("f" * 16)
    sess.feed(0, pcm(2000))
    assert _take({"stt_stream_id": "f" * 16, "stt_stream_samples": 2000}, wav(2001)) is None
    # empty transcript -> batch STT must re-check the clip
    sess, _ = open_session("1" * 16, FakeStream(text=""))
    sess.feed(0, pcm(2000))
    assert _take({"stt_stream_id": "1" * 16, "stt_stream_samples": 2000}, wav(2000)) is None
    # garbage payload never raises
    assert _take({"stt_stream_id": "2" * 16, "stt_stream_samples": "x"}, wav(10)) is None
    assert _take({"stt_stream_id": 5}, wav(10)) is None
    assert _take({}, wav(10)) is None


def test_flag_off_turn_ignores_stream_and_endpoint_refuses(monkeypatch):
    sess, _ = open_session("3" * 16)
    sess.feed(0, pcm(2000))
    monkeypatch.setenv("ZOE_STT_STREAM_UNDER_SPEECH", "0")
    assert _take({"stt_stream_id": "3" * 16, "stt_stream_samples": 2000}, wav(2000)) is None
    assert "3" * 16 in ss._sessions  # untouched: the flag-off path does not even take it
    with pytest.raises(HTTPException) as e:
        asyncio.run(vt.voice_stt_stream_chunk(
            {"stream_id": "4" * 16, "seq": 0, "pcm_base64": base64.b64encode(pcm(10)).decode()}, caller={}))
    assert e.value.status_code == 409


def test_chunk_endpoint_validates_and_feeds(monkeypatch):
    fake = FakeStream()
    monkeypatch.setattr(vt, "_ensure_moonshine",
                        lambda: types.SimpleNamespace(create_stream=lambda: types.SimpleNamespace(
                            start=lambda: None, add_audio=fake.add_audio, stop=fake.stop, close=fake.close)))
    body = {"stream_id": "5" * 16, "seq": 0, "pcm_base64": base64.b64encode(pcm(800)).decode()}
    out = asyncio.run(vt.voice_stt_stream_chunk(body, caller={}))
    assert out == {"ok": True, "samples": 800}
    with pytest.raises(HTTPException) as e:
        asyncio.run(vt.voice_stt_stream_chunk({"stream_id": "NOT HEX", "seq": 0, "pcm_base64": ""}, caller={}))
    assert e.value.status_code == 400


def test_negative_control_sample_check_is_what_protects_the_transcript(monkeypatch):
    """Break the fix: neutralise the wav/stream sample check -> a mismatched clip would be served the stream text."""
    sess, _ = open_session("6" * 16)
    sess.feed(0, pcm(2000))
    monkeypatch.setattr(ss, "wav_sample_count", lambda raw: 2000)  # the broken check always agrees
    got = _take({"stt_stream_id": "6" * 16, "stt_stream_samples": 2000}, wav(3333))  # a different-length clip
    assert got.lower() == "what is on my calendar"  # RED proof: the unbroken check (previous test) returns None for this shape


# -- router wiring (TestClient; fakes only) ------------------------------------------------------------------------

def _turn_app(monkeypatch, seen):
    from fastapi import FastAPI

    monkeypatch.setenv("ZOE_VOICE_FILLER_ENABLED", "0")
    monkeypatch.setenv("ZOE_VOICE_GREETING_ENABLED", "0")

    async def _fake_stt(_path, capture=True, **kw):
        seen.append(kw.get("pre_text"))
        return kw.get("pre_text") or "batch transcript"

    async def _brain(payload, caller=None, stream=True, db=None):
        return {"reply": "ok", "audio_base64": base64.b64encode(b"RIFFnoon").decode()}

    async def _fake_tts(_text):
        return b"RIFFfakewav"

    monkeypatch.setattr(vt, "_transcribe_audio", _fake_stt)
    monkeypatch.setattr(vt, "_synthesize_kokoro_sidecar", _fake_tts)
    monkeypatch.setattr(vt, "voice_command", _brain)
    app = FastAPI()
    app.include_router(vt.router)
    app.dependency_overrides[vt._require_voice_auth] = lambda: {"source": "device", "panel_id": "p", "user_id": "u"}
    from database import get_db as _real_get_db
    app.dependency_overrides[_real_get_db] = lambda: None
    return app


def _post_turn(client, n, extra):
    body = {"audio_base64": base64.b64encode(wav(n)).decode(), "panel_id": "p", **extra}
    assert client.post("/api/voice/turn_stream", json=body).status_code == 200


def test_turn_stream_uses_stream_text_only_when_flag_on_and_samples_match(monkeypatch):
    from fastapi.testclient import TestClient

    seen: list = []
    with TestClient(_turn_app(monkeypatch, seen)) as client:
        sess, _ = open_session("7" * 16)
        sess.feed(0, pcm(3000))
        _post_turn(client, 3000, {"stt_stream_id": "7" * 16, "stt_stream_samples": 3000})
        assert seen == ["What is on my calendar"]  # the stream text reached the shared STT wrapper
        seen.clear()
        _post_turn(client, 3000, {})  # no stream id: byte-identical call, no kwarg at all
        assert seen == [None]
        sess, _ = open_session("8" * 16)
        sess.feed(0, pcm(3000))
        _post_turn(client, 3001, {"stt_stream_id": "8" * 16, "stt_stream_samples": 3000})  # clip != stream
        assert seen[-1] is None
