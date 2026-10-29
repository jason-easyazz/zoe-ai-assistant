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
        with self._guard:
            self.inside += 1
            self.peak = max(self.peak, self.inside)
        time.sleep(0.002)                       # long enough for an unserialised second caller to overlap
        with self._guard:
            self.inside -= 1
        return iter([np.ones(4, dtype=np.float32) for _ in texts])


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
