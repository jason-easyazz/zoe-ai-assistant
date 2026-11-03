"""Regression (PR #2110 CI hang): ``local_text_logs.locked`` must never wait forever for the in-process append lock.

The router's ``_shadow_write_lock`` is a non-reentrant ``threading.Lock``. ``locked()`` used a bare ``with lock`` (only the flock was
bounded), so a second ``locked(ROUTER_SHADOW)`` on a thread that already held it - or any holder that never released - blocked the
sweep / forget cascade forever; in CI it hung ``test_redact_router_shadow`` for 3h once ``semantic_router`` was in ``sys.modules``.
Break: put ``with _append_lock(log):`` back in ``locked`` -> the worker below never finishes and the assertions fail (no hang).
"""
from __future__ import annotations

import sys
import threading
import types
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services" / "zoe-data"))


def _run_bounded(fn, wait=10.0):
    """Run ``fn`` on a daemon thread and fail (not hang) if it does not return in ``wait`` seconds."""
    box: dict = {}

    def target():
        try:
            fn()
            box["out"] = "returned"
        except BaseException as exc:  # noqa: BLE001
            box["out"] = exc

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(wait)
    assert not t.is_alive(), "locked() is still waiting for the in-process lock: unbounded wait"
    return box["out"]


@pytest.fixture
def router_lock(monkeypatch, tmp_path):
    import local_text_logs as ltl

    fake = types.ModuleType("semantic_router")
    fake._shadow_write_lock = threading.Lock()
    monkeypatch.setitem(sys.modules, "semantic_router", fake)
    monkeypatch.setattr(ltl, "LOCK_WAIT_S", 0.2)
    monkeypatch.setenv("ZOE_ROUTER_HEAD_LOG", str(tmp_path / "shadow.jsonl"))
    return ltl, fake._shadow_write_lock


def test_a_lock_that_is_never_released_times_out(router_lock):
    ltl, lock = router_lock
    lock.acquire()                                   # a holder that never lets go
    try:
        out = _run_bounded(lambda: ltl.locked(ltl.ROUTER_SHADOW).__enter__())
    finally:
        lock.release()
    assert isinstance(out, TimeoutError)


def test_reentering_locked_on_the_same_thread_times_out(router_lock):
    ltl, _lock = router_lock

    def reenter():
        with ltl.locked(ltl.ROUTER_SHADOW):
            with ltl.locked(ltl.ROUTER_SHADOW):
                pass

    assert isinstance(_run_bounded(reenter), TimeoutError)


def test_an_uncontended_pass_still_takes_and_releases_the_lock(router_lock):
    ltl, lock = router_lock

    def once():
        with ltl.locked(ltl.ROUTER_SHADOW):
            assert lock.locked()
        assert not lock.locked()

    assert _run_bounded(once) == "returned"
