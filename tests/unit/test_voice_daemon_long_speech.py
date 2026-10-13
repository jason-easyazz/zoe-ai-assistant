"""Long speech is ONE turn in the panel daemon (`scripts/setup/zoe_voice_daemon.py`).

Live 2026-10-12: four follow-up recordings hit the 12.32 s cap mid-sentence; Zoe answered
the first half and the leftover second half was transcribed as a separate garbage turn
that the brain read as distress. These pin the three guards, torch-free — the REAL
`_Endpointer` / `record_command` / `_follow_up_listen` / `_turn_with_monitor` /
`_do_single_turn_stream` run against a scripted VAD, a fake mic and a fake server:

  * soft cap (RECORD_SECONDS_MAX, 12 s) -> hard cap (30 s; 60 s in a conversation) ->
    snap to a pause. Past the soft cap a recording stays open only while the speaker is
    still flowing (room noise stops at the soft cap), and at the hard cap it runs on to
    the first non-speech chunk so the cut lands in a pause, not a word;
  * a recording that still ends on the hard cap mid-speech is not sent: the daemon
    listens for the rest and sends ONE merged clip (bounded);
  * the server's `incomplete` frame (transcript ends "...and") is honoured once: listen,
    merge, re-send with `continued` - never twice; `skipped` (side speech) is silent;
  * barge-in seeds carry no `window` hint (the user is deliberately addressing Zoe).

Server half: services/zoe-data/tests/test_voice_utterance_shape.py.
"""
from __future__ import annotations

import base64
import importlib.util
import io
import json
import re
import sys
import types
import wave
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe  # GitHub-CI opt-in: runs in validate.yml's `-m ci_safe` lane

DAEMON = Path(__file__).resolve().parents[2] / "scripts" / "setup" / "zoe_voice_daemon.py"
_SRC = DAEMON.read_text(encoding="utf-8")

SPEECH = 0.9
DEEP = 0.02
_ENV = ("RECORD_SECONDS_MAX", "RECORD_SECONDS_HARD_MAX", "CONV_RECORD_SECONDS_HARD_MAX",
        "RECORD_SNAP_S", "RECORD_STILL_SPEAKING_RATIO", "ZOE_CONTINUE_WINDOW_S")


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
    mp = pytest.MonkeyPatch()
    for var in _ENV:
        mp.delenv(var, raising=False)
    try:
        yield _load("zoe_voice_daemon_long_speech_test")
    finally:
        mp.undo()


class _FakeStream:
    def __init__(self):
        self.closed = False
        self.reads = 0

    def read(self, size, exception_on_overflow=True):  # noqa: ARG002
        self.reads += 1
        return b"\x01\x00" * size

    def stop_stream(self):
        pass

    def close(self):
        self.closed = True


class _FakePA:
    def __init__(self):
        self.streams: list[_FakeStream] = []

    def get_sample_size(self, fmt):  # noqa: ARG002
        return 2

    def open(self, **kw):  # noqa: ARG002
        s = _FakeStream()
        self.streams.append(s)
        return s


def _vad(daemon, monkeypatch, probs):
    """Install a scripted VAD (live 640/800 endpoint config) behind the real _Endpointer."""
    seq = iter(probs)
    monkeypatch.setattr(daemon, "_vad_prob", lambda _m, _c: next(seq))
    monkeypatch.setattr(daemon, "VAD_ENDPOINT_ENABLED", True)
    monkeypatch.setattr(daemon, "_get_silero_vad", lambda: (object(), None))
    monkeypatch.setattr(daemon, "ZOE_VAD_TAIL_MS", 640)
    monkeypatch.setattr(daemon, "ZOE_SPECULATIVE_TURN", False)
    monkeypatch.setattr(daemon, "_PREROLL", [])
    monkeypatch.setattr(daemon, "_last_record_capped", False)


def _secs(wav: bytes) -> float:
    with wave.open(io.BytesIO(wav)) as wf:
        return wf.getnframes() / float(wf.getframerate())


def _wav(daemon, seconds: float, byte: bytes = b"\x01\x00") -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(daemon.SAMPLE_RATE)
        wf.writeframes(byte * int(seconds * daemon.SAMPLE_RATE))
    return buf.getvalue()


CHUNK_S = 1280 / 16000.0  # 80 ms


# ── defaults + env knobs ─────────────────────────────────────────────────────

