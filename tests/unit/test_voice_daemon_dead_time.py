"""Per-turn dead time in the panel daemon (`scripts/setup/zoe_voice_daemon.py`).

Three knobs, pinned torch-free (the real `_Endpointer` runs against a scripted
`_vad_prob` stream, same recipe as test_voice_endpointer_tail.py):

  * the adaptive endpoint tail — ZOE_VAD_CLEAN_TAIL_MS closes a CLEAN stop
    (at most ZOE_VAD_CLEAN_FALL_CHUNKS ambiguous decay chunks, then only deep
    quiet) sooner, never below 0.5 s and only after enough speech;
    ZOE_VAD_HESITATION_TAIL_MS lets a quiet run that went deep and came back
    up wait longer (capped at 1.5 s). Both default OFF (byte-identical);
  * POST_PLAY_COOLDOWN_S default 0.4 s (was 1.5 s), env-tunable;
  * RECORD_SECONDS_MAX default 12 s (was 8 s), env-tunable.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe  # GitHub-CI opt-in: runs in validate.yml's `-m ci_safe` lane

DAEMON = Path(__file__).resolve().parents[2] / "scripts" / "setup" / "zoe_voice_daemon.py"

SPEECH = 0.9      # >= VAD_ENDPOINT_THRESHOLD (0.35)
DEEP = 0.02       # < ZOE_VAD_TAIL_DEEP_PROB (0.10)
DECAY = 0.2       # quiet but not deep: in [0.10, 0.35)
_ENV = ("RECORD_SECONDS_MAX", "POST_PLAY_COOLDOWN_S", "ZOE_VAD_CLEAN_TAIL_MS",
        "ZOE_VAD_CLEAN_FALL_CHUNKS", "ZOE_VAD_CLEAN_MIN_SPEECH_MS",
        "ZOE_VAD_HESITATION_TAIL_MS")


def _load(name: str):
    stubs = {}
    fake_pyaudio = types.ModuleType("pyaudio")
    fake_pyaudio.paInt16 = 8
    fake_pyaudio.PyAudio = object
    stubs["pyaudio"] = fake_pyaudio
    stubs["requests"] = types.ModuleType("requests")
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
        yield _load("zoe_voice_daemon_dead_time_test")
    finally:
        mp.undo()


def close_at(daemon, monkeypatch, probs, *, clean_ms=0, hes_ms=0, fall=2,
             min_speech_ms=480, deep_tail_ms=640):
    """Push ``probs`` through a real vad-mode _Endpointer; return (n, rule) at close."""
    monkeypatch.setattr(daemon, "VAD_ENDPOINT_ENABLED", True)
    monkeypatch.setattr(daemon, "_get_silero_vad", lambda: (object(), None))
    monkeypatch.setattr(daemon, "ZOE_VAD_TAIL_MS", deep_tail_ms)
    monkeypatch.setattr(daemon, "ZOE_VAD_TAIL_DEEP_PROB", 0.10)
    monkeypatch.setattr(daemon, "VAD_ENDPOINT_THRESHOLD", 0.35)
    monkeypatch.setattr(daemon, "VAD_ENDPOINT_SILENCE_S", 0.8)
    monkeypatch.setattr(daemon, "SAMPLE_RATE", 16000)
    monkeypatch.setattr(daemon, "CHUNK_SIZE", 1280)
    monkeypatch.setattr(daemon, "ZOE_VAD_CLEAN_TAIL_MS", clean_ms)
    monkeypatch.setattr(daemon, "ZOE_VAD_CLEAN_FALL_CHUNKS", fall)
    monkeypatch.setattr(daemon, "ZOE_VAD_CLEAN_MIN_SPEECH_MS", min_speech_ms)
    monkeypatch.setattr(daemon, "ZOE_VAD_HESITATION_TAIL_MS", hes_ms)
    seq = iter(probs)
    monkeypatch.setattr(daemon, "_vad_prob", lambda _m, _c: next(seq))
    ep = daemon._Endpointer()
    for n in range(1, len(probs) + 1):
        if ep.push(b"\x00\x00" * 1280, n):
            return n, ep.tail_rule
    return None, ep.tail_rule


def _quiet_after(speech_chunks: int, tail: list[float]) -> list[float]:
    return [SPEECH] * speech_chunks + tail


# ── adaptive tail ────────────────────────────────────────────────────────────

def test_flags_off_is_the_live_640_800_behaviour(daemon, monkeypatch):
    # Clean fall: 1 decay chunk then deep -> the deep tail needs 8 deep = quiet 9.
    probs = _quiet_after(10, [DECAY] + [DEEP] * 20)
    assert close_at(daemon, monkeypatch, probs) == (10 + 9, "deep")


def test_clean_stop_ends_early(daemon, monkeypatch):
    probs = _quiet_after(10, [DECAY] + [DEEP] * 20)
    n, rule = close_at(daemon, monkeypatch, probs, clean_ms=560)
    assert (n - 10, rule) == (7, "clean")  # 560 ms of quiet, 160 ms sooner than today


def test_clean_tail_is_never_shorter_than_half_a_second(daemon, monkeypatch):
    probs = _quiet_after(10, [DEEP] * 20)
    n, rule = close_at(daemon, monkeypatch, probs, clean_ms=100)
    assert rule == "clean" and (n - 10) * 80 >= 500


def test_hesitation_is_never_a_clean_stop(daemon, monkeypatch):
    # Went deep, came back up (breath / "um"), deep again: not a clean fall.
    probs = _quiet_after(10, [DECAY, DEEP, DEEP, DECAY] + [DEEP] * 20)
    # Same close as with the flag off: the 800 ms any-quiet tail.
    assert close_at(daemon, monkeypatch, probs, clean_ms=560) == (10 + 10, "quiet")


def test_slow_decay_is_not_a_clean_stop(daemon, monkeypatch):
    probs = _quiet_after(10, [DECAY] * 3 + [DEEP] * 20)
    assert close_at(daemon, monkeypatch, probs, clean_ms=560, fall=2) == (10 + 10, "quiet")


def test_clean_tail_needs_enough_speech_first(daemon, monkeypatch):
    # A lone "yes"-length blip (3 chunks = 240 ms < 480 ms) keeps the full tail.
    probs = [DEEP] * 7 + [SPEECH] * 3 + [DEEP] * 20
    n, rule = close_at(daemon, monkeypatch, probs, clean_ms=560)
    assert rule == "deep" and n == 7 + 3 + 8


def test_hesitation_waits_longer_and_is_capped(daemon, monkeypatch):
    probs = _quiet_after(10, [DEEP, DECAY] + [DECAY] * 40)
    assert close_at(daemon, monkeypatch, probs) == (10 + 10, "quiet")          # today: 800 ms
    assert close_at(daemon, monkeypatch, probs, hes_ms=1200) == (10 + 15, "hesitation")
    assert close_at(daemon, monkeypatch, probs, hes_ms=9000) == (10 + 19, "hesitation")  # 1.5 s cap


def test_hesitation_tail_never_delays_a_plain_decay(daemon, monkeypatch):
    # Ambiguous straight after speech (no deep in between) is the normal
    # 800 ms tail — the long wait is only for a run that came back up.
    probs = _quiet_after(10, [DECAY] * 40)
    assert close_at(daemon, monkeypatch, probs, hes_ms=1500) == (10 + 10, "quiet")


def test_env_knobs_parse_and_default_off(daemon, monkeypatch):
    assert daemon.ZOE_VAD_CLEAN_TAIL_MS == 0 and daemon.ZOE_VAD_HESITATION_TAIL_MS == 0
    for k, v in {"ZOE_VAD_CLEAN_TAIL_MS": "560", "ZOE_VAD_HESITATION_TAIL_MS": "1500",
                 "ZOE_VAD_CLEAN_FALL_CHUNKS": "1", "ZOE_VAD_CLEAN_MIN_SPEECH_MS": "800"}.items():
        monkeypatch.setenv(k, v)
    mod = _load("zoe_voice_daemon_dead_time_env_test")
    assert (mod.ZOE_VAD_CLEAN_TAIL_MS, mod.ZOE_VAD_HESITATION_TAIL_MS) == (560, 1500)
    assert (mod.ZOE_VAD_CLEAN_FALL_CHUNKS, mod.ZOE_VAD_CLEAN_MIN_SPEECH_MS) == (1, 800)


# ── cooldown + cap ───────────────────────────────────────────────────────────

def test_cooldown_and_cap_defaults(daemon):
    assert daemon.POST_PLAY_COOLDOWN_S == 0.4
    assert daemon.RECORD_SECONDS == 12


def test_cooldown_and_cap_env_overrides(monkeypatch):
    monkeypatch.setenv("POST_PLAY_COOLDOWN_S", "1.5")
    monkeypatch.setenv("RECORD_SECONDS_MAX", "8")
    mod = _load("zoe_voice_daemon_dead_time_override_test")
    assert mod.POST_PLAY_COOLDOWN_S == 1.5 and mod.RECORD_SECONDS == 8


def test_malformed_cooldown_or_cap_keeps_the_default(monkeypatch):
    monkeypatch.setenv("POST_PLAY_COOLDOWN_S", "soon")
    monkeypatch.setenv("RECORD_SECONDS_MAX", "long")
    mod = _load("zoe_voice_daemon_dead_time_bad_test")
    assert mod.POST_PLAY_COOLDOWN_S == 0.4 and mod.RECORD_SECONDS == 12
