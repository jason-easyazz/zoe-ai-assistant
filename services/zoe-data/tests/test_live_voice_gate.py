"""``live_voice_gate``: the read-only "is live voice / the harness / the night unit using the box" answer. Synthetic locks and fakes only."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ci_safe

import fcntl
import subprocess
import sys
import threading
import types

import live_voice_gate as g


@pytest.fixture(autouse=True)
def clean(monkeypatch, tmp_path):
    g.reset()
    monkeypatch.setenv("ZOE_VOICE_HARNESS_LOCK", str(tmp_path / "harness.lock"))
    monkeypatch.delenv("ZOE_OMI_YIELD_TO_VOICE", raising=False)
    monkeypatch.setattr(g, "night_unit_active", lambda now_mono=None: False)       # never ask the real systemd
    monkeypatch.delitem(sys.modules, "routers.voice_tts", raising=False)
    monkeypatch.delitem(sys.modules, "voice_stt_stream", raising=False)
    yield
    g.reset()


def test_harness_flock_is_seen_while_held_and_released_after(tmp_path):
    path = tmp_path / "harness.lock"
    assert g.harness_lock_held() is False                                  # no file at all: not held
    path.write_text("")
    assert g.harness_lock_held() is False                                  # negative control: file exists, nobody holds it
    with open(path, "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        assert g.harness_lock_held() is True and g.busy_reason(100.0) == "harness"
    assert g.harness_lock_held() is False


def test_checking_the_harness_lock_never_takes_it(tmp_path):
    path = tmp_path / "harness.lock"
    path.write_text("")
    g.harness_lock_held()
    with open(path, "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)                     # would raise BlockingIOError if the check had left a lock


def test_a_held_moonshine_infer_lock_is_live_stt():
    lock = threading.Lock()
    sys.modules["routers.voice_tts"] = types.SimpleNamespace(_moonshine_infer_lock=lock)
    assert g.busy_reason(1.0) is None
    with lock:
        assert g.busy_reason(2.0) == "live_stt"


def test_an_open_stt_stream_is_live_stt():
    sys.modules["voice_stt_stream"] = types.SimpleNamespace(_sessions={}, _pending={})
    assert g.busy_reason(1.0) is None
    sys.modules["voice_stt_stream"]._sessions["abc"] = object()
    assert g.busy_reason(2.0) == "live_stt"


def test_cooloff_holds_for_n_seconds_after_the_last_busy_signal(monkeypatch):
    monkeypatch.setenv("ZOE_OMI_YIELD_COOLOFF_S", "15")
    lock = threading.Lock()
    sys.modules["routers.voice_tts"] = types.SimpleNamespace(_moonshine_infer_lock=lock)
    with lock:
        assert g.busy_reason(100.0) == "live_stt"
    assert g.busy_reason(110.0) == "cooloff"
    assert g.busy_reason(116.0) is None                                    # past the window: free again


def test_night_unit_is_cached_and_systemctl_trouble_reads_as_inactive(monkeypatch):
    monkeypatch.undo()                                                     # drop the autouse stub for this one
    g.reset()
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert g.night_unit_active(1000.0) is True and g.night_unit_active(1001.0) is True and len(calls) == 1   # cached
    g.reset()

    def boom(cmd, **kw):
        raise FileNotFoundError("systemctl")

    monkeypatch.setattr(subprocess, "run", boom)
    assert g.night_unit_active(2000.0) is False