def test_defaults(daemon):
    assert daemon.RECORD_SECONDS == 12                 # soft cap unchanged
    assert daemon.RECORD_SECONDS_HARD == 30
    assert daemon.CONV_RECORD_SECONDS_HARD == 60
    assert daemon.RECORD_SNAP_S == 3.0
    assert daemon.ZOE_CONTINUE_WINDOW_S == 2.0
    assert daemon._record_caps() == (150, 375, 37)
    assert daemon._record_caps(60) == (150, 750, 37)


def test_hard_cap_is_never_below_the_soft_cap_and_bad_values_keep_defaults(monkeypatch):
    monkeypatch.setenv("RECORD_SECONDS_MAX", "40")
    mod = _load("zoe_voice_daemon_long_speech_hard_floor")
    assert mod.RECORD_SECONDS == 40 and mod.RECORD_SECONDS_HARD == 40 and mod.CONV_RECORD_SECONDS_HARD == 60
    monkeypatch.setenv("RECORD_SECONDS_MAX", "12")
    for var, bad in (("RECORD_SECONDS_HARD_MAX", "long"), ("RECORD_SNAP_S", "nan"),
                     ("ZOE_CONTINUE_WINDOW_S", "inf"), ("RECORD_STILL_SPEAKING_RATIO", "7")):
        monkeypatch.setenv(var, bad)
    mod = _load("zoe_voice_daemon_long_speech_bad_env")
    assert (mod.RECORD_SECONDS_HARD, mod.RECORD_SNAP_S, mod.ZOE_CONTINUE_WINDOW_S,
            mod.RECORD_STILL_SPEAKING_RATIO) == (30, 3.0, 2.0, 0.5)


def test_rollback_knobs_restore_the_old_cap(monkeypatch):
    for var, val in (("RECORD_SECONDS_HARD_MAX", "12"), ("CONV_RECORD_SECONDS_HARD_MAX", "12"),
                     ("RECORD_SNAP_S", "0"), ("ZOE_CONTINUE_WINDOW_S", "0")):
        monkeypatch.setenv(var, val)
    mod = _load("zoe_voice_daemon_long_speech_rollback")
    assert mod._record_caps() == (150, 150, 0)
    assert mod.ZOE_CONTINUE_WINDOW_S == 0.0


# ── the cap rule ─────────────────────────────────────────────────────────────

class _EP:
    def __init__(self, still, mid):
        self._still, self._mid = still, mid

    def still_speaking(self, ratio):  # noqa: ARG002
        return self._still

    def mid_speech(self):
        return self._mid


def test_cap_rule_table(daemon):
    caps = (150, 375, 37)
    stop = lambda n, still, mid: daemon._record_cap_stop(n, caps, _EP(still, mid))  # noqa: E731
    assert stop(149, False, False) is False                  # before the soft cap: never
    assert stop(150, True, True) is False                    # soft cap, still flowing: keep going
    assert stop(150, False, False) is True                   # soft cap, speaker stopped flowing: stop
    assert stop(150, False, True) is False                   # ...but never inside a resumed word
    assert stop(374, True, True) is False
    assert stop(375, True, True) is False                    # hard cap mid-word: snap onward
    assert stop(375, True, False) is True                    # hard cap, first non-speech chunk: stop here
    assert stop(411, True, True) is False
    assert stop(412, True, True) is True                     # hard + snap: always


# ── the endpointer cues ──────────────────────────────────────────────────────

def _endpointer(daemon, monkeypatch, probs):
    _vad(daemon, monkeypatch, probs)
    return daemon._Endpointer()


def test_still_speaking_needs_speech_heard_and_a_dense_recent_window(daemon, monkeypatch):
    ep = _endpointer(daemon, monkeypatch, [DEEP] * 30)
    for n in range(1, 31):
        ep.push(b"\x00\x00" * 1280, n)
    assert ep.still_speaking(0.5) is False and ep.mid_speech() is False   # never spoke
    probs = [SPEECH] * 30
    ep = _endpointer(daemon, monkeypatch, probs)
    for n in range(1, 31):
        ep.push(b"\x00\x00" * 1280, n)
    assert ep.still_speaking(0.5) is True and ep.mid_speech() is True
    probs = ([SPEECH] + [DEEP] * 3) * 8            # 25% speech: breath noise, not a speaker
    ep = _endpointer(daemon, monkeypatch, probs)
    for n in range(1, len(probs) + 1):
        ep.push(b"\x00\x00" * 1280, n)
    assert ep.still_speaking(0.5) is False


