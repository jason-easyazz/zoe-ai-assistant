"""P8.a (live person bench, 2026-10-10: 9 of 10): "good night Zoe" got "Good evening! Hope your day was wonderful. Sweet
dreams, and see you tomorrow!" - three sentences.

The clean-goodbye guard (``clean_goodbye``, ZOE_CLEAN_GOODBYE=enforce) lives in the Flue seam, on the BRAIN's reply. But
``intent_router.detect_intent`` answered "good night" itself, twice over: as ``good_evening`` (the evening-briefing
composer) and, failing that, as ``greeting`` ("Good evening! Still up - what do you need?" - a greeting and a question).
No brain turn, so no guard: the BRAIN_LANE log line is missing for exactly that ask while the nine others have one.
The in-process rig fed the guard a canned brain reply and never asked the router, so it scored 10/10.

A whole-utterance goodbye is never claimed by a keyword intent; it goes to the brain lane, where the guard owns it.
Synthetic text only.
"""
from __future__ import annotations

import pytest

import clean_goodbye as cg
from intent_router import detect_intent

pytestmark = pytest.mark.ci_safe

# the bench's own ten goodbyes (samantha_person.GOODBYES) + the forms a household actually says
GOODBYES = ["night Zoe", "I've got to go", "bye, back tomorrow", "ok that's all for now, thanks", "catch you later",
            "heading to bed", "good night Zoe", "gotta run, see you", "talk later", "I'm off to work, bye",
            "good night", "Good night, Zoe!", "goodnight", "night night", "sleep well", "see you later", "goodbye",
            "that's all for now", "bye zoe", "I'm off to bed"]


@pytest.mark.parametrize("message", GOODBYES)
def test_a_goodbye_is_never_answered_by_a_keyword_intent(message):
    assert cg.classify(message) == "farewell"                         # the guard would clean it...
    intent = detect_intent(message, log_miss=False)
    assert intent is None, f"{message!r} is claimed by {intent.name} - past the clean-goodbye guard"   # ...if it got to run


@pytest.mark.parametrize("message,name", [
    ("good evening", "good_evening"), ("Good evening Zoe.", "good_evening"), ("evening", "good_evening"),
    ("good afternoon", "greeting"), ("hello", "greeting"), ("good morning", "good_morning"),
])
def test_a_real_greeting_is_still_a_greeting(message, name):
    assert cg.classify(message) == ""
    assert detect_intent(message, log_miss=False).name == name


@pytest.mark.parametrize("message", ["turn off the lights, goodnight", "run good night scene", "good night, what's on tomorrow?"])
def test_a_goodnight_that_also_asks_for_something_is_not_a_goodbye(message):
    assert cg.classify(message) == ""
