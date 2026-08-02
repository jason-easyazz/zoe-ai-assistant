"""Pins how the self-recall result is wired into the startup probe.

Two properties matter and neither is provable from the probe's unit tests:

1. A self-recall MISS must mark memory `degraded` so /health surfaces it — the
   2026-07-31 index corruption ran ~8 hours reporting "ok".
2. A self-recall MISS must NEVER be fatal, even under
   ZOE_MEMORY_STARTUP_STRICT. Crash-looping on a bad index is the failure the
   probe exists to detect, not an acceptable reaction to it.
"""
from __future__ import annotations

import asyncio

import pytest

import main

pytestmark = pytest.mark.ci_safe


class _Svc:
    """Minimal MemoryService stand-in: the probe only needs these three."""

    def __init__(self, collection=object()):
        self._col = collection

    async def load_for_prompt(self, *a, **k):
        return []

    async def search(self, *a, **k):
        return []

    @staticmethod
    async def _run_sync(fn, *a):
        return fn(*a)

    def _collection(self):
        return self._col


@pytest.fixture
def probe_rig(monkeypatch):
    monkeypatch.setattr(main, "_memory_capture_health", {"status": "unknown", "detail": ""})
    monkeypatch.setattr("memory_extractor.extract_candidates",
                        lambda *a, **k: [{"text": "x"}])
    monkeypatch.setattr("memory_service.get_memory_service", lambda: _Svc())

    def set_recall(result):
        monkeypatch.setattr("memory_recall_probe.run_self_recall_check",
                            lambda col, **k: result)

    return set_recall


def _run():
    asyncio.run(main._run_memory_capture_startup_probe())
    return main._memory_capture_health


def test_healthy_self_recall_reports_ok(probe_rig):
    probe_rig({"status": "ok", "detail": "row r1 recalled itself"})
    health = _run()
    assert health["status"] == "ok"
    assert "self-recall ok" in health["detail"]


def test_self_recall_miss_marks_memory_degraded(probe_rig):
    probe_rig({"status": "degraded", "detail": "self-recall MISS: row r1 absent"})
    health = _run()
    assert health["status"] == "degraded"
    assert "MISS" in health["detail"]


def test_empty_store_is_not_degraded(probe_rig):
    """A fresh palace has nothing to recall — that is not corruption."""
    probe_rig({"status": "empty", "detail": "no stored rows to self-recall"})
    assert _run()["status"] == "ok"


def test_self_recall_miss_is_not_fatal_under_strict_mode(probe_rig, monkeypatch):
    """The load-bearing guard: strict mode must not turn a bad index into a
    crash loop."""
    monkeypatch.setenv("ZOE_MEMORY_STARTUP_STRICT", "true")
    probe_rig({"status": "degraded", "detail": "self-recall MISS: row r1 absent"})
    health = _run()  # must not raise
    assert health["status"] == "degraded"


def test_self_recall_exception_is_contained(probe_rig, monkeypatch):
    """Even a raising check degrades rather than propagating under strict mode."""
    monkeypatch.setenv("ZOE_MEMORY_STARTUP_STRICT", "true")

    def _boom(col, **k):
        raise RuntimeError("hnsw segfault-adjacent")

    monkeypatch.setattr("memory_recall_probe.run_self_recall_check", _boom)
    health = _run()  # must not raise
    assert health["status"] == "degraded"
    assert "did not complete" in health["detail"]


def test_plumbing_failure_still_honours_strict_mode(probe_rig, monkeypatch):
    """Negative control: the ORIGINAL strict-mode contract is untouched — a real
    extractor failure still raises when strict is on."""
    monkeypatch.setenv("ZOE_MEMORY_STARTUP_STRICT", "true")
    monkeypatch.setattr("memory_extractor.extract_candidates", lambda *a, **k: [])
    with pytest.raises(RuntimeError):
        asyncio.run(main._run_memory_capture_startup_probe())