# ── record_command: the recorder honours the caps ────────────────────────────

def _record(daemon, monkeypatch, probs):
    _vad(daemon, monkeypatch, probs)
    stream = _FakeStream()
    wav = daemon.record_command(_FakePA(), stream=stream)
    return wav, stream.reads


def test_continuous_speech_past_the_old_12s_cap_is_one_recording(daemon, monkeypatch):
    # 16 s of speech then silence. Old behaviour: stop=max_duration at 12 s with the
    # speaker mid-sentence, the rest becoming a second turn.
    wav, reads = _record(daemon, monkeypatch, [SPEECH] * 200 + [DEEP] * 40)
    assert reads == 200 + 8                              # closed on the 640 ms deep tail
    assert _secs(wav) == pytest.approx(reads * CHUNK_S)
    assert _secs(wav) > 16.0
    assert daemon._last_record_capped is False


def test_room_noise_still_stops_at_the_soft_cap(daemon, monkeypatch):
    # 25 % speech density for ever (a TV in the next room): not "still speaking".
    probs = ([SPEECH] + [DEEP] * 3) * 200
    wav, reads = _record(daemon, monkeypatch, probs)
    assert reads == 150 and _secs(wav) == pytest.approx(12.0)
    assert daemon._last_record_capped is False


def test_soft_cap_never_cuts_a_resumed_word(daemon, monkeypatch):
    # A pause, then speech resumes exactly at the soft cap: the 2 s speech ratio is low
    # (~25 %) but the CURRENT chunk is speech, so the recording runs on to the next quiet
    # chunk instead of cutting the word.
    probs = [SPEECH] * 2 + ([DEEP] * 3 + [SPEECH]) * 40 + [DEEP] * 40
    assert probs[149] == SPEECH and probs[150] == DEEP
    wav, reads = _record(daemon, monkeypatch, probs)
    assert reads == 151                                   # not 150: ended on the first non-speech chunk
    assert daemon._last_record_capped is False


def test_non_stop_speech_ends_at_hard_plus_snap_and_reports_capped(daemon, monkeypatch):
    wav, reads = _record(daemon, monkeypatch, [SPEECH] * 600)
    assert reads == 375 + 37
    assert daemon._last_record_capped is True


def test_hard_cap_snaps_to_the_first_pause_not_mid_word(daemon, monkeypatch):
    # Speech to 5 chunks past the hard cap, then the first quiet chunk: stop THERE.
    probs = [SPEECH] * 380 + [DEEP] * 40
    wav, reads = _record(daemon, monkeypatch, probs)
    assert reads == 381
    assert daemon._last_record_capped is False           # ended in a pause: nothing to continue


def test_a_short_turn_is_unchanged(daemon, monkeypatch):
    wav, reads = _record(daemon, monkeypatch, [SPEECH] * 20 + [DEEP] * 40)
    assert reads == 20 + 8 and daemon._last_record_capped is False


# ── _follow_up_listen: conversation windows get the higher hard cap ──────────

def _follow_up(daemon, monkeypatch, probs, **kw):
    _vad(daemon, monkeypatch, probs)
    monkeypatch.setattr(daemon, "FOLLOW_UP_VAD_THRESHOLD", 0.5)
    pa = _FakePA()
    wav = daemon._follow_up_listen(pa, window_s=5.0, **kw)
    return wav, pa.streams[0].reads


def test_followup_window_is_capped_at_the_default_hard_cap(daemon, monkeypatch):
    # onset probe + 600 speech chunks: the recording stops at hard + snap and is "capped"
    wav, reads = _follow_up(daemon, monkeypatch, [SPEECH] * 700)
    assert daemon._last_record_capped is True
    assert _secs(wav) < 36.0


def test_conversation_window_runs_to_its_pause_up_to_60s(daemon, monkeypatch):
    wav, reads = _follow_up(daemon, monkeypatch, [SPEECH] * 600 + [DEEP] * 40,
                            hard_cap_s=daemon.CONV_RECORD_SECONDS_HARD)
    assert daemon._last_record_capped is False
    assert _secs(wav) > 40.0


