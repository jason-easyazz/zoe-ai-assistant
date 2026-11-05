"""The two panel cut-offs of 2026-11-05 (daemon side; the server side is in test_voice_window_speaker_gate.py).

Torch-free like test_voice_daemon_speculation.py: ``_vad_prob`` is scripted and the REAL ``_Endpointer`` /
``record_command`` / ``_follow_up_listen`` / ``_do_single_turn_stream`` / ``voice_command`` run against fakes. Neutral,
synthetic audio and phrases only. 80 ms chunks (the daemon default). Pins:

  CUT-OFF 2 (after the wake word):
    1. the wake recording's no-speech window starts when the chime + the echo-suppressed run end, so chime + muted audio
       + a 1.7 s pause + speech records the speech; NEGATIVE CONTROL: the old rule (SILENCE_TIMEOUT_S from recording
       start) closes on the wake word alone;
    2. it is bounded: no speech closes on the window, a dead-quiet room on the echo cap, a hung chime on the chime cap;
    3. follow-up windows keep their own rule (no post-wake window on _follow_up_listen / the barge seed);
    4. a wake turn whose transcript is only the wake word plays nothing (no greeting over the command) and voice_command
       opens one follow-up listen with the cue instead; a follow-up turn / the flag off is unchanged (controls).
  CUT-OFF 1 (continuation after a complete sentence, ZOE_SPECULATIVE_TURN=windows on the Pi, default off):
    5. with the recommended values a 1.19 s pause then more speech is ONE recording, fired at 640 ms and ``resumed``;
       NEGATIVE CONTROL: the live 640/800 values close before the continuation;
    6. a follow-up recording speculates only with the flag on, and a cancelled speculation re-runs the FULL recording
       as the normal follow-up turn with the claim already scored.
  Small: the ``Recorded`` line logs the real close rule.
"""
from __future__ import annotations

import base64
import importlib.util
import io
import json
import re
import sys
import threading
import types
import wave
from pathlib import Path

import numpy as np
import pytest

pytestmark = pytest.mark.ci_safe

DAEMON = Path(__file__).resolve().parents[2] / "scripts" / "setup" / "zoe_voice_daemon.py"

SPEECH = 0.9
DEEP = 0.02
AMB = 0.2     # ambiguous band: below the 0.35 speech threshold, above the 0.10 deep threshold
CHUNK_MS = 80


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
    return _load("zoe_voice_daemon_cutoffs_test")


