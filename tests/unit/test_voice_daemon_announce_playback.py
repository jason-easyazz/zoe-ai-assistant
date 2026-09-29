"""The panel daemon reports an announcement as PLAYED only when audio played.

`play_audio_b64` used to swallow every playback error and return None, so
`_speak_announcement` returned True for a TTS clip the player never produced —
and the played-ACK (`POST /api/voice/announcements/{id}/played`) told zoe-data
a brief nobody heard had been heard. Pinned here with the real daemon module
(pyaudio/requests stubbed, same loader as test_voice_daemon_dead_time.py) and a
fake player process.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe  # GitHub-CI opt-in: runs in validate.yml's `-m ci_safe` lane

DAEMON = Path(__file__).resolve().parents[2] / "scripts" / "setup" / "zoe_voice_daemon.py"
ANNOUNCE = DAEMON.with_name("zoe_voice_announce.py")


def _load(name: str, path: Path):
    stubs = {"pyaudio": types.ModuleType("pyaudio"), "requests": types.ModuleType("requests")}
    stubs["pyaudio"].paInt16 = 8
    stubs["pyaudio"].PyAudio = object
    saved = {n: sys.modules.get(n) for n in stubs}
    sys.modules.update(stubs)
    try:
        spec = importlib.util.spec_from_file_location(name, path)
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
    return _load("zoe_voice_daemon_announce_playback_test", DAEMON)


class _Proc:
    """A fake aplay: exits with ``code``; ``barge`` raises a barge-in mid-play."""

    def __init__(self, daemon, code=0, barge=False):
        self._daemon, self._code, self._barge = daemon, code, barge
        self.returncode = None
        self._polls = 0

    def poll(self):
        self._polls += 1
        if self._barge:
            self._daemon._barge_in_requested.set()
            return None
        if self._polls > 1:
            self.returncode = self._code
        return self.returncode

    def terminate(self):
        self.returncode = -15

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.returncode = -9


def _player(monkeypatch, daemon, **kw):
    def popen(cmd):
        if kw.get("missing"):
            raise FileNotFoundError("aplay")
        return _Proc(daemon, code=kw.get("code", 0), barge=kw.get("barge", False))

    monkeypatch.setattr(daemon.subprocess, "Popen", popen)
    monkeypatch.setattr(daemon.time, "sleep", lambda s: None)


@pytest.mark.parametrize("kw,played", [
    ({"code": 0}, True),         # played to the end
    ({"barge": True}, True),     # heard, and the listener talked over it
    ({"code": 1}, False),        # aplay exited non-zero (device busy / missing)
    ({"missing": True}, False),  # the player could not start
])
def test_play_audio_reports_whether_audio_played(monkeypatch, daemon, kw, played):
    _player(monkeypatch, daemon, **kw)
    assert daemon.play_audio_b64("AAAA", "audio/wav") is played


def test_nothing_to_play_is_not_played(daemon):
    assert daemon.play_audio_b64("", "audio/wav") is False


@pytest.mark.parametrize("code,expect", [(0, True), (1, False)])
def test_speak_announcement_returns_the_playback_result(monkeypatch, daemon, code, expect):
    """The poller ACKs only a True from here, so a failed player is never ACKed."""
    _player(monkeypatch, daemon, code=code)
    monkeypatch.setattr(daemon, "_api_post", lambda *a, **k: {"audio_base64": "AAAA"})
    assert daemon._speak_announcement({"id": "a1", "text": "Good morning"}) is expect


@pytest.mark.parametrize("resp,ok", [
    ({"ok": True, "updated": True}, True),
    ({"ok": True, "updated": False}, True),   # already ACKed / not ours: retrying cannot help
    ({"ok": False, "error": "HTTP 503"}, False),
])
def test_post_played_ack_success_is_the_servers_ok(monkeypatch, daemon, resp, ok):
    calls = []
    monkeypatch.setattr(daemon, "_api_post", lambda path, data, **k: calls.append((path, k)) or resp)
    assert daemon._post_played_ack("a1") is ok
    assert calls == [("/api/voice/announcements/a1/played", {"timeout": 5, "retries": 0})]


def test_ack_runs_off_the_announce_thread_with_bounded_retries(monkeypatch, daemon):
    announce = _load("zoe_voice_announce_ack_test", ANNOUNCE)
    started = {}

    class _Thread:
        def __init__(self, target, kwargs, daemon, name):
            started.update(target=target, kwargs=kwargs, daemon=daemon)

        def start(self):
            started["started"] = True

    monkeypatch.setattr(daemon, "_announce_logic", announce)
    monkeypatch.setattr(daemon.threading, "Thread", _Thread)
    daemon._ack_announcement({"id": "a1"})
    assert started["started"] and started["daemon"] is True
    assert started["target"] is announce.ack_with_retries
    assert started["kwargs"]["post"] is daemon._post_played_ack
    assert started["kwargs"]["ann_id"] == "a1"