def test_follow_up_listen_clears_the_capped_flag_on_entry(daemon, monkeypatch):
    _vad(daemon, monkeypatch, [DEEP] * 10)
    monkeypatch.setattr(daemon, "_last_record_capped", True)
    monkeypatch.setattr(daemon, "FOLLOW_UP_VAD_THRESHOLD", 0.5)
    # No speech inside a 0 s window -> returns None, and the stale flag must be gone.
    assert daemon._follow_up_listen(_FakePA(), window_s=0.0) is None
    assert daemon._last_record_capped is False


# ── all three recorders share the rule ───────────────────────────────────────

def test_every_recorder_uses_the_shared_cap_rule():
    for pattern in (r"\ndef record_command\(", r"\ndef _follow_up_listen\(", r"\n    def _capture_seed\("):
        m = re.search(pattern + r".*?(?=\n(?:def |class |    def )\w)", _SRC, re.DOTALL)
        assert m, pattern
        body = m.group(0)
        assert "_record_cap_stop(" in body, f"{pattern} lost the soft/hard/snap cap rule"
        assert "_note_capture_end(" in body, f"{pattern} no longer reports a hard-cap stop"
        assert "int(RECORD_SECONDS * SAMPLE_RATE" not in body, f"{pattern} re-introduced the flat cap"


# ── merging ──────────────────────────────────────────────────────────────────

def test_merge_wavs_joins_with_a_short_gap(daemon):
    a, b = _wav(daemon, 1.0), _wav(daemon, 0.5)
    merged = daemon._merge_wavs(a, b)
    assert _secs(merged) == pytest.approx(1.0 + 0.2 + 0.5, abs=0.001)
    with wave.open(io.BytesIO(merged)) as wf:
        assert (wf.getnchannels(), wf.getsampwidth(), wf.getframerate()) == (1, 2, daemon.SAMPLE_RATE)


def test_merge_wavs_keeps_the_first_when_formats_differ_or_input_is_garbage(daemon):
    a = _wav(daemon, 1.0)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(8000)
        wf.writeframes(b"\x01\x00" * 800)
    assert daemon._merge_wavs(a, buf.getvalue()) == a
    assert daemon._merge_wavs(a, b"not a wav") == a


def test_reply_question_detection(daemon):
    assert daemon._reply_is_question("Would you like me to read it?")
    assert daemon._reply_is_question('She asked, "is it ready?"  ')
    assert not daemon._reply_is_question("It is ready.")
    assert not daemon._reply_is_question("")


# ── hard-cap continuation: ONE merged clip, bounded ──────────────────────────

def _fake_listen(daemon, monkeypatch, pieces, *, recap=False):
    calls = []

    def _listen(pa, window_s=None, hard_cap_s=None):  # noqa: ARG001
        calls.append(window_s)
        daemon._last_record_capped = bool(recap and len(calls) < len(pieces))
        return pieces[len(calls) - 1] if len(calls) <= len(pieces) else None
    monkeypatch.setattr(daemon, "_follow_up_listen", _listen)
    monkeypatch.setattr(daemon, "_recording_active", types.SimpleNamespace(set=lambda: None, clear=lambda: None))
    return calls


def test_capped_recording_waits_for_the_rest_and_merges_once(daemon, monkeypatch):
    first, rest = _wav(daemon, 2.0), _wav(daemon, 1.0)
    monkeypatch.setattr(daemon, "_last_record_capped", True)
    calls = _fake_listen(daemon, monkeypatch, [rest])
    out = daemon._gather_capped_continuation(_FakePA(), first)
    assert calls == [daemon.ZOE_CONTINUE_WINDOW_S]
    assert _secs(out) == pytest.approx(2.0 + 0.2 + 1.0, abs=0.001)


def test_capped_recording_with_no_continuation_is_sent_unchanged(daemon, monkeypatch):
    first = _wav(daemon, 2.0)
    monkeypatch.setattr(daemon, "_last_record_capped", True)
    calls = _fake_listen(daemon, monkeypatch, [None])
    assert daemon._gather_capped_continuation(_FakePA(), first) == first
    assert len(calls) == 1


def test_monologue_merging_is_bounded(daemon, monkeypatch):
    pieces = [_wav(daemon, 1.0) for _ in range(10)]
    monkeypatch.setattr(daemon, "_last_record_capped", True)
    calls = _fake_listen(daemon, monkeypatch, pieces, recap=True)
    out = daemon._gather_capped_continuation(_FakePA(), _wav(daemon, 1.0))
    assert len(calls) == daemon._CAP_MERGE_MAX
    assert _secs(out) == pytest.approx(1.0 + daemon._CAP_MERGE_MAX * 1.2, abs=0.01)


