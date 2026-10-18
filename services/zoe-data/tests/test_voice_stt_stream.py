"""STT under speech (ZOE_STT_STREAM_UNDER_SPEECH, default OFF), server half: flag off = 409 and no stream id is read; a
stream transcript is used ONLY when its sample count equals the WAV's and is non-empty; every other shape falls back to
batch STT; sessions are single-use and bounded; a negative control shows the sample-count check is what protects it.
Fakes only (no Moonshine, network or DB)."""
from __future__ import annotations

import asyncio
import base64
import io
import threading
import time
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
    ss._sessions.clear(); ss._live.clear()
    monkeypatch.setenv("ZOE_STT_STREAM_UNDER_SPEECH", "1")
    yield
    for s in list(ss._sessions.values()):
        s.abort()
    ss._sessions.clear(); ss._live.clear()


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


def _daemon_default(pattern: str) -> float:
    import re
    from pathlib import Path

    src = (Path(__file__).resolve().parents[3] / "scripts" / "setup" / "zoe_voice_daemon.py").read_text(encoding="utf-8")
    m = re.search(pattern, src)
    assert m, f"daemon default not found: {pattern}"
    return float(m.group(1))


def test_the_cap_covers_the_longest_turn_the_daemon_sends():
    """A conversation window records up to CONV_RECORD_SECONDS_HARD_MAX plus the RECORD_SNAP_S tail (60 + 3 s by
    default). The cap was 30 s, so every longer conversation turn broke its stream and fell back to batch STT. Drift
    pin: raising the daemon's defaults without the server cap goes red here (break-the-fix: MAX_TURN_AUDIO_S = 30)."""
    conv = _daemon_default(r'_count_env\("CONV_RECORD_SECONDS_HARD_MAX",\s*(\d+)')
    snap = _daemon_default(r'_float_env\("RECORD_SNAP_S",\s*([\d.]+)\)')
    longest = (conv + snap) * ss.SAMPLE_RATE
    assert ss.MAX_SAMPLES >= longest, (ss.MAX_SAMPLES, longest)
    # and one such turn, fed the way the daemon sends it (0.32 s batches), is accepted end to end
    sess, _ = open_session()
    step = int(0.32 * ss.SAMPLE_RATE)
    total, seq = int(longest), 0
    for start in range(0, total, step):
        assert sess.feed(seq, pcm(min(step, total - start))), f"chunk {seq} refused at {start / ss.SAMPLE_RATE:.1f}s"
        seq += 1
    assert sess.samples_fed == total and sess.broken is None
    sess.abort()


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


def test_speculative_turn_never_takes_a_stream():
    """B1.1 safety contract, server-enforced: a speculative turn must not consume the single-use stream."""
    sess, _ = open_session("9" * 16)
    sess.feed(0, pcm(2000))
    payload = {"stt_stream_id": "9" * 16, "stt_stream_samples": 2000, "speculative": True, "turn_id": "t1"}
    assert _take(payload, wav(2000)) is None
    assert "9" * 16 in ss._sessions  # untouched: the committed turn can still take it
    payload.pop("speculative")
    assert _take(payload, wav(2000)).lower() == "what is on my calendar"


def test_finish_timeout_falls_back(monkeypatch):
    release = threading.Event()

    class HungStream(FakeStream):
        def stop(self):
            release.wait(5)
            return super().stop()

    monkeypatch.setenv("ZOE_STT_STREAM_FINISH_TIMEOUT_MS", "50")
    sess, _ = open_session("b" * 16, HungStream())
    sess.feed(0, pcm(2000))
    try:
        assert _take({"stt_stream_id": "b" * 16, "stt_stream_samples": 2000}, wav(2000)) is None
        assert sess.broken == "timeout"
    finally:
        release.set()


def test_idle_session_is_reaped(monkeypatch):
    sess, _ = open_session("c" * 16)
    sess.touched -= ss.IDLE_TTL_S + 1  # idle past the TTL
    ss.get_or_open("0" * 16, 0, lambda: FakeStream(), threading.Lock())  # any open reaps first
    assert "c" * 16 not in ss._sessions and "0" * 16 in ss._sessions


