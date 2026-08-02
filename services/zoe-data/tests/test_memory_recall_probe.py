"""Unit tests for the memory self-recall integrity check (2026-07-31 incident).

The check must catch the "search works but returns garbage" state a torn HNSW
persist produces, while never raising and never flagging benign states (empty
store, exact-duplicate ties).
"""
from __future__ import annotations

import pytest

from memory_recall_probe import run_self_recall_check

pytestmark = pytest.mark.ci_safe


class _FakeCollection:
    def __init__(self, *, ids=None, docs=None, hit_ids=None, distances=None, boom=None):
        self._ids = ids if ids is not None else ["row-1"]
        self._docs = docs if docs is not None else ["Jason lives in Geraldton"]
        self._hit_ids = hit_ids if hit_ids is not None else [["row-1"]]
        self._distances = distances if distances is not None else [[0.12]]
        self._boom = boom
        self.queried_with = None

    def get(self, **kwargs):
        if self._boom == "get":
            raise RuntimeError("sqlite exploded")
        return {"ids": self._ids, "documents": self._docs}

    def query(self, **kwargs):
        if self._boom == "query":
            raise RuntimeError("hnsw exploded")
        self.queried_with = kwargs
        return {"ids": self._hit_ids, "distances": self._distances}


def test_ok_when_row_recalls_itself():
    col = _FakeCollection(hit_ids=[["other", "row-1"]], distances=[[0.0, 0.05]])
    assert run_self_recall_check(col)["status"] == "ok"
    # read-only contract: the probe queries by text, it never writes
    assert "query_texts" in col.queried_with


def test_ok_when_exact_duplicate_wins_the_tie():
    col = _FakeCollection(hit_ids=[["dup-a", "dup-b"]], distances=[[0.0, 0.0]])
    assert run_self_recall_check(col)["status"] == "ok"


def test_degraded_when_own_row_missing_and_neighbours_are_far():
    # The 2026-07-31 corruption signature: self-query top hit at distance ~1.36.
    col = _FakeCollection(hit_ids=[["garbage-1", "garbage-2"]], distances=[[1.36, 1.36]])
    res = run_self_recall_check(col)
    assert res["status"] == "degraded"
    assert "row-1" in res["detail"]


def test_empty_store_is_not_degraded():
    col = _FakeCollection(ids=[], docs=[])
    assert run_self_recall_check(col)["status"] == "empty"


@pytest.mark.parametrize("boom", ["get", "query"])
def test_never_raises_on_backend_error(boom):
    res = run_self_recall_check(_FakeCollection(boom=boom))
    assert res["status"] == "degraded"
    assert "errored" in res["detail"]
