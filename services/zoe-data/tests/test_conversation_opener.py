"""Tests for the "hey zoe, let's talk" conversation-session opener.

Covers the pure phrase matcher, the ZOE_CONVERSATION_OPENER_ENABLED flag gate
(DEFAULT OFF must be a true no-op), the warm-ack payload contract
(conversation_mode=True + rotating warm line), and the fire-and-forget LiveKit
warmup.
"""

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import conversation_opener
from conversation_opener import (
    conversation_opener_enabled,
    is_conversation_opener,
    maybe_conversation_opener,
    opener_ack_phrase,
    opener_ack_phrases,
)

pytestmark = pytest.mark.ci_safe

_ENABLED_ENV = {"ZOE_CONVERSATION_OPENER_ENABLED": "1"}


@pytest.fixture(autouse=True)
def _reset_opener_state(monkeypatch):
    monkeypatch.setattr(conversation_opener, "_OPENER_ACK_CURSOR", 0)
    monkeypatch.setattr(conversation_opener, "_BACKGROUND_TASKS", set())
    # The flag must never leak in from the ambient environment.
    monkeypatch.delenv("ZOE_CONVERSATION_OPENER_ENABLED", raising=False)
    monkeypatch.delenv("ZOE_CONVERSATION_OPENER_PHRASES", raising=False)


# ── Phrase matcher ───────────────────────────────────────────────────────────

ALL_OPENER_PHRASES = [
    "let's talk",
    "lets talk",
    "let's chat",
    "lets chat",
    "let's have a chat",
    "let's have a talk",
    "can we talk",
    "can we chat",
    "talk to me",
    "i want to talk",
    "i want to chat",
]


@pytest.mark.parametrize("phrase", ALL_OPENER_PHRASES)
def test_every_listed_opener_phrase_fires(phrase):
    assert is_conversation_opener(phrase) is True


@pytest.mark.parametrize("phrase", ALL_OPENER_PHRASES)
def test_opener_fires_with_case_and_punctuation(phrase):
    assert is_conversation_opener(phrase.upper()) is True
    assert is_conversation_opener(phrase.capitalize() + ".") is True
    assert is_conversation_opener("  " + phrase + "!  ") is True


@pytest.mark.parametrize("phrase", ALL_OPENER_PHRASES)
def test_opener_fires_with_trailing_zoe(phrase):
    assert is_conversation_opener(phrase + " zoe") is True
    assert is_conversation_opener(phrase + ", Zoe!") is True


def test_opener_handles_curly_apostrophe():
    assert is_conversation_opener("Let’s talk") is True
    assert is_conversation_opener("Let’s chat, Zoe?") is True


@pytest.mark.parametrize(
    "text",
    [
        "let's talk about mum's birthday",
        "let's talk about the calendar tomorrow",
        "can we talk later tonight",
        "talk to me about the weather",
        "i want to talk to jason",
        "let's chat over dinner",
        "zoe let's talk about it",  # leading zoe + trailing content — not a bare opener
        "we should talk",
        "talk",
        "chat",
        "let's",
        "",
        None,
        "hey zoe",
        "what time is it",
        "add milk to the shopping list",
    ],
)
def test_longer_or_unrelated_utterances_do_not_fire(text):
    assert is_conversation_opener(text) is False


# ── Flag gate (DEFAULT OFF must be a true no-op) ─────────────────────────────

def test_flag_default_off():
    assert conversation_opener_enabled({}) is False
    assert conversation_opener_enabled({"ZOE_CONVERSATION_OPENER_ENABLED": "0"}) is False
    assert conversation_opener_enabled({"ZOE_CONVERSATION_OPENER_ENABLED": "false"}) is False
    assert conversation_opener_enabled({"ZOE_CONVERSATION_OPENER_ENABLED": "1"}) is True
    assert conversation_opener_enabled({"ZOE_CONVERSATION_OPENER_ENABLED": "true"}) is True


def test_flag_off_is_true_noop_at_the_wiring_seam(monkeypatch):
    """With the flag OFF, the seam main.py consults returns None WITHOUT even
    consulting the matcher or warming LiveKit — the normal flow proceeds."""

    def _explode(*_args, **_kwargs):  # pragma: no cover - must not run
        raise AssertionError("fast-path consulted while flag OFF")

    monkeypatch.setattr(conversation_opener, "is_conversation_opener", _explode)
    monkeypatch.setattr(conversation_opener, "_warm_livekit", _explode)

    assert maybe_conversation_opener("let's talk", {}) is None
    assert maybe_conversation_opener("let's talk", {"ZOE_CONVERSATION_OPENER_ENABLED": "0"}) is None


