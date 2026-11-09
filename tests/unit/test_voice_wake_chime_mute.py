"""The first-turn cut: the wake chime muted the speakerphone's mic under the first words (2026-11-09).

The Jabra mutes its own mic (exact digital zeros, 0.25-0.8 s) whenever it plays anything. The chime lands ~0.5 s
after the wake word - when people start the command - so whatever was said under the mute never reached STT. Only the
first turn after a wake has a chime, so only first turns lost words. Pins, with neutral synthetic audio only:

  1. on_wake plays NO chime by default (WAKE_CHIME_ON_WAKE off) and leaves the post-wake gate open;
     NEGATIVE CONTROL: WAKE_CHIME_ON_WAKE=true (and WAKE_BEEP_ENABLED) is the old behaviour, chime and gate closed
     until it ends; WAKE_BEEP_ENABLED=false silences it even with the flag on.
  2. the OTHER beeps still obey WAKE_BEEP_ENABLED alone (the enrolment cue is not the wake chime).
  3. _muted_ms counts exact digital silence after the pre-roll; NEGATIVE CONTROLS: room noise and echo-suppressed
     near-zero audio count as 0, the pre-roll is skipped.
  4. the ``Recorded command`` line carries ``muted=<ms>`` so a live first turn can be checked for lost audio.
"""
from __future__ import annotations

import importlib.util
import logging
import sys
import threading
import types
from pathlib import Path

import numpy as np
import pytest

pytestmark = pytest.mark.ci_safe

DAEMON = Path(__file__).resolve().parents[2] / "scripts" / "setup" / "zoe_voice_daemon.py"
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
    return _load("zoe_voice_daemon_chime_mute_test")


def _chunk(amp: int) -> bytes:
    return np.full(1280, amp, dtype=np.int16).tobytes()


def _quiet_wake(daemon, monkeypatch):
    monkeypatch.setattr(daemon, "_wake_panel_agent", lambda: None)
    monkeypatch.setattr(daemon, "_notify_wake_background", lambda: None)
    played = threading.Event()
    monkeypatch.setattr(daemon, "play_wake_beep", played.set)
    daemon._wake_chime_done.set()
    return played


def test_default_wake_plays_no_chime_and_the_gate_stays_open(daemon, monkeypatch):
    played = _quiet_wake(daemon, monkeypatch)
    monkeypatch.setattr(daemon, "WAKE_BEEP_ENABLED", True)   # the Pi's value: other beeps stay on
    assert daemon.WAKE_CHIME_ON_WAKE is False                # the shipped default
    daemon.on_wake()
    assert not played.wait(timeout=0.3), "the wake chime played into the open mic"
    assert daemon._wake_chime_done.is_set(), "no chime is playing, so the post-wake gate must not wait for one"


def test_negative_control_flag_on_is_the_old_chime(daemon, monkeypatch):
    played = _quiet_wake(daemon, monkeypatch)
    monkeypatch.setattr(daemon, "WAKE_BEEP_ENABLED", True)
    monkeypatch.setattr(daemon, "WAKE_CHIME_ON_WAKE", True)
    daemon.on_wake()
    assert played.wait(timeout=2.0), "WAKE_CHIME_ON_WAKE=true must restore the chime (rollback)"


def test_beeps_disabled_wins_over_the_chime_flag(daemon, monkeypatch):
    played = _quiet_wake(daemon, monkeypatch)
    monkeypatch.setattr(daemon, "WAKE_BEEP_ENABLED", False)
    monkeypatch.setattr(daemon, "WAKE_CHIME_ON_WAKE", True)
    daemon.on_wake()
    assert not played.wait(timeout=0.3)
    assert daemon._wake_chime_done.is_set()


def test_other_beeps_still_obey_wake_beep_enabled_only(daemon, monkeypatch):
    """play_wake_beep itself (the enrolment cue calls it) is unchanged by the on-wake flag."""
    calls = []
    monkeypatch.setattr(daemon, "WAKE_BEEP_ENABLED", True)
    monkeypatch.setattr(daemon, "WAKE_CHIME_ON_WAKE", False)
    monkeypatch.setattr(daemon._PLATFORM, "play_file_blocking", lambda *a, **k: calls.append(a), raising=False)
    daemon.play_wake_beep()
    assert calls, "the enrolment/listen cue must still play with the on-wake chime off"


def test_muted_ms_counts_exact_digital_silence_after_the_preroll(daemon):
    pre = [_chunk(0)] * 3                                     # pre-roll zeros are not ours: skipped
    frames = pre + [_chunk(4000)] + [_chunk(0)] * 3 + [_chunk(900)]
    assert daemon._muted_ms(frames, len(pre)) == 3 * CHUNK_MS
    assert daemon._muted_ms(frames, 0) == 6 * CHUNK_MS


def test_negative_control_room_noise_and_echo_suppression_are_not_a_mute(daemon):
    noise = [_chunk(12)] * 5                                  # room noise ~8-18
    echo = [_chunk(3)] * 5                                    # echo suppression 0-5, never exact zero
    assert daemon._muted_ms(noise + echo) == 0
    assert daemon._muted_ms([]) == 0


class _Stream:
    def __init__(self, script):
        self.script = list(script)
        self.i = 0

    def read(self, size, exception_on_overflow=True):  # noqa: ARG002
        amp = self.script[self.i] if self.i < len(self.script) else 10
        self.i += 1
        return _chunk(amp)

    def stop_stream(self):
        pass

    def close(self):
        pass


class _PA:
    def get_sample_size(self, fmt):  # noqa: ARG002
        return 2


def test_recorded_command_line_reports_muted_ms(daemon, monkeypatch, caplog):
    monkeypatch.setattr(daemon, "VAD_ENDPOINT_ENABLED", False)   # amplitude endpointing: no model
    monkeypatch.setattr(daemon, "_PREROLL", [])
    monkeypatch.setattr(daemon, "_start_stt_stream", lambda frames: None)
    monkeypatch.setattr(daemon, "POST_WAKE_NO_SPEECH_S", 0.0)
    monkeypatch.setattr(daemon, "SILENCE_TIMEOUT_S", 0.4)
    script = [4000] * 3 + [0] * 4 + [4000] * 4               # speech, 4 muted chunks (320 ms), speech, then quiet
    with caplog.at_level(logging.INFO, logger=daemon.log.name):
        daemon.record_command(_PA(), stream=_Stream(script))
    line = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("Recorded command"))
    assert f"muted={4 * CHUNK_MS}ms" in line, line


def test_negative_control_clean_recording_reports_zero_muted(daemon, monkeypatch, caplog):
    monkeypatch.setattr(daemon, "VAD_ENDPOINT_ENABLED", False)
    monkeypatch.setattr(daemon, "_PREROLL", [])
    monkeypatch.setattr(daemon, "_start_stt_stream", lambda frames: None)
    monkeypatch.setattr(daemon, "POST_WAKE_NO_SPEECH_S", 0.0)
    monkeypatch.setattr(daemon, "SILENCE_TIMEOUT_S", 0.4)
    with caplog.at_level(logging.INFO, logger=daemon.log.name):
        daemon.record_command(_PA(), stream=_Stream([4000] * 8))
    line = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("Recorded command"))
    assert "muted=0ms" in line, line