def test_taken_sessions_count_until_their_worker_closes(monkeypatch):
    """A turn take()s a session but its worker may still run (slow / timed-out stop()): it must keep its slot."""
    release = threading.Event()

    class HungStream(FakeStream):
        def stop(self):
            release.wait(5)
            return super().stop()

    monkeypatch.setenv("ZOE_STT_STREAM_FINISH_TIMEOUT_MS", "50")
    try:
        for sid in ("d1" * 8, "d2" * 8):
            sess, _ = open_session(sid, HungStream())
            sess.feed(0, pcm(2000))
            assert _take({"stt_stream_id": sid, "stt_stream_samples": 2000}, wav(2000)) is None  # timed out
        assert not ss._sessions  # both consumed by their turns...
        assert ss.get_or_open("d3" * 8, 0, lambda: FakeStream(), threading.Lock()) is None  # ...but still counted
    finally:
        release.set()
    for s in list(ss._live):
        s._done.wait(5)
    assert ss.get_or_open("d4" * 8, 0, lambda: FakeStream(), threading.Lock()) is not None  # slots freed on close


# -- router wiring (TestClient; fakes only) ------------------------------------------------------------------------

ABSENT = "<no pre_text kwarg>"


def _turn_app(monkeypatch, seen):
    from fastapi import FastAPI

    monkeypatch.setenv("ZOE_VOICE_FILLER_ENABLED", "0")
    monkeypatch.setenv("ZOE_VOICE_GREETING_ENABLED", "0")

    async def _fake_stt(_path, capture=True, **kw):
        seen.append(kw.get("pre_text", ABSENT))
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
        assert seen == [ABSENT]
        sess, _ = open_session("8" * 16)
        sess.feed(0, pcm(3000))
        _post_turn(client, 3001, {"stt_stream_id": "8" * 16, "stt_stream_samples": 3000})  # clip != stream
        assert seen[-1] == ABSENT


# -- Greptile round 1 (#1972) ---------------------------------------------------------------------------------------

def test_take_does_not_block_on_a_stream_being_created():
    """P1: stream creation happens outside the registry lock, so take() on the event loop never waits on a model load."""
    started, release = threading.Event(), threading.Event()

    def slow_make():
        started.set()
        release.wait(5)
        return FakeStream()

    opener = threading.Thread(target=ss.get_or_open, args=("a1" * 8, 0, slow_make, threading.Lock()))
    opener.start()
    try:
        assert started.wait(2)
        done = []
        t = threading.Thread(target=lambda: done.append(ss.take("b2" * 8)))
        t.start()
        t.join(1.0)
        assert not t.is_alive(), "take() blocked behind stream creation"
        assert done == [None]
        # the bound still counts the reserved slot
        assert ss.get_or_open("c3" * 8, 0, lambda: FakeStream(), threading.Lock()) is not None
        assert ss.get_or_open("d4" * 8, 0, lambda: FakeStream(), threading.Lock()) is None
    finally:
        release.set()
        opener.join(3)
    assert "a1" * 8 in ss._sessions and not ss._pending


def test_failed_stream_creation_releases_the_reservation():
    def boom():
        raise RuntimeError("model load failed")

    with pytest.raises(RuntimeError):
        ss.get_or_open("e5" * 8, 0, boom, threading.Lock())
    assert not ss._pending and "e5" * 8 not in ss._sessions