def _n(ms: int) -> int:
    return -(-ms // CHUNK_MS)


def _chunk(amp: int) -> bytes:
    return np.full(1280, amp, dtype=np.int16).tobytes()


class _ScriptStream:
    """A mic stream that plays a script of (amplitude, vad prob) chunks, then deep room quiet for ever.
    ``on_read`` runs before each read (1-based count) - the chime thread finishing, say."""

    def __init__(self, daemon, monkeypatch, script, *, tail=(10, DEEP), on_read=None):
        self.script = list(script)
        self.tail = tail
        self.reads = 0
        self.on_read = on_read
        self._cur_prob = DEEP
        # The scripted VAD scores the chunk just read (record_command scores each chunk right after reading it).
        monkeypatch.setattr(daemon, "_vad_prob", lambda _m, _c: self._cur_prob)

    def read(self, size, exception_on_overflow=True):  # noqa: ARG002
        self.reads += 1
        if self.on_read is not None:
            self.on_read(self.reads)
        amp, prob = self.script[self.reads - 1] if self.reads <= len(self.script) else self.tail
        self._cur_prob = prob
        return _chunk(amp)

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


def _vad_mode(daemon, monkeypatch, *, tail_ms=640, silence_s=0.8, spec=False, spec_tail_ms=320):
    monkeypatch.setattr(daemon, "VAD_ENDPOINT_ENABLED", True)
    monkeypatch.setattr(daemon, "_get_silero_vad", lambda: (object(), None))
    monkeypatch.setattr(daemon, "ZOE_VAD_TAIL_MS", tail_ms)
    monkeypatch.setattr(daemon, "VAD_ENDPOINT_SILENCE_S", silence_s)
    monkeypatch.setattr(daemon, "SILENCE_TIMEOUT_S", 1.5)
    monkeypatch.setattr(daemon, "ZOE_SPECULATIVE_TURN", spec)
    monkeypatch.setattr(daemon, "ZOE_SPECULATIVE_TAIL_MS", spec_tail_ms)
    monkeypatch.setattr(daemon, "_PREROLL", [])
    monkeypatch.setattr(daemon, "_start_stt_stream", lambda frames: None)


# ── CUT-OFF 2: the post-wake no-speech window ────────────────────────────────

# The owner's shape, synthetic: 3 loud chime chunks, 11 near-zero echo-suppressed chunks, a 1.7 s pause of room noise,
# then a 2 s command and room quiet. The chime thread reports done after the chime's 3 chunks.
CHIME = [(3300, DEEP)] * 3
MUTED = [(1, DEEP)] * 11
PAUSE = [(10, DEEP)] * _n(1700)
COMMAND = [(4000, SPEECH)] * _n(2000)
WAKE_SCRIPT = CHIME + MUTED + PAUSE + COMMAND
SPEECH_AT = len(CHIME) + len(MUTED) + len(PAUSE)   # 1-based read index of the first command chunk is SPEECH_AT + 1


def _record_after_wake(daemon, monkeypatch, *, window_s, script=WAKE_SCRIPT, chime_done_at=3, tail=(10, DEEP)):
    _vad_mode(daemon, monkeypatch)
    monkeypatch.setattr(daemon, "POST_WAKE_NO_SPEECH_S", window_s)
    daemon._wake_chime_done.clear()                       # on_wake() cleared it; the chime thread sets it
    stream = _ScriptStream(daemon, monkeypatch, script, tail=tail,
                           on_read=lambda n: daemon._wake_chime_done.set() if n > chime_done_at else None)
    try:
        wav = daemon.record_command(_FakePA(), stream=stream)
    finally:
        daemon._wake_chime_done.set()
    return wav, stream.reads


def test_pause_after_the_chime_records_the_command(daemon, monkeypatch, caplog):
    import logging
    with caplog.at_level(logging.INFO, logger=daemon.log.name):
        wav, reads = _record_after_wake(daemon, monkeypatch, window_s=3.0)
    assert reads > SPEECH_AT + len(COMMAND), f"closed at read {reads}, before the command ended"
    line = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("Recorded command"))
    assert "tail=deep" in line and "close=deep=640ms" in line and "silence_timeout" not in line, line


def test_negative_control_old_rule_closes_on_the_wake_word_alone(daemon, monkeypatch):
    """POST_WAKE_NO_SPEECH_S=0 is the old rule: SILENCE_TIMEOUT_S (1.5 s) counted from recording start, chime and
    echo suppression included. The recording closes before the command starts - the 2026-11-05 cut-off."""
    wav, reads = _record_after_wake(daemon, monkeypatch, window_s=0.0)
    assert reads == int(1.5 / 0.08) == 18 and reads <= SPEECH_AT, reads   # 18 chunks: the logged 1.44 s


def test_window_counts_from_listen_start_not_recording_start(daemon, monkeypatch, caplog):
    """No command at all: the recording closes exactly POST_WAKE_NO_SPEECH_S after the chime + muted run end, and the
    log names the rule and when listening started."""
    import logging
    script = CHIME + MUTED
    with caplog.at_level(logging.INFO, logger=daemon.log.name):
        wav, reads = _record_after_wake(daemon, monkeypatch, window_s=3.0, script=script)
    assert reads == len(CHIME) + len(MUTED) + _n(3000), reads
    line = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("Recorded command"))
    assert f"close=no_speech=3040ms+listen@{(len(CHIME) + len(MUTED)) * CHUNK_MS}ms" in line, line


def test_dead_quiet_room_is_bounded_by_the_echo_cap(daemon, monkeypatch):
    """A room as quiet as echo suppression must still close: the muted run is skipped for at most
    _POST_WAKE_ECHO_MAX_MS."""
    monkeypatch.setattr(daemon, "_POST_WAKE_ECHO_MAX_MS", 1200)
    wav, reads = _record_after_wake(daemon, monkeypatch, window_s=3.0, script=CHIME, tail=(0, DEEP))
    assert reads == len(CHIME) + _n(1200) + _n(3000), reads