def test_an_uncapped_recording_never_listens_again(daemon, monkeypatch):
    first = _wav(daemon, 2.0)
    monkeypatch.setattr(daemon, "_last_record_capped", False)
    calls = _fake_listen(daemon, monkeypatch, [_wav(daemon, 1.0)])
    assert daemon._gather_capped_continuation(_FakePA(), first) == first and calls == []


def test_continuation_off_never_listens(daemon, monkeypatch):
    monkeypatch.setattr(daemon, "_last_record_capped", True)
    monkeypatch.setattr(daemon, "ZOE_CONTINUE_WINDOW_S", 0.0)
    calls = _fake_listen(daemon, monkeypatch, [_wav(daemon, 1.0)])
    first = _wav(daemon, 2.0)
    assert daemon._gather_capped_continuation(_FakePA(), first) == first and calls == []


# ── _turn_with_monitor: the server's `incomplete` frame ──────────────────────

class _Mon:
    seeds: list = []

    def __init__(self, pa):  # noqa: ARG002
        pass

    def start(self):
        pass

    def stop(self):
        pass

    def take_seed(self):
        return None


def _turn_harness(daemon, monkeypatch, script):
    """script: one dict per turn call: {"incomplete": bool, "played": bool}."""
    calls: list[dict] = []

    def _turn(pa, wav, **kw):  # noqa: ARG001
        spec = script[len(calls)]
        calls.append({"wav": wav, **kw})
        daemon._last_turn_flags.clear()
        daemon._last_turn_flags["voice_claim"] = ("casey", 0.8)
        if spec.get("incomplete"):
            daemon._last_turn_flags["incomplete"] = True
        return spec.get("played", False)
    monkeypatch.setattr(daemon, "_do_single_turn_stream", _turn)
    monkeypatch.setattr(daemon, "_BargeMonitor", _Mon)
    monkeypatch.setattr(daemon, "_last_record_capped", False)
    monkeypatch.setattr(daemon, "_recording_active", types.SimpleNamespace(set=lambda: None, clear=lambda: None))
    return _turn, calls


def test_a_normal_turn_is_one_call_that_offers_to_continue(daemon, monkeypatch):
    turn, calls = _turn_harness(daemon, monkeypatch, [{"played": True}])
    listened = []
    monkeypatch.setattr(daemon, "_follow_up_listen", lambda *a, **k: listened.append(1))
    played, seed = daemon._turn_with_monitor(_FakePA(), b"W1", turn, window="followup", prompt_on_empty=False)
    assert played is True and seed is None
    assert len(calls) == 1 and listened == []
    assert calls[0]["can_continue"] is True and calls[0]["window"] == "followup"
    assert "continued" not in calls[0] and calls[0]["prompt_on_empty"] is False


def test_a_held_turn_listens_merges_and_resends_once_as_continued(daemon, monkeypatch):
    turn, calls = _turn_harness(daemon, monkeypatch, [{"incomplete": True}, {"played": True}])
    first, rest = _wav(daemon, 1.0), _wav(daemon, 0.5)
    seen = []
    monkeypatch.setattr(daemon, "_follow_up_listen", lambda pa, window_s=None, **k: seen.append(window_s) or rest)
    played, _ = daemon._turn_with_monitor(_FakePA(), first, turn, window="followup")
    assert played is True and len(calls) == 2
    assert seen == [daemon.ZOE_CONTINUE_WINDOW_S]
    assert calls[0]["can_continue"] is True and "continued" not in calls[0]
    assert calls[0]["wav"] == first
    assert calls[1]["continued"] is True and "can_continue" not in calls[1]
    assert _secs(calls[1]["wav"]) == pytest.approx(1.0 + 0.2 + 0.5, abs=0.001), "ONE merged clip"
    assert calls[1]["voice_claim"] == ("casey", 0.8), "the held half's speaker score is reused, not redone"
    assert calls[1]["window"] == "followup"


