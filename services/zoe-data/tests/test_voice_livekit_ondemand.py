"""Tests for the on-demand LiveKit lifecycle (start-on-token, idle-reap)."""
import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from routers import voice_livekit

pytestmark = pytest.mark.ci_safe


@pytest.fixture(autouse=True)
def _reset_lifecycle(monkeypatch):
    # Always have credentials so the start path isn't short-circuited.
    monkeypatch.setenv("LIVEKIT_API_KEY", "test-key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "test-secret")
    # monkeypatch.setattr restores the original bindings on teardown even if a
    # test raises before the cleanup below — no stale global leaks into the next
    # test. Use a fresh set so in-test mutations are discarded with it.
    monkeypatch.setattr(voice_livekit, "_agent_task", None)
    monkeypatch.setattr(voice_livekit, "_idle_task", None)
    monkeypatch.setattr(voice_livekit, "_lifecycle_lock", None)
    monkeypatch.setattr(voice_livekit, "_active_participant_sids", set())
    monkeypatch.setattr(voice_livekit, "_last_activity", 0.0)
    voice_livekit.reset_voice_health_for_tests()
    yield
    # Tasks created during the test must still be cancelled — monkeypatch
    # restores the binding, not any task object the test spawned into it.
    for handle in (voice_livekit._agent_task, voice_livekit._idle_task):
        if handle is not None and not handle.done():
            handle.cancel()
    voice_livekit.reset_voice_health_for_tests()


async def _never_ending():
    # Stand-in for _agent_loop: runs until cancelled.
    while True:
        await asyncio.sleep(3600)


@pytest.mark.asyncio
async def test_ensure_started_starts_container_and_agent(monkeypatch):
    monkeypatch.setenv("ZOE_LIVEKIT_ONDEMAND", "true")
    calls = []

    async def _fake_running():
        return False

    async def _fake_docker(*args, timeout=20.0):
        calls.append(args)
        return 0, ""

    async def _fake_wait_port(host, port, timeout):
        return True

    monkeypatch.setattr(voice_livekit, "_container_running", _fake_running)
    monkeypatch.setattr(voice_livekit, "_docker_cmd", _fake_docker)
    monkeypatch.setattr(voice_livekit, "_wait_port", _fake_wait_port)
    monkeypatch.setattr(voice_livekit, "_agent_loop", _never_ending)
    monkeypatch.setattr(voice_livekit, "_idle_monitor", _never_ending)

    ready = await voice_livekit.ensure_livekit_started(wait_ready=1.0)

    assert ready is True
    assert ("start", voice_livekit._CONTAINER_NAME) in calls
    assert voice_livekit._agent_task is not None and not voice_livekit._agent_task.done()
    assert voice_livekit._idle_task is not None and not voice_livekit._idle_task.done()


@pytest.mark.asyncio
async def test_ensure_started_skips_docker_when_disabled(monkeypatch):
    monkeypatch.setenv("ZOE_LIVEKIT_ONDEMAND", "false")
    started = []

    async def _fake_docker(*args, timeout=20.0):
        started.append(args)
        return 0, ""

    async def _fake_wait_port(host, port, timeout):
        return True

    monkeypatch.setattr(voice_livekit, "_docker_cmd", _fake_docker)
    monkeypatch.setattr(voice_livekit, "_wait_port", _fake_wait_port)

    ready = await voice_livekit.ensure_livekit_started(wait_ready=1.0)

    assert ready is True
    assert started == []  # boot mode owns the always-on agent
    assert voice_livekit._agent_task is None


@pytest.mark.asyncio
async def test_ensure_started_returns_false_when_docker_start_fails(monkeypatch):
    monkeypatch.setenv("ZOE_LIVEKIT_ONDEMAND", "true")

    async def _fake_running():
        return False

    async def _fake_docker(*args, timeout=20.0):
        return 1, "no such container"

    monkeypatch.setattr(voice_livekit, "_container_running", _fake_running)
    monkeypatch.setattr(voice_livekit, "_docker_cmd", _fake_docker)

    ready = await voice_livekit.ensure_livekit_started(wait_ready=1.0)

    assert ready is False
    assert voice_livekit._agent_task is None


@pytest.mark.asyncio
async def test_reap_if_idle_stops_when_idle(monkeypatch):
    monkeypatch.setenv("ZOE_LIVEKIT_ONDEMAND", "true")
    monkeypatch.setenv("ZOE_LIVEKIT_IDLE_TIMEOUT_S", "0")
    stopped = []

    async def _fake_running():
        return True

    async def _fake_stop(reason="idle"):
        stopped.append(reason)

    monkeypatch.setattr(voice_livekit, "_container_running", _fake_running)
    monkeypatch.setattr(voice_livekit, "stop_livekit_ondemand", _fake_stop)

    monkeypatch.setattr(voice_livekit, "_last_activity", 0.0)  # long ago

    should_exit = await voice_livekit._reap_if_idle()

    assert should_exit is True
    assert stopped == ["idle"]


@pytest.mark.asyncio
async def test_reap_if_idle_rechecks_after_container_await(monkeypatch):
    """TOCTOU guard: if a /livekit-token request connects a participant (or bumps
    activity) while _container_running() yields, _reap_if_idle must NOT reap the
    now-active room."""
    monkeypatch.setenv("ZOE_LIVEKIT_ONDEMAND", "true")
    monkeypatch.setenv("ZOE_LIVEKIT_IDLE_TIMEOUT_S", "0")
    stopped = []

    async def _fake_stop(reason="idle"):
        stopped.append(reason)

    async def _running_but_late_join():
        # Simulate a participant arriving during the await window.
        voice_livekit._active_participant_sids.add("late-joiner")
        return True

    monkeypatch.setattr(voice_livekit, "stop_livekit_ondemand", _fake_stop)
    monkeypatch.setattr(voice_livekit, "_container_running", _running_but_late_join)
    monkeypatch.setattr(voice_livekit, "_last_activity", 0.0)

    should_exit = await voice_livekit._reap_if_idle()

    assert stopped == []  # must not reap once a participant appeared mid-check
    assert should_exit is False


@pytest.mark.asyncio
async def test_reap_if_idle_holds_open_with_participants(monkeypatch):
    monkeypatch.setenv("ZOE_LIVEKIT_ONDEMAND", "true")
    monkeypatch.setenv("ZOE_LIVEKIT_IDLE_TIMEOUT_S", "0")
    stopped = []

    async def _fake_stop(reason="idle"):
        stopped.append(reason)

    monkeypatch.setattr(voice_livekit, "stop_livekit_ondemand", _fake_stop)

    monkeypatch.setattr(voice_livekit, "_last_activity", 0.0)
    monkeypatch.setattr(voice_livekit, "_active_participant_sids", {"participant-1"})

    should_exit = await voice_livekit._reap_if_idle()

    assert should_exit is False  # keep looping
    assert stopped == []  # never stopped while a participant is present


@pytest.mark.asyncio
async def test_reap_if_idle_waits_out_recent_activity(monkeypatch):
    monkeypatch.setenv("ZOE_LIVEKIT_ONDEMAND", "true")
    monkeypatch.setenv("ZOE_LIVEKIT_IDLE_TIMEOUT_S", "300")
    stopped = []

    async def _fake_stop(reason="idle"):
        stopped.append(reason)

    monkeypatch.setattr(voice_livekit, "stop_livekit_ondemand", _fake_stop)

    voice_livekit.note_voice_activity()  # just now
    voice_livekit._active_participant_sids.clear()

    should_exit = await voice_livekit._reap_if_idle()

    assert should_exit is False
    assert stopped == []


def test_agent_identity_does_not_hold_reaper_open():
    """The agent's own join must not count as an active participant, else the
    container never reaps (the aiortc backend emits participant_connected for
    every participant already in the room when the agent joins)."""

    class _Room:
        def __init__(self):
            self.handlers = {}

        def on(self, event):
            def _decorator(func):
                self.handlers[event] = func
                return func
            return _decorator

    class _P:
        def __init__(self, identity, sid):
            self.identity = identity
            self.sid = sid

    room = _Room()
    voice_livekit._build_room_handlers(room, {}, {})

    # Agent's own identity → ignored.
    room.handlers["participant_connected"](_P(voice_livekit._AGENT_IDENTITY, "PA_agent"))
    assert "PA_agent" not in voice_livekit._active_participant_sids

    # A real browser participant → tracked.
    room.handlers["participant_connected"](_P("browser-1", "PA_browser"))
    assert "PA_browser" in voice_livekit._active_participant_sids


@pytest.mark.asyncio
async def test_direct_stop_tears_down_idle_monitor(monkeypatch):
    """Calling stop_livekit_ondemand directly (not from the monitor itself) must
    cancel + clear _idle_task, so the next ensure_livekit_started() recreates it
    rather than skipping it via the done()-guard."""
    async def _fake_docker(*args, timeout=20.0):
        return 0, ""

    monkeypatch.setattr(voice_livekit, "_docker_cmd", _fake_docker)
    idle = asyncio.create_task(_never_ending())
    monkeypatch.setattr(voice_livekit, "_idle_task", idle)

    await voice_livekit.stop_livekit_ondemand(reason="manual")

    assert voice_livekit._idle_task is None
    await asyncio.sleep(0)
    assert idle.cancelled() or idle.done()


@pytest.mark.asyncio
async def test_start_agent_ondemand_does_not_run_loop(monkeypatch):
    monkeypatch.setenv("ZOE_LIVEKIT_ONDEMAND", "true")
    ran = []

    async def _loop():
        ran.append(True)

    monkeypatch.setattr(voice_livekit, "_agent_loop", _loop)
    monkeypatch.setattr(voice_livekit, "_idle_monitor", _never_ending)

    await voice_livekit.start_livekit_agent()

    assert ran == []  # boot must not start the agent loop in on-demand mode
    assert voice_livekit.get_voice_health()["status"] == "stopped"
    # but the idle reaper IS started so an orphaned container still gets reaped
    assert voice_livekit._idle_task is not None and not voice_livekit._idle_task.done()
