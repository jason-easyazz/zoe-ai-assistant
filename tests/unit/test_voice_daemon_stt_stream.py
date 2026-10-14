"""Daemon half of STT under speech (ZOE_STT_STREAM_UPLOAD, default OFF).

Pins: flag off -> no uploader and no payload fields; uploader batches the mic audio in order with the exact sample
total; any failed POST (or a 409 from a server with the flag off) means NO stream id on the turn and, for 409, a latch;
the id is attached only when the sample count equals the WAV's, once, never to a speculative turn.
Every recording that becomes a turn of its own streams, not just the wake command: record_command AND the follow-up /
conversation windows (_follow_up_source -> _follow_up_listen(stream_upload=True)). The continuation listens (hard-cap merge,
held-turn resume) never do - their audio is merged into an earlier clip.
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


def _digest(n: int) -> str:
    import hashlib
    return hashlib.sha256(b"\x02\x00" * n).hexdigest()


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
    daemon._pending_stt_stream.update(stream_id="ab" * 8, samples=500, digest=_digest(500))
    bad: dict = {}
    daemon._attach_stt_stream(bad, _wav(501), None)
    assert bad == {} and not daemon._pending_stt_stream  # consumed even on mismatch: never reused for another clip
    daemon._pending_stt_stream.update(stream_id="ab" * 8, samples=500, digest=_digest(500))
    good: dict = {}
    daemon._attach_stt_stream(good, _wav(500), None)
    assert good == {"stt_stream_id": "ab" * 8, "stt_stream_samples": 500}
    again: dict = {}
    daemon._attach_stt_stream(again, _wav(500), None)
    assert again == {}


def test_speculative_turn_never_takes_the_stream(daemon):
    daemon._pending_stt_stream.update(stream_id="cd" * 8, samples=500, digest=_digest(500))
    payload: dict = {}
    daemon._attach_stt_stream(payload, _wav(500), object())
    assert payload == {} and daemon._pending_stt_stream  # left for the real (non-speculative) turn


def test_close_is_bounded_when_the_upload_hangs(daemon):
    """A stuck chunk POST must not hold up the turn: close() gives up at its deadline and attaches nothing."""
    import threading
    import time

    release = threading.Event()

    def slow_post(path, body, **kw):
        release.wait(5)
        return {"ok": True}

    up = daemon._SttStreamUploader(post=slow_post)
    try:
        up.push(b"\x01\x00" * 800)
        t0 = time.monotonic()
        assert up.close(wait_s=0.2) is None  # still uploading at the deadline -> batch STT, no stream id
        assert time.monotonic() - t0 < 1.0
    finally:
        release.set()


def test_a_stale_upload_never_attaches_to_a_different_clip_of_the_same_length(daemon):
    """Codex P2: an upload left pending (e.g. after a committed speculative turn) must not name a LATER clip, even
    when the lengths match - the server would serve the previous utterance's transcript."""
    import hashlib
    other = hashlib.sha256(b"\x07\x00" * 500).hexdigest()  # the earlier utterance: same length, different audio
    daemon._pending_stt_stream.update(stream_id="ef" * 8, samples=500, digest=other)
    payload: dict = {}
    daemon._attach_stt_stream(payload, _wav(500), None)
    assert payload == {} and not daemon._pending_stt_stream  # not attached, and consumed


def test_uploader_digest_matches_the_wav_it_produced(daemon):
    up = daemon._SttStreamUploader(post=lambda path, body, **kw: {"ok": True})
    up.push(b"\x02\x00" * 300)
    up.push(b"\x02\x00" * 200)
    assert up.close() is not None
    assert up.digest() == _digest(500)