def test_a_hung_chime_is_bounded(daemon, monkeypatch):
    wav, reads = _record_after_wake(daemon, monkeypatch, window_s=3.0, script=[], chime_done_at=10_000)
    assert reads == _n(int(daemon._POST_WAKE_CHIME_WAIT_MAX_S * 1000)) + _n(3000), reads


def test_followup_and_seed_recordings_keep_their_own_rule(daemon):
    """The post-wake window is the WAKE recording's only: the follow-up recorder and the barge seed build their
    endpointer without it (they start on detected speech), and the default endpointer keeps the legacy rule."""
    src = DAEMON.read_text()
    for fn in (r"\ndef _follow_up_listen\(", r"\n    def _capture_seed\("):
        body = re.split(fn, src, maxsplit=1)[1].split("\ndef ", 1)[0]
        assert "_Endpointer(spoke=True, vad=vad)" in body and "no_speech_s" not in body
    ep = daemon._Endpointer()
    assert ep._pre_max_silent is None and ep._listen_gate is None


def test_amplitude_mode_gets_the_same_window(daemon, monkeypatch):
    monkeypatch.setattr(daemon, "VAD_ENDPOINT_ENABLED", False)
    monkeypatch.setattr(daemon, "SILENCE_TIMEOUT_S", 1.5)
    done = threading.Event()
    gate = daemon._PostWakeGate(done)
    ep = daemon._Endpointer(no_speech_s=3.0, listen_gate=gate)
    assert ep.mode == "amplitude"
    n = 20
    for i in range(3):                                   # chime still playing (its echo would read as speech here)
        n += 1
        assert ep.push(_chunk(10), n) is False
    done.set()
    for i in range(_n(1700)):                             # pause: room noise, not speech (< RECORD_SILENCE_AMPLITUDE)
        n += 1
        assert ep.push(_chunk(10), n) is False, i
    n += 1
    assert ep.push(_chunk(4000), n) is False             # the command starts: heard
    assert ep._spoke
    # NEGATIVE CONTROL: the legacy endpointer closes inside the same pause (SILENCE_TIMEOUT_S from the first chunk).
    old = daemon._Endpointer()
    closed = [old.push(_chunk(10), 20 + i) for i in range(3 + _n(1700))]
    assert any(closed)


# ── CUT-OFF 2: a wake-word-only transcript is not greeted ────────────────────

@pytest.mark.parametrize("text,expected", [
    ("Hey Zoe!", True), ("Zoe.", True), ("hey zoey", True), ("Okay, Zoe?", True),
    ("Hey Zoe, add milk to the list.", False), ("Zoe is great", False), ("", False), ("Hey.", False),
])
def test_wake_only_transcript_shapes(daemon, text, expected):
    assert daemon._is_wake_only_transcript(text) is expected


class _FakeResp:
    def __init__(self, lines):
        self._lines = lines

    def raise_for_status(self):
        pass

    def iter_lines(self, decode_unicode=False):  # noqa: ARG002
        return iter(self._lines)


def _greeting_wire(daemon, monkeypatch, transcript):
    lines = [
        json.dumps({"transcript": transcript}).encode(),
        json.dumps({"chunk": 0, "text": "Good evening."}).encode(),
        base64.b64encode(b"RIFFgreet"),
        json.dumps({"done": True, "reply": "Good evening. What can I do for you?"}).encode(),
    ]
    played: list = []
    monkeypatch.setattr(daemon.requests, "post", lambda *a, **k: _FakeResp(lines), raising=False)
    monkeypatch.setattr(daemon, "_speaker_claim_for_turn", lambda _w: None)
    monkeypatch.setattr(daemon, "_feed_pcm_chunk", lambda aplay, b: played.append(b) or None)
    monkeypatch.setattr(daemon, "play_follow_up_beep", lambda: None)
    monkeypatch.setattr(daemon, "_do_single_turn", lambda *a, **k: pytest.fail("blocking re-POST"))
    return played


