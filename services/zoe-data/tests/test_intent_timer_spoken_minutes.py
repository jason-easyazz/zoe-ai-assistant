"""Timer intent accepts spelled-out minutes ("set a timer for five minutes").

Intent-miss cluster 40253b9060b845b8b83cecf79521a42d: STT renders short numbers
as words, and the digit-only timer patterns fell through to the slow brain.
"""
from __future__ import annotations

import pytest

from intent_router import detect_intent

pytestmark = pytest.mark.ci_safe


def _detect(text: str):
    return detect_intent(text, log_miss=False)


@pytest.mark.parametrize("text,minutes", [
    # the representative miss from the cluster
    ("set a timer for five minutes", 5),
    ("set a timer for ten minutes", 10),
    ("start a timer for two minutes", 2),
    ("set a five minute timer", 5),
    ("five minute timer", 5),
    ("start a timer for twenty five minutes", 25),
    ("set a timer for forty-five minutes", 45),
    # wake-word prefix is stripped by normalization before matching
    ("hey zoe set a timer for five minutes", 5),
])
def test_spoken_minutes_route_to_timer_create(text, minutes):
    intent = _detect(text)
    assert intent is not None, f"{text!r} fell through to the brain"
    assert intent.name == "timer_create"
    assert intent.slots["minutes"] == minutes


@pytest.mark.parametrize("text,minutes,label", [
    ("set a timer for ten minutes called pasta", 10, "Pasta"),
    ("set a three minute timer called eggs", 3, "Eggs"),
])
def test_spoken_minutes_keep_label_slot(text, minutes, label):
    intent = _detect(text)
    assert intent is not None and intent.name == "timer_create"
    assert intent.slots == {"minutes": minutes, "label": label}


@pytest.mark.parametrize("text,minutes", [
    # digit forms must keep working exactly as before
    ("set a timer for 5 minutes", 5),
    ("set a 3 minute timer", 3),
    ("10 minute timer", 10),
    ("set a timer", 5),
])
def test_digit_minutes_unchanged(text, minutes):
    intent = _detect(text)
    assert intent is not None and intent.name == "timer_create"
    assert intent.slots["minutes"] == minutes


def test_non_number_words_still_fall_through():
    assert _detect("set a timer for eleventy minutes") is None


@pytest.mark.parametrize("text,minutes,label", [
    # label spelled like the duration must survive (positional, not value-based)
    ("set a five minute timer called five", 5, "Five"),
    ("set a timer for 5 minutes called 5", 5, "5"),
])
def test_label_equal_to_duration_is_kept(text, minutes, label):
    intent = _detect(text)
    assert intent is not None and intent.name == "timer_create"
    assert intent.slots == {"minutes": minutes, "label": label}
