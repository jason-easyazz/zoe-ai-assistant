"""B0.8: the drawers embedding function against the REAL chromadb API (Jetson lane only).

Deliberately NOT ``ci_safe``: the slim GitHub lane has no chromadb, and an importorskip there
would report a green skip for a test that never ran. Unmarked, this runs in the Jetson
full-directory lane (``self-hosted-tests.yml``), where chromadb is installed.

The ``"default"`` name is what lets chromadb 1.x accept the cached EF against the palace's
persisted identity without "Embedding function conflict". On 0.6.3, which persists no identity,
the same assertions hold, because the name comes from our subclass.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import memory_service  # noqa: E402


def test_ef_identity_is_default_and_cached(monkeypatch):
    pytest.importorskip("chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2")
    monkeypatch.setattr(memory_service, "_DRAWERS_EF", None)
    ef = memory_service._drawers_embedding_function()
    assert ef.name() == "default"
    assert memory_service._drawers_embedding_function() is ef  # one ONNX session per process
