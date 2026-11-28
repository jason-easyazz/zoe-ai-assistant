"""The panel daemon's wake fire log, wake-time energy gate and silent drop (2026-11-28).

A confirmed wake logs WAKE_FIRE (score + the level of the 1.6 s the detector just scored). With
WAKE_ENERGY_GATE on, a wake whose loudest 80 ms chunk is under WAKE_ENERGY_FLOOR goes no further. With the
gate off, such a wake still runs its turn but an empty transcript plays no retry chime (WAKE_SILENT_DROP).

Synthetic audio only. Named controls:

  * ``test_silent_and_noise_windows_never_wake`` / ``test_a_quiet_but_real_phrase_still_wakes`` - the two sides of the
    floor, with levels taken from the corpus measurement (room floor, loudest silent wake, a real wake 12 dB down);
  * ``test_gate_off_is_log_only`` - the flag is the only thing that blocks;
  * ``test_main_loop_gates_the_wake_word_and_not_the_orb_tap`` + ``test_removing_the_gate_call_is_seen`` - the call
    site, and a negative control that deletes it from a copy of the source;
  * ``test_silent_drop_disarms_the_retry_chime_only_for_a_quiet_wake_word_turn`` - quiet / loud / orb tap / flag off.
"""
from __future__ import annotations

import ast
import importlib.util
import logging
import sys
import types
from pathlib import Path

import numpy as np
import pytest

pytestmark = pytest.mark.ci_safe

DAEMON = Path(__file__).resolve().parents[2] / "scripts" / "setup" / "zoe_voice_daemon.py"
CHUNK = 1280
SR = 16000


def _load(name: str):
    fake_pyaudio = types.ModuleType("pyaudio")
    fake_pyaudio.paInt16 = 8
    fake_pyaudio.PyAudio = object
    fake_requests = types.ModuleType("requests")
    fake_requests.exceptions = types.SimpleNamespace(
        SSLError=type("SSLError", (Exception,), {}), HTTPError=type("HTTPError", (Exception,), {}),
        RequestException=type("RequestException", (Exception,), {}))
    stubs = {"pyaudio": fake_pyaudio, "requests": fake_requests}
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
    return _load("zoe_voice_daemon_wake_gate_test")


@pytest.fixture(autouse=True)
def _defaults(daemon, monkeypatch):
    monkeypatch.setattr(daemon, "WAKE_ENERGY_GATE", True)
    monkeypatch.setattr(daemon, "WAKE_SILENT_DROP", True)
    monkeypatch.setattr(daemon, "_wake_window_quiet", False)
    yield


def _chunks(x: np.ndarray) -> list:
    x = np.clip(np.round(x), -32768, 32767).astype(np.int16)
    return [x[i:i + CHUNK].tobytes() for i in range(0, len(x) - CHUNK + 1, CHUNK)]


def _noise(rms: float, seconds: float = 1.6, seed: int = 3, dc: float = -0.5) -> np.ndarray:
    return np.random.default_rng(seed).normal(dc, rms, int(seconds * SR))


def _phrase(loud_rms: float, floor_rms: float = 30.0) -> np.ndarray:
    """A two-syllable voiced burst (0.25 s + 0.35 s of harmonics under a raised-cosine envelope) on a room floor,
    scaled so its loudest 80 ms chunk has RMS ``loud_rms``."""
    n = int(1.6 * SR)
    t = np.arange(n) / SR
    env = np.zeros(n)
    for start, dur in ((0.55, 0.25), (0.90, 0.35)):
        i, m = int(start * SR), int(dur * SR)
        env[i:i + m] = 0.5 - 0.5 * np.cos(2 * np.pi * np.arange(m) / m)
    voice = env * sum(np.sin(2 * np.pi * f * t) / k for k, f in enumerate((140, 280, 420, 700, 1200, 2400), 1))
    loudest = max(float(np.sqrt(np.mean(voice[i:i + CHUNK] ** 2))) for i in range(0, n - CHUNK + 1, CHUNK))
    return voice * (loud_rms / loudest) + _noise(floor_rms, seed=5)