def test_wake_only_wake_turn_plays_no_greeting(daemon, monkeypatch):
    monkeypatch.setattr(daemon, "WAKE_ONLY_LISTEN", True)
    played = _greeting_wire(daemon, monkeypatch, "Hey Zoe!")
    ok = daemon._do_single_turn_stream(_FakePA(), b"RIFFwav", wake_turn=True)
    assert ok is False and played == []
    assert daemon._last_turn_flags.get("wake_only") is True


@pytest.mark.parametrize("wake_turn,flag", [(False, True), (True, False)])
def test_negative_controls_wake_only_followup_or_flag_off_is_played(daemon, monkeypatch, wake_turn, flag):
    monkeypatch.setattr(daemon, "WAKE_ONLY_LISTEN", flag)
    monkeypatch.setattr(daemon, "_is_junk_transcript", lambda _t, **_k: False)
    played = _greeting_wire(daemon, monkeypatch, "Hey Zoe!")
    daemon._do_single_turn_stream(_FakePA(), b"RIFFwav", wake_turn=wake_turn)
    assert played == [b"RIFFgreet"]
    assert not daemon._last_turn_flags.get("wake_only")


def test_a_real_command_on_a_wake_turn_is_played(daemon, monkeypatch):
    monkeypatch.setattr(daemon, "WAKE_ONLY_LISTEN", True)
    monkeypatch.setattr(daemon, "_is_junk_transcript", lambda _t, **_k: False)
    played = _greeting_wire(daemon, monkeypatch, "Hey Zoe, what time is it?")
    daemon._do_single_turn_stream(_FakePA(), b"RIFFwav", wake_turn=True)
    assert played == [b"RIFFgreet"]


class _OWW:
    def reset(self):
        pass


def _drive_voice_command(daemon, monkeypatch, *, first_flags, sources, turn_results):
    calls: list = []
    srcs: list = []
    src = iter(sources)
    res = iter(turn_results)
    monkeypatch.setattr(daemon, "record_command", lambda pa, stream=None, speculation=None: b"W0")
    monkeypatch.setattr(daemon, "_speculation_available", lambda: False)
    monkeypatch.setattr(daemon, "POST_PLAY_TAIL_S", 0.0)
    monkeypatch.setattr(daemon, "FOLLOW_UP_MAX_TURNS", 5)
    monkeypatch.setattr(daemon, "FOLLOW_UP_LISTEN_S", 5.0)

    def _source(pa, seed, **kw):  # noqa: ARG001
        srcs.append({"seed": seed, **kw})
        return next(src, None)
    monkeypatch.setattr(daemon, "_follow_up_source", _source)

    def _tw(pa, wav, turn_fn, *, window="", **kw):  # noqa: ARG001
        calls.append({"wav": wav, "window": window, **kw})
        daemon._last_turn_flags.clear()
        if len(calls) == 1:
            daemon._last_turn_flags.update(first_flags)
        return next(res)
    monkeypatch.setattr(daemon, "_turn_with_monitor", _tw)
    daemon.voice_command(_FakePA(), _OWW(), wake_stream=None)
    return calls, srcs


def test_wake_only_opens_one_listen_and_runs_the_command_as_a_wake_turn(daemon, monkeypatch):
    calls, srcs = _drive_voice_command(
        daemon, monkeypatch, first_flags={"wake_only": True},
        sources=[b"CMD", None], turn_results=[(False, None), (True, None)])
    assert calls[0]["wake_turn"] is True
    assert calls[1]["wav"] == b"CMD" and calls[1]["window"] == "" and "wake_turn" not in calls[1]
    assert srcs[0]["seed"] is None and "beep" not in srcs[0]           # the default listening cue plays
    assert srcs[0]["window_s"] >= 3.0
    assert len(calls) == 2 and len(srcs) == 2                          # then the normal follow-up window


def test_negative_control_a_normal_wake_turn_opens_no_extra_listen(daemon, monkeypatch):
    calls, srcs = _drive_voice_command(
        daemon, monkeypatch, first_flags={}, sources=[None], turn_results=[(False, None)])
    assert len(calls) == 1 and srcs == []


# ── CUT-OFF 1: a continuation after a complete sentence ──────────────────────

