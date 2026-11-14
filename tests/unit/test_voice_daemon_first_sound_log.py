"""The panel daemon's first-sound instrument (S0, log-only): ``PCM_WRITE`` per chunk and ``FIRST_SOUND`` per turn.

Torch-free like test_voice_daemon_panel_cutoffs.py: ``_vad_prob`` is scripted, the clock (``_fs_now``) is scripted, and
the REAL ``record_command`` / ``_follow_up_listen`` / ``_do_single_turn_stream`` / ``_feed_pcm_chunk`` /
``_BargeMonitor._run`` run against fakes. Synthetic audio and invented phrases only. Pins:

  1. field order and the -1 sentinels (no close known, no sound);
  2. underrun arithmetic on a scripted write schedule (back-to-back playout, not write + duration);
  3. t_close is stamped at the committed close on wake and follow-up recordings, and carried to the turn;
     a speculative stream only takes a close stamped after it started;
  4. post-close talk counting in the monitor's idle branch (threshold, 1.5 s window, stops at the first sound);
  5. NO text from the person or the reply can reach either line;
  6. the existing ``turn_stream TTFA=`` and ``Recorded command:`` lines are unchanged;
  7. behaviour neutrality: the same playback bytes, POSTs, return value and barge decisions with the instrument
     stubbed out, and with it raising.
Each computation has a negative control (a naive variant that the test would catch).
"""
from __future__ import annotations

import base64
import importlib.util
import io
import json
import logging
import re
import sys
import types
import wave
from pathlib import Path

import numpy as np
import pytest

pytestmark = pytest.mark.ci_safe

DAEMON = Path(__file__).resolve().parents[2] / "scripts" / "setup" / "zoe_voice_daemon.py"

SPEECH = 0.9
DEEP = 0.02

FIRST_SOUND_RE = re.compile(
    r"^FIRST_SOUND close=(deep|quiet|clean|hesitation|no_speech|cap|other|none)=(\d+)ms post_ms=(-?\d+) "
    r"reflex=none skip=- reflex_at_ms=-1 reflex_dur_ms=-1 reflex_cut=0 reflex_wait_ms=0 "
    r"server_first_ms=(-?\d+) server_kind=(content|filler|tool_ack|greeting|canned|unknown|none) gap_ms=-1 "
    r"still_talking=([01]) talk_chunks=(-?\d+) chunks=(\d+) underrun_max_ms=(\d+) underrun_sum_ms=(\d+)$")
PCM_WRITE_RE = re.compile(r"^PCM_WRITE i=(\d+) at_ms=(-?\d+) dur_ms=(\d+) underrun_ms=(\d+)$")


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


@pytest.fixture(scope="module")
def daemon():
    return _load("zoe_voice_daemon_first_sound_test")


@pytest.fixture(autouse=True)
def _fresh(daemon):
    daemon._turn_close.clear()
    daemon._TALK.begin()
    daemon._TALK.armed = False
    yield
    daemon._turn_close.clear()


class _Clock:
    def __init__(self, t: float = 1000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


def _msgs(caplog, prefix: str) -> list:
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith(prefix)]


