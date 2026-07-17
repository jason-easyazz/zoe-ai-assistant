"""GitHub-CI-safe lifecycle/robustness tests for the LiveKit voice agent.

These exercise the reliability hardening in ``routers.voice_livekit`` WITHOUT the
livekit-ffi / aiortc native stacks (which are Jetson-only): ``voice_livekit`` lazy-
imports ``livekit`` and ``livekit_aiortc`` *inside* functions, so we inject slim
fakes into ``sys.modules`` and drive ``_collect_audio_stream`` directly.

Covered:
  - native ``AudioStream`` constructor crash → agent flips to the aiortc backend
    (``_force_aiortc``) and returns instead of dying silently (P1 root cause);
  - the native ``AudioStream`` is ``aclose()``-d in a ``finally`` (P2 FFI leak), and
    a mid-stream backend error is surfaced at WARNING, not swallowed at DEBUG;
  - the aiortc stand-in (no ``aclose``) is handled without error and never trips the
    native→aiortc switch;
  - ``stop_livekit_ondemand`` cancels the tracked cooldown watchdog (P2 task leak).

No models, no network, no DB — safe on the slim GitHub runner.
"""
import asyncio
import logging
import sys
import types

import pytest

import routers.voice_livekit as v

pytestmark = pytest.mark.ci_safe


def _run(coro):
    """Drive a coroutine on a fresh loop — keeps each case isolated and avoids
    depending on a session-scoped pytest-asyncio loop (mirrors test_voice_smoke_ci)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _FakeFrame:
    def __init__(self, data: bytes):
        self.data = data


class _FakeFrameEvent:
    def __init__(self, data: bytes):
        self.frame = _FakeFrame(data)


def _silence(n: int = 320) -> bytes:
    """A below-VAD-threshold (zero-energy) PCM frame, so no pipeline is triggered."""
    return b"\x00" * n


def _install_fake_aiortc(monkeypatch):
    """Inject a minimal fake ``livekit_aiortc`` module."""
    mod = types.ModuleType("livekit_aiortc")

    class _RemoteAudioTrack:  # marker type for the isinstance() backend branch
        pass

    class _TrackKind:
        KIND_AUDIO = 1

    def make_audio_stream(track, **_kwargs):  # default: echo back the async-iterable
        return track

    mod._RemoteAudioTrack = _RemoteAudioTrack
    mod._TrackKind = _TrackKind
    mod.make_audio_stream = make_audio_stream
    monkeypatch.setitem(sys.modules, "livekit_aiortc", mod)
    return mod


def _install_fake_livekit(monkeypatch, audio_stream_factory):
    """Inject a fake ``livekit`` package whose ``rtc.AudioStream`` is ours."""
    rtc = types.ModuleType("livekit.rtc")
    rtc.AudioStream = audio_stream_factory
    pkg = types.ModuleType("livekit")
    pkg.rtc = rtc
    monkeypatch.setitem(sys.modules, "livekit", pkg)
    monkeypatch.setitem(sys.modules, "livekit.rtc", rtc)


def test_native_audiostream_ctor_failure_switches_to_aiortc(monkeypatch):
    monkeypatch.setattr(v, "_force_aiortc", False, raising=False)
    _install_fake_aiortc(monkeypatch)

    def boom(track, **_kwargs):
        # The exact symptom seen on the Jetson half-broken FFI backend.
        raise RuntimeError("'NoneType' object has no attribute 'add_done_callback'")

    _install_fake_livekit(monkeypatch, boom)

    # A plain object is NOT an aiortc track → native (livekit-ffi) construction path.
    _run(v._collect_audio_stream(object(), "sid-deadbeef", {}, object()))

    assert v._force_aiortc is True, "ctor crash must flip the agent to the aiortc backend"


def test_native_audiostream_aclosed_and_miduse_failure_switches(monkeypatch, caplog):
    monkeypatch.setattr(v, "_force_aiortc", False, raising=False)
    _install_fake_aiortc(monkeypatch)

    aclosed: list = []

    class _Stream:
        def __init__(self, track, **_kwargs):
            self._frames = [_FakeFrameEvent(_silence()), _FakeFrameEvent(_silence())]

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self._frames:
                return self._frames.pop(0)
            # A genuine backend error mid-stream (not a clean StopAsyncIteration).
            raise RuntimeError("backend exploded")

        async def aclose(self):
            aclosed.append(True)

    _install_fake_livekit(monkeypatch, _Stream)

    sid = "sid-stream01"
    ps = {sid: v._make_participant_state(sid)}
    with caplog.at_level(logging.WARNING, logger="routers.voice_livekit"):
        _run(v._collect_audio_stream(object(), sid, ps, object()))

    assert aclosed == [True], "native AudioStream must be aclose()'d in the finally"
    assert any("failed mid-use" in r.getMessage() for r in caplog.records), \
        "a native mid-use failure must be visible at WARNING"
    # A NATIVE stream that fails after construction also leaves the room deaf, so it
    # MUST force the one-way native→aiortc switch (not just log and stay connected).
    assert v._force_aiortc is True


def test_aiortc_miduse_failure_does_not_force_switch(monkeypatch, caplog):
    monkeypatch.setattr(v, "_force_aiortc", False, raising=False)
    mod = _install_fake_aiortc(monkeypatch)

    class _AiortcStream:  # the aiortc stand-in has NO aclose()
        def __init__(self):
            self._frames = [_FakeFrameEvent(_silence())]

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self._frames:
                return self._frames.pop(0)
            # Even an aiortc-side error must NOT force another fallback (no flap/loop).
            raise RuntimeError("aiortc hiccup")

    mod.make_audio_stream = lambda track, **_k: _AiortcStream()

    track = mod._RemoteAudioTrack()  # instance → aiortc branch (no native import)
    sid = "sid-aiortc1"
    ps = {sid: v._make_participant_state(sid)}
    with caplog.at_level(logging.WARNING, logger="routers.voice_livekit"):
        # Must not raise even though the stream has no aclose().
        _run(v._collect_audio_stream(track, sid, ps, object()))
    # Already on aiortc → the switch must stay put (one-way, idempotent).
    assert v._force_aiortc is False
    assert any("audio stream error" in r.getMessage() for r in caplog.records)


def test_stop_livekit_ondemand_cancels_cooldown_watchdog(monkeypatch):
    # Avoid touching real docker / a stale cross-loop lifecycle lock.
    async def _fake_docker(*_a, **_k):
        return (0, "")

    monkeypatch.setattr(v, "_docker_cmd", _fake_docker)
    monkeypatch.setattr(v, "_lifecycle_lock", None, raising=False)

    async def _body():
        async def _forever():
            while True:
                await asyncio.sleep(0.05)

        task = asyncio.ensure_future(_forever())
        v._cooldown_task = task
        await v.stop_livekit_ondemand(reason="test")
        await asyncio.gather(task, return_exceptions=True)
        return task

    task = _run(_body())
    assert task.cancelled(), "cooldown watchdog must be cancelled on stop"
    assert v._cooldown_task is None
