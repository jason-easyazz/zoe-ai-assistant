"""Open-loop quality rules (``open_loop_quality``): table-driven, fixtures only.

The owner's first review of real loops (2026-09-30), paraphrased generically: two
were Zoe's own mechanics (a "let's talk" opener, a correction) with no entity; two
were real (a relative in hospital + travel to visit; tired from busy work).
Negative controls: make ``loop_is_concrete`` return True, or ``loop_turn_is_meta``
return False — the junk rows below go red.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ci_safe  # pure functions over in-tree modules

from open_loop_quality import loop_anchors, loop_is_concrete, loop_turn_is_meta


@pytest.mark.parametrize("text, concrete", [
    ("The user expressed a desire to talk continuously", False),
    ("The user mentioned a problem with something they said that needs to be fixed", False),
    ("Expressed a wish to chat more often", False),        # sentence-initial capital ≠ name
    ("A relative is in hospital and the user is travelling to visit them", True),
    ("The user feels tired because work is busy", True),
    ("User is anxious about a job interview at the aquarium on Friday", True),
    ("User said their friend Priya might drop by", True),  # relation + mid-sentence name
    ("User is waiting on a quote from the plumber", True),
    ("User starts a new course next week", True),
])
def test_loop_is_concrete(text, concrete):
    assert loop_is_concrete(text) is concrete


def test_anchors_are_cue_words_not_time_words():
    anchors = loop_anchors("User's sister Marisol is flying in from Lisbon on Thursday")
    assert {"sister", "marisol", "lisbon"} <= anchors and "thursday" not in anchors


@pytest.mark.parametrize("turn, meta", [
    ("let's talk", True), ("Hey Zoe", True), ("that's wrong", True),
    ("you got that wrong, fix that", True), ("set a timer for ten minutes", True),
    ("thanks, that's all", True),
    ("My relative is in hospital and I'm flying over on Friday", False),
    ("honestly I'm so tired, work has been flat out", False),
])
def test_meta_turns_never_feed_the_extractor(turn, meta):
    assert loop_turn_is_meta(turn) is meta