# 1.6 s sentence, a clean 1.19 s pause (2 decay chunks then deep), a 2 s continuation, then deep quiet.
FIRST = [SPEECH] * 20
GAP = [AMB, AMB] + [DEEP] * (_n(1190) - 2)
SECOND = [SPEECH] * 25


def _drive(daemon, ep, probs):
    chunk = b"\x00\x00" * daemon.CHUNK_SIZE
    fire = stop = None
    n = ep._min_frames + 1
    seq = iter(probs)
    for i, p in enumerate(probs):
        n += 1
        if ep.push(chunk, n):
            stop = i
            break
        if fire is None and ep.speculative_ready(n):
            fire = i
    return fire, stop, ep.resumed_after_speculation


def _scripted(daemon, monkeypatch, probs):
    it = iter(probs)
    monkeypatch.setattr(daemon, "_vad_prob", lambda _m, _c: next(it))


def test_recommended_values_keep_a_continuation_in_one_recording(daemon, monkeypatch):
    probs = FIRST + GAP + SECOND + [DEEP] * 40
    _vad_mode(daemon, monkeypatch, tail_ms=1400, silence_s=1.5, spec=True, spec_tail_ms=640)
    _scripted(daemon, monkeypatch, probs)
    fire, stop, resumed = _drive(daemon, daemon._Endpointer(spoke=True), probs)
    assert fire is not None and len(FIRST) <= fire < len(FIRST) + len(GAP)   # the turn started at 640 ms of deep quiet
    assert stop is not None and stop >= len(FIRST) + len(GAP) + len(SECOND)  # ... and the continuation was recorded
    assert resumed is True                                                    # -> resolve: the server re-answers it


def test_negative_control_live_values_close_before_the_continuation(daemon, monkeypatch):
    probs = FIRST + GAP + SECOND + [DEEP] * 40
    _vad_mode(daemon, monkeypatch, tail_ms=640, silence_s=0.8, spec=False)
    _scripted(daemon, monkeypatch, probs)
    fire, stop, resumed = _drive(daemon, daemon._Endpointer(spoke=True), probs)
    assert fire is None and stop is not None and stop < len(FIRST) + len(GAP)


class _Spec:
    def __init__(self):
        self.fired = False
        self.fired_wav = None
        self.resumed = None
        self.abandoned = False

    def fire(self, wav):
        assert not self.fired, "must fire once"
        self.fired, self.fired_wav = True, wav

    def recording_closed(self, *, resumed):
        self.resumed = resumed


def test_followup_recording_fires_the_speculation_and_reports_the_resume(daemon, monkeypatch):
    probs = FIRST + GAP + SECOND + [DEEP] * 40
    _vad_mode(daemon, monkeypatch, tail_ms=1400, silence_s=1.5, spec=True, spec_tail_ms=640)
    _scripted(daemon, monkeypatch, [DEEP] * 0 + probs)
    monkeypatch.setattr(daemon, "_new_vad_stream", lambda: object())
    stream = types.SimpleNamespace(read=lambda size, exception_on_overflow=False: b"\x00\x00" * size,
                                   stop_stream=lambda: None, close=lambda: None)
    spec = _Spec()
    wav = daemon._follow_up_listen(_FakePA(stream), window_s=5.0, speculation=spec)
    assert wav is not None and spec.fired and spec.resumed is True
    with wave.open(io.BytesIO(wav)) as a, wave.open(io.BytesIO(spec.fired_wav)) as b:
        assert b.getnframes() < a.getnframes()                # the speculative wav is a strict prefix
        assert a.getnframes() >= (len(FIRST) + len(GAP) + len(SECOND)) * daemon.CHUNK_SIZE


def test_window_speculation_only_with_both_flags(daemon, monkeypatch):
    monkeypatch.setattr(daemon, "_speculation_available", lambda: True)
    monkeypatch.setattr(daemon, "ZOE_SPECULATIVE_WINDOWS", False)   # ZOE_SPECULATIVE_TURN=1: wake only
    assert daemon._window_speculation(_FakePA(), None, "followup") is None          # default OFF: unchanged
    monkeypatch.setattr(daemon, "ZOE_SPECULATIVE_WINDOWS", True)
    spec = daemon._window_speculation(_FakePA(), None, "followup")
    assert spec is not None and spec.window == "followup"
    assert daemon._window_speculation(_FakePA(), b"SEED", "followup") is None       # a barge seed never speculates
    assert daemon._window_speculation(_FakePA(), None, "") is None
    monkeypatch.setattr(daemon, "_speculation_available", lambda: False)
    assert daemon._window_speculation(_FakePA(), None, "followup") is None


