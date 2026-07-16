"""zoe_core_client.prewarm — spawn a session's brain worker ahead of the turn.

Called on wake-word so the first turn doesn't pay the Pi subprocess cold start.
Must be best-effort (never raise) and just start the SAME worker the turn will use.
"""
import asyncio

import pytest

import zoe_core_client as zc

pytestmark = pytest.mark.ci_safe


def _run(coro):
    return asyncio.run(coro)


class _FakeProc:
    returncode = None  # alive


class _FakeWorker:
    def __init__(self, *, fail=False):
        self._lock = asyncio.Lock()
        self.proc = None
        self.last_used = 0.0
        self.started = 0
        self._fail = fail

    async def _ensure_started(self):
        # Model the real guard: no-op when a live subprocess already exists.
        if self.proc is not None and self.proc.returncode is None:
            return
        self.started += 1
        if self._fail:
            raise RuntimeError("spawn failed")
        self.proc = _FakeProc()


def test_prewarm_starts_the_worker(monkeypatch):
    w = _FakeWorker()

    async def _fake_worker_for(user_id, session_id, *, voice_mode=False):
        assert (user_id, session_id) == ("jason", "panel-1")
        return w

    monkeypatch.setattr(zc, "_worker_for", _fake_worker_for)
    assert _run(zc.prewarm("jason", "panel-1")) is True
    assert w.started == 1 and w.proc is not None


def test_prewarm_voice_mode_warms_capped_worker(monkeypatch):
    # voice_mode must be forwarded so prewarm warms the SAME (voice-capped) worker
    # the voice turn will use — otherwise prewarm warms the chat worker and the
    # voice turn still pays the cold boot.
    w = _FakeWorker()
    seen = {}

    async def _fake_worker_for(user_id, session_id, *, voice_mode=False):
        seen["voice_mode"] = voice_mode
        return w

    monkeypatch.setattr(zc, "_worker_for", _fake_worker_for)
    assert _run(zc.prewarm("jason", "panel-1", voice_mode=True)) is True
    assert seen["voice_mode"] is True


def test_prewarm_idempotent_when_already_running(monkeypatch):
    # Worker already has a live subprocess → prewarm is a no-op (no second spawn).
    w = _FakeWorker()
    w.proc = _FakeProc()

    async def _fake_worker_for(user_id, session_id, *, voice_mode=False):
        return w
    monkeypatch.setattr(zc, "_worker_for", _fake_worker_for)
    assert _run(zc.prewarm("u", "s")) is True
    assert w.started == 0  # _ensure_started's guard skipped the spawn


def test_prewarm_swallows_errors(monkeypatch):
    async def _boom(user_id, session_id, *, voice_mode=False):
        raise RuntimeError("registry down")
    monkeypatch.setattr(zc, "_worker_for", _boom)
    assert _run(zc.prewarm("u", "s")) is False  # never raises


def test_prewarm_spawn_failure_returns_false(monkeypatch):
    w = _FakeWorker(fail=True)

    async def _fake_worker_for(user_id, session_id, *, voice_mode=False):
        return w
    monkeypatch.setattr(zc, "_worker_for", _fake_worker_for)
    assert _run(zc.prewarm("u", "s")) is False
    assert w.started == 1 and w.proc is None