def _fire_lines(caplog) -> list:
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith("WAKE_FIRE ")]


def test_levels_are_peak_rms_and_loudest_chunk_with_dc_removed(daemon):
    quiet = np.full(CHUNK, 500.0)                       # pure DC: no sound at all
    tone = 1000.0 * np.sin(2 * np.pi * 400 * np.arange(CHUNK) / SR) + 500.0
    peak, rms, loud = daemon._wake_window_levels(_chunks(np.concatenate([quiet, tone])))
    assert peak == pytest.approx(1000, abs=2)
    assert loud == pytest.approx(1000 / np.sqrt(2), rel=0.01)
    assert rms == pytest.approx(500.0, rel=0.01)        # half the samples are silent
    assert daemon._wake_window_levels([]) == (0, 0.0, 0.0)


def test_levels_use_only_the_window_the_detector_scored(daemon):
    old_shout = _chunks(np.random.default_rng(1).normal(0, 8000, 10 * CHUNK))
    recent_quiet = _chunks(_noise(30.0))
    assert len(recent_quiet) == daemon._WAKE_GATE_CHUNKS
    _peak, _rms, loud = daemon._wake_window_levels(old_shout + recent_quiet)
    assert loud < 60


@pytest.mark.parametrize("label,rms", [("digital_silence", 0.0), ("room_floor", 30.0), ("raised_floor", 90.0),
                                       ("loudest_silent_wake_in_the_corpus", 270.0)])
def test_silent_and_noise_windows_never_wake(daemon, caplog, label, rms):
    caplog.set_level(logging.INFO)
    assert daemon._wake_fire_allowed(_chunks(_noise(rms)), 0.62, 0.91) is False
    assert daemon._wake_window_quiet is True
    (line,) = _fire_lines(caplog)
    assert "gate=block" in line and "quiet=1" in line


@pytest.mark.parametrize("label,loud_rms", [("quietest_real_wake_10_db_down", 694.0), ("quietest_real_wake", 2196.0),
                                            ("typical_real_wake", 5000.0)])
def test_a_quiet_but_real_phrase_still_wakes(daemon, caplog, label, loud_rms):
    """The first case pins the shipped default: a floor raised to within 10 dB of the quietest real wake measured
    turns it red."""
    caplog.set_level(logging.INFO)
    assert daemon._wake_fire_allowed(_chunks(_phrase(loud_rms)), 0.97, 0.99) is True
    assert daemon._wake_window_quiet is False
    (line,) = _fire_lines(caplog)
    assert "gate=pass" in line and "quiet=0" in line


def test_default_floor_keeps_the_measured_margins(daemon):
    """The corpus numbers the default stands on (docs/knowledge/wake-and-speaker-fa-instruments.md): loudest silent
    wake 273, quietest real wake 2196 (loudest-chunk int16 RMS). The default must stay at least 6 dB clear of both."""
    floor = _load("zoe_voice_daemon_wake_gate_defaults").WAKE_ENERGY_FLOOR
    assert 273 * 2 <= floor <= 2196 / 2


def test_fire_log_line_carries_score_and_levels(daemon, caplog):
    caplog.set_level(logging.INFO)
    daemon._wake_fire_allowed(_chunks(_phrase(4000.0)), 0.8125, 0.9911)
    (line,) = _fire_lines(caplog)
    fields = dict(kv.split("=", 1) for kv in line.split()[1:])
    assert fields["score"] == "0.8125" and fields["score_max"] == "0.9911"
    assert float(fields["loud_rms"]) == pytest.approx(4000.0, rel=0.05)
    assert int(fields["peak"]) > float(fields["loud_rms"]) > float(fields["rms"]) > 0
    assert float(fields["floor"]) == daemon.WAKE_ENERGY_FLOOR


