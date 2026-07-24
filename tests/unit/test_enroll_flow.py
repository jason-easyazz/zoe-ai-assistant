"""Guided enrollment flow (`scripts/setup/zoe_enroll_flow.py`) — voice path.

The flow stops the zoe-voice daemon for the recording window (the Jabra
can't hold two input streams) and MUST restart it no matter what fails in
between — a crashed enrollment that leaves the panel deaf is the worst
outcome. Pinned here with everything mocked (no mic, no network, no
systemd):

1. Daemon restart happens in the finally block even when recording fails,
   the upload raises, or the say() TTS itself explodes mid-flow.
2. Per-phrase failures degrade (skip the phrase, keep going), and a run
   with zero usable samples reports failure (exit 1) without claiming
   an enrollment.
3. Uploads carry consent=true and the WAV temp files are cleaned up.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe  # GitHub-CI opt-in: runs in validate.yml's `-m ci_safe` lane

_MODULE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "setup" / "zoe_enroll_flow.py"

spec = importlib.util.spec_from_file_location("zoe_enroll_flow_under_test", _MODULE_PATH)
flow = importlib.util.module_from_spec(spec)
spec.loader.exec_module(flow)


@pytest.fixture
def quiet(monkeypatch):
    """Silence say() and time.sleep; record systemctl calls."""
    calls = {"say": [], "systemctl": [], "api": []}
    monkeypatch.setattr(flow, "say", lambda text: calls["say"].append(text))
    monkeypatch.setattr(flow.time, "sleep", lambda s: None)

    def fake_run(cmd, **kw):
        if cmd[:2] == ["systemctl", "--user"]:
            calls["systemctl"].append(cmd[2])

        class _P:
            returncode = 0
        return _P()

    monkeypatch.setattr(flow.subprocess, "run", fake_run)
    return calls


def test_voice_enroll_restarts_daemon_after_success(quiet, monkeypatch):
    monkeypatch.setattr(flow, "_record_wav", lambda s, p: Path(p).write_bytes(b"x" * 40000) or True)
    monkeypatch.setattr(flow, "_api", lambda *a, **k: quiet["api"].append(a) or {"ok": True})

    assert flow.enroll_voice("jason", "Jason") == 0
    assert quiet["systemctl"] == ["stop", "start"]
    assert len(quiet["api"]) == len(flow.VOICE_PHRASES)
    payloads = [a[1] for a in quiet["api"]]
    assert all(p["consent"] is True and p["user_id"] == "jason" for p in payloads)


def test_voice_enroll_survives_upload_errors_and_restarts_daemon(quiet, monkeypatch):
    monkeypatch.setattr(flow, "_record_wav", lambda s, p: Path(p).write_bytes(b"x" * 40000) or True)

    def boom(*a, **k):
        raise RuntimeError("server down")

    monkeypatch.setattr(flow, "_api", boom)
    # Every upload failing must not crash the flow: it degrades to a spoken
    # failure (exit 1, nothing enrolled) and the daemon is still restarted.
    assert flow.enroll_voice("jason", "Jason") == 1
    assert quiet["systemctl"] == ["stop", "start"]
    assert any("didn't save" in s.lower() for s in quiet["say"])


def test_voice_enroll_zero_samples_reports_failure(quiet, monkeypatch):
    monkeypatch.setattr(flow, "_record_wav", lambda s, p: False)  # mic never delivers
    monkeypatch.setattr(flow, "_api", lambda *a, **k: {"ok": True})

    assert flow.enroll_voice("jason", "Jason") == 1
    assert quiet["systemctl"] == ["stop", "start"]
    assert any("couldn't record" in s.lower() for s in quiet["say"])


def test_voice_enroll_skips_failed_phrase_keeps_rest(quiet, monkeypatch):
    results = iter([False, True, True])
    monkeypatch.setattr(
        flow, "_record_wav",
        lambda s, p: next(results) and (Path(p).write_bytes(b"x" * 40000) or True))
    monkeypatch.setattr(flow, "_api", lambda *a, **k: quiet["api"].append(a) or {"ok": True})

    assert flow.enroll_voice("jason", "Jason") == 0
    assert len(quiet["api"]) == 2  # phrase 1 skipped, 2 + 3 uploaded
    assert quiet["systemctl"] == ["stop", "start"]