def _wav(ms: int, rate: int = 24000, amp: int = 3000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(np.full(rate * ms // 1000, amp, dtype=np.int16).tobytes())
    return buf.getvalue()


# ── 1. field order and sentinels ─────────────────────────────────────────────

def test_line_with_nothing_known_is_all_sentinels(daemon):
    fs = daemon._FirstSound(speculative=False)
    line = fs.line((-1, 0))
    assert line == ("FIRST_SOUND close=none=0ms post_ms=-1 reflex=none skip=- reflex_at_ms=-1 reflex_dur_ms=-1 "
                    "reflex_cut=0 reflex_wait_ms=0 server_first_ms=-1 server_kind=none gap_ms=-1 still_talking=0 "
                    "talk_chunks=-1 chunks=0 underrun_max_ms=0 underrun_sum_ms=0")
    assert FIRST_SOUND_RE.match(line)


def test_field_order_is_fixed(daemon, monkeypatch):
    clock = _Clock(50.0)
    monkeypatch.setattr(daemon, "_fs_now", clock)
    ep = types.SimpleNamespace(tail_rule="deep", close_ms=640)
    daemon._stamp_close(50.0, ep, "silence")
    fs = daemon._FirstSound(speculative=False)
    fs.post_at = 50.21
    fs.header({"chunk": -2, "provider": "greeting"})
    fs.write(51.5, 0.4)
    line = fs.line((4, 1))
    keys = [kv.split("=", 1)[0] for kv in line.split(" ")[1:]]
    assert keys == ["close", "post_ms", "reflex", "skip", "reflex_at_ms", "reflex_dur_ms", "reflex_cut",
                    "reflex_wait_ms", "server_first_ms", "server_kind", "gap_ms", "still_talking", "talk_chunks",
                    "chunks", "underrun_max_ms", "underrun_sum_ms"]
    m = FIRST_SOUND_RE.match(line)
    assert m and m.group(1, 2, 3, 4, 5, 6, 7, 8) == ("deep", "640", "210", "1500", "greeting", "1", "4", "1")


@pytest.mark.parametrize("header,kind", [
    ({"chunk": -2, "provider": "greeting"}, "greeting"),
    ({"chunk": -1, "provider": "filler"}, "filler"),
    ({"chunk": 3, "provider": "cached-processing-ack", "processing_ack": True}, "filler"),
    ({"chunk": 0, "provider": "kokoro"}, "canned"),
    ({"chunk": 0, "provider": "stitch"}, "content"),
    ({"chunk": 0, "provider": "kokoro-sidecar"}, "unknown"),   # content OR a tool ack: today's header cannot tell
    ({"chunk": 0, "provider": "edge-tts"}, "unknown"),
    ({"chunk": 0, "provider": "kokoro-sidecar", "kind": "tool_ack"}, "tool_ack"),  # the server follow-up field
    ({"chunk": 0, "provider": "kokoro-sidecar", "kind": "a sentence someone said"}, "unknown"),
    ({"chunk": 0, "provider": "kokoro-sidecar", "kind": ["content"]}, "unknown"),
])
def test_server_kind_reads_only_what_the_header_carries(daemon, header, kind):
    assert daemon._server_kind(header) == kind


# ── 2. underrun arithmetic ───────────────────────────────────────────────────

def test_underrun_is_starvation_against_back_to_back_playout(daemon):
    daemon._turn_close.update(t=100.0, rule="quiet", ms=800, seq=1)
    fs = daemon._FirstSound(speculative=False)
    schedule = [(100.5, 1.0), (100.6, 1.0), (102.1, 0.2), (102.8, 0.1), (102.85, 0.05)]
    lines = [fs.write(at, dur) for at, dur in schedule]
    # playout: [100.5,101.5] [101.5,102.5] [102.5,102.7] (no starvation: the write landed while chunk 1 played)
    #          write 102.8 > end 102.7 -> 100 ms starved; [102.8,102.9]; write 102.85 < 102.9 -> 0
    assert lines == [
        "PCM_WRITE i=0 at_ms=500 dur_ms=1000 underrun_ms=0",
        "PCM_WRITE i=1 at_ms=600 dur_ms=1000 underrun_ms=0",
        "PCM_WRITE i=2 at_ms=2100 dur_ms=200 underrun_ms=0",
        "PCM_WRITE i=3 at_ms=2800 dur_ms=100 underrun_ms=100",
        "PCM_WRITE i=4 at_ms=2850 dur_ms=50 underrun_ms=0",
    ]
    assert (fs.underrun_max, fs.underrun_sum, fs.writes) == (100, 100, 5)
    assert FIRST_SOUND_RE.match(fs.line((0, 0))).group(4) == "500"
    # NEGATIVE CONTROL: modelling each chunk's end as its own write + duration (ignoring the queue) would call the
    # third write starved by 500 ms (102.1 - 101.6) - the schedule above is chosen so that error is visible.
    naive_end_after_1 = 100.6 + 1.0
    assert round((102.1 - naive_end_after_1) * 1000) == 500 and lines[2].endswith("underrun_ms=0")


def test_underrun_sums_several_starvations(daemon):
    fs = daemon._FirstSound(speculative=False)
    for at, dur in [(0.0, 0.5), (0.7, 0.5), (1.5, 0.5), (2.0, 0.5)]:
        fs.write(at, dur)
    assert (fs.underrun_max, fs.underrun_sum) == (300, 500)
    # no close known: at_ms is the sentinel, the underrun maths still holds
    assert fs.write(5.0, 0.1) == "PCM_WRITE i=4 at_ms=-1 dur_ms=100 underrun_ms=2500"


# ── 3. t_close: stamped at the close, carried to the turn ────────────────────

class _ScriptStream:
    def __init__(self, daemon, monkeypatch, clock, probs, *, tail=DEEP, amp=4000):
        self.probs, self.tail, self.clock, self.reads, self.amp = list(probs), tail, clock, 0, amp
        self._cur = DEEP
        monkeypatch.setattr(daemon, "_vad_prob", lambda _m, _c: self._cur)

    def read(self, size, exception_on_overflow=True):  # noqa: ARG002
        self.reads += 1
        self.clock.t += 0.08
        self._cur = self.probs[self.reads - 1] if self.reads <= len(self.probs) else self.tail
        return np.full(1280, self.amp if self._cur >= 0.35 else 10, dtype=np.int16).tobytes()

    def stop_stream(self):
        pass

    def close(self):
        pass


class _FakePA:
    def __init__(self, stream=None):
        self.stream = stream

    def get_sample_size(self, fmt):  # noqa: ARG002
        return 2

    def open(self, **kw):  # noqa: ARG002
        return self.stream


def _vad_mode(daemon, monkeypatch, clock):
    monkeypatch.setattr(daemon, "VAD_ENDPOINT_ENABLED", True)
    monkeypatch.setattr(daemon, "_get_silero_vad", lambda: (object(), None))
    monkeypatch.setattr(daemon, "ZOE_VAD_TAIL_MS", 640)
    monkeypatch.setattr(daemon, "VAD_ENDPOINT_SILENCE_S", 0.8)
    monkeypatch.setattr(daemon, "ZOE_SPECULATIVE_TURN", False)
    monkeypatch.setattr(daemon, "POST_WAKE_NO_SPEECH_S", 0.0)
    monkeypatch.setattr(daemon, "_PREROLL", [])
    monkeypatch.setattr(daemon, "_start_stt_stream", lambda frames: None)
    monkeypatch.setattr(daemon, "_fs_now", clock)


def test_wake_recording_stamps_the_committed_close(daemon, monkeypatch):
    clock = _Clock(10.0)
    _vad_mode(daemon, monkeypatch, clock)
    stream = _ScriptStream(daemon, monkeypatch, clock, [SPEECH] * 20)
    assert daemon.record_command(_FakePA(), stream=stream)
    # 20 speech chunks, then 8 deep chunks (640 ms) close it: the stamp is the clock at that 28th read
    assert stream.reads == 28
    assert daemon._turn_close["t"] == pytest.approx(10.0 + 28 * 0.08)
    assert (daemon._turn_close["rule"], daemon._turn_close["ms"]) == ("deep", 640)


def test_followup_recording_stamps_and_an_empty_window_does_not(daemon, monkeypatch):
    clock = _Clock(20.0)
    _vad_mode(daemon, monkeypatch, clock)
    monkeypatch.setattr(daemon, "_new_vad_stream", lambda: object())
    # deep-only after one ambiguous chunk -> the 800 ms quiet rule cannot win before 640 deep; use ambiguous to force quiet
    probs = [DEEP, DEEP] + [SPEECH] * 10 + [0.2] * 10
    stream = _ScriptStream(daemon, monkeypatch, clock, probs, tail=0.2)
    wav = daemon._follow_up_listen(_FakePA(stream), window_s=5.0)
    assert wav
    stamped = dict(daemon._turn_close)
    assert stamped["rule"] == "quiet" and stamped["ms"] == 800
    assert stamped["t"] == pytest.approx(20.0 + stream.reads * 0.08)
    # NEGATIVE CONTROL: a window that hears nothing is not a recording - the previous close stays.
    quiet = _ScriptStream(daemon, monkeypatch, clock, [], tail=DEEP)
    assert daemon._follow_up_listen(_FakePA(quiet), window_s=0.05) is None
    assert daemon._turn_close == stamped


def test_a_cap_close_is_named_cap(daemon):
    ep = types.SimpleNamespace(tail_rule="", close_ms=0)
    daemon._stamp_close(5.0, ep, "max_duration")
    assert daemon._turn_close["rule"] == "cap" and daemon._turn_close["ms"] == 0
    daemon._stamp_close(6.0, types.SimpleNamespace(tail_rule="no_speech", close_ms=3040), "silence")
    assert (daemon._turn_close["rule"], daemon._turn_close["ms"]) == ("no_speech", 3040)


def test_speculative_stream_takes_only_its_own_close(daemon):
    daemon._turn_close.update(t=1.0, rule="deep", ms=640, seq=7)       # the PREVIOUS turn's close
    spec = daemon._FirstSound(speculative=True)
    plain = daemon._FirstSound(speculative=False)
    spec.post_at = plain.post_at = 5.0
    assert spec.rel_ms(5.0) == -1                                       # its own recording has not closed yet
    assert plain.rel_ms(5.0) == 4000                                    # NEGATIVE CONTROL: a plain turn takes it
    daemon._turn_close.update(t=5.3, rule="deep", ms=640, seq=8)         # now the recording closes
    assert spec.rel_ms(5.9) == 600
    assert FIRST_SOUND_RE.match(spec.line((-1, 0))).group(3) == "-300"  # the POST went out before the close


class _FakeProc:
    def __init__(self, sink):
        self.stdin = types.SimpleNamespace(write=sink.append, flush=lambda: None, close=lambda: None)
        self.pid = 4242

    def poll(self):
        return 0

    def terminate(self):
        pass

    def kill(self):
        pass


class _FakeResp:
    def __init__(self, items, clock):
        self._items, self._clock = items, clock

    def raise_for_status(self):
        pass

    def iter_lines(self, decode_unicode=False):  # noqa: ARG002
        for dt, line in self._items:
            self._clock.t += dt
            yield line


TRANSCRIPT = "please remind me about the purple umbrella"
REPLY_TEXT = "Your purple umbrella reminder is set"


def _wire(transcript=TRANSCRIPT):
    j = lambda o: json.dumps(o).encode()  # noqa: E731
    return [
        (0.30, j({"transcript": transcript})),
        (0.20, j({"chunk": -2, "text": "Good morning.", "provider": "greeting"})),
        (0.00, base64.b64encode(_wav(400))),
        (0.90, j({"chunk": 0, "text": REPLY_TEXT, "provider": "kokoro-sidecar"})),
        (0.00, base64.b64encode(_wav(500))),
        (0.10, j({"chunk": 1, "text": "Anything else about the umbrella?", "provider": "kokoro-sidecar"})),
        (0.00, base64.b64encode(_wav(300))),
        (0.10, j({"done": True, "reply": REPLY_TEXT})),
    ]


def _stream_turn_setup(daemon, monkeypatch, clock, *, wire=None, writes=None, posts=None):
    writes = [] if writes is None else writes
    posts = [] if posts is None else posts
    monkeypatch.setattr(daemon, "_fs_now", clock)
    monkeypatch.setattr(daemon, "_speaker_claim_to_attach", lambda _w: None)
    monkeypatch.setattr(daemon, "_is_junk_transcript", lambda _t, **_k: False)
    monkeypatch.setattr(daemon, "_do_single_turn", lambda *a, **k: pytest.fail("blocking re-POST"))
    monkeypatch.setattr(daemon, "_PLATFORM", types.SimpleNamespace(
        start_pcm_stream=lambda rate, ch, width: _FakeProc(writes)))
    items = _wire() if wire is None else wire

    def _post(url, json=None, **kw):  # noqa: A002
        posts.append((url, sorted(json)))
        return _FakeResp(items, clock)

    monkeypatch.setattr(daemon.requests, "post", _post, raising=False)
    return writes, posts


def test_turn_lines_are_relative_to_the_carried_close(daemon, monkeypatch, caplog):
    clock = _Clock(100.0)
    daemon._turn_close.update(t=99.9, rule="deep", ms=640, seq=1)
    _stream_turn_setup(daemon, monkeypatch, clock)
    with caplog.at_level(logging.INFO, logger=daemon.log.name):
        ok = daemon._do_single_turn_stream(_FakePA(), b"RIFFwav", wake_turn=True)
    assert ok is True
    pcm = _msgs(caplog, "PCM_WRITE")
    # writes at 100.5 (greeting 400 ms -> ends 100.9), 101.4 (500 ms, 500 ms starved), 101.5 (queued, no starvation)
    assert pcm == ["PCM_WRITE i=0 at_ms=600 dur_ms=400 underrun_ms=0",
                   "PCM_WRITE i=1 at_ms=1500 dur_ms=500 underrun_ms=500",
                   "PCM_WRITE i=2 at_ms=1600 dur_ms=300 underrun_ms=0"]
    (line,) = _msgs(caplog, "FIRST_SOUND")
    assert line == ("FIRST_SOUND close=deep=640ms post_ms=100 reflex=none skip=- reflex_at_ms=-1 reflex_dur_ms=-1 "
                    "reflex_cut=0 reflex_wait_ms=0 server_first_ms=600 server_kind=greeting gap_ms=-1 still_talking=0 "
                    "talk_chunks=-1 chunks=3 underrun_max_ms=500 underrun_sum_ms=500")


def test_followup_window_turn_is_relative_to_its_carried_close(daemon, monkeypatch, caplog):
    clock = _Clock(100.0)
    daemon._turn_close.update(t=99.0, rule="quiet", ms=800, seq=1)
    _stream_turn_setup(daemon, monkeypatch, clock)
    with caplog.at_level(logging.INFO, logger=daemon.log.name):
        daemon._do_single_turn_stream(_FakePA(), b"RIFFwav", window="followup", prompt_on_empty=False)
    (line,) = _msgs(caplog, "FIRST_SOUND")
    assert FIRST_SOUND_RE.match(line)
    assert line.startswith("FIRST_SOUND close=quiet=800ms post_ms=1000 ") and "server_first_ms=1500 " in line


def test_a_turn_with_no_audio_still_logs_one_line(daemon, monkeypatch, caplog):
    clock = _Clock(100.0)
    daemon._turn_close.update(t=99.0, rule="quiet", ms=800, seq=1)
    wire = [(0.3, json.dumps({"transcript": TRANSCRIPT}).encode()), (0.1, b'{"done": true, "reply": ""}')]
    _stream_turn_setup(daemon, monkeypatch, clock, wire=wire)
    monkeypatch.setattr(daemon, "play_follow_up_beep", lambda: None)
    with caplog.at_level(logging.INFO, logger=daemon.log.name):
        daemon._do_single_turn_stream(_FakePA(), b"RIFFwav", prompt_on_empty=False)
    assert _msgs(caplog, "PCM_WRITE") == []
    (line,) = _msgs(caplog, "FIRST_SOUND")
    m = FIRST_SOUND_RE.match(line)
    assert m.group(3, 4, 5, 8) == ("1000", "-1", "none", "0")


# ── 4. post-close talk ──────────────────────────────────────────────────────

def test_talk_counting(daemon):
    talk = daemon._PostCloseTalk()
    daemon._turn_close.update(t=10.0, rule="deep", ms=640, seq=1)
    talk.begin()
    talk.mic_open()
    script = [(10.10, 0.9), (10.18, 0.2), (10.26, 0.5), (10.34, 0.35), (11.6, 0.9), (11.68, 0.9), (12.0, 0.1)]
    for at, prob in script:
        talk.feed(prob, at)
    # >= 0.35 counts: 10.10, 10.26, 10.34 inside 1.5 s; 11.6, 11.68 outside it
    assert (talk.chunks, talk.early) == (5, 3)
    talk.first_sound()
    talk.feed(0.9, 10.5)                       # after the first server sound: not counted
    assert talk.take() == (5, 1)
    assert talk.take() == (-1, 0)              # taken once: the next turn's line cannot reuse it


def test_talk_negative_controls(daemon):
    daemon._turn_close.update(t=10.0, rule="deep", ms=640, seq=1)
    talk = daemon._PostCloseTalk()
    talk.begin()
    talk.mic_open()
    for at in (10.1, 10.2, 11.55):             # third chunk is 1.55 s after the close: outside the window
        talk.feed(0.9, at)
    assert talk.take() == (3, 0)
    talk.begin()                               # monitor started, but its mic never opened
    talk.feed(0.9, 10.1)
    assert talk.take() == (-1, 0)
    talk.begin()
    talk.mic_open()
    for at in (10.1, 10.2, 10.3):
        talk.feed(0.34, at)                     # just under the endpointer's speech threshold
    talk.feed(float("nan"), 10.4)
    assert talk.take() == (0, 0)


class _MonStream:
    def __init__(self, n):
        self.n, self.reads = n, 0

    def read(self, size, exception_on_overflow=True):  # noqa: ARG002
        self.reads += 1
        if self.reads > self.n:
            raise OSError("end of script")
        return np.zeros(1280, dtype=np.int16).tobytes()

    def get_read_available(self):
        return 0

    def stop_stream(self):
        pass

    def close(self):
        pass


def _run_monitor(daemon, monkeypatch, *, talk_feed=None):
    """Reads 1-10 idle (speech on 3-7), then playing (speech from read 19 on). Returns the barge decisions."""
    probs = [0.0, 0.0, 0.9, 0.9, 0.9, 0.9, 0.9] + [0.0] * 11 + [0.95] * 40
    stream = _MonStream(len(probs))
    monkeypatch.setattr(daemon, "BARGE_IN_ENABLED", True)
    monkeypatch.setattr(daemon, "BARGE_DUCK_ENABLED", False)
    monkeypatch.setattr(daemon, "_new_vad_stream", lambda: types.SimpleNamespace(reset=lambda: None))
    monkeypatch.setattr(daemon, "_vad_prob", lambda _v, _c: probs[stream.reads - 1])
    proc = types.SimpleNamespace(pid=1, poll=lambda: None)
    monkeypatch.setattr(daemon, "_active_playback",
                        lambda: None if stream.reads <= 10 else (proc, 50.0 + 10 * 0.08))
    monkeypatch.setattr(daemon, "_heal_duck_leak", lambda p: None)

    monkeypatch.setattr(daemon.time, "monotonic", lambda: 50.0 + stream.reads * 0.08)
    decisions = []
    monkeypatch.setattr(daemon, "_fire_barge_in", lambda src, prob, det, p: decisions.append((src, stream.reads)))
    if talk_feed is not None:
        monkeypatch.setattr(daemon._TALK, "feed", talk_feed)
    daemon._turn_close.update(t=50.0, rule="deep", ms=640, seq=1)
    mon = daemon._BargeMonitor(_FakePA(stream))
    daemon._TALK.begin()
    mon._run()
    return decisions, daemon._TALK.take()


def test_monitor_counts_idle_speech_only(daemon, monkeypatch):
    decisions, talk = _run_monitor(daemon, monkeypatch)
    assert talk == (5, 1) and decisions


def test_monitor_decisions_are_identical_without_or_with_a_broken_instrument(daemon, monkeypatch):
    base, _ = _run_monitor(daemon, monkeypatch)
    off, talk_off = _run_monitor(daemon, monkeypatch, talk_feed=lambda p, t: None)

    def _boom(p, t):
        raise RuntimeError("instrument fault")

    broken, _ = _run_monitor(daemon, monkeypatch, talk_feed=_boom)
    assert base == off == broken and base
    assert talk_off == (0, 0)     # NEGATIVE CONTROL: with the count stubbed out nothing is counted


# ── 5. no text can reach either line ─────────────────────────────────────────

def test_no_text_reaches_either_line(daemon, monkeypatch, caplog):
    clock = _Clock(100.0)
    daemon._turn_close.update(t=99.9, rule="deep", ms=640, seq=1)
    wire = _wire()
    wire[3] = (0.9, json.dumps({"chunk": 0, "text": REPLY_TEXT, "provider": "purple umbrella",
                                "kind": "purple umbrella"}).encode())
    _stream_turn_setup(daemon, monkeypatch, clock, wire=wire)
    with caplog.at_level(logging.INFO, logger=daemon.log.name):
        daemon._do_single_turn_stream(_FakePA(), b"RIFFwav", wake_turn=True)
    lines = _msgs(caplog, "PCM_WRITE") + _msgs(caplog, "FIRST_SOUND")
    assert len(lines) == 4
    words = set((TRANSCRIPT + " " + REPLY_TEXT + " good morning anything else").lower().split())
    for line in lines:
        assert PCM_WRITE_RE.match(line) or FIRST_SOUND_RE.match(line), line
        assert not (set(re.split(r"[ =]", line.lower())) & words), line
        assert "utt=" not in line and "len=" not in line


def test_a_transcript_object_cannot_be_formatted_into_a_line(daemon):
    """The numeric fields are %d: a text-bearing object there raises instead of printing (caught by the caller)."""
    fs = daemon._FirstSound(speculative=False)
    fs.kind = "the words someone said"
    assert "said" not in fs.line((0, 0))
    with pytest.raises((TypeError, ValueError)):
        fs.line(("three chunks", 0))


# ── 6. the existing lines are unchanged ──────────────────────────────────────

def test_existing_log_lines_are_byte_identical(daemon, monkeypatch, caplog):
    src = DAEMON.read_text()
    assert src.count('log.info("turn_stream TTFA=%.2fs %s", ttfa if ttfa is not None else -1.0, '
                     'utterance_tag(transcript))') == 1
    assert src.count('"Recorded command: duration=%.2fs chunks=%d stop=%s endpoint=%s tail=%s close=%s muted=%dms",'
                     ) == 1
    assert src.count('"Recorded follow-up: duration=%.2fs chunks=%d stop=%s endpoint=%s tail=%s close=%s",') == 1
    clock = _Clock(100.0)
    _stream_turn_setup(daemon, monkeypatch, clock)
    with caplog.at_level(logging.INFO, logger=daemon.log.name):
        daemon._do_single_turn_stream(_FakePA(), b"RIFFwav", wake_turn=True)
    msgs = [r.getMessage() for r in caplog.records]
    ttfa = [i for i, m in enumerate(msgs) if m.startswith("turn_stream TTFA=")]
    first = [i for i, m in enumerate(msgs) if m.startswith("FIRST_SOUND")]
    assert len(ttfa) == 1 and len(first) == 1 and ttfa[0] < first[0]
    assert re.fullmatch(r"turn_stream TTFA=\d+\.\d\ds len=\d+ utt=[0-9a-f]{12}", msgs[ttfa[0]])


# ── 7. behaviour neutrality ──────────────────────────────────────────────────

class _NullFirstSound:
    post_at = None

    def __init__(self, **kw):
        pass

    def header(self, h):
        pass

    def sound(self, at):
        pass

    def line(self, talk):
        return ""


def _effects(daemon, monkeypatch, mode):
    clock = _Clock(100.0)
    daemon._turn_close.update(t=99.9, rule="deep", ms=640, seq=1)
    writes, posts = _stream_turn_setup(daemon, monkeypatch, clock)
    registered = []
    real_register = daemon._register_tts_process
    monkeypatch.setattr(daemon, "_register_tts_process", lambda p: (registered.append(p.pid), real_register(p)))
    if mode == "off":
        monkeypatch.setattr(daemon, "_FirstSound", _NullFirstSound)
        monkeypatch.setattr(daemon, "_fs_note_write", lambda at, dur: None)
    elif mode == "broken":
        def _boom(*a, **k):
            raise RuntimeError("instrument fault")
        monkeypatch.setattr(daemon._FirstSound, "write", _boom)
        monkeypatch.setattr(daemon._FirstSound, "line", _boom)
    ok = daemon._do_single_turn_stream(_FakePA(), b"RIFFwav", wake_turn=True)
    return ok, [len(w) for w in writes], b"".join(writes), posts, registered, dict(daemon._last_turn_flags)


def test_playback_and_posts_identical_with_and_without_the_instrument(daemon, monkeypatch):
    with monkeypatch.context() as m:
        on = _effects(daemon, m, "on")
    with monkeypatch.context() as m:
        off = _effects(daemon, m, "off")
    with monkeypatch.context() as m:
        broken = _effects(daemon, m, "broken")
    assert on == off == broken
    assert on[0] is True and len(on[1]) == 3 and len(on[3]) == 1
