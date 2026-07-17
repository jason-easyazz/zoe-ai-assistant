"""Voice generation-length cap for the zoe-core brain worker.

The Pi RPC `prompt` message has no per-request maxTokens field, so the only safe
lever is the worker's spawn env (ZOE_CORE_MODEL_MAXTOKENS, read by
provider-local-gemma.ts at registration). These tests pin the contract:

  * voice workers bake the voice cap into their env; chat workers don't;
  * voice and non-voice turns get SEPARATE workers (so the cap never leaks onto
    chat replies even within the same user+session);
  * the cap is a no-op when disabled (<= 0).

Pure unit tests — no subprocess spawn, no live pi.

    python -m pytest services/zoe-data/tests/test_voice_maxtokens_cap.py -v
"""
from __future__ import annotations

import asyncio

import pytest

import zoe_core_client as zc

pytestmark = pytest.mark.ci_safe


def _run(coro):
    return asyncio.run(coro)


# ── _worker_env: the cap lands only on voice workers ────────────────────────

def test_voice_worker_env_carries_cap(monkeypatch):
    monkeypatch.setattr(zc, "_VOICE_MODEL_MAXTOKENS", 512)
    env = zc._worker_env("jason", voice_mode=True)
    assert env["ZOE_CORE_MODEL_MAXTOKENS"] == "512"


def test_chat_worker_env_has_no_voice_cap(monkeypatch):
    # Non-voice: we must NOT inject the voice cap — the provider's own default
    # (or a process-level ZOE_CORE_MODEL_MAXTOKENS) governs chat turns.
    monkeypatch.setattr(zc, "_VOICE_MODEL_MAXTOKENS", 512)
    monkeypatch.delenv("ZOE_CORE_MODEL_MAXTOKENS", raising=False)
    env = zc._worker_env("jason", voice_mode=False)
    assert "ZOE_CORE_MODEL_MAXTOKENS" not in env


def test_default_worker_env_is_unchanged(monkeypatch):
    # The default _worker_env (no voice_mode arg) behaves exactly as before.
    monkeypatch.delenv("ZOE_CORE_MODEL_MAXTOKENS", raising=False)
    env = zc._worker_env("jason")
    assert "ZOE_CORE_MODEL_MAXTOKENS" not in env


def test_cap_disabled_when_non_positive(monkeypatch):
    # 0 / negative disables the cap even on a voice worker.
    monkeypatch.setattr(zc, "_VOICE_MODEL_MAXTOKENS", 0)
    monkeypatch.delenv("ZOE_CORE_MODEL_MAXTOKENS", raising=False)
    env = zc._worker_env("jason", voice_mode=True)
    assert "ZOE_CORE_MODEL_MAXTOKENS" not in env


# ── worker keying: voice vs chat never share a process ──────────────────────

def test_voice_and_chat_get_distinct_workers(monkeypatch):
    # Same (user, session) but different voice_mode -> two different worker objects,
    # so the voice cap (baked at spawn) can't bleed onto the chat turn.
    monkeypatch.setattr(zc, "_WORKERS", zc.OrderedDict())

    async def _go():
        chat = await zc._worker_for("jason", "sess-1", voice_mode=False)
        voice = await zc._worker_for("jason", "sess-1", voice_mode=True)
        chat2 = await zc._worker_for("jason", "sess-1", voice_mode=False)
        return chat, voice, chat2

    chat, voice, chat2 = _run(_go())
    assert chat is not voice
    assert chat is chat2  # chat worker is reused (stable key)
    assert voice.voice_mode is True
    assert chat.voice_mode is False


def test_voice_worker_object_bakes_cap(monkeypatch):
    monkeypatch.setattr(zc, "_VOICE_MODEL_MAXTOKENS", 512)
    monkeypatch.setattr(zc, "_WORKERS", zc.OrderedDict())

    async def _go():
        return await zc._worker_for("jason", "sess-1", voice_mode=True)

    voice = _run(_go())
    assert voice.env["ZOE_CORE_MODEL_MAXTOKENS"] == "512"
