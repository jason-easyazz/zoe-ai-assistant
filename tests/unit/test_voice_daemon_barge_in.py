"""Panel barge-in guards (`scripts/setup/zoe_voice_daemon.py`, _BargeDetector).

Self-interruption 2026-09-28 (docs/knowledge/incident-runbook.md §12): three
consecutive panel replies were cut 0.4-0.8 s after playback began, and one
earlier turn at t+11ms, because the barge monitor (open since turn START)
counted audio that was not an interruption of Zoe. The fix anchors the decision
to playback start:

  1. anything captured before playback began is discarded (window cleared,
     mic backlog drained / stale queue items dropped);
  2. the first BARGE_GRACE_MS of playback is ignored (Zoe's own onset);
  3. sustained speech — BARGE_MIN_CHUNKS of BARGE_WINDOW_CHUNKS — or the fast
     path, BARGE_FAST_CHUNKS consecutive chunks >= BARGE_FAST_PROB.

A real interruption 1 s into the reply must still stop playback within ~300 ms.

The monitor tests drive the REAL `_BargeMonitor._run` synchronously against a
scripted fake mic stream and a fake clock, with `_vad_prob` replaced by a
decoder of the probability encoded in each chunk — no mic, no Silero, no audio.
"""

from __future__ import annotations

import importlib.util
import logging
import queue
import sys
import threading
import types
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

pytestmark = pytest.mark.ci_safe  # GitHub-CI opt-in: runs in validate.yml's `-m ci_safe` lane

_DAEMON_PATH = Path(__file__).resolve().parents[2] / "scripts" / "setup" / "zoe_voice_daemon.py"
_BARGE_ENV = ("BARGE_MIN_CHUNKS", "BARGE_WINDOW_CHUNKS", "BARGE_GRACE_MS",
              "BARGE_FAST_PROB", "BARGE_FAST_CHUNKS", "BARGE_IN_THRESHOLD",
              "CHUNK_SIZE", "SAMPLE_RATE", "BARGE_DUCK_ENABLED", "BARGE_DUCK_DB",
              "BARGE_DUCK_RAMP_MS", "BARGE_COMMIT_SPEECH_MS", "BARGE_RESUME_SILENCE_MS",
              "BARGE_DECIDE_MAX_MS", "BARGE_PLAYOUT_LATENCY_MS", "BARGE_SEED_NEXT_TURN",
              "FOLLOWUP_LOOKBACK_CHUNKS", "VAD_ENDPOINT_ENABLED")