def test_a_held_turn_with_no_continuation_resends_the_original(daemon, monkeypatch):
    turn, calls = _turn_harness(daemon, monkeypatch, [{"incomplete": True}, {"played": True}])
    monkeypatch.setattr(daemon, "_follow_up_listen", lambda *a, **k: None)
    first = _wav(daemon, 1.0)
    played, _ = daemon._turn_with_monitor(_FakePA(), first, turn)
    assert played is True and len(calls) == 2
    assert calls[1]["wav"] == first and calls[1]["continued"] is True


def test_a_turn_is_never_held_twice(daemon, monkeypatch):
    # A misbehaving server answers `incomplete` to the retry too: the daemon stops at two calls.
    turn, calls = _turn_harness(daemon, monkeypatch, [{"incomplete": True}, {"incomplete": True}])
    monkeypatch.setattr(daemon, "_follow_up_listen", lambda *a, **k: None)
    played, _ = daemon._turn_with_monitor(_FakePA(), _wav(daemon, 1.0), turn)
    assert played is False and len(calls) == 2


def test_continuation_off_never_asks_the_server_to_hold(daemon, monkeypatch):
    turn, calls = _turn_harness(daemon, monkeypatch, [{"played": True}])
    monkeypatch.setattr(daemon, "ZOE_CONTINUE_WINDOW_S", 0.0)
    daemon._turn_with_monitor(_FakePA(), b"W1", turn)
    assert "can_continue" not in calls[0]


def test_the_blocking_turn_gets_no_new_kwargs(daemon, monkeypatch):
    seen = {}

    def _blocking(pa, wav, **kw):  # noqa: ARG001
        seen.update(kw)
        return True
    _turn_harness(daemon, monkeypatch, [])
    monkeypatch.setattr(daemon, "_do_single_turn", _blocking)
    daemon._turn_with_monitor(_FakePA(), b"W1", daemon._do_single_turn, window="followup",
                              prompt_on_empty=False)
    assert seen == {"prompt_on_empty": False}


def test_the_blocking_turn_also_merges_a_hard_cap_continuation(daemon, monkeypatch):
    # ZOE_VOICE_STREAM=0 must not recreate the split: the cap merge is not a streaming-only feature (review, #2000).
    sent = []

    def _blocking(pa, wav, **kw):  # noqa: ARG001
        sent.append(wav)
        return True
    _turn_harness(daemon, monkeypatch, [])
    monkeypatch.setattr(daemon, "_do_single_turn", _blocking)
    first, rest = _wav(daemon, 2.0), _wav(daemon, 1.0)
    monkeypatch.setattr(daemon, "_last_record_capped", True)
    _fake_listen(daemon, monkeypatch, [rest])
    daemon._turn_with_monitor(_FakePA(), first, daemon._do_single_turn, window="followup", prompt_on_empty=False)
    assert len(sent) == 1 and _secs(sent[0]) == pytest.approx(2.0 + 0.2 + 1.0, abs=0.001)


def test_the_monitor_is_stopped_before_the_continuation_listen(daemon, monkeypatch):
    # The Jabra cannot hold two input streams: the barge monitor must be down first.
    order = []

    class _OrderedMon(_Mon):
        def start(self):
            order.append("start")

        def stop(self):
            order.append("stop")
    turn, calls = _turn_harness(daemon, monkeypatch, [{"incomplete": True}, {"played": True}])
    monkeypatch.setattr(daemon, "_BargeMonitor", _OrderedMon)
    monkeypatch.setattr(daemon, "_follow_up_listen", lambda *a, **k: order.append("listen") or None)
    daemon._turn_with_monitor(_FakePA(), _wav(daemon, 1.0), turn)
    assert order == ["start", "stop", "listen", "start", "stop"]


# ── the wire: payload fields and the two silent frames ───────────────────────

class _FakeResp:
    def __init__(self, lines):
        self._lines = lines

    def raise_for_status(self):
        pass

    def iter_lines(self, decode_unicode=False):  # noqa: ARG002
        return iter(self._lines)


def _wire(daemon, monkeypatch, lines):
    posted: list[dict] = []
    played: list[bytes] = []

    def _post(url, json=None, headers=None, timeout=None, stream=False, verify=True):  # noqa: A002, ARG001
        posted.append(json)
        return _FakeResp(lines)
    monkeypatch.setattr(daemon.requests, "post", _post, raising=False)
    monkeypatch.setattr(daemon, "_speaker_claim_to_attach", lambda _w: None)
    monkeypatch.setattr(daemon, "_feed_pcm_chunk", lambda aplay, b: played.append(b) or None)
    monkeypatch.setattr(daemon, "_is_junk_transcript", lambda _t: False)
    monkeypatch.setattr(daemon, "play_follow_up_beep", lambda: pytest.fail("chime on a silent frame"))
    monkeypatch.setattr(daemon, "_do_single_turn", lambda *a, **k: pytest.fail("blocking re-POST"))
    monkeypatch.setattr(daemon, "_prev_reply_asked", False)
    return posted, played


