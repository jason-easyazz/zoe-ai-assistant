"""ZOE_ORT_CPU_ARENA - the embedders' ORT CPU arena is OFF by default (ort_arena.py).

Fakes only: no fastembed, no onnxruntime, no model.

Negative controls:
  * unset / garbage -> the arena is OFF (``enable_cpu_mem_arena=False`` reaches fastembed);
  * ``ZOE_ORT_CPU_ARENA=1`` (the revert switch) -> fastembed gets no extra kwargs at all, i.e.
    exactly the pre-change construction;
  * the Chroma session builder disables the arena on the options it is handed and keeps the
    CPU provider; the memory_service override is installed only while the arena is off.
"""
from __future__ import annotations

import sys
import types

import pytest

import ort_arena

pytestmark = pytest.mark.ci_safe


@pytest.mark.parametrize("raw,enabled", [
    (None, False), ("", False), ("0", False), ("no", False), ("maybe", False),
    ("1", True), ("true", True), ("YES", True), (" on ", True),
])
def test_arena_enabled_only_by_explicit_revert_switch(monkeypatch, raw, enabled):
    if raw is None:
        monkeypatch.delenv("ZOE_ORT_CPU_ARENA", raising=False)
    else:
        monkeypatch.setenv("ZOE_ORT_CPU_ARENA", raw)
    assert ort_arena.arena_enabled() is enabled
    assert ort_arena.fastembed_kwargs() == ({} if enabled else {"enable_cpu_mem_arena": False})


def test_build_session_disables_arena_and_keeps_cpu_provider():
    calls = []

    class FakeOrt:
        def InferenceSession(self, path, providers, sess_options):
            calls.append((path, providers, sess_options))
            return "session"

    so = types.SimpleNamespace(enable_cpu_mem_arena=True)
    assert ort_arena.build_session(FakeOrt(), "m.onnx", so) == "session"
    assert calls == [("m.onnx", ["CPUExecutionProvider"], so)] and so.enable_cpu_mem_arena is False


@pytest.mark.parametrize("flag,expected", [(None, {"enable_cpu_mem_arena": False}), ("1", {})])
def test_persona_drift_passes_mode_to_fastembed(monkeypatch, flag, expected):
    import persona_drift

    seen: list[dict] = []

    class FakeTextEmbedding:
        def __init__(self, model_name, **kw):
            seen.append(kw)

    monkeypatch.setitem(sys.modules, "fastembed", types.SimpleNamespace(TextEmbedding=FakeTextEmbedding))
    if flag is None:
        monkeypatch.delenv("ZOE_ORT_CPU_ARENA", raising=False)
    else:
        monkeypatch.setenv("ZOE_ORT_CPU_ARENA", flag)
    persona_drift.load_bge_embedder()
    assert seen == [expected]


@pytest.mark.parametrize("flag,overridden", [(None, True), ("1", False)])
def test_chroma_ef_override_installed_only_while_arena_off(monkeypatch, flag, overridden):
    pytest.importorskip("chromadb")
    import memory_service

    if flag is None:
        monkeypatch.delenv("ZOE_ORT_CPU_ARENA", raising=False)
    else:
        monkeypatch.setenv("ZOE_ORT_CPU_ARENA", flag)
    monkeypatch.setattr(memory_service, "_DRAWERS_EF", None)
    ef = memory_service._drawers_embedding_function()
    assert ef.name() == "default"
    assert ("model" in type(ef).__dict__) is overridden
