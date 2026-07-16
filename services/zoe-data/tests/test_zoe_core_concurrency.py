"""Concurrency-hardening regression tests for the Pi-RPC brain client.

Under load (more distinct sessions than workers, single llama-server slot) brain
turns used to come back empty. These tests pin the two guards:
  - a global semaphore bounds how many turns run at once (ZOE_CORE_MAX_CONCURRENCY)
  - run_zoe_core retries once on a transient empty/failed turn
without touching real subprocesses or the GPU.
"""
import asyncio

import pytest

import zoe_core_client as zc

pytestmark = pytest.mark.ci_safe


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _reset_brain_sem():
    """Drop the lazily-created semaphore before/after each test so it never leaks
    across tests bound to a now-closed event loop (each test uses asyncio.run)."""
    zc._BRAIN_SEM = None
    yield
    zc._BRAIN_SEM = None


class _FakeWorker:
    """Stand-in for _ZoeCoreWorker. `script` drives each stream() call:
    a string -> yields it; "empty" -> yields nothing; "raise" -> raises."""

    def __init__(self, script, *, on_stream=None):
        self._script = list(script)
        self.calls = 0
        self.resets = 0
        self._lock = asyncio.Lock()
        self._on_stream = on_stream

    async def stream(self, composed, *, timeout_s):
        behavior = self._script[min(self.calls, len(self._script) - 1)]
        self.calls += 1
        if self._on_stream is not None:
            await self._on_stream()
        if behavior == "raise":
            raise RuntimeError("boom")
        if behavior == "empty":
            return
            yield  # pragma: no cover - makes this an async generator
        else:
            yield behavior

    async def reset(self):
        self.resets += 1


def _patch_single_worker(monkeypatch, worker):
    async def _fake_worker_for(user_id, session_id, *, voice_mode=False):
        return worker
    monkeypatch.setattr(zc, "_worker_for", _fake_worker_for)

    async def _fake_reset(user_id, session_id, *, voice_mode=False):
        await worker.reset()
    monkeypatch.setattr(zc, "_reset_worker_for", _fake_reset)


def test_retry_once_on_empty_then_succeeds(monkeypatch):
    w = _FakeWorker(["empty", "Bread added."])
    _patch_single_worker(monkeypatch, w)
    out = _run(zc.run_zoe_core("add bread", "s1", "u1"))
    assert out == "Bread added."
    assert w.calls == 2 and w.resets == 1  # retried once after the empty turn


def test_retry_once_on_exception_then_succeeds(monkeypatch):
    w = _FakeWorker(["raise", "Recovered."])
    _patch_single_worker(monkeypatch, w)
    out = _run(zc.run_zoe_core("hi", "s1", "u1"))
    assert out == "Recovered." and w.calls == 2 and w.resets == 1


def test_both_empty_returns_blank(monkeypatch):
    w = _FakeWorker(["empty", "empty"])
    _patch_single_worker(monkeypatch, w)
    assert _run(zc.run_zoe_core("x", "s1", "u1")) == ""
    assert w.calls == 2  # one retry, then give up with ""


def test_both_attempts_raise_reraises(monkeypatch):
    w = _FakeWorker(["raise", "raise"])
    _patch_single_worker(monkeypatch, w)
    with pytest.raises(RuntimeError):
        _run(zc.run_zoe_core("x", "s1", "u1"))
    assert w.calls == 2


def test_semaphore_bounds_concurrency(monkeypatch):
    # Force a known concurrency cap and a fresh semaphore on the test loop.
    monkeypatch.setattr(zc, "_MAX_CONCURRENCY", 2)
    monkeypatch.setattr(zc, "_BRAIN_SEM", None)

    state = {"cur": 0, "max": 0}

    async def _track():
        state["cur"] += 1
        state["max"] = max(state["max"], state["cur"])
        await asyncio.sleep(0.02)  # hold the slot so overlap is observable
        state["cur"] -= 1

    # Each session gets its own worker; all share the concurrency tracker.
    workers = {}

    async def _fake_worker_for(user_id, session_id, *, voice_mode=False):
        w = workers.get(session_id)
        if w is None:
            w = _FakeWorker(["ok"], on_stream=_track)
            workers[session_id] = w
        return w
    monkeypatch.setattr(zc, "_worker_for", _fake_worker_for)

    async def go():
        await asyncio.gather(*[
            zc.run_zoe_core("m", f"s{i}", "u") for i in range(8)
        ])
    _run(go())
    # Never more than the cap ran at once, and all 8 still completed.
    assert state["max"] <= 2, f"observed {state['max']} concurrent (cap 2)"
    assert len(workers) == 8
