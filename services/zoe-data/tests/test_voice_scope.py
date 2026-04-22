"""Unit tests for `voice_scope.classify`.

Covers every branch of the classifier with 40+ phrases. No I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import pytest

from voice_scope import classify, ScopeDecision


# Small duck-typed stand-in for intent_router.Intent so we don't need the
# real chat stack during unit tests.
@dataclass
class _FakeIntent:
    name: str
    slots: Optional[dict[str, Any]] = None


# ── text-only cases (no intent) ────────────────────────────────────────────
TEXT_PUBLIC = [
    "what time is it",
    "what's the date today",
    "what is the weather",
    "what's the weather like tomorrow",
    "what's the temperature outside",
    "tell me a joke",
    "how are you",
    "good morning",
    "good evening",
    "hello there",
    "hi zoe",
    "thanks",
    "turn on the kitchen light",
    "turn off all the lights",
    "set the thermostat to 21",
    "dim the bedroom lamp",
    "play some jazz",
    "play lo-fi beats",
    "volume up",
    "mute the tv",
    "skip this song",
]

TEXT_USER_SCOPED = [
    "check my calendar",
    "what's on my agenda tomorrow",
    "remind me to call mum at six",
    "what did i do yesterday",
    "show me my journal streak",
    "remember that my lucky number is 47",
    "what's my lucky number",
    "play my daily mix",
    "play my liked songs",
    "resume where i left off",
    "add milk to my shopping list",
    "did i finish the groceries",
    "have i logged coffee today",
    "i need to book a dentist",
    "i want to add a note",
]


@pytest.mark.parametrize("phrase", TEXT_PUBLIC)
def test_text_public(phrase: str) -> None:
    d = classify(phrase)
    assert d.scope == "public", f"{phrase!r} → {d}"


@pytest.mark.parametrize("phrase", TEXT_USER_SCOPED)
def test_text_user_scoped(phrase: str) -> None:
    d = classify(phrase)
    assert d.scope == "user_scoped", f"{phrase!r} → {d}"


# ── intent-first cases (intent dominates over text) ───────────────────────

def test_intent_public_time_query() -> None:
    d = classify("tell me the time please", _FakeIntent("time_query"))
    assert d.scope == "public"
    assert d.intent_name == "time_query"


def test_intent_weather_is_public() -> None:
    d = classify("any idea what it'll do today", _FakeIntent("weather"))
    assert d.scope == "public"


def test_intent_timer_is_public() -> None:
    d = classify("start a five minute timer", _FakeIntent("timer_create"))
    assert d.scope == "public"


def test_intent_ha_prefix_public() -> None:
    d = classify("do the thing with the lamp", _FakeIntent("ha_toggle"))
    assert d.scope == "public"


def test_intent_light_prefix_public() -> None:
    d = classify("...", _FakeIntent("light_turn_on"))
    assert d.scope == "public"


# ── list intents with list_type slot ──────────────────────────────────────

def test_intent_list_add_shopping_is_public() -> None:
    d = classify(
        "add milk to the shopping list",
        _FakeIntent("list_add", {"list_type": "shopping"}),
    )
    assert d.scope == "public"


def test_intent_list_add_family_is_public() -> None:
    d = classify(
        "add bleach to the family list",
        _FakeIntent("list_add", {"list_type": "family"}),
    )
    assert d.scope == "public"


def test_intent_list_add_personal_is_user_scoped() -> None:
    d = classify(
        "add wedding ring to my private list",
        _FakeIntent("list_add", {"list_type": "private"}),
    )
    # Unknown list type + "my " → personal_list
    assert d.scope == "user_scoped"


def test_intent_list_show_unknown_list_type_defaults_public() -> None:
    d = classify(
        "show the list",
        _FakeIntent("list_show", {"list_type": ""}),
    )
    # No possessive, unknown type → defaults to public (fail-public).
    assert d.scope == "public"


# ── music intents ─────────────────────────────────────────────────────────

def test_intent_music_generic_is_public() -> None:
    d = classify("put on some jazz", _FakeIntent("music_play"))
    assert d.scope == "public"


def test_intent_music_my_library_is_user_scoped() -> None:
    d = classify("play my liked songs", _FakeIntent("music_play"))
    assert d.scope == "user_scoped"


# ── user-scoped intents ───────────────────────────────────────────────────

def test_intent_journal_create_is_user_scoped() -> None:
    d = classify("add to journal that i ran 5k", _FakeIntent("journal_create"))
    assert d.scope == "user_scoped"


def test_intent_calendar_show_is_public_household() -> None:
    d = classify("what's coming up", _FakeIntent("calendar_show"))
    assert d.scope == "public"


def test_intent_memory_remember_is_user_scoped() -> None:
    d = classify("remember pin is 1234", _FakeIntent("memory_remember"))
    assert d.scope == "user_scoped"


def test_intent_build_widget_is_user_scoped() -> None:
    d = classify("add a moon widget", _FakeIntent("build_widget"))
    assert d.scope == "user_scoped"


# ── ambiguity / edge cases ────────────────────────────────────────────────

def test_empty_string_defaults_public() -> None:
    d = classify("")
    assert d.scope == "public"
    assert d.reason == "empty_text"


def test_empty_string_strict_mode() -> None:
    d = classify("", default_when_ambiguous="user_scoped")
    assert d.scope == "user_scoped"


def test_rubbish_text_defaults_public() -> None:
    d = classify("asdf qwerty zxcv")
    assert d.scope == "public"


def test_my_house_is_public_not_user_scoped() -> None:
    # "my house"/"my home"/"my kitchen" etc. are in the exclusion list on
    # the possessive regex — they describe household-shared rooms.
    assert classify("turn on my kitchen light").scope == "public"
    assert classify("dim my living room lights").scope == "public"


def test_possessive_beats_fallback() -> None:
    assert classify("what did i say yesterday").scope == "user_scoped"


def test_intent_overrides_text_heuristic() -> None:
    # Intent still takes precedence over raw text path selection.
    d = classify("what time is it", _FakeIntent("calendar_show"))
    assert d.scope == "public"


def test_unknown_intent_falls_through_to_text() -> None:
    # Intent name we've never heard of → text heuristic kicks in.
    d = classify("play jazz", _FakeIntent("some_future_intent"))
    assert d.scope == "public"


def test_returns_dataclass() -> None:
    d = classify("what time is it")
    assert isinstance(d, ScopeDecision)
    assert d.reason  # non-empty


def test_pure_no_mutation() -> None:
    # Calling classify should never mutate its input intent/slots.
    intent = _FakeIntent("list_add", {"list_type": "shopping"})
    before = dict(intent.slots or {})
    classify("add milk", intent)
    assert (intent.slots or {}) == before