def _frames(*objs):
    return [json.dumps(o).encode() for o in objs]


def test_payload_carries_window_and_continue_fields_only_when_asked(daemon, monkeypatch):
    posted, _ = _wire(daemon, monkeypatch, _frames({"transcript": "x"}, {"done": True, "reply": ""}))
    daemon._do_single_turn_stream(_FakePA(), b"RIFFwav", prompt_on_empty=False)
    for key in ("window", "prev_reply_question", "can_continue", "continued"):
        assert key not in posted[0], f"{key} leaked into a plain turn (old server contract)"
    daemon._do_single_turn_stream(_FakePA(), b"RIFFwav", prompt_on_empty=False,
                                  window="followup", can_continue=True)
    assert posted[1]["window"] == "followup" and posted[1]["can_continue"] is True
    assert "prev_reply_question" not in posted[1] and "continued" not in posted[1]
    daemon._do_single_turn_stream(_FakePA(), b"RIFFwav", prompt_on_empty=False, continued=True)
    assert posted[2]["continued"] is True and "window" not in posted[2]


def test_incomplete_frame_is_silent_and_flags_the_turn(daemon, monkeypatch):
    _, played = _wire(daemon, monkeypatch, _frames({"done": True, "reply": "", "incomplete": True}))
    daemon._prev_reply_asked = True
    ok = daemon._do_single_turn_stream(_FakePA(), b"RIFFwav", prompt_on_empty=True, can_continue=True)
    assert ok is False and played == []
    assert daemon._last_turn_flags.get("incomplete") is True
    assert daemon._prev_reply_asked is True, "a held turn says nothing about Zoe's last reply"


def test_skipped_frame_is_silent_no_chime_and_no_state_change(daemon, monkeypatch):
    _, played = _wire(daemon, monkeypatch, _frames(
        {"done": True, "reply": "", "skipped": "not_addressed", "reason": "vocative"}))
    ok = daemon._do_single_turn_stream(_FakePA(), b"RIFFwav", prompt_on_empty=True, window="followup")
    assert ok is False and played == []
    assert daemon._last_turn_flags.get("skipped") == "not_addressed"
    assert not daemon._last_turn_flags.get("incomplete")


def test_a_question_reply_is_remembered_for_the_next_followup(daemon, monkeypatch):
    # the audio frame between a chunk header and the next JSON line is raw base64
    lines = [json.dumps({"transcript": "what should we cook"}).encode(),
             json.dumps({"chunk": 0, "text": "How about pasta tonight?"}).encode(),
             base64.b64encode(b"RIFFpasta"),
             json.dumps({"done": True, "reply": "How about pasta tonight?"}).encode()]
    posted, played = _wire(daemon, monkeypatch, lines)
    assert daemon._do_single_turn_stream(_FakePA(), b"RIFFwav", prompt_on_empty=False) is True
    assert played == [b"RIFFpasta"] and daemon._prev_reply_asked is True
    daemon._do_single_turn_stream(_FakePA(), b"RIFFwav", prompt_on_empty=False, window="followup")
    assert posted[1]["prev_reply_question"] is True
    # a statement reply clears it
    lines2 = [json.dumps({"transcript": "ok"}).encode(),
              json.dumps({"chunk": 0, "text": "Done."}).encode(),
              base64.b64encode(b"RIFFdone"),
              json.dumps({"done": True, "reply": "Done."}).encode()]
    posted2, _ = _wire(daemon, monkeypatch, lines2)
    daemon._prev_reply_asked = True
    daemon._do_single_turn_stream(_FakePA(), b"RIFFwav", prompt_on_empty=False)
    assert daemon._prev_reply_asked is False


# ── the offline replay instrument ────────────────────────────────────────────