def test_close_deadline_cancels_the_remaining_upload(daemon):
    """Codex P2: once close() gives up, the queued batches of the abandoned turn are discarded, not uploaded."""
    import threading

    release = threading.Event()
    sent = []

    def slow_post(path, body, **kw):
        sent.append(body["seq"])
        release.wait(5)
        return {"ok": True}

    up = daemon._SttStreamUploader(post=slow_post)
    batch = int(2 * daemon.SAMPLE_RATE * daemon._STT_STREAM_BATCH_S)
    for _ in range(4):
        up.push(b"\x01\x00" * (batch // 2))  # four full batches queued behind the stuck first POST
    assert up.close(wait_s=0.2) is None
    release.set()
    up._thread.join(2)
    assert not up._thread.is_alive() and sent == [0]  # only the in-flight batch was ever sent


def test_uploads_only_when_turns_use_the_streaming_endpoint(daemon, monkeypatch):
    """Codex P2: the blocking /turn never consumes a stream id, so ZOE_VOICE_STREAM=0 must not upload."""
    monkeypatch.setattr(daemon, "ZOE_STT_STREAM_UPLOAD", True)
    monkeypatch.setattr(daemon, "VOICE_STREAM_ENABLED", True)
    assert daemon._stt_stream_available()
    monkeypatch.setattr(daemon, "VOICE_STREAM_ENABLED", False)
    assert not daemon._stt_stream_available()


# -- every turn-making recording streams (live 2026-10-13: only the wake command did; follow-ups fell back to batch) --

SPEECH, DEEP = 0.9, 0.02


class _FakeStream:
    def __init__(self, fail_after: int | None = None):
        self.reads = 0
        self._fail_after = fail_after

    def read(self, size, exception_on_overflow=True):  # noqa: ARG002
        self.reads += 1
        if self._fail_after is not None and self.reads > self._fail_after:
            raise OSError("mic gone")
        return b"\x01\x00" * size

    def stop_stream(self):
        pass

    def close(self):
        pass


class _FakePA:
    def __init__(self, fail_after: int | None = None):
        self._fail_after = fail_after

    def get_sample_size(self, fmt):  # noqa: ARG002
        return 2

    def open(self, **kw):  # noqa: ARG002
        return _FakeStream(self._fail_after)


@pytest.fixture()
def lane(daemon, monkeypatch):
    """Flags on, scripted VAD, and a fake chunk endpoint recording every POST."""
    posts: list[dict] = []
    state = {"resp": {"ok": True}}

    def fake_post(path, body, timeout=5, retries=0):  # noqa: ARG001
        posts.append({"path": path, "stream_id": body["stream_id"], "seq": body["seq"],
                      "pcm": base64.b64decode(body["pcm_base64"])})
        return state["resp"]

    monkeypatch.setattr(daemon, "ZOE_STT_STREAM_UPLOAD", True)
    monkeypatch.setattr(daemon, "VOICE_STREAM_ENABLED", True)
    monkeypatch.setattr(daemon, "_api_post", fake_post)
    monkeypatch.setattr(daemon, "VAD_ENDPOINT_ENABLED", True)
    monkeypatch.setattr(daemon, "ZOE_VAD_TAIL_MS", 640)
    monkeypatch.setattr(daemon, "ZOE_SPECULATIVE_TURN", False)
    monkeypatch.setattr(daemon, "FOLLOW_UP_VAD_THRESHOLD", 0.5)
    monkeypatch.setattr(daemon, "_PREROLL", [])
    monkeypatch.setattr(daemon, "_get_silero_vad", lambda: (object(), None))
    monkeypatch.setattr(daemon, "_last_record_capped", False)
    monkeypatch.setattr(daemon, "_notify_wake_background", lambda: None)

    def script(probs):
        seq = iter(probs)
        monkeypatch.setattr(daemon, "_vad_prob", lambda _m, _c: next(seq))

    return types.SimpleNamespace(posts=posts, state=state, script=script)


def _assert_stream_covers(daemon, lane, wav: bytes) -> str:
    """The posted chunks are ONE stream id, contiguous from seq 0, carry exactly the WAV's PCM, and the turn payload
    names that stream with the WAV's sample count."""
    with wave.open(io.BytesIO(wav), "rb") as w:
        pcm = w.readframes(w.getnframes())
        frames = w.getnframes()
    assert lane.posts, "no chunk was uploaded"
    assert {p["stream_id"] for p in lane.posts} == {lane.posts[0]["stream_id"]}
    assert [p["seq"] for p in lane.posts] == list(range(len(lane.posts)))
    assert b"".join(p["pcm"] for p in lane.posts) == pcm
    payload: dict = {}
    daemon._attach_stt_stream(payload, wav, None)
    assert payload == {"stt_stream_id": lane.posts[0]["stream_id"], "stt_stream_samples": frames}
    return lane.posts[0]["stream_id"]


def test_follow_up_window_uploads_with_its_own_session_and_names_it_on_the_turn(daemon, lane):
    lane.script([SPEECH] * 40 + [DEEP] * 40)
    wav = daemon._follow_up_source(_FakePA(), None, beep=False, window_s=5.0)
    assert wav is not None
    _assert_stream_covers(daemon, lane, wav)
    assert all(p["path"] == "/api/voice/stt_stream/chunk" for p in lane.posts)


def test_conversation_window_uploads_too(daemon, lane):
    lane.script([SPEECH] * 40 + [DEEP] * 40)
    wav = daemon._follow_up_source(_FakePA(), None, beep=False, window_s=5.0,
                                   hard_cap_s=daemon.CONV_RECORD_SECONDS_HARD)
    assert wav is not None
    _assert_stream_covers(daemon, lane, wav)


def test_wake_command_still_uploads_after_the_shared_helper_refactor(daemon, lane):
    lane.script([SPEECH] * 40 + [DEEP] * 40)
    wav = daemon.record_command(_FakePA(), stream=_FakeStream())
    assert wav is not None
    _assert_stream_covers(daemon, lane, wav)


def test_each_recording_gets_a_fresh_stream_id(daemon, lane):
    lane.script([SPEECH] * 40 + [DEEP] * 40 + [SPEECH] * 40 + [DEEP] * 40)
    first = daemon._follow_up_source(_FakePA(), None, beep=False, window_s=5.0)
    id1 = _assert_stream_covers(daemon, lane, first)
    lane.posts.clear()
    second = daemon._follow_up_source(_FakePA(), None, beep=False, window_s=5.0)
    id2 = _assert_stream_covers(daemon, lane, second)
    assert id1 != id2


def test_negative_control_old_wiring_sends_no_chunks(daemon, lane, monkeypatch):
    """Break the fix: a _follow_up_source that does not ask for streaming (the pre-fix wiring) uploads nothing and the
    turn carries no stream id - this is exactly what the live follow-up turns did."""
    real = daemon._follow_up_listen
    monkeypatch.setattr(daemon, "_follow_up_listen",
                        lambda pa, **kw: real(pa, **{k: v for k, v in kw.items() if k != "stream_upload"}))
    lane.script([SPEECH] * 40 + [DEEP] * 40)
    wav = daemon._follow_up_source(_FakePA(), None, beep=False, window_s=5.0)
    assert wav is not None and lane.posts == []
    payload: dict = {}
    daemon._attach_stt_stream(payload, wav, None)
    assert payload == {}


def test_continuation_listens_never_stream_and_leave_the_earlier_clips_id_alone(daemon, lane):
    """The hard-cap merge and the held-turn resume call _follow_up_listen for audio that is merged into an earlier clip;
    streaming them would clobber that clip's pending id with one that can never match the merged WAV."""
    daemon._pending_stt_stream.update(stream_id="ab" * 8, samples=500, digest=_digest(500))
    lane.script([SPEECH] * 40 + [DEEP] * 40)
    assert daemon._follow_up_listen(_FakePA(), window_s=5.0) is not None
    assert lane.posts == []
    assert daemon._pending_stt_stream["stream_id"] == "ab" * 8
    src = (DAEMON).read_text(encoding="utf-8")
    assert src.count("_follow_up_listen(pa, window_s=ZOE_CONTINUE_WINDOW_S)") == 2  # both continuation callers, flag-less


def test_a_merged_continuation_clip_never_names_the_stream(daemon, lane):
    lane.script([SPEECH] * 40 + [DEEP] * 40)
    first = daemon._follow_up_source(_FakePA(), None, beep=False, window_s=5.0)
    merged = daemon._merge_wavs(first, _wav(8000))
    payload: dict = {}
    daemon._attach_stt_stream(payload, merged, None)
    assert payload == {}  # digest/length mismatch -> batch STT for the merged clip, no wrong transcript


def test_upload_error_falls_back_to_batch_and_keeps_the_recording(daemon, lane):
    lane.state["resp"] = {"ok": False, "error": "HTTP 500"}
    lane.script([SPEECH] * 40 + [DEEP] * 40)
    wav = daemon._follow_up_source(_FakePA(), None, beep=False, window_s=5.0)
    assert wav is not None
    payload: dict = {}
    daemon._attach_stt_stream(payload, wav, None)
    assert payload == {} and not daemon._stt_stream_disabled.is_set()


def test_409_in_a_follow_up_latches_off_and_the_next_window_does_not_upload(daemon, lane):
    lane.state["resp"] = {"ok": False, "error": "HTTP 409"}
    lane.script([SPEECH] * 40 + [DEEP] * 40 + [SPEECH] * 40 + [DEEP] * 40)
    wav = daemon._follow_up_source(_FakePA(), None, beep=False, window_s=5.0)
    assert wav is not None and daemon._stt_stream_disabled.is_set()
    lane.posts.clear()
    assert daemon._follow_up_source(_FakePA(), None, beep=False, window_s=5.0) is not None
    assert lane.posts == []


def test_flag_off_follow_up_uploads_nothing(daemon, lane, monkeypatch):
    monkeypatch.setattr(daemon, "ZOE_STT_STREAM_UPLOAD", False)
    lane.script([SPEECH] * 40 + [DEEP] * 40)
    assert daemon._follow_up_source(_FakePA(), None, beep=False, window_s=5.0) is not None
    assert lane.posts == [] and not daemon._pending_stt_stream


def test_a_mic_error_mid_follow_up_abandons_the_upload(daemon, lane):
    lane.script([SPEECH] * 80)
    # drain (2 reads) + trigger read, then the mic dies mid-recording
    assert daemon._follow_up_source(_FakePA(fail_after=8), None, beep=False, window_s=5.0) is None
    assert not daemon._pending_stt_stream
    import threading
    assert not [t for t in threading.enumerate() if t.name == "stt-stream-upload" and t.is_alive()]


def test_a_stale_pending_stream_is_dropped_when_a_new_window_opens(daemon, lane):
    """A pending id left by a committed speculative wake turn must not survive into the next recording."""
    daemon._pending_stt_stream.update(stream_id="ab" * 8, samples=500, digest=_digest(500))
    lane.script([DEEP] * 10)
    assert daemon._follow_up_source(_FakePA(), None, beep=False, window_s=0.0) is None  # silent window
    assert not daemon._pending_stt_stream
    daemon._pending_stt_stream.update(stream_id="cd" * 8, samples=500, digest=_digest(500))
    assert daemon._follow_up_source(_FakePA(), b"SEED", beep=False) == b"SEED"
    assert not daemon._pending_stt_stream  # a barge-in seed was never uploaded
