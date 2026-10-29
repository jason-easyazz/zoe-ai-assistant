"""2026-10-29: the ci_safe lane segfaulted twice (``Fatal Python error: Segmentation fault``) in
``fastembed ... tokenize <- semantic_router.embed <- restraint_head._run_worker``. One fastembed model (a Rust ``tokenizers``
Tokenizer + an ONNX session) was entered from two threads at once - the restraint-head worker and a ``route()`` on the
``asyncio.to_thread`` pool (live: voice + chat + the worker), or a worker a previous test left running.

These tests pin the two invariants without needing fastembed or a model (a fake model that DETECTS concurrent entry):
  1. ``semantic_router.embed`` never has two threads inside the model at once.
  2. ``restraint_head.stop_worker`` joins the background worker, so no test (or interpreter exit) leaves it embedding.
"""
from __future__ import annotations

import threading
import time

import numpy as np
import pytest

import restraint_head
import semantic_router

pytestmark = pytest.mark.ci_safe


class _DetectingModel:
    """Stands in for fastembed's TextEmbedding: records the peak number of threads inside ``embed``."""

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self.inside = 0
        self.peak = 0

    def embed(self, texts):
        # LAZY like fastembed: the native work happens while the caller reads the iterator, so the overlap is measured there
        # and a regression that moves ``next()`` outside the lock is caught too.
        for _ in texts:
            with self._guard:
                self.inside += 1
                self.peak = max(self.peak, self.inside)
            time.sleep(0.002)                   # long enough for an unserialised second caller to overlap
            with self._guard:
                self.inside -= 1
            yield np.ones(4, dtype=np.float32)


@pytest.fixture
def fake_model(monkeypatch):
    m = _DetectingModel()
    monkeypatch.setattr(semantic_router, "_MODEL", m)
    monkeypatch.setattr(semantic_router, "_EMBED_CACHE", None)
    monkeypatch.setattr(semantic_router, "_ensure_loaded", lambda: None)
    return m


def _hammer(n_threads: int, per_thread: int) -> list[str]:
    errs: list[str] = []

    def work(i: int) -> None:
        try:
            for k in range(per_thread):
                semantic_router.embed(f"text {i} {k}")      # distinct text: the one-slot cache never short-circuits
        except BaseException as exc:  # noqa: BLE001
            errs.append(repr(exc))

    ts = [threading.Thread(target=work, args=(i,)) for i in range(n_threads)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(30)
    return errs


def test_embed_never_enters_the_model_from_two_threads(fake_model):
    assert _hammer(6, 25) == []
    assert fake_model.peak == 1


def test_negative_control_the_detector_sees_overlap_without_the_lock(fake_model, monkeypatch):
    """Break the fix (swap the lock for a no-op) and the detector must fire - proves invariant 1 measures something."""
    class _NoLock:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(semantic_router, "_EMBED_LOCK", _NoLock())
    assert _hammer(6, 25) == []
    assert fake_model.peak > 1


def test_restraint_worker_and_route_threads_share_the_model_serially(fake_model, monkeypatch):
    """The real crash shape: the restraint worker embeds while other threads call ``embed`` (what ``route`` does)."""
    monkeypatch.setattr(restraint_head, "_EMBEDDER", None)
    monkeypatch.setattr(restraint_head, "_live_embedder", lambda: semantic_router.embed)
    monkeypatch.setattr(restraint_head, "_decide", lambda v: ("none",))
    monkeypatch.setattr(restraint_head, "_load", lambda: ("c", None, None, None, {}))
    monkeypatch.setattr(restraint_head, "_model_pending", lambda: False)
    restraint_head.reset()
    for i in range(40):
        restraint_head.defer(f"queued text number {i}")
    errs = _hammer(4, 20)
    assert restraint_head.drain(20.0)
    assert errs == []
    assert fake_model.peak == 1


def test_stop_worker_joins_the_background_thread(monkeypatch):
    monkeypatch.setattr(restraint_head, "_load", lambda: ("c", None, None, None, {}))
    monkeypatch.setattr(restraint_head, "_model_pending", lambda: False)
    monkeypatch.setattr(restraint_head, "lookup", lambda raw, embed=False: None)
    restraint_head.reset()
    assert restraint_head.defer("a text that starts the worker")
    assert restraint_head.drain(5.0)
    w = restraint_head._WORKER
    assert w is not None and w.is_alive()
    assert restraint_head.stop_worker(5.0) is True
    assert not w.is_alive()
    assert restraint_head._WORKER is None
    # and it restarts on demand afterwards
    assert restraint_head.defer("another text")
    assert restraint_head.drain(5.0)
    assert restraint_head.stop_worker(5.0) is True


def test_a_timed_out_stop_does_not_poison_the_next_worker(monkeypatch):
    """Cross-review finding: a join that times out must leave the worker restartable (per-worker stop event, unregistered up front)."""
    gate = threading.Event()
    entered = threading.Event()
    seen: list[str] = []

    def slow_lookup(raw, embed=False):
        entered.set()
        gate.wait(10)                                   # the first text holds the worker inside "native" code
        seen.append(raw)
        return None

    monkeypatch.setattr(restraint_head, "_load", lambda: ("c", None, None, None, {}))
    monkeypatch.setattr(restraint_head, "_model_pending", lambda: False)
    monkeypatch.setattr(restraint_head, "lookup", slow_lookup)
    restraint_head.reset()
    assert restraint_head.defer("first, blocks the worker")
    assert entered.wait(5)
    old = restraint_head._WORKER
    assert restraint_head.stop_worker(0.05) is False    # join timed out
    gate.set()                                          # the old worker finishes, sees its own stop event, exits
    old.join(5)
    assert not old.is_alive()
    assert restraint_head.defer("second, needs a live worker")
    assert restraint_head.drain(5.0)
    assert "second, needs a live worker" in seen
    assert restraint_head._WORKER is not None and restraint_head._WORKER.is_alive()
    assert restraint_head.stop_worker(5.0) is True


def test_stop_leaves_no_stray_wakeup_that_would_hang_drain(monkeypatch):
    monkeypatch.setattr(restraint_head, "_load", lambda: ("c", None, None, None, {}))
    monkeypatch.setattr(restraint_head, "_model_pending", lambda: False)
    monkeypatch.setattr(restraint_head, "lookup", lambda raw, embed=False: None)
    restraint_head.reset()
    restraint_head.defer("start the worker")
    assert restraint_head.drain(5.0)
    assert restraint_head.stop_worker(5.0) is True
    assert restraint_head.drain(2.0)                    # nothing unfinished is left behind


def test_defer_overlapping_a_stop_never_strands_the_old_worker(monkeypatch):
    """Review: a replacement worker must not steal the old worker's wake-up (each worker polls its OWN stop event)."""
    monkeypatch.setattr(restraint_head, "_load", lambda: ("c", None, None, None, {}))
    monkeypatch.setattr(restraint_head, "_model_pending", lambda: False)
    monkeypatch.setattr(restraint_head, "lookup", lambda raw, embed=False: None)
    monkeypatch.setattr(restraint_head, "_POLL_S", 0.05)
    restraint_head.reset()
    for rnd in range(15):
        restraint_head.defer(f"idle worker {rnd}")
        assert restraint_head.drain(5.0)
        old = restraint_head._WORKER
        t = threading.Thread(target=lambda: restraint_head.defer(f"racing text {rnd}"))
        t.start()
        assert restraint_head.stop_worker(5.0) is True       # the old (idle) worker exits within the timeout every time
        t.join(5)
        assert not old.is_alive()
    assert restraint_head.stop_worker(5.0) is True
    assert restraint_head._STOPPING == []
