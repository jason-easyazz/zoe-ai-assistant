"""QA concern #4 — make reconcile_for_ingest's fail-open-to-ADD path observable.

The shared ``memory_quality.reconcile_for_ingest`` chokepoint (#1280) prefers
DUPLICATES OVER LOST FACTS: on a search timeout / empty result / error it stores
the fact as ADD without a supersession check. That tradeoff is deliberate and
UNCHANGED here — these tests prove it is now *counted* (Prometheus counter,
labelled by cause) and *alertable* (a sliding-window ``sustained`` flag that
escalates the log WARNING → ERROR), so a sustained duplicate-factory burst can't
hide behind a green /health again.
"""
import asyncio
import logging

import pytest

memory_quality = pytest.importorskip("memory_quality")
memory_metrics = pytest.importorskip("memory_metrics")

pytestmark = pytest.mark.ci_safe  # fakes only — no DB, no model, no live service


def _run(coro):
    return asyncio.run(coro)


def _counter_value(cause: str) -> float:
    return memory_metrics.memory_reconcile_failopen_count.labels(
        cause=cause
    )._value.get()


@pytest.fixture(autouse=True)
def _clean_window():
    """Each test starts with an empty sliding window (the deque is process-global)."""
    memory_metrics._RECONCILE_FAILOPEN_EVENTS.clear()
    yield
    memory_metrics._RECONCILE_FAILOPEN_EVENTS.clear()


class _EmptySvc:
    """Search always returns [] — the fail-open-to-ADD trigger."""

    async def search(self, text, *, user_id, limit, timeout_s=None):
        return []


class _BrokenSvc:
    async def search(self, *a, **k):
        raise RuntimeError("store down")


def test_timeout_labels_counter_and_still_adds(monkeypatch):
    """An empty result that burned ~the whole search budget is labelled
    ``search_timeout`` — and the fact is STILL stored as ADD (unchanged)."""
    # Force the elapsed-time heuristic to read every empty result as a timeout.
    monkeypatch.setattr(memory_quality, "_RECONCILE_SEARCH_TIMEOUT_S", 0.0)
    before = _counter_value("search_timeout")

    op, target = _run(
        memory_quality.reconcile_for_ingest(_EmptySvc(), "my dad's name is Kevin", "u1")
    )

    assert (op, target) == ("add", None), "fail-open decision must stay ADD"
    assert _counter_value("search_timeout") == before + 1


def test_fast_empty_labels_empty_results(monkeypatch):
    """A fast empty result (default 15 s budget unspent) is a cold/empty store,
    labelled ``empty_results`` — a different cause than a timeout."""
    monkeypatch.setattr(memory_quality, "_RECONCILE_SEARCH_TIMEOUT_S", 15.0)
    before = _counter_value("empty_results")

    _run(memory_quality.reconcile_for_ingest(_EmptySvc(), "my dad's name is Kevin", "u1"))

    assert _counter_value("empty_results") == before + 1


def test_search_error_labels_counter_and_adds():
    """A raised search fails open to ADD and is counted under ``search_error``."""
    before = _counter_value("search_error")

    op, target = _run(
        memory_quality.reconcile_for_ingest(_BrokenSvc(), "my dad's name is Kevin", "u1")
    )

    assert (op, target) == ("add", None)
    assert _counter_value("search_error") == before + 1


def test_sustained_rate_trips_threshold_and_escalates_to_error(monkeypatch, caplog):
    """Repeated timeouts trip the sliding-window watcher: ``sustained`` flips
    True and the log escalates from WARNING to ERROR."""
    monkeypatch.setattr(memory_quality, "_RECONCILE_SEARCH_TIMEOUT_S", 0.0)
    monkeypatch.setattr(memory_metrics, "_RECONCILE_FAILOPEN_THRESHOLD", 3)
    monkeypatch.setattr(memory_metrics, "_RECONCILE_FAILOPEN_WINDOW_S", 300.0)

    svc = _EmptySvc()
    for _ in range(2):
        _run(memory_quality.reconcile_for_ingest(svc, "my dad's name is Kevin", "u1"))
    assert memory_metrics.reconcile_failopen_status()["sustained"] is False

    with caplog.at_level(logging.ERROR, logger="memory_quality"):
        _run(memory_quality.reconcile_for_ingest(svc, "my dad's name is Kevin", "u1"))

    status = memory_metrics.reconcile_failopen_status()
    assert status["count"] >= 3
    assert status["sustained"] is True
    assert any(
        r.levelno == logging.ERROR and "SUSTAINED fail-open rate" in r.getMessage()
        for r in caplog.records
    ), "a sustained fail-open rate must log at ERROR"


def test_window_prunes_old_events(monkeypatch):
    """Events older than the window fall out of the rate, so a slow trickle of
    fail-opens never trips ``sustained`` — only a real burst does."""
    monkeypatch.setattr(memory_metrics, "_RECONCILE_FAILOPEN_WINDOW_S", 100.0)
    now = 1_000_000.0
    memory_metrics.record_reconcile_failopen("search_timeout", now=now - 200.0)  # stale
    memory_metrics.record_reconcile_failopen("search_timeout", now=now - 10.0)   # fresh

    status = memory_metrics.reconcile_failopen_status(now=now)
    assert status["count"] == 1