def test_cancelled_window_speculation_reruns_the_full_recording(daemon, monkeypatch):
    calls: list = []
    monkeypatch.setattr(daemon, "BARGE_IN_ENABLED", False)

    def _tw(pa, wav, turn_fn, *, window="", **kw):  # noqa: ARG001
        calls.append({"wav": wav, "window": window, **kw})
        return True, None
    monkeypatch.setattr(daemon, "_turn_with_monitor", _tw)
    spec = daemon._SpeculativeTurn(_FakePA(), window="followup")
    spec.fired, spec.voice_claim = True, ("member-1", 0.5)

    def _finish(final_wav):
        spec.cancelled = True              # resolve -> the transcripts differ -> the server cancelled the prefix
        return False
    monkeypatch.setattr(spec, "finish", _finish)
    played, _seed = daemon._window_turn(_FakePA(), b"FULL", None, spec, window="followup", prompt_on_empty=False)
    assert played is True
    assert calls == [{"wav": b"FULL", "window": "followup", "voice_claim": ("member-1", 0.5), "prompt_on_empty": False}]


def test_committed_window_speculation_is_not_rerun(daemon, monkeypatch):
    monkeypatch.setattr(daemon, "BARGE_IN_ENABLED", False)
    monkeypatch.setattr(daemon, "_turn_with_monitor", lambda *a, **k: pytest.fail("re-ran a committed turn"))
    spec = daemon._SpeculativeTurn(_FakePA(), window="followup")
    spec.fired = True
    monkeypatch.setattr(spec, "finish", lambda _w: True)
    assert daemon._window_turn(_FakePA(), b"FULL", None, spec, window="followup") == (True, None)


def test_speculative_window_payload_carries_the_window_and_a_wake_one_does_not(daemon, monkeypatch):
    posted: list = []
    monkeypatch.setattr(daemon.requests, "post",
                        lambda url, json=None, **k: posted.append(json) or _FakeResp([]), raising=False)  # noqa: A002
    monkeypatch.setattr(daemon, "_speaker_claim_for_turn", lambda _w: None)
    spec = daemon._SpeculativeTurn(_FakePA(), window="followup")
    daemon._do_single_turn_stream(_FakePA(), b"RIFFwav", prompt_on_empty=False, speculation=spec, window="followup")
    wake = daemon._SpeculativeTurn(_FakePA())
    daemon._do_single_turn_stream(_FakePA(), b"RIFFwav", prompt_on_empty=False, speculation=wake)
    assert posted[0]["speculative"] is True and posted[0]["window"] == "followup"
    assert posted[1]["speculative"] is True and "window" not in posted[1]


def test_windows_value_turns_on_window_speculation_and_plain_on_does_not():
    """ZOE_SPECULATIVE_TURN=windows (Pi only) = wake + windows; ``1`` = wake only (unchanged); unset = off."""
    import os
    for raw, turn, windows in (("windows", True, True), ("1", True, False), ("", False, False)):
        old = os.environ.get("ZOE_SPECULATIVE_TURN")
        os.environ["ZOE_SPECULATIVE_TURN"] = raw
        try:
            mod = _load(f"zoe_voice_daemon_specflag_{raw or 'off'}")
        finally:
            if old is None:
                os.environ.pop("ZOE_SPECULATIVE_TURN", None)
            else:
                os.environ["ZOE_SPECULATIVE_TURN"] = old
        assert (mod.ZOE_SPECULATIVE_TURN, mod.ZOE_SPECULATIVE_WINDOWS) == (turn, windows), raw


# ── cross-review round 1 (codex, PR #2315) ───────────────────────────────────

