"""W11 expressive-delivery mapper tests.

The load-bearing property is NOT "does it sound nice" — it is **what does it leave
alone**. The Kokoro sidecar's phrase cache serves only at speed 1.0 (~2ms vs a
1-2.5s cold synthesis), so every utterance this mapper slows down is one that
stops being cacheable. A mapper that fires eagerly would quietly turn the hot
path 1000x slower while sounding like an improvement.

So the tests that matter most are the negative ones: flag off, short utterance,
ordinary content. Neutral must mean `speed is None` — "send the request exactly
as before" — not a literal 1.0, so the flag-off payload stays byte-identical.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import voice_delivery  # noqa: E402


LONG_NEUTRAL = (
    "The forecast for tomorrow is twenty-four degrees with a light breeze from "
    "the south-west, and it should stay dry through the afternoon."
)
LONG_WARM = (
    "I'm sorry, that sounds hard. Take your time with it, and I'm here whenever "
    "you feel like talking about it a bit more."
)
SHORT_WARM = "I'm sorry."


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in (
        "ZOE_EXPRESSIVE_TTS", "ZOE_EXPRESSIVE_MIN_CHARS",
        "ZOE_EXPRESSIVE_GENTLE_FROM_H", "ZOE_EXPRESSIVE_GENTLE_TO_H",
    ):
        monkeypatch.delenv(key, raising=False)
    importlib.reload(voice_delivery)
    yield
    importlib.reload(voice_delivery)


def _on(monkeypatch):
    monkeypatch.setenv("ZOE_EXPRESSIVE_TTS", "1")


# ── The negatives: what must stay untouched ────────────────────────────────

def test_flag_off_is_neutral_even_for_warm_content_at_night():
    """Default OFF. Nothing about the request may change until it is enabled."""
    d = voice_delivery.resolve(LONG_WARM, hour=2)
    assert d.speed is None, "flag-off must send NO speed key at all"
    assert d.profile == "neutral"


def test_neutral_is_none_not_one_point_zero(monkeypatch):
    """`None` means byte-identical payload; 1.0 would be a behaviour change."""
    _on(monkeypatch)
    assert voice_delivery.resolve(LONG_NEUTRAL, hour=12).speed is None


def test_short_utterances_are_never_slowed(monkeypatch):
    """Confirmations/acks/fillers are the phrase cache's hot set — hands off."""
    _on(monkeypatch)
    assert voice_delivery.resolve(SHORT_WARM, hour=2).speed is None
    assert voice_delivery.resolve("Okay.", hour=2).speed is None
    assert voice_delivery.resolve("Let me check your calendar.", hour=2).speed is None


def test_ordinary_long_reply_in_daytime_is_neutral(monkeypatch):
    """No positive reason => no change. The mapper opts IN, never out."""
    _on(monkeypatch)
    assert voice_delivery.resolve(LONG_NEUTRAL, hour=12).speed is None


# ── The positives ──────────────────────────────────────────────────────────

def test_consolation_content_is_warm_at_any_hour(monkeypatch):
    _on(monkeypatch)
    d = voice_delivery.resolve(LONG_WARM, hour=12)
    assert d.profile == "warm"
    assert d.speed == voice_delivery.SPEED_WARM


def test_late_night_ordinary_reply_is_gentle(monkeypatch):
    _on(monkeypatch)
    d = voice_delivery.resolve(LONG_NEUTRAL, hour=2)
    assert d.profile == "gentle"
    assert d.speed == voice_delivery.SPEED_GENTLE


def test_both_reasons_take_the_slower_reading(monkeypatch):
    """A 2am consolation: when two reasons to soften agree, soften more."""
    _on(monkeypatch)
    d = voice_delivery.resolve(LONG_WARM, hour=3)
    assert d.speed == min(voice_delivery.SPEED_GENTLE, voice_delivery.SPEED_WARM)


# ── Window arithmetic ──────────────────────────────────────────────────────

@pytest.mark.parametrize("hour,gentle", [
    (21, False), (22, True), (23, True), (0, True),
    (3, True), (6, True), (7, False), (12, False),
])
def test_quiet_hours_wrap_midnight(monkeypatch, hour, gentle):
    _on(monkeypatch)
    d = voice_delivery.resolve(LONG_NEUTRAL, hour=hour)
    assert (d.speed is not None) is gentle, f"hour={hour}"


def test_min_chars_is_tunable(monkeypatch):
    _on(monkeypatch)
    monkeypatch.setenv("ZOE_EXPRESSIVE_MIN_CHARS", "5")
    importlib.reload(voice_delivery)
    assert voice_delivery.resolve(SHORT_WARM, hour=12).profile == "warm"


def test_degenerate_window_disables_gentle(monkeypatch):
    """from == to is an empty window, not a 24h one."""
    _on(monkeypatch)
    monkeypatch.setenv("ZOE_EXPRESSIVE_GENTLE_FROM_H", "22")
    monkeypatch.setenv("ZOE_EXPRESSIVE_GENTLE_TO_H", "22")
    importlib.reload(voice_delivery)
    assert voice_delivery.resolve(LONG_NEUTRAL, hour=23).speed is None


def test_empty_and_none_text_are_safe(monkeypatch):
    _on(monkeypatch)
    assert voice_delivery.resolve("", hour=2).speed is None
    assert voice_delivery.resolve(None, hour=2).speed is None  # type: ignore[arg-type]