def test_batch_fallback_is_bounded_behind_a_stalled_stream(monkeypatch):
    """P1: a stream that timed out in stop() still holds the shared infer lock; the batch path (real _run_moonshine
    locking) must give up in bounded time instead of waiting for it."""
    release = threading.Event()
    lock = threading.Lock()

    class HungStream(FakeStream):
        def stop(self):
            release.wait(10)
            return super().stop()

    monkeypatch.setenv("ZOE_STT_STREAM_FINISH_TIMEOUT_MS", "50")
    monkeypatch.setattr(ss, "STALL_LOCK_WAIT_S", 0.3)
    sess = ss.get_or_open("f6" * 8, 0, lambda: HungStream(), lock)
    sess.feed(0, pcm(2000))
    monkeypatch.setattr(vt, "_moonshine_infer_lock", lock)
    monkeypatch.setattr(vt, "_ensure_moonshine", lambda: types.SimpleNamespace(
        transcribe_without_streaming=lambda a, sr: types.SimpleNamespace(lines=[types.SimpleNamespace(text="hi")])))
    import sys
    fake_utils = types.ModuleType("moonshine_voice.utils")
    fake_utils.load_wav_file = lambda p: ([0.0] * 1600, 16000)
    monkeypatch.setitem(sys.modules, "moonshine_voice", types.ModuleType("moonshine_voice"))
    monkeypatch.setitem(sys.modules, "moonshine_voice.utils", fake_utils)
    try:
        assert _take({"stt_stream_id": "f6" * 8, "stt_stream_samples": 2000}, wav(2000)) is None
        assert sess in ss._stalled
        t0 = time.monotonic()
        with pytest.raises(TimeoutError):
            asyncio.run(vt._run_moonshine("/nonexistent.wav"))
        assert time.monotonic() - t0 < 3
        release.set()
        sess._done.wait(3)
        assert asyncio.run(vt._run_moonshine("/nonexistent.wav")) == "Hi"  # recovered once the stall cleared
    finally:
        release.set()


def test_infer_guard_is_a_plain_lock_without_a_stall():
    lock = threading.Lock()
    with ss.infer_guard(lock):
        assert lock.locked()
    assert not lock.locked()


def test_abandoned_session_self_cleans(monkeypatch):
    """P2: a session nobody feeds or finishes ends its own worker after the idle TTL and deregisters."""
    monkeypatch.setattr(ss, "IDLE_TTL_S", 0.2)
    stream = FakeStream()
    sess = ss.get_or_open("a7" * 8, 0, lambda: stream, threading.Lock())
    assert sess._done.wait(3), "worker still blocked on an abandoned session"
    assert sess.broken == "idle" and stream.closed
    assert "a7" * 8 not in ss._sessions


def test_empty_chunks_are_rejected_and_do_not_refresh_idle(monkeypatch):
    sess, _ = open_session("a8" * 8)
    sess.touched -= 10
    before = sess.touched
    assert sess.feed(0, b"") is False
    assert sess.next_seq == 0 and sess.touched == before and not sess.broken
    assert sess.feed(0, pcm(100)) is True  # seq 0 is still the next expected
    # endpoint: 400 on empty
    with pytest.raises(HTTPException) as ei:
        asyncio.run(vt.voice_stt_stream_chunk(
            {"stream_id": "a9" * 8, "seq": 0, "pcm_base64": ""}, caller={}))
    assert ei.value.status_code == 400


def test_queue_length_is_bounded(monkeypatch):
    gate = threading.Event()

    class Slow(FakeStream):
        def add_audio(self, audio, sr):
            gate.wait(5)

    monkeypatch.setattr(ss, "MAX_QUEUE", 3)
    sess, _ = open_session("b9" * 8, Slow())
    try:
        results = [sess.feed(i, pcm(10)) for i in range(8)]
        assert results[:2] == [True, True] and False in results
        assert sess.broken == "bad_chunk"
    finally:
        gate.set()


def _uses_production_finish_timeout(src: str) -> bool:
    return "ss.finish_timeout_s()" in src and "sess.finish, n, 30.0" not in src


def test_replay_arm_uses_the_production_finish_timeout(monkeypatch, tmp_path):
    """Reads the harness SOURCE by path. It used to ``import tests.replay_samples``, which
    resolves only when services/zoe-data is the rootdir (from the repo root it found the
    repo-level ``tests/`` namespace: ModuleNotFoundError) and, when it did resolve, ran the
    module's import-time ``os.chdir`` for the rest of the session."""
    from pathlib import Path

    monkeypatch.chdir(tmp_path)                       # the answer must not depend on the cwd
    src = (Path(__file__).resolve().parent / "replay_samples.py").read_text(encoding="utf-8")
    assert _uses_production_finish_timeout(src)


def test_negative_control_the_timeout_check_goes_red_on_a_hardcoded_wait():
    good = "await asyncio.to_thread(sess.finish, n, ss.finish_timeout_s())"
    assert _uses_production_finish_timeout(good)
    assert not _uses_production_finish_timeout(good.replace("ss.finish_timeout_s()", "30.0"))