def test_flag_on_but_non_opener_returns_none(monkeypatch):
    warm_calls = []
    monkeypatch.setattr(conversation_opener, "_warm_livekit", lambda: warm_calls.append(1))
    assert maybe_conversation_opener("what time is it", _ENABLED_ENV) is None
    assert maybe_conversation_opener("let's talk about mum's birthday", _ENABLED_ENV) is None
    assert warm_calls == []


# ── Payload contract ─────────────────────────────────────────────────────────

def test_opener_payload_carries_conversation_mode_and_warm_line(monkeypatch):
    monkeypatch.setattr(conversation_opener, "_warm_livekit", lambda: None)

    result = maybe_conversation_opener("Let's talk!", _ENABLED_ENV)

    assert result is not None
    assert result["conversation_mode"] is True
    assert result["phrase"] in opener_ack_phrases(_ENABLED_ENV)
    assert result["phrase"].strip()


def test_opener_ack_phrases_rotate(monkeypatch):
    monkeypatch.setattr(conversation_opener, "_warm_livekit", lambda: None)
    phrases = opener_ack_phrases({})
    assert len(phrases) >= 3  # a few warm variants ship by default

    seen = [maybe_conversation_opener("let's chat", _ENABLED_ENV)["phrase"] for _ in phrases]
    assert seen == phrases  # round-robin across calls
    # Wraps around after a full cycle.
    assert maybe_conversation_opener("let's chat", _ENABLED_ENV)["phrase"] == phrases[0]


def test_opener_ack_phrase_env_override():
    env = {"ZOE_CONVERSATION_OPENER_PHRASES": "Hey. | Talk to me. "}
    assert opener_ack_phrases(env) == ["Hey.", "Talk to me."]
    assert opener_ack_phrase(env, index=1) == "Talk to me."


# ── LiveKit warmup ───────────────────────────────────────────────────────────

def test_opener_fires_ensure_livekit_started(monkeypatch):
    from routers import voice_livekit

    calls = []

    async def _fake_ensure(*args, **kwargs):
        calls.append(1)
        return True

    monkeypatch.setattr(voice_livekit, "ensure_livekit_started", _fake_ensure)

    async def _run():
        result = maybe_conversation_opener("can we talk", _ENABLED_ENV)
        assert result is not None and result["conversation_mode"] is True
        # Let the fire-and-forget task run.
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    asyncio.run(_run())
    assert calls == [1]


def test_warm_livekit_without_running_loop_is_best_effort(monkeypatch):
    """Outside an event loop (or with LiveKit unavailable) the opener must
    still answer — warmup failure is swallowed."""
    result = maybe_conversation_opener("talk to me", _ENABLED_ENV)
    assert result is not None
    assert result["conversation_mode"] is True


# ── Conversation ENDERS (turn_stream lane) ────────────────────────────────────
# Honoured only when the daemon reports an active conversation; fullmatch after
# normalization so long sentences containing the words never fire.

from conversation_opener import is_conversation_ender, next_ender_ack


@pytest.mark.parametrize("text", [
    "stop", "Stop.", "stop talking", "that's all", "thats all", "that's it",
    "that's enough", "we're done", "I'm done", "goodbye", "bye", "Bye, Zoe!",
    "end conversation", "no that's all", "thanks, that's all",
])
def test_ender_phrases_fire(text):
    assert is_conversation_ender(text) is True, f"{text!r} should end a conversation"


@pytest.mark.parametrize("text", [
    "stop the music",                      # command, not an ender
    "that's all the milk we have",         # sentence containing the words
    "goodbye parties are fun",
    "don't stop",
    "add stop to my list",
    "",
])
def test_non_ender_phrases_do_not_fire(text):
    assert is_conversation_ender(text) is False, f"{text!r} must NOT end a conversation"


def test_ender_ack_rotates():
    seen = {next_ender_ack() for _ in range(6)}
    assert len(seen) >= 2                   # rotation across defaults
    assert all(s.strip() for s in seen)


@pytest.mark.parametrize("text", [
    "that's all, thanks.", "Thanks, that's all", "That's all thank you",
    "okay that's it, thanks", "no thanks, that's all",
])
def test_ender_with_courtesy_edges_fires(text):
    # STT commonly appends/prepends courtesy words; edges are stripped (e2e
    # caught "That's all, thanks." not matching).
    assert is_conversation_ender(text) is True


@pytest.mark.parametrize("text", [
    "thanks",                      # courtesy alone is not an ender
    "thank you so much",
    "thanks for the recipe that's all about pasta",
])
def test_courtesy_alone_or_mid_sentence_does_not_fire(text):
    assert is_conversation_ender(text) is False