def _instrument():
    path = DAEMON.parents[1] / "perf" / "measure_long_speech_capture.py"
    spec = importlib.util.spec_from_file_location("_long_speech_instrument_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_replay_instrument_sees_the_old_cap_and_the_new_continue(daemon, monkeypatch):
    import numpy as np
    inst = _instrument()
    _vad(daemon, monkeypatch, [])          # installs flags; replay() swaps in its own scripted VAD
    audio = np.full(1280 * 154, 3000, dtype=np.int16)         # a 12.32 s clip, speech all the way
    probs = [SPEECH] * 154
    row = inst.replay(daemon, audio, probs, daemon._record_caps())
    assert row["stop"] == "clip_ends_recorder_open" and row["would_continue"] is True
    # Negative control: the old flat 12 s cap stops that same clip.
    monkeypatch.setattr(daemon, "RECORD_SECONDS_HARD", 12)
    monkeypatch.setattr(daemon, "RECORD_SNAP_S", 0.0)
    row = inst.replay(daemon, audio, probs, daemon._record_caps())
    assert row["stop"] == "max_duration" and row["would_continue"] is False
    # A clip the endpointer closes on silence is a control and reports its tail rule.
    monkeypatch.setattr(daemon, "RECORD_SECONDS_HARD", 30)
    monkeypatch.setattr(daemon, "RECORD_SNAP_S", 3.0)
    probs = [SPEECH] * 40 + [DEEP] * 20
    row = inst.replay(daemon, np.zeros(1280 * 60, dtype=np.int16), probs, daemon._record_caps())
    assert row["stop"] == "silence" and row["would_continue"] is False


# ── voice_command: which turns carry which window ────────────────────────────

class _OWW:
    def reset(self):
        pass


def _drive_voice_command(daemon, monkeypatch, *, first_flags, sources, turn_results):
    calls: list[dict] = []
    src_calls: list[dict] = []
    src = iter(sources)
    res = iter(turn_results)
    monkeypatch.setattr(daemon, "record_command", lambda pa, stream=None, speculation=None: b"W0")
    monkeypatch.setattr(daemon, "_speculation_available", lambda: False)
    monkeypatch.setattr(daemon, "POST_PLAY_TAIL_S", 0.0)
    monkeypatch.setattr(daemon, "FOLLOW_UP_MAX_TURNS", 5)
    monkeypatch.setattr(daemon, "FOLLOW_UP_LISTEN_S", 5.0)
    monkeypatch.setattr(daemon, "CONV_SILENT_WINDOWS", 1)

    def _source(pa, seed, **kw):  # noqa: ARG001
        src_calls.append({"seed": seed, **kw})
        return next(src, None)
    monkeypatch.setattr(daemon, "_follow_up_source", _source)

    def _tw(pa, wav, turn_fn, *, window="", **kw):  # noqa: ARG001
        calls.append({"wav": wav, "window": window, **kw})
        if len(calls) == 1:
            daemon._last_turn_flags.clear()
            daemon._last_turn_flags.update(first_flags)
        return next(res)
    monkeypatch.setattr(daemon, "_turn_with_monitor", _tw)
    daemon.voice_command(_FakePA(), _OWW(), wake_stream=None)
    return calls, src_calls


def test_voice_command_labels_followup_windows_and_leaves_barge_seeds_unlabelled(daemon, monkeypatch):
    calls, srcs = _drive_voice_command(
        daemon, monkeypatch, first_flags={},
        sources=[b"W1", b"W2"],
        turn_results=[(True, b"SEED"), (True, None), (False, None)],
    )
    assert [c["window"] for c in calls] == ["", "", "followup"]
    # turn 1 produced a barge seed -> follow-up window 1 is seeded -> no hint; window 2 is a real listen.
    assert srcs[0]["seed"] == b"SEED" and srcs[1]["seed"] is None
    assert all(c.get("prompt_on_empty") is False for c in calls[1:])


def test_voice_command_conversation_windows_get_the_conversation_hint_and_hard_cap(daemon, monkeypatch):
    calls, srcs = _drive_voice_command(
        daemon, monkeypatch, first_flags={"conversation_mode": True},
        sources=[b"C1", None],
        turn_results=[(True, None), (True, None)],
    )
    assert [c["window"] for c in calls] == ["", "conversation"]
    assert calls[1]["conversation"] is True and calls[1]["prompt_on_empty"] is False
    assert all(s["hard_cap_s"] == daemon.CONV_RECORD_SECONDS_HARD for s in srcs)