def _load_daemon(name: str):
    stubs = {n: MagicMock() for n in ("pyaudio",) if n not in sys.modules}
    saved = {n: sys.modules.get(n) for n in stubs}
    sys.modules.update(stubs)
    try:
        spec = importlib.util.spec_from_file_location(name, _DAEMON_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        for n, prev in saved.items():
            if prev is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = prev


@pytest.fixture(scope="module")
def daemon():
    mp = pytest.MonkeyPatch()
    for var in _BARGE_ENV:  # the defaults under test, whatever the runner's env holds
        mp.delenv(var, raising=False)
    try:
        yield _load_daemon("zoe_voice_daemon_barge_under_test")
    finally:
        mp.undo()


# ── fakes ────────────────────────────────────────────────────────────────────

class _Clock:
    def __init__(self, t: float = 1000.0):
        self.t = t

    def monotonic(self) -> float:
        return self.t


class _Proc:
    """Stands in for the aplay subprocess."""

    pid = 4242

    def __init__(self):
        self.terminated = False

    def poll(self):
        return 0 if self.terminated else None

    def terminate(self):
        self.terminated = True


def _chunk(daemon, prob: float) -> bytes:
    return np.full(daemon.CHUNK_SIZE, int(round(prob * 10000)), dtype=np.int16).tobytes()


def _decode_prob(_model, arr) -> float:
    return float(arr[0]) / 10000.0


class _MicScript:
    """A mic stream whose blocking read() advances the fake clock by one chunk.

    ``live`` is the list of probabilities heard in real time. At live read
    index ``start_at`` playback begins (the player is registered at the clock
    time the read RETURNS, i.e. the chunk in hand was captured before it), and
    ``backlog`` chunks are left sitting in the device buffer — audio that was
    captured earlier but has not been read yet. Backlog reads return instantly.
    """

    def __init__(self, daemon, clock: _Clock, live: list[float], *,
                 start_at: int | None = None, backlog: list[float] | None = None,
                 keep_reading_after_kill: bool = False, end_at: int | None = None,
                 restart_at: int | None = None):
        self.d = daemon
        self.keep_reading_after_kill = keep_reading_after_kill
        self.end_at, self.restart_at = end_at, restart_at  # the player exits / a new one starts
        self.clock = clock
        self.live = list(live)
        self.start_at = start_at
        self.pending_backlog = list(backlog or [])
        self.buffer: list[float] = []
        self.i = 0
        self.proc: _Proc | None = None
        self.started_at: float | None = None
        self.backlog_reads = 0

    def read(self, n, exception_on_overflow=False):
        if self.buffer:
            # Buffered audio returns at once; only the reader's own work (VAD
            # inference, ~20 ms on the Pi) moves the clock between these reads.
            self.backlog_reads += 1
            self.clock.t += 0.02
            return _chunk(self.d, self.buffer.pop(0))
        if self.i >= len(self.live) or (self.proc is not None and self.proc.terminated
                                        and not self.keep_reading_after_kill):
            raise OSError("script exhausted")
        prob = self.live[self.i]
        self.clock.t += self.d._CHUNK_S
        if self.end_at is not None and self.i == self.end_at:
            self.proc.terminated = True
        if self.i in (self.start_at, self.restart_at):
            self.proc = _Proc()
            self.d._register_tts_process(self.proc)
            self.started_at = self.clock.t
            self.buffer = list(self.pending_backlog)
        self.i += 1
        return _chunk(self.d, prob)

    def get_read_available(self):
        return len(self.buffer) * self.d.CHUNK_SIZE

    def stop_stream(self):
        pass

    def close(self):
        pass


@pytest.fixture()
def rig(daemon, monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(daemon, "time", types.SimpleNamespace(monotonic=clock.monotonic))
    monkeypatch.setattr(daemon, "_vad_prob", _decode_prob)
    monkeypatch.setattr(daemon, "_get_silero_vad", lambda: (object(), None))
    monkeypatch.setattr(daemon, "_tts_process", None)
    monkeypatch.setattr(daemon, "_tts_started_at", None)
    monkeypatch.setattr(daemon, "_barge_in_requested", threading.Event())
    monkeypatch.setattr(daemon, "_shutdown", threading.Event())
    monkeypatch.setattr(daemon, "BARGE_IN_ENABLED", True)
    return clock


def _run_monitor(daemon, script: _MicScript) -> bool:
    pa = MagicMock()
    pa.open.return_value = script
    daemon._BargeMonitor(pa)._run()
    return daemon._barge_in_requested.is_set()


def _silence(n):
    return [0.01] * n


def _chunks_for(daemon, seconds: float) -> int:
    return int(round(seconds / daemon._CHUNK_S))


def _fire_line(caplog) -> str:
    lines = [r.getMessage() for r in caplog.records
             if r.getMessage().startswith("Barge-in detected during playback")]
    assert len(lines) == 1, lines
    return lines[0]


def _fire_offset_ms(line: str) -> int:
    return int(line.split("t+")[1].split("ms")[0])


# ── defaults + env ───────────────────────────────────────────────────────────

def test_defaults_require_sustained_speech_after_a_grace(daemon):
    assert (daemon.BARGE_MIN_CHUNKS, daemon.BARGE_WINDOW_CHUNKS) == (3, 6)
    assert daemon.BARGE_GRACE_MS == 800
    assert (daemon.BARGE_FAST_CHUNKS, daemon.BARGE_FAST_PROB) == (2, 0.95)
    det = daemon._BargeDetector()
    assert (det.min_chunks, det.window_chunks, det.fast_chunks) == (3, 6, 2)
    assert det.grace_s == pytest.approx(0.8)


def test_env_overrides_are_respected(monkeypatch):
    for k, v in {"BARGE_MIN_CHUNKS": "4", "BARGE_WINDOW_CHUNKS": "8", "BARGE_GRACE_MS": "250",
                 "BARGE_FAST_PROB": "0.97", "BARGE_FAST_CHUNKS": "0",
                 "BARGE_IN_THRESHOLD": "0.75"}.items():
        monkeypatch.setenv(k, v)
    mod = _load_daemon("zoe_voice_daemon_barge_env_under_test")
    assert (mod.BARGE_MIN_CHUNKS, mod.BARGE_WINDOW_CHUNKS, mod.BARGE_GRACE_MS) == (4, 8, 250)
    assert (mod.BARGE_FAST_PROB, mod.BARGE_FAST_CHUNKS) == (0.97, 0)
    det = mod._BargeDetector()
    assert (det.min_chunks, det.window_chunks, det.fast_chunks) == (4, 8, 0)
    assert det.grace_s == pytest.approx(0.25)
    assert det.threshold == 0.75


def test_malformed_env_keeps_defaults(monkeypatch):
    monkeypatch.setenv("BARGE_GRACE_MS", "soon")
    monkeypatch.setenv("BARGE_FAST_PROB", "high")
    mod = _load_daemon("zoe_voice_daemon_barge_badenv_under_test")
    assert mod.BARGE_GRACE_MS == 800 and mod.BARGE_FAST_PROB == 0.95


@pytest.mark.parametrize("bad", ["nan", "inf", "-inf", "1.5", "-0.1", "abc"])
def test_invalid_probability_env_keeps_the_default(monkeypatch, caplog, bad):
    """Greptile #1765: float("nan") parses, and every comparison against NaN is
    False — BARGE_FAST_PROB=nan (or 1.5) would silently switch the fast path off."""
    caplog.set_level(logging.WARNING)
    monkeypatch.setenv("BARGE_FAST_PROB", bad)
    monkeypatch.setenv("BARGE_IN_THRESHOLD", bad)
    mod = _load_daemon("zoe_voice_daemon_barge_badprob_under_test")
    assert mod.BARGE_FAST_PROB == 0.95 and mod.BARGE_IN_THRESHOLD == 0.5
    warned = " ".join(r.getMessage() for r in caplog.records)
    assert "BARGE_FAST_PROB" in warned and "BARGE_IN_THRESHOLD" in warned and bad in warned


@pytest.mark.parametrize("bad", ["nan", "inf", "abc"])
def test_float_env_rejects_non_finite(daemon, monkeypatch, bad):
    monkeypatch.setenv("ZOE_TEST_FLOAT_KNOB", bad)
    assert daemon._float_env("ZOE_TEST_FLOAT_KNOB", 0.4) == 0.4


def test_zero_threshold_is_rejected_but_zero_fast_prob_bounds_are_ok(monkeypatch):
    monkeypatch.setenv("BARGE_IN_THRESHOLD", "0")   # would call every chunk speech
    monkeypatch.setenv("BARGE_FAST_PROB", "1.0")    # edge of the range: allowed
    mod = _load_daemon("zoe_voice_daemon_barge_edges_under_test")
    assert mod.BARGE_IN_THRESHOLD == 0.5 and mod.BARGE_FAST_PROB == 1.0


@pytest.mark.parametrize("name,bad,default", [
    ("BARGE_MIN_CHUNKS", "0", 3), ("BARGE_MIN_CHUNKS", "-2", 3),
    ("BARGE_WINDOW_CHUNKS", "0", 6), ("BARGE_WINDOW_CHUNKS", "abc", 6),
    ("BARGE_GRACE_MS", "-100", 800), ("BARGE_GRACE_MS", "1.5", 800),
    ("BARGE_FAST_CHUNKS", "-1", 2),
])
def test_invalid_count_env_keeps_the_default(monkeypatch, caplog, name, bad, default):
    caplog.set_level(logging.WARNING)
    monkeypatch.setenv(name, bad)
    mod = _load_daemon("zoe_voice_daemon_barge_badcount_under_test")
    assert getattr(mod, name) == default
    assert any(name in r.getMessage() for r in caplog.records)


def test_zero_grace_and_zero_fast_chunks_are_valid_settings(monkeypatch):
    monkeypatch.setenv("BARGE_GRACE_MS", "0")
    monkeypatch.setenv("BARGE_FAST_CHUNKS", "0")
    mod = _load_daemon("zoe_voice_daemon_barge_zero_under_test")
    assert mod.BARGE_GRACE_MS == 0 and mod.BARGE_FAST_CHUNKS == 0


# ── the pure detector ────────────────────────────────────────────────────────

def test_detector_ignores_chunks_captured_before_playback(daemon):
    det = daemon._BargeDetector(grace_ms=0)
    det.new_playback(100.0)
    for k in range(10):
        assert not det.feed(0.99, 100.0 - 0.08 * (10 - k))
    assert det.hits() == 0


def test_detector_window_and_fast_path(daemon):
    det = daemon._BargeDetector(threshold=0.5, min_chunks=3, window_chunks=6, grace_ms=0,
                                fast_prob=0.95, fast_chunks=2)
    det.new_playback(0.0)
    assert not det.feed(0.8, 0.00) and not det.feed(0.8, 0.08)   # 2 of 6: not sustained
    assert det.feed(0.8, 0.16) and det.reason == "window"          # 3 of 6
    assert not det.feed(0.99, 0.24)                                # fires once per playback
    det.new_playback(5.0)
    assert not det.feed(0.99, 5.0) and det.feed(0.99, 5.08) and det.reason == "fast"
    det.new_playback(9.0)
    assert not det.feed(0.99, 9.0) and not det.feed(0.3, 9.08)    # a dip resets the fast run
    assert not det.feed(0.99, 9.16)


def test_fast_path_is_never_looser_than_the_threshold(daemon):
    det = daemon._BargeDetector(threshold=0.97, fast_prob=0.9)
    assert det.fast_prob == 0.97


def test_idle_clears_the_window(daemon):
    det = daemon._BargeDetector(min_chunks=3, window_chunks=6, grace_ms=0, fast_chunks=0)
    det.new_playback(0.0)
    det.feed(0.9, 0.0)
    det.feed(0.9, 0.08)
    det.idle()
    det.new_playback(1.0)
    assert not det.feed(0.9, 1.0)  # only 1 hit after the reset, not 3


# ── the real monitor loop ────────────────────────────────────────────────────

def test_stale_backlog_at_playback_start_does_not_trigger(daemon, rig, monkeypatch, caplog):
    """Speech sitting in the device buffer when playback begins is audio from
    BEFORE Zoe spoke. Isolated from the grace (grace=0) so this pins the drain."""
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(daemon, "BARGE_GRACE_MS", 0)
    script = _MicScript(daemon, rig, _silence(5) + _silence(30), start_at=5,
                        backlog=[0.99] * 8)
    assert not _run_monitor(daemon, script)
    assert script.backlog_reads == 8
    assert any("barge monitor: dropped 8 stale chunks (~640ms)" in r.getMessage()
               for r in caplog.records)


def test_user_still_talking_at_playback_start_does_not_trigger(daemon, rig):
    """Live 2026-09-28 18:24:50: speech right up to playback start fired at t+11ms."""
    script = _MicScript(daemon, rig, [0.99] * 20 + _silence(30), start_at=19)
    assert not _run_monitor(daemon, script)


def test_zoe_onset_inside_the_grace_does_not_trigger(daemon, rig):
    """Zoe's own onset leaking through the echo canceller (0.4-0.8 s in, live
    2026-09-28 20:44-20:45) at prob 0.99 for the first 720 ms of playback."""
    onset = _chunks_for(daemon, 0.72)
    script = _MicScript(daemon, rig, _silence(5) + [0.99] * onset + _silence(30), start_at=4)
    assert not _run_monitor(daemon, script)


@pytest.mark.parametrize("prob", [0.8, 0.99])
def test_real_interruption_one_second_in_stops_playback_within_300ms(daemon, rig, caplog, prob):
    caplog.set_level(logging.INFO)
    lead = _chunks_for(daemon, 1.0)
    script = _MicScript(daemon, rig, _silence(5) + _silence(lead) + [prob] * 20, start_at=4)
    assert _run_monitor(daemon, script)
    assert script.proc.terminated
    line = _fire_line(caplog)
    assert "(monitor, prob=" in line and "window=" in line
    assert f"th={daemon.BARGE_IN_THRESHOLD:.2f}" in line  # the EFFECTIVE threshold (env-overridden live)
    # Speech begins 1.0 s after playback start (+ the partial first chunk).
    assert 1000 < _fire_offset_ms(line) <= 1000 + 80 + 300


def test_sub_threshold_room_noise_never_triggers(daemon, rig):
    script = _MicScript(daemon, rig, _silence(5) + [0.3, 0.45] * 30, start_at=4)
    assert not _run_monitor(daemon, script)


def test_a_single_blip_after_the_grace_does_not_trigger(daemon, rig):
    lead = _chunks_for(daemon, 1.0)
    script = _MicScript(daemon, rig, _silence(5 + lead) + [0.9] + _silence(3) + [0.9]
                        + _silence(20), start_at=4)
    assert not _run_monitor(daemon, script)


# ── the queue-fed thread (playback outside a turn: announcements) ────────────

def test_queue_thread_drops_stale_items_and_honours_the_grace(daemon, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    q: queue.Queue = queue.Queue()
    monkeypatch.setattr(daemon, "_BARGE_QUEUE", q)
    monkeypatch.setattr(daemon, "_vad_prob", _decode_prob)
    monkeypatch.setattr(daemon, "_get_silero_vad", lambda: (object(), None))
    monkeypatch.setattr(daemon, "_barge_in_requested", threading.Event())
    shutdown = threading.Event()
    monkeypatch.setattr(daemon, "_shutdown", shutdown)
    proc = _Proc()
    start = 5000.0
    monkeypatch.setattr(daemon, "_tts_process", proc)
    monkeypatch.setattr(daemon, "_tts_started_at", start)
    step = daemon._CHUNK_S
    for k in range(10):  # captured before playback — the user's own words
        q.put((start - step * (10 - k), _chunk(daemon, 0.99)))
    for k in range(8):   # Zoe's onset inside the grace
        q.put((start + step * k, _chunk(daemon, 0.99)))
    t = threading.Thread(target=daemon._barge_in_vad_thread, daemon=True)
    t.start()
    try:
        assert not daemon._barge_in_requested.wait(0.5)
        assert any("barge queue: dropped 10 stale chunks" in r.getMessage() for r in caplog.records)
        for k in range(4):  # a real interruption after the grace
            q.put((start + 1.0 + step * k, _chunk(daemon, 0.8)))
        assert daemon._barge_in_requested.wait(2.0)
        assert proc.terminated
        assert "(queue, prob=0.80" in _fire_line(caplog)
    finally:
        shutdown.set()
        t.join(timeout=2)


# ── phase 1: duck → decide → resume (BARGE_DUCK_ENABLED, default off) ────────
# docs/research/barge-in-duck-decide-resume-2026-10-04.md §4.1 / §6.1-2.

_PACTL_LISTING = """Sink Input #12
\tSink: 1
\tVolume: front-left: 45000 / 69% / -9.72 dB,   front-right: 45000 / 69% / -9.72 dB
\tProperties:
\t\tapplication.name = "shairport-sync"
\t\tapplication.process.id = "777"
Sink Input #40
\tSink: 1
\tVolume: mono: 65536 / 100% / 0.00 dB
\tProperties:
\t\tapplication.name = "ALSA plug-in [aplay]"
\t\tapplication.process.id = "4242"
"""


class _Pactl:
    """pactl spy: answers `list sink-inputs` with a canned listing, records the rest."""

    def __init__(self, listing: str = _PACTL_LISTING):
        self.listing = listing
        self.calls: list[tuple[str, ...]] = []

    fail_sets = False  # pactl exits non-zero on every set-* call

    def __call__(self, *args: str):
        self.calls.append(args)
        if args[:2] == ("list", "sink-inputs"):
            return self.listing
        return None if self.fail_sets else ""

    def volume_calls(self):
        return [c for c in self.calls if c[0] != "list"]


class _Resp:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


@pytest.fixture()
def duck(daemon, rig, monkeypatch):
    """The rig with the duck ON: a pactl spy, an open turn response and a VAD
    endpointer (so the seed capture stops on the decoded probabilities)."""
    pactl = _Pactl()
    monkeypatch.setattr(daemon, "_pactl", pactl)
    monkeypatch.setattr(daemon, "BARGE_DUCK_ENABLED", True)
    monkeypatch.setattr(daemon, "VAD_ENDPOINT_ENABLED", True)
    monkeypatch.setattr(daemon, "_PLAYOUT", daemon._PlayoutLedger())
    monkeypatch.setattr(daemon, "_barge_stream_closed", threading.Event())
    resp = _Resp()
    daemon._set_turn_response(resp)
    yield types.SimpleNamespace(pactl=pactl, resp=resp, clock=rig)
    daemon._set_turn_response(None)


def _decide_lines(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith("BARGE_DECIDE ")]


def _decide_ms(line: str) -> int:
    return int(line.split(" ms=")[1].split()[0])


def _lead(daemon) -> list[float]:
    return _silence(5) + _silence(_chunks_for(daemon, 1.0))


def _run_duck(daemon, rig, live, **kw):
    pa = MagicMock()
    pa.get_sample_size.return_value = 2
    script = _MicScript(daemon, rig, live, start_at=4, keep_reading_after_kill=True, **kw)
    pa.open.return_value = script
    mon = daemon._BargeMonitor(pa)
    mon._run()
    return mon, script


def test_duck_flags_default_off_and_validated(daemon, monkeypatch, caplog):
    assert daemon.BARGE_DUCK_ENABLED is False and daemon.BARGE_SEED_NEXT_TURN is True
    assert (daemon.BARGE_DUCK_DB, daemon.BARGE_DUCK_RAMP_MS) == (-15.0, 0)
    assert (daemon.BARGE_COMMIT_SPEECH_MS, daemon.BARGE_RESUME_SILENCE_MS,
            daemon.BARGE_DECIDE_MAX_MS, daemon.BARGE_PLAYOUT_LATENCY_MS) == (900, 400, 2000, 100)
    caplog.set_level(logging.WARNING)
    for k, v in {"BARGE_DUCK_DB": "5", "BARGE_DUCK_RAMP_MS": "-1", "BARGE_COMMIT_SPEECH_MS": "0",
                 "BARGE_DECIDE_MAX_MS": "soon"}.items():
        monkeypatch.setenv(k, v)
    mod = _load_daemon("zoe_voice_daemon_duck_badenv_under_test")
    assert (mod.BARGE_DUCK_DB, mod.BARGE_DUCK_RAMP_MS, mod.BARGE_COMMIT_SPEECH_MS,
            mod.BARGE_DECIDE_MAX_MS) == (-15.0, 0, 900, 2000)
    assert "BARGE_DUCK_DB" in " ".join(r.getMessage() for r in caplog.records)


def test_flag_off_is_todays_hard_stop_and_never_touches_pactl(daemon, rig, monkeypatch, caplog):
    """Byte-identity lock: with the duck OFF a sustained interruption kills the
    player at the fire, nothing is ducked, nothing is decided. Negative control
    for the flag read: the same script with the flag ON must NOT terminate at the
    fire (next test) — remove the BARGE_DUCK_ENABLED read and one of them reddens."""
    caplog.set_level(logging.INFO)
    pactl = _Pactl()
    monkeypatch.setattr(daemon, "_pactl", pactl)
    script = _MicScript(daemon, rig, _lead(daemon) + [0.99] * 20, start_at=4)
    assert _run_monitor(daemon, script)
    assert script.proc.terminated and pactl.calls == [] and _decide_lines(caplog) == []
    assert "ducked" not in _fire_line(caplog)


@pytest.mark.parametrize("prob", [0.8, 0.99])
def test_sustained_speech_ducks_then_commits_within_1100ms_of_onset(daemon, duck, caplog, prob):
    caplog.set_level(logging.INFO)
    daemon._PLAYOUT.note(duck.clock.t + 0.2, 1.0)  # sentence 1: heard before the barge
    daemon._PLAYOUT.note(duck.clock.t + 0.3, 8.0)  # sentence 2: still playing at commit
    mon, script = _run_duck(daemon, duck.clock, _lead(daemon) + [prob] * 16 + _silence(15))
    assert "ducked -15dB, deciding" in _fire_line(caplog)
    assert script.proc.terminated and daemon._barge_in_requested.is_set() and duck.resp.closed
    assert daemon._barge_stream_closed.is_set()
    (line,) = _decide_lines(caplog)
    assert "outcome=commit" in line and "duck_db=-15.0" in line and "heard_chunks=1 heard_ms=1000" in line
    assert 900 <= _decide_ms(line) <= 1100
    # pactl: duck the aplay sink-input (#40, by pid 4242), restore its absolute
    # baseline; never the sink, never the AirPlay stream (#12).
    assert duck.pactl.volume_calls() == [("set-sink-input-volume", "40", "-15.0dB"),
                                         ("set-sink-input-volume", "40", "65536")]
    assert daemon._last_barge_commit["heard_chunks"] == 1


def test_short_burst_resumes_and_a_later_interruption_still_commits(daemon, duck, caplog):
    """A 240 ms burst: duck, 400 ms of quiet, RESUME — the player lives, the
    volume is back. The detector re-arms: sustained speech 1 s later commits."""
    caplog.set_level(logging.INFO)
    live = _lead(daemon) + [0.99] * 3 + _silence(12) + [0.99] * 16 + _silence(15)
    mon, script = _run_duck(daemon, duck.clock, live)
    lines = _decide_lines(caplog)
    assert [l.split()[1] for l in lines] == ["outcome=resume", "outcome=commit"]
    assert _decide_ms(lines[0]) == 240 + 400
    assert script.proc.terminated  # by the SECOND episode only
    assert [c[2] for c in duck.pactl.volume_calls()] == ["-15.0dB", "65536", "-15.0dB", "65536"]


def test_short_burst_alone_never_terminates_the_player(daemon, duck, caplog):
    caplog.set_level(logging.INFO)
    mon, script = _run_duck(daemon, duck.clock, _lead(daemon) + [0.99] * 3 + _silence(30))
    assert not script.proc.terminated and not daemon._barge_in_requested.is_set()
    assert not duck.resp.closed and mon.take_seed() is None
    assert [l.split()[1] for l in _decide_lines(caplog)] == ["outcome=resume"]


def test_unresolved_window_hits_the_ceiling_and_resumes(daemon, duck, caplog):
    """Speech too choppy to commit (<900 ms total) and never quiet long enough
    to resume (<400 ms runs): the 2 s ceiling resumes."""
    caplog.set_level(logging.INFO)
    live = _lead(daemon) + [0.9] * 3 + [0.1] * 4 + ([0.9] * 2 + [0.1] * 4) * 6 + _silence(20)
    mon, script = _run_duck(daemon, duck.clock, live)
    lines = _decide_lines(caplog)
    assert lines and lines[0].split()[1] == "outcome=ceiling", lines
    assert _decide_ms(lines[0]) == 2000
    assert not script.proc.terminated and duck.pactl.volume_calls()[1][2] == "65536"


def test_vad_failure_sentinel_never_commits(daemon, duck, caplog):
    """A broken Silero (-1.0 sentinel) after the fire is neither speech nor
    quiet: the window can only end at the ceiling, never in a kill. Negative
    control: the same chunks at 0.99 commit."""
    caplog.set_level(logging.INFO)
    mon, script = _run_duck(daemon, duck.clock, _lead(daemon) + [0.99] * 2 + [-1.0] * 40)
    assert not script.proc.terminated
    assert [l.split()[1] for l in _decide_lines(caplog)] == ["outcome=ceiling"]
    caplog.clear()
    daemon._barge_in_requested.clear()
    mon, script = _run_duck(daemon, duck.clock, _lead(daemon) + [0.99] * 2 + [0.99] * 40)
    assert script.proc.terminated and _decide_lines(caplog)[0].split()[1] == "outcome=commit"


def test_no_sink_input_for_the_player_falls_back_to_the_hard_stop(daemon, duck, caplog):
    caplog.set_level(logging.INFO)
    duck.pactl.listing = _PACTL_LISTING.replace('"4242"', '"9999"')
    mon, script = _run_duck(daemon, duck.clock, _lead(daemon) + [0.99] * 20)
    assert script.proc.terminated and duck.pactl.volume_calls() == []
    assert _decide_lines(caplog) == [] and "ducked" not in _fire_line(caplog)
    assert any("duck unavailable" in r.getMessage() for r in caplog.records)


def test_commit_seeds_the_next_turn_from_the_lookback_to_the_endpoint(daemon, duck, caplog):
    """The seed is the 4-chunk lookback before onset, the decide window, then the
    rest of the utterance to the normal endpoint — handed over once."""
    caplog.set_level(logging.INFO)
    speech = [0.99] * 16 + [0.6] * 5
    mon, script = _run_duck(daemon, duck.clock, _lead(daemon) + speech + _silence(40))
    seed = mon.take_seed()
    assert seed is not None and mon.take_seed() is None
    probs = list(np.frombuffer(seed[44:], dtype=np.int16)[::daemon.CHUNK_SIZE] / 10000.0)
    # The 4-chunk ring holds the fire chunk + the one before (both speech on the
    # fast path) and the 2 room chunks before onset — the same ring shape as
    # _follow_up_listen, so the first syllable is never clipped.
    assert probs[:2] == [0.01] * 2, "lookback before onset"
    assert probs[2:2 + len(speech)] == pytest.approx(speech), "the whole interrupting utterance"
    assert probs[2 + len(speech):] == [0.01] * 10, "then the VAD endpoint (0.8 s)"
    assert any("Barge-in seed:" in r.getMessage() for r in caplog.records)


def test_seed_frames_are_bounded_by_the_decide_ceiling(daemon, duck, monkeypatch):
    """The lookback ring is FOLLOWUP_LOOKBACK_CHUNKS long and the episode frames
    stop growing at lookback + ceiling/chunk + 1, whatever the window does."""
    captured, fed = {}, []
    monkeypatch.setattr(daemon._BargeMonitor, "_capture_seed",
                        lambda self, stream, frames: captured.setdefault("n", len(frames)))
    # A decider that ignores its ceiling: 400 undecided chunks, then a commit.
    monkeypatch.setattr(daemon._BargeDecider, "feed",
                        lambda self, prob, at: fed.append(at) or ("commit" if len(fed) == 400 else ""))
    mon, script = _run_duck(daemon, duck.clock, _silence(80) + [0.99] * 450)
    assert captured["n"] == 4 + int(np.ceil(2.0 / daemon._CHUNK_S)) + 1 == 30 < 400


def test_player_exiting_mid_decide_heals_the_persisted_duck_on_the_next_player(
        daemon, duck, caplog, monkeypatch):
    """The reply ends while deciding: outcome=ended, no kill, the user still
    talking becomes the seed. The player exited ducked, so PulseAudio's
    stream-restore holds -15 dB for aplay: the next player is healed."""
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(daemon, "_duck_leak", None)
    lead = _lead(daemon)
    live = lead + [0.99] * 6 + _silence(30)
    mon, script = _run_duck(daemon, duck.clock, live, end_at=len(lead) + 4)
    lines = _decide_lines(caplog)
    assert [l.split()[1] for l in lines] == ["outcome=ended"] and not daemon._barge_in_requested.is_set()
    assert mon.take_seed() is not None, "still talking at reply end: seeded, no beep"
    assert daemon._duck_leak == 65536
    # heal: the next player's sink-input (same pid in this rig) gets the baseline back
    mon, script = _run_duck(daemon, duck.clock, _silence(5) + _silence(20))
    assert daemon._duck_leak is None
    assert [c[2] for c in duck.pactl.volume_calls()] == ["-15.0dB", "65536"]
    assert any("healed leaked stream volume" in r.getMessage() for r in caplog.records)
    # A failed restore (pactl error) is a recorded leak too; the heal is tried
    # once per player and keeps the leak while pactl keeps failing.
    duck.pactl.fail_sets = True
    monkeypatch.setattr(daemon, "_duck_heal_tried", None)
    mon, script = _run_duck(daemon, duck.clock, _lead(daemon) + [0.99] * 3 + _silence(30))
    assert daemon._duck_leak == 65536 and "outcome=resume" in _decide_lines(caplog)[-1]
    assert sum(1 for c in duck.pactl.volume_calls() if c[2] == "65536") == 3  # restore, heal x2: one per player


def test_seed_off_keeps_the_commit_but_hands_nothing_over(daemon, duck, monkeypatch):
    monkeypatch.setattr(daemon, "BARGE_SEED_NEXT_TURN", False)
    mon, script = _run_duck(daemon, duck.clock, _lead(daemon) + [0.99] * 16 + _silence(30))
    assert script.proc.terminated and mon.take_seed() is None


def test_follow_up_source_prefers_the_seed_and_skips_the_beep(daemon, monkeypatch):
    calls = []
    monkeypatch.setattr(daemon, "play_follow_up_beep", lambda: calls.append("beep"))
    monkeypatch.setattr(daemon, "_follow_up_listen",
                        lambda pa, window_s=None: calls.append("listen") or b"L")
    monkeypatch.setattr(daemon, "_notify_wake_background", lambda: None)
    assert daemon._follow_up_source(None, b"SEED") == b"SEED" and calls == []
    assert daemon._follow_up_source(None, None) == b"L" and calls == ["beep", "listen"]


def test_decider_and_ledger_are_pure(daemon):
    d = daemon._BargeDecider(threshold=0.5, commit_ms=900, resume_ms=400, max_ms=2000)
    d.start(0.0, speech_ms=160)
    t, out = 0.16, ""
    while not out:
        out = d.feed(0.9, t)
        t += daemon._CHUNK_S
    assert out == "commit" and d.speech_ms >= 900 and d.feed(0.9, t) == ""  # decided once
    d.start(0.0)
    outs = [d.feed(p, 0.08 * k) for k, p in enumerate([0.9, 0.1, -1.0, 0.1, 0.1, 0.1, 0.1])]
    assert outs[-1] == "resume" and d.quiet_ms == 400  # the sentinel added to neither side
    led = daemon._PlayoutLedger()
    led.note(10.0, 1.0)   # plays 10.0-11.0
    led.note(10.2, 2.0)   # queued behind it: plays 11.0-13.0
    assert led.heard(11.05, 0.1) == (0, 0) and led.heard(11.1, 0.1) == (1, 1000)
    assert led.heard(13.1, 0.1) == (2, 3000)


def test_ducker_ramp_steps_and_relative_restore_without_a_baseline(daemon, monkeypatch):
    pactl = _Pactl(_PACTL_LISTING.replace("Volume: mono: 65536 / 100% / 0.00 dB", "Mute: no"))
    monkeypatch.setattr(daemon, "_pactl", pactl)
    monkeypatch.setattr(daemon, "time",
                        types.SimpleNamespace(monotonic=lambda: 0.0, sleep=lambda s: None))
    d = daemon._SinkInputDucker(4242, db=-15.0, ramp_ms=450)
    assert d.duck() and d.baseline is None
    for th in threading.enumerate():
        if th.name == "barge-duck-ramp":
            th.join(2)
    d.restore()
    assert [c[2] for c in pactl.volume_calls()] == ["-5.0dB", "-5.0dB", "-5.0dB", "+15.0dB"]
