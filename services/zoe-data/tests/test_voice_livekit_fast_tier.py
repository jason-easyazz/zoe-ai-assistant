"""Unit tests for LiveKit's opt-in deterministic fast tier.

Covers `_livekit_fast_tiers_enabled` and `_maybe_fast_tier` in
routers/voice_livekit.py — the ZOE_LIVEKIT_FAST_TIERS gated path. LiveKit is
conversation-mode, so the default (flag off) MUST defer every turn to the brain.
"""
import asyncio

import pytest

import fast_tiers
from routers import voice_livekit as vlk

pytestmark = pytest.mark.ci_safe


def _run(coro):
    return asyncio.run(coro)


class _Res:
    def __init__(self, reply):
        self.reply = reply


def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ZOE_LIVEKIT_FAST_TIERS", raising=False)
    assert vlk._livekit_fast_tiers_enabled() is False

    called = {"n": 0}

    async def _fake_resolve(*a, **k):
        called["n"] += 1
        return _Res("should not be used")

    monkeypatch.setattr(fast_tiers, "resolve", _fake_resolve)
    # Flag off → defer to the brain AND never even call the core.
    assert _run(vlk._maybe_fast_tier("what time is it", "u", "s")) is None
    assert called["n"] == 0


@pytest.mark.parametrize("val", ["1", "true", "YES", "on"])
def test_enable_flag_truthy(monkeypatch, val):
    monkeypatch.setenv("ZOE_LIVEKIT_FAST_TIERS", val)
    assert vlk._livekit_fast_tiers_enabled() is True


@pytest.mark.parametrize("val", ["0", "false", "no", "", "off"])
def test_enable_flag_falsy(monkeypatch, val):
    monkeypatch.setenv("ZOE_LIVEKIT_FAST_TIERS", val)
    assert vlk._livekit_fast_tiers_enabled() is False


def test_enabled_returns_fast_reply(monkeypatch):
    monkeypatch.setenv("ZOE_LIVEKIT_FAST_TIERS", "1")
    seen = {}

    async def _fake_resolve(text, user_id, session_id, **kwargs):
        seen.update(text=text, user_id=user_id, session_id=session_id, kwargs=kwargs)
        return _Res("It's 5 PM.")

    monkeypatch.setattr(fast_tiers, "resolve", _fake_resolve)
    out = _run(vlk._maybe_fast_tier("what time is it", "jason", "lk1"))
    assert out == "It's 5 PM."
    # Routed through the shared core with the livekit channel tag.
    assert seen["kwargs"].get("channel") == "livekit"
    assert seen["text"] == "what time is it" and seen["user_id"] == "jason"


def test_enabled_none_defers_to_brain(monkeypatch):
    monkeypatch.setenv("ZOE_LIVEKIT_FAST_TIERS", "1")

    async def _fake_resolve(*a, **k):
        return None

    monkeypatch.setattr(fast_tiers, "resolve", _fake_resolve)
    assert _run(vlk._maybe_fast_tier("tell me a joke", "u", "s")) is None


def test_enabled_empty_reply_defers(monkeypatch):
    monkeypatch.setenv("ZOE_LIVEKIT_FAST_TIERS", "1")

    async def _fake_resolve(*a, **k):
        return _Res("")  # confident-but-empty must not be spoken

    monkeypatch.setattr(fast_tiers, "resolve", _fake_resolve)
    assert _run(vlk._maybe_fast_tier("x", "u", "s")) is None


def test_enabled_swallows_errors(monkeypatch):
    monkeypatch.setenv("ZOE_LIVEKIT_FAST_TIERS", "1")

    async def _boom(*a, **k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(fast_tiers, "resolve", _boom)
    # A failing fast tier must never break the turn — defer to the brain.
    assert _run(vlk._maybe_fast_tier("what time is it", "u", "s")) is None
