"""The deep-quiet fast tail (ZOE_VAD_TAIL_MS) must be opt-in and gated.

The panel daemon has no test framework on the Pi, so the endpointer's decision
table is pinned here, torch-free: `_vad_prob` is replaced with a scripted
probability sequence and the real `_Endpointer` runs against it. The properties
that make the flag safe to deploy dark:

  1. flag unset (0) -> behaviour identical to the pre-flag endpointer
     (close after VAD_ENDPOINT_SILENCE_S of quiet, never earlier),
  2. flag set -> close after ZOE_VAD_TAIL_MS of consecutive DEEP quiet,
  3. ambiguous quiet (below the speech threshold but above the deep threshold)
     must never take the fast exit — it resets the deep counter and falls back
     to the full 800ms tail,
  4. the fast exit needs confirmed speech first and respects the
     minimum-recording guard.
"""
import importlib.util
import sys
import types
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

DAEMON = Path(__file__).resolve().parents[2] / "scripts" / "setup" / "zoe_voice_daemon.py"

SPEECH = 0.9      # >= VAD_ENDPOINT_THRESHOLD (0.35)
DEEP = 0.02       # < ZOE_VAD_TAIL_DEEP_PROB (0.10)
BORDERLINE = 0.2  # quiet, but not deep: in [0.10, 0.35)


@pytest.fixture(scope="module")
def daemon():
    """Import the daemon with its device-only top-level deps stubbed (same
    recipe as test_voice_wake_no_dead_air.py: pyaudio/requests are mic + HTTP,
    absent in the slim CI venv and irrelevant to the endpointer)."""
    stubs = {}
    fake_pyaudio = types.ModuleType("pyaudio")
    fake_pyaudio.paInt16 = 8
    fake_pyaudio.PyAudio = object
    stubs["pyaudio"] = fake_pyaudio
    stubs["requests"] = types.ModuleType("requests")

    saved = {name: sys.modules.get(name) for name in stubs}
    sys.modules.update(stubs)
    try:
        spec = importlib.util.spec_from_file_location("zoe_voice_daemon_tail_test", DAEMON)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        yield mod
    finally:
        for name, prev in saved.items():
            if prev is not None:
                sys.modules[name] = prev
            else:
                sys.modules.pop(name, None)


def make_endpointer(daemon, monkeypatch, probs, tail_ms, spoke=False):
    """Real _Endpointer in vad mode with a scripted probability stream."""
    monkeypatch.setattr(daemon, "VAD_ENDPOINT_ENABLED", True)
    monkeypatch.setattr(daemon, "_get_silero_vad", lambda: (object(), None))
    monkeypatch.setattr(daemon, "ZOE_VAD_TAIL_MS", tail_ms)
    # Pin EVERY threshold the decision table depends on — the module read them
    # from the process env at import, so a developer (or CI lane) running with
    # VAD_ENDPOINT_SILENCE_S etc. set would silently shift every expected chunk
    # count in this file (Copilot, #1573).
    monkeypatch.setattr(daemon, "ZOE_VAD_TAIL_DEEP_PROB", 0.10)
    monkeypatch.setattr(daemon, "VAD_ENDPOINT_THRESHOLD", 0.35)
    monkeypatch.setattr(daemon, "VAD_ENDPOINT_SILENCE_S", 0.8)
    monkeypatch.setattr(daemon, "SAMPLE_RATE", 16000)
    monkeypatch.setattr(daemon, "CHUNK_SIZE", 1280)
    seq = iter(probs)
    monkeypatch.setattr(daemon, "_vad_prob", lambda _model, _chunk: next(seq))
    ep = daemon._Endpointer(spoke=spoke)
    assert ep.mode == "vad"
    return ep


def closes_at(ep, n_chunks, n_frames=100):
    """Feed n_chunks; return the 1-based chunk index that closed, or None.

    n_frames is fixed comfortably above the 0.5s minimum-recording guard so the
    guard does not mask the property under test (it gets its own test below).
    """
    for i in range(1, n_chunks + 1):
        if ep.push(b"", n_frames):
            return i
    return None


def test_flag_off_is_the_preflag_endpointer(daemon, monkeypatch):
    # 800ms tail = 10 chunks of 80ms. Chunk 1 is speech, so quiet #10 lands on
    # chunk 11 — closing there, and NOT one chunk earlier, is the pre-flag law.
    ep = make_endpointer(daemon, monkeypatch, [SPEECH] + [DEEP] * 20, tail_ms=0)
    assert closes_at(ep, 21) == 11


def test_flag_off_deep_bookkeeping_changes_nothing(daemon, monkeypatch):
    # Same stream, mixed deep/borderline quiet: with the flag off the deep
    # counter may tick internally but the close point must stay the regular one.
    probs = [SPEECH] + [DEEP, BORDERLINE] * 10
    ep = make_endpointer(daemon, monkeypatch, probs, tail_ms=0)
    assert closes_at(ep, 21) == 11


def test_fast_tail_closes_on_deep_silence(daemon, monkeypatch):
    # 640ms = 8 chunks: speech on chunk 1, eight deep-quiet chunks -> close on
    # chunk 9, a full 160ms before the 800ms tail would have.
    ep = make_endpointer(daemon, monkeypatch, [SPEECH] + [DEEP] * 20, tail_ms=640)
    assert closes_at(ep, 21) == 9


def test_ambiguous_quiet_never_takes_the_fast_exit(daemon, monkeypatch):
    # All-borderline pause: quiet for the regular counter, never deep. The fast
    # tail must not fire; the close is the regular 10-quiet one (chunk 11).
    ep = make_endpointer(daemon, monkeypatch, [SPEECH] + [BORDERLINE] * 20, tail_ms=640)
    assert closes_at(ep, 21) == 11


def test_borderline_resets_the_deep_counter(daemon, monkeypatch):
    # 7 deep, one borderline, then deep again: the deep run restarts, and the
    # regular 10-quiet limit (chunk 11) wins before 8 consecutive deep recurs.
    probs = [SPEECH] + [DEEP] * 7 + [BORDERLINE] + [DEEP] * 12
    ep = make_endpointer(daemon, monkeypatch, probs, tail_ms=640)
    assert closes_at(ep, 21) == 11


def test_fast_tail_requires_confirmed_speech(daemon, monkeypatch):
    # No speech yet: deep silence alone must not fast-close (the slow-starter
    # protection). 12 deep chunks pass without closing; only the amplitude-mode
    # timeout (1.5s = 18 chunks) may end a speechless recording.
    ep = make_endpointer(daemon, monkeypatch, [DEEP] * 20, tail_ms=640)
    assert closes_at(ep, 12) is None


def test_min_recording_guard_applies_to_fast_tail(daemon, monkeypatch):
    # spoke=True (follow-up recorder) + instant deep silence, but n_frames at or
    # below the 0.5s guard: the fast exit must wait for the guard.
    ep = make_endpointer(daemon, monkeypatch, [DEEP] * 20, tail_ms=640, spoke=True)
    for _ in range(10):
        assert not ep.push(b"", n_frames=ep._min_frames)
