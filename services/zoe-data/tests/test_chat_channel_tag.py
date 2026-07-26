"""Phase 1 plumbing: the optional `channel` tag flows /api/chat → fast_tiers.

Two contracts are proven here:

  1. `routers.chat._resolve_channel(body)` — the single-source normalizer the
     endpoint uses to derive the channel tag. Absent / blank `channel` MUST
     resolve to "chat" (the historical hardcoded default) so web / voice / touch
     behaviour is byte-identical to before the field existed; an explicit tag
     (e.g. "telegram") is lowercased + trimmed and passed through.

  2. `fast_tiers.profile_for("telegram")` selects the telegram CHANNEL_PROFILES
     entry, and a "telegram"-tagged `resolve()` actually applies that profile
     (here: allow_writes=True reaches expert_dispatch). This is what makes the
     plumbed tag meaningful end-to-end.

These import local service modules (`routers.chat`), so per
tests/AGENTS.md they run only on the self-hosted Jetson runner.
"""
import asyncio
import types

import pytest

pytestmark = pytest.mark.ci_safe

chat_router = pytest.importorskip("routers.chat", reason="needs service modules")
fast_tiers = pytest.importorskip("fast_tiers")
expert_dispatch = pytest.importorskip("expert_dispatch")
semantic_router = pytest.importorskip("semantic_router")
intent_router = pytest.importorskip("intent_router")


def _run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------- #
# 1: _resolve_channel — absent → "chat" (backward-compatible default).        #
# --------------------------------------------------------------------------- #
def test_resolve_channel_absent_defaults_to_chat():
    # No `channel` key at all → "chat" (byte-identical to the pre-field default).
    assert chat_router._resolve_channel({}) == "chat"
    assert chat_router._resolve_channel({"message": "hi"}) == "chat"


def test_resolve_channel_blank_and_null_default_to_chat():
    # Explicit null / empty / whitespace-only must still collapse to "chat",
    # never to "" (which would select an unknown empty profile).
    for value in (None, "", "   ", "\t"):
        assert chat_router._resolve_channel({"channel": value}) == "chat"


def test_resolve_channel_explicit_telegram_passed_through():
    assert chat_router._resolve_channel({"channel": "telegram"}) == "telegram"


def test_resolve_channel_normalizes_case_and_whitespace():
    assert chat_router._resolve_channel({"channel": "  Telegram  "}) == "telegram"


def test_resolve_channel_non_string_defaults_to_chat():
    # Raw JSON can carry a non-string `channel`; must not 500 — falls to "chat".
    for value in (123, True, 1.5, {"x": 1}, ["telegram"]):
        assert chat_router._resolve_channel({"channel": value}) == "chat"


def test_resolve_channel_unknown_defaults_to_chat():
    # Unknown / typo'd channels are NOT honored — they fall back to the safe
    # "chat" profile (deferred writes), never an empty (writes-allowed) profile.
    for value in ("web", "totally-made-up", "telegramm"):
        assert chat_router._resolve_channel({"channel": value}) == "chat"


# --------------------------------------------------------------------------- #
# 2: the telegram tag selects the telegram profile in fast_tiers.             #
# --------------------------------------------------------------------------- #
def test_telegram_profile_exists_and_is_text_chat_shaped():
    prof = fast_tiers.profile_for("telegram")
    # Telegram is a real text-chat channel where the user is identified, so it
    # runs the shared Tier-0 read shortcut. (It carries allow_writes=True, like
    # the other identified conversational channels — see fast_tiers design notes.)
    assert prof.get("run_tier0") is True
    assert "run_tier0" in prof  # profile is present, not the unknown-channel {}


def test_default_chat_tag_keeps_writes_deferred(monkeypatch):
    # Absent channel → "chat" → allow_writes=False reaches dispatch, unchanged.
    monkeypatch.setattr(expert_dispatch, "is_enabled", lambda: True)
    monkeypatch.setattr(semantic_router, "is_enabled", lambda: True)
    monkeypatch.setattr(intent_router, "detect_intent",
                        lambda text, log_miss=False: None)  # Tier-0 miss
    seen = {}

    async def _fake_dispatch(domain, text, ctx, *, write_ok=True):
        seen["write_ok"] = write_ok
        return expert_dispatch.DispatchResult(domain=domain, reply="OK")

    monkeypatch.setattr(expert_dispatch, "dispatch", _fake_dispatch)
    channel = chat_router._resolve_channel({})  # absent → "chat"
    _run(fast_tiers.resolve("add milk", "u", "s", channel=channel,
                            router_decision={"domain": "lists", "score": 0.95}))
    assert seen["write_ok"] is False


def test_telegram_tag_applies_telegram_profile(monkeypatch):
    # channel="telegram" → telegram profile (allow_writes=True) reaches dispatch.
    monkeypatch.setattr(expert_dispatch, "is_enabled", lambda: True)
    monkeypatch.setattr(semantic_router, "is_enabled", lambda: True)
    monkeypatch.setattr(intent_router, "detect_intent",
                        lambda text, log_miss=False: None)  # Tier-0 miss
    seen = {}

    async def _fake_dispatch(domain, text, ctx, *, write_ok=True):
        seen["write_ok"] = write_ok
        return expert_dispatch.DispatchResult(domain=domain, reply="OK")

    monkeypatch.setattr(expert_dispatch, "dispatch", _fake_dispatch)
    channel = chat_router._resolve_channel({"channel": "telegram"})
    _run(fast_tiers.resolve("add milk", "u", "s", channel=channel,
                            router_decision={"domain": "lists", "score": 0.95}))
    assert seen["write_ok"] is True