def test_a_speculation_the_daemon_cancelled_is_never_committed(daemon, monkeypatch):
    """Junk on a speculative window stream marks it cancelled; finish() must then send ``cancel``, never
    ``commit`` (a commit could release deferred writes the re-run repeats). NEGATIVE CONTROL: not cancelled -> commit."""
    calls: list = []
    monkeypatch.setattr(daemon, "_api_post", lambda path, body, **k: calls.append(body) or {"verdict": "ok"})
    spec = daemon._SpeculativeTurn(_FakePA(), window="followup")
    spec.fired, spec.cancelled, spec.resumed = True, True, True
    assert spec.finish(b"RIFFfinal") is False
    assert calls == [{"turn_id": spec.turn_id, "action": "cancel"}]
    calls.clear()
    spec2 = daemon._SpeculativeTurn(_FakePA(), window="followup")
    spec2.fired = True
    spec2.finish(b"RIFFfinal")
    assert calls == [{"turn_id": spec2.turn_id, "action": "commit"}]


def test_junk_on_a_speculative_window_stream_cancels_it(daemon, monkeypatch):
    lines = [json.dumps({"transcript": "you"}).encode(),
             json.dumps({"chunk": 0, "text": "Hm?"}).encode(), base64.b64encode(b"RIFFx")]
    played = _greeting_wire(daemon, monkeypatch, "unused")
    monkeypatch.setattr(daemon.requests, "post", lambda *a, **k: _FakeResp(lines), raising=False)
    monkeypatch.setattr(daemon, "_is_junk_transcript", lambda _t, **_k: True)
    spec = daemon._SpeculativeTurn(_FakePA(), window="followup")
    daemon._do_single_turn_stream(_FakePA(), b"RIFFwav", prompt_on_empty=False, speculation=spec, window="followup")
    assert spec.cancelled is True and played == []


def test_a_dropped_wake_recording_abandons_its_fired_speculation(daemon, monkeypatch):
    abandoned: list = []

    class _Spec2:
        def __init__(self, pa):
            pass

        def abandon(self):
            abandoned.append(True)
    monkeypatch.setattr(daemon, "_SpeculativeTurn", _Spec2)
    monkeypatch.setattr(daemon, "_speculation_available", lambda: True)
    monkeypatch.setattr(daemon, "record_command", lambda pa, stream=None, speculation=None: None)
    monkeypatch.setattr(daemon, "POST_PLAY_TAIL_S", 0.0)
    daemon.voice_command(_FakePA(), _OWW(), wake_stream=None)
    assert abandoned == [True]


def test_blocking_wake_only_plays_no_greeting_and_a_stale_flag_never_leaks(daemon, monkeypatch):
    played: list = []
    monkeypatch.setattr(daemon, "WAKE_ONLY_LISTEN", True)
    monkeypatch.setattr(daemon, "_BUFFER_DELAY_S", 5.0)
    monkeypatch.setattr(daemon, "_speaker_claim_for_turn", lambda _w: None, raising=False)
    monkeypatch.setattr(daemon, "play_audio_b64", lambda *a, **k: played.append(a))
    resp = {"ok": True, "text": "Hey Zoe.", "reply": "Good evening.", "audio_base64": "UklGRg=="}
    monkeypatch.setattr(daemon, "_post_turn_riding_restart", lambda *a, **k: dict(resp))
    assert daemon._do_single_turn(_FakePA(), b"RIFFwav", wake_turn=True) is False
    assert played == [] and daemon._last_turn_flags.get("wake_only") is True
    # a later FAILED blocking turn must not inherit the flag
    monkeypatch.setattr(daemon, "_post_turn_riding_restart", lambda *a, **k: {"ok": False, "error": "down"})
    monkeypatch.setattr(daemon, "_espeak_local", lambda _t: None)
    daemon._do_single_turn(_FakePA(), b"RIFFwav", wake_turn=True)
    assert "wake_only" not in daemon._last_turn_flags


@pytest.mark.parametrize("seconds", [0.0804, 0.081, 3.0, 2.95])
def test_no_speech_window_is_never_shorter_than_configured(daemon, seconds):
    ep = daemon._Endpointer(no_speech_s=seconds)
    assert ep._pre_max_silent * daemon.CHUNK_SIZE / daemon.SAMPLE_RATE >= seconds - 1e-9
