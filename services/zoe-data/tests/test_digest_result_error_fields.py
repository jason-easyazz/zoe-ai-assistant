"""The digest / dreaming RESULT dicts carry an error field that names the failure by TYPE, never by message.

``run_memory_digest`` logs its result dict (``logger.info("memory_digest: %s", result)``), so a field built from ``str(exc)`` writes
whatever the failed vector-store or model call quoted into the service log. Shapes pinned here (readers in brackets):

- ``result["error"]`` of a failed digest: ``load_failed:<cause type>`` for a ``TranscriptLoadError`` [``memory_metrics._classify_rows``,
  ``test_transcript_load_error``], else the exception TYPE name; ``extractor_failed:<kind>`` is unchanged.
- ``result["emotional_error"]``: the TYPE name [``test_memory_digest_idle_verdict``: ``startswith("TimeoutError")``].
- the music-taste digest ``error`` (no reader): the TYPE name.
- the dreaming ``open_loops`` / ``portrait`` / ``agent_sync`` / ``lint`` and digest ``night_mind`` error dicts: ``{"status": "error",
  "error": <type name>}`` (no reader depends on the old text; they are only logged and returned).
Synthetic sentinel only.
"""
from __future__ import annotations

import ast
import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import memory_digest as md  # noqa: E402

pytestmark = pytest.mark.ci_safe

SENTINEL = "Zorblax-tangerine-9137 lives at the shed"


def test_error_field_shapes():
    assert md._error_field(md.TranscriptLoadError(TypeError(SENTINEL))) == "load_failed:TypeError"
    assert md._error_field(RuntimeError(SENTINEL)) == "RuntimeError"
    assert SENTINEL not in md._error_field(md.TranscriptLoadError(ValueError(SENTINEL)))


def test_negative_control_the_old_field_shapes_carry_the_message():
    exc = RuntimeError(SENTINEL)
    assert SENTINEL in str(exc) and SENTINEL in f"{type(exc).__name__}: {exc}"


def test_a_failed_digest_row_has_a_message_free_error_and_emotional_error(monkeypatch):
    async def boom(_uid, _db=None):
        raise RuntimeError(SENTINEL)

    monkeypatch.setattr(md, "_load_todays_messages", boom)
    row = asyncio.run(md.run_memory_digest("u1"))
    assert row.get("error") == "RuntimeError" and SENTINEL not in repr(row)


def test_a_wrapped_load_failure_keeps_the_inner_type_and_no_message(monkeypatch):
    async def boom(_uid, _db=None):
        raise md.TranscriptLoadError(TypeError(SENTINEL))

    monkeypatch.setattr(md, "_load_todays_messages", boom)
    row = asyncio.run(md.run_memory_digest("u1"))
    assert row.get("error") == "load_failed:TypeError" and SENTINEL not in repr(row)


def test_every_result_error_assignment_is_message_free():
    """Static pin: no ``result[...] = ...`` / ``{"error": ...}`` in ``memory_digest.py`` builds an error field from the exception's
    message (a bare ``exc``, ``str(exc)``, an f-string over it)."""
    src = (ROOT / "memory_digest.py").read_text(encoding="utf-8")
    bad = []

    def uses_message(node: ast.AST) -> bool:
        for n in ast.walk(node):
            if isinstance(n, ast.Name) and n.id in ("exc", "je", "e", "err"):
                # ``type(exc).__name__`` / ``_error_field(exc)`` / ``exc.kind`` are fine
                return True
        return False

    def safe(node: ast.AST) -> bool:
        if isinstance(node, ast.Call):
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else ""
            if name in ("type", "_error_field"):
                return True
        if isinstance(node, ast.Attribute) and node.attr == "kind":
            return True
        if isinstance(node, ast.JoinedStr):     # f"extractor_failed:{exc.kind}": ``kind`` is a class name, never the message
            return all(safe(v.value) for v in node.values if isinstance(v, ast.FormattedValue))
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Call):   # type(exc).__name__
            return safe(node.value)
        return not uses_message(node)

    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Subscript):
            key = node.targets[0].slice
            if isinstance(key, ast.Constant) and key.value in ("error", "emotional_error") and not safe(node.value):
                bad.append(node.lineno)
        if isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                if isinstance(k, ast.Constant) and k.value == "error" and not safe(v):
                    bad.append(node.lineno)
    assert bad == []