def test_gate_off_is_log_only(daemon, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(daemon, "WAKE_ENERGY_GATE", False)
    assert daemon._wake_fire_allowed(_chunks(_noise(30.0)), 0.6, 0.7) is True
    assert daemon._wake_window_quiet is True            # still measured: the silent drop reads it
    (line,) = _fire_lines(caplog)
    assert "gate=off" in line and "quiet=1" in line


def test_unreadable_levels_never_block_a_wake(daemon, monkeypatch, caplog):
    caplog.set_level(logging.INFO)

    def _boom(_chunks):
        raise ValueError("bad buffer")
    monkeypatch.setattr(daemon, "_wake_window_levels", _boom)
    assert daemon._wake_fire_allowed([b"\x00"], 0.6, 0.7) is True
    assert daemon._wake_window_quiet is False
    assert "gate=pass" in _fire_lines(caplog)[0]


def test_flags_default_on_and_parse(monkeypatch):
    for name, value, expect in (("WAKE_ENERGY_GATE", None, True), ("WAKE_ENERGY_GATE", "0", False),
                                ("WAKE_SILENT_DROP", None, True), ("WAKE_SILENT_DROP", "false", False)):
        monkeypatch.delenv("WAKE_ENERGY_GATE", raising=False)
        monkeypatch.delenv("WAKE_SILENT_DROP", raising=False)
        if value is not None:
            monkeypatch.setenv(name, value)
        assert getattr(_load("zoe_voice_daemon_wake_gate_flags"), name) is expect
    monkeypatch.setenv("WAKE_ENERGY_FLOOR", "nan")
    assert _load("zoe_voice_daemon_wake_gate_floor").WAKE_ENERGY_FLOOR == 550.0


# ── the call site ──────────────────────────────────────────────────────────

def _wake_branches(source: str) -> dict:
    """{'wake_word': [call names in order], 'orb_tap': [...]} for main()'s two on_wake() sites."""
    main = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == "main")
    out: dict = {}
    for node in ast.walk(main):
        if not isinstance(node, ast.If):
            continue
        test = ast.unparse(node.test)
        key = ("wake_word" if test == "wake_hits >= WAKE_CONFIRM_COUNT" else
               "orb_tap" if test == "_orb_activate_event.is_set()" else None)
        if key:
            out[key] = [c.func.id for c in ast.walk(node) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)]
    return out


def _gated(source: str) -> bool:
    calls = _wake_branches(source)["wake_word"]
    return "_wake_fire_allowed" in calls and calls.index("_wake_fire_allowed") < calls.index("on_wake")


def test_main_loop_gates_the_wake_word_and_not_the_orb_tap():
    source = DAEMON.read_text()
    branches = _wake_branches(source)
    assert _gated(source)
    assert "on_wake" in branches["orb_tap"] and "_wake_fire_allowed" not in branches["orb_tap"]
    # a blocked wake must not start the minimum interval between wakes
    block = source[source.index("if not _wake_fire_allowed(_PREROLL, mx, wake_score_max):"):]
    assert block.index("continue") < block.index("_last_wake_at = now")


def test_removing_the_gate_call_is_seen():
    source = DAEMON.read_text()
    call = ("                if not _wake_fire_allowed(_PREROLL, mx, wake_score_max):\n"
            "                    _keep_blocked_wake(_PREROLL)\n                    continue\n")
    assert source.count(call) == 1
    assert not _gated(source.replace(call, ""))


# ── silent drop ────────────────────────────────────────────────────────────

class _Oww:
    def reset(self):
        pass


def _run_wake_turn(daemon, monkeypatch, *, wake_stream, played=False):
    seen: dict = {}

    def _turn_with_monitor(pa, wav, turn_fn, **kw):
        seen.update(kw)
        return played, None
    monkeypatch.setattr(daemon, "record_command", lambda *a, **k: b"RIFF")
    monkeypatch.setattr(daemon, "_speculation_available", lambda: False)
    monkeypatch.setattr(daemon, "_turn_with_monitor", _turn_with_monitor)
    monkeypatch.setattr(daemon, "FOLLOW_UP_LISTEN_S", 0.0)
    monkeypatch.setattr(daemon, "POST_PLAY_TAIL_S", 0.0)
    daemon._last_turn_flags.clear()
    daemon.voice_command(object(), _Oww(), wake_stream=wake_stream)
    return seen


