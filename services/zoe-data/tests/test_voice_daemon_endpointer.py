"""Regression net for the daemon's VAD endpointing (turn-tail latency).

The daemon (scripts/setup/zoe_voice_daemon.py) can't be imported in CI (pyaudio
mic loop at module scope), so — like test_voice_daemon_capture.py — we pin the
contract via source inspection, and exercise the REAL _Endpointer class by
exec'ing its extracted source with stubbed module globals.

Why it exists: amplitude endpointing waits SILENCE_TIMEOUT_S (1.5s) of raw
quiet after every utterance — a fixed +1.5s on every panel turn. VAD mode
closes the turn after VAD_ENDPOINT_SILENCE_S (0.8s) of Silero speech-absence
once speech was heard, and still uses the long timeout before any speech so
slow starters aren't cut off.
"""
import array
import os
import re

import pytest

pytestmark = pytest.mark.ci_safe

_DAEMON = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "scripts", "setup", "zoe_voice_daemon.py")
)
_SRC = open(_DAEMON, encoding="utf-8").read()

# Pure-stdlib numpy stand-in: the CI validate env has no numpy (the daemon
# itself runs on the Pi, where it does). Only what _Endpointer.push touches.
class _AbsView(list):
    def mean(self):
        return sum(self) / len(self)


class _NpShim:
    int16 = "int16"

    @staticmethod
    def frombuffer(data, dtype=None):
        return array.array("h", data)

    @staticmethod
    def abs(arr):
        return _AbsView(abs(x) for x in arr)


np = _NpShim()

# 80ms chunks at the daemon defaults (16000Hz / 1280 samples).
_QUIET = array.array("h", [0] * 1280).tobytes()
_LOUD = array.array("h", [3000] * 1280).tobytes()


def test_flag_defaults_and_both_recorders_use_endpointer():
    # Ships OFF; enable per-device via /home/pi/.zoe-voice/.env.voice.
    assert re.search(r'VAD_ENDPOINT_ENABLED"\s*,\s*"false"', _SRC)
    assert re.search(r'VAD_ENDPOINT_SILENCE_S"\s*,\s*"0\.8"', _SRC)
    assert re.search(r'VAD_ENDPOINT_THRESHOLD"\s*,\s*"0\.35"', _SRC)
    # Both recording loops must go through the shared endpointer — a revert to
    # inline amplitude counting in either loop reintroduces the 1.5s tail.
    assert _SRC.count("endpointer.push(data, len(frames))") == 2
    assert "_Endpointer(spoke=True)" in _SRC  # follow-up seeds fast tail


def _make_endpointer(vad_enabled, vad_probs=None, spoke=False):
    """Exec the real _Endpointer source with stubbed daemon globals."""
    m = re.search(r"\nclass _Endpointer:.*?(?=\n\ndef _api_post)", _SRC, re.DOTALL)
    assert m, "_Endpointer class not found in daemon source"
    probs = iter(vad_probs or [])
    g = {
        "np": np,
        "VAD_ENDPOINT_ENABLED": vad_enabled,
        "VAD_ENDPOINT_SILENCE_S": 0.8,
        "VAD_ENDPOINT_THRESHOLD": 0.35,
        "SILENCE_TIMEOUT_S": 1.5,
        "RECORD_SILENCE_AMPLITUDE": 300,
        "SAMPLE_RATE": 16000,
        "CHUNK_SIZE": 1280,
        "_get_silero_vad": lambda: (object(), None),
        "_vad_prob": lambda model, chunk: next(probs),
    }
    exec(compile(m.group(0), _DAEMON, "exec"), g)
    return g["_Endpointer"](spoke=spoke)


def _chunks_until_stop(ep, chunks):
    for i, data in enumerate(chunks, start=1):
        if ep.push(data, n_frames=i + 20):  # past the min-frames floor
            return i
    return None


