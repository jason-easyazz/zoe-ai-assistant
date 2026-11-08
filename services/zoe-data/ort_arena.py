"""ONNX Runtime CPU memory arena for the embedders inside zoe-data - OFF by default.

ORT's CPU arena grows by doubling and never shrinks back to the OS. The two embedders
that live in zoe-data - fastembed's bge-small (semantic router stage 1 + persona drift)
and Chroma's MiniLM (the recall palace) - hold one session each for the life of the
process, so whatever the arena grew to during warm-up stays resident.

Measured A/B (docs/knowledge/ram-audit-nonvoice-2026-11.md, fresh process per run, same
inputs, identical output fingerprint): ``enable_cpu_mem_arena=False`` took the two
sessions from ~434-443 MB to ~322-332 MB RSS (-111 MB, HWM -86..-95 MB) with embed p50
within noise (fastembed 15-25 ms, Chroma 54-65 ms). ``ZOE_ORT_CPU_ARENA=1`` restores
ORT's arena (and Chroma's own session builder) if that ever regresses.

Moonshine STT, the VAD and the smart-turn model are not touched: they keep their
sessions exactly as built.
"""
from __future__ import annotations

import os
from typing import Any


def arena_enabled() -> bool:
    """True only when ``ZOE_ORT_CPU_ARENA`` is explicitly on (the revert switch)."""
    return os.environ.get("ZOE_ORT_CPU_ARENA", "0").strip().lower() in ("1", "true", "yes", "on")


def fastembed_kwargs() -> dict[str, Any]:
    """Extra ``TextEmbedding(...)`` kwargs: no CPU arena unless the revert switch is on."""
    return {} if arena_enabled() else {"enable_cpu_mem_arena": False}


def build_session(ort: Any, path: str, so: Any) -> Any:
    """Chroma MiniLM session without the CPU arena (``so`` = the options chroma would use)."""
    so.enable_cpu_mem_arena = False
    return ort.InferenceSession(path, providers=["CPUExecutionProvider"], sess_options=so)