class _Stream:
    def stop_stream(self):
        pass

    def close(self):
        pass


def test_silent_drop_disarms_the_retry_chime_only_for_a_quiet_wake_word_turn(daemon, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(daemon, "_wake_window_quiet", True)
    assert _run_wake_turn(daemon, monkeypatch, wake_stream=_Stream())["prompt_on_empty"] is False
    assert any(r.getMessage().startswith("false_wake:") for r in caplog.records)

    caplog.clear()                                     # control: something was said in the wake window
    monkeypatch.setattr(daemon, "_wake_window_quiet", False)
    assert _run_wake_turn(daemon, monkeypatch, wake_stream=_Stream())["prompt_on_empty"] is True
    assert not any(r.getMessage().startswith("false_wake:") for r in caplog.records)

    monkeypatch.setattr(daemon, "_wake_window_quiet", True)
    # control: an orb tap (no wake stream) is deliberate and keeps its chime
    assert _run_wake_turn(daemon, monkeypatch, wake_stream=None)["prompt_on_empty"] is True
    # control: the flag
    monkeypatch.setattr(daemon, "WAKE_SILENT_DROP", False)
    assert _run_wake_turn(daemon, monkeypatch, wake_stream=_Stream())["prompt_on_empty"] is True


def test_a_quiet_wake_that_did_play_is_not_logged_as_false(daemon, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(daemon, "_wake_window_quiet", True)
    _run_wake_turn(daemon, monkeypatch, wake_stream=_Stream(), played=True)
    assert not any(r.getMessage().startswith("false_wake:") for r in caplog.records)


# ── a blocked wake keeps its window on the panel ───────────────────────────

def test_a_blocked_wake_keeps_its_window_bounded_and_private(daemon, monkeypatch, tmp_path):
    """The 1.6 s the blocked wake fired on is kept (mode 600, newest WAKE_GATE_KEEP only) so the floor can be
    re-checked. Controls: WAKE_GATE_KEEP=0 keeps nothing; an unwritable directory never raises. Negative control run:
    the pruning loop deleted from the daemon -> the count assertion failed."""
    import os
    import wave
    keep = tmp_path / "false-wakes"
    monkeypatch.setattr(daemon, "WAKE_GATE_KEEP_DIR", str(keep))
    monkeypatch.setattr(daemon, "WAKE_GATE_KEEP", 3)
    window = _chunks(_noise(30.0, seconds=2.4))                 # 30 chunks: only the last 20 are the scored window
    paths = [daemon._keep_blocked_wake(window) for _ in range(5)]
    assert all(paths)
    left = sorted(keep.iterdir())
    assert len(left) == 3
    with wave.open(str(left[-1]), "rb") as w:
        assert (w.getnchannels(), w.getframerate(), w.getnframes()) == (1, SR, daemon._WAKE_GATE_CHUNKS * CHUNK)
    assert os.stat(left[-1]).st_mode & 0o777 == 0o600
    monkeypatch.setattr(daemon, "WAKE_GATE_KEEP", 0)
    assert daemon._keep_blocked_wake(window) is None
    monkeypatch.setattr(daemon, "WAKE_GATE_KEEP", 3)
    (tmp_path / "file").write_text("x")
    monkeypatch.setattr(daemon, "WAKE_GATE_KEEP_DIR", str(tmp_path / "file" / "sub"))
    assert daemon._keep_blocked_wake(window) is None


def test_the_main_loop_keeps_only_blocked_wakes():
    """Negative control run: the _keep_blocked_wake call deleted from main() -> this and the call-site test failed."""
    source = DAEMON.read_text()
    block = source[source.index("if not _wake_fire_allowed(_PREROLL, mx, wake_score_max):"):]
    assert block.index("_keep_blocked_wake(_PREROLL)") < block.index("continue") < block.index("on_wake()")