def test_vad_mode_closes_after_short_tail_once_spoken():
    # 3 speech chunks then silence: stop 10 quiet chunks (0.8s) later,
    # NOT the 18 chunks (1.5s) amplitude would need.
    probs = [0.9, 0.9, 0.9] + [0.05] * 30
    ep = _make_endpointer(True, vad_probs=probs)
    assert ep.mode == "vad"
    stopped_at = _chunks_until_stop(ep, [_LOUD] * 3 + [_QUIET] * 30)
    assert stopped_at == 3 + 10, f"0.8s VAD tail = 10 chunks, got {stopped_at}"


def test_vad_mode_keeps_long_timeout_before_any_speech():
    # Never spoke: the long 1.5s (18-chunk) timeout applies, not the fast tail.
    ep = _make_endpointer(True, vad_probs=[0.05] * 40)
    stopped_at = _chunks_until_stop(ep, [_QUIET] * 40)
    assert stopped_at == 18, f"pre-speech timeout must stay 1.5s, got {stopped_at}"


def test_follow_up_seed_applies_fast_tail_immediately():
    ep = _make_endpointer(True, vad_probs=[0.05] * 30, spoke=True)
    stopped_at = _chunks_until_stop(ep, [_QUIET] * 30)
    assert stopped_at == 10


def test_amplitude_mode_unchanged_when_flag_off():
    ep = _make_endpointer(False)
    assert ep.mode == "amplitude"
    # 5 loud then quiet: stops 18 quiet chunks later (1.5s), exactly as before.
    stopped_at = _chunks_until_stop(ep, [_LOUD] * 5 + [_QUIET] * 30)
    assert stopped_at == 5 + 18


def test_vad_mode_falls_back_to_amplitude_without_silero():
    m = re.search(r"\nclass _Endpointer:.*?(?=\n\ndef _api_post)", _SRC, re.DOTALL)
    g = {
        "np": np,
        "VAD_ENDPOINT_ENABLED": True,
        "VAD_ENDPOINT_SILENCE_S": 0.8,
        "VAD_ENDPOINT_THRESHOLD": 0.35,
        "SILENCE_TIMEOUT_S": 1.5,
        "RECORD_SILENCE_AMPLITUDE": 300,
        "SAMPLE_RATE": 16000,
        "CHUNK_SIZE": 1280,
        "_get_silero_vad": lambda: (None, None),  # model unavailable
        "_vad_prob": lambda model, chunk: 0.0,
    }
    exec(compile(m.group(0), _DAEMON, "exec"), g)
    assert g["_Endpointer"]().mode == "amplitude"


def test_barge_monitor_requires_live_tts_process():
    """The monitor must NOT set the barge flag when nothing is playing: speech
    right after the endpointer closes a recording is the user still talking,
    and a stale flag aborts a reply that never started (live 22:28:10 — barge
    prob=0.99 fired 189ms after a 7.84s recording closed, killing the turn).
    The playback-alive guard must run BEFORE the flag is set / TTS killed,
    mirroring the legacy queue-fed thread's `if _tts_process is None` guard."""
    i = _SRC.find("class _BargeMonitor")
    assert i != -1
    block = _SRC[i : i + 4500]
    fire = block.find('log.info("Barge-in detected during playback')
    guard = block.find("proc = _tts_process")
    set_flag = block.find("_barge_in_requested.set()")
    assert guard != -1 and fire != -1 and set_flag != -1
    assert guard < fire < set_flag, "playback-alive guard must precede fire+flag"
    assert "proc is None or proc.poll() is not None" in block


def test_daemon_never_reposts_a_processed_turn():
    """A transcript from turn_stream means the server executed the turn (writes
    included). Re-POSTing the same wav via the blocking /api/voice/turn runs it
    again — live 2026-07-07: every barge-aborted list add landed twice. Both
    the exception path and the clean no-audio path must refuse to re-POST."""
    assert "blocking fallback." not in _SRC, "the unguarded transcript+no-audio re-POST must stay dead"
    assert _SRC.count("NOT re-POSTing") >= 2, "both paths must carry the no-re-POST guard"
    assert "barged before first audio" in _SRC, "barge before audio returns to follow-up, not a re-POST"
