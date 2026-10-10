"""Daemon half of STT under speech (ZOE_STT_STREAM_UPLOAD, default OFF).

Pins: flag off -> no uploader and no payload fields; uploader batches the mic audio in order with the exact sample
total; any failed POST (or a 409 from a server with the flag off) means NO stream id on the turn and, for 409, a latch;
the id is attached only when the sample count equals the WAV's, once, never to a speculative turn.
Loaded with stubbed pyaudio/requests like test_voice_daemon_speculation.py.
"""
from __future__ import annotations

import base64
import importlib.util
import io
import sys
import types
import wave
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

DAEMON = Path(__file__).resolve().parents[2] / "scripts" / "setup" / "zoe_voice_daemon.py"


def _load(name: str):
    stubs = {}
    fake_pyaudio = types.ModuleType("pyaudio")
    fake_pyaudio.paInt16 = 8
    fake_pyaudio.PyAudio = object
    stubs["pyaudio"] = fake_pyaudio
    fake_requests = types.ModuleType("requests")
    fake_requests.exceptions = types.SimpleNamespace(
        SSLError=type("SSLError", (Exception,), {}),
        HTTPError=type("HTTPError", (Exception,), {}),
        RequestException=type("RequestException", (Exception,), {}),
    )
    stubs["requests"] = fake_requests
    saved = {n: sys.modules.get(n) for n in stubs}
    sys.modules.update(stubs)
    try:
        spec = importlib.util.spec_from_file_location(name, DAEMON)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        for n, prev in saved.items():
            if prev is not None:
                sys.modules[n] = prev
            else:
                sys.modules.pop(n, None)


@pytest.fixture()
def daemon():
    d = _load("zoe_voice_daemon_sttstream_test")
    d._stt_stream_disabled.clear()
    d._pending_stt_stream.clear()
    return d


def _wav(n: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x02\x00" * n)
    return buf.getvalue()


def test_flag_off_is_inert(daemon, monkeypatch):
    monkeypatch.setattr(daemon, "ZOE_STT_STREAM_UPLOAD", False)
    assert not daemon._stt_stream_available()
    payload: dict = {}
    daemon._attach_stt_stream(payload, _wav(100), None)
    assert payload == {}


def test_uploader_sends_every_sample_in_order(daemon):
    sent = []

    def post(path, body, timeout=5, retries=0):
        sent.append((path, body["seq"], base64.b64decode(body["pcm_base64"])))
        return {"ok": True}

    up = daemon._SttStreamUploader(post=post)
    chunk = b"\x01\x00" * daemon.CHUNK_SIZE
    for _ in range(40):
        up.push(chunk)
    sid, samples = up.close()
    assert samples == 40 * daemon.CHUNK_SIZE and sid == up.stream_id
    assert [s[1] for s in sent] == list(range(len(sent)))  # contiguous seq from 0
    assert sum(len(s[2]) for s in sent) == 2 * samples
    assert all(s[0] == "/api/voice/stt_stream/chunk" for s in sent)
    assert len(sent) > 1  # batched, not one giant POST at the end


def test_failed_post_means_no_stream_id(daemon):
    up = daemon._SttStreamUploader(post=lambda *a, **k: {"ok": False, "error": "HTTP 500"})
    up.push(b"\x01\x00" * 6000)
    assert up.close() is None
    assert not daemon._stt_stream_disabled.is_set()


def test_409_latches_the_uploader_off(daemon, monkeypatch):
    monkeypatch.setattr(daemon, "ZOE_STT_STREAM_UPLOAD", True)
    up = daemon._SttStreamUploader(post=lambda *a, **k: {"ok": False, "error": "HTTP 409"})
    up.push(b"\x01\x00" * 6000)
    assert up.close() is None
    assert daemon._stt_stream_disabled.is_set() and not daemon._stt_stream_available()


def test_attach_requires_matching_samples_and_is_single_use(daemon):
    daemon._pending_stt_stream.update(stream_id="ab" * 8, samples=500)
    bad: dict = {}
    daemon._attach_stt_stream(bad, _wav(501), None)
    assert bad == {} and not daemon._pending_stt_stream  # consumed even on mismatch: never reused for another clip
    daemon._pending_stt_stream.update(stream_id="ab" * 8, samples=500)
    good: dict = {}
    daemon._attach_stt_stream(good, _wav(500), None)
    assert good == {"stt_stream_id": "ab" * 8, "stt_stream_samples": 500}
    again: dict = {}
    daemon._attach_stt_stream(again, _wav(500), None)
    assert again == {}


def test_speculative_turn_never_takes_the_stream(daemon):
    daemon._pending_stt_stream.update(stream_id="cd" * 8, samples=500)
    payload: dict = {}
    daemon._attach_stt_stream(payload, _wav(500), object())
    assert payload == {} and daemon._pending_stt_stream  # left for the real (non-speculative) turn
