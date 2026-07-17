"""Fast-path coverage: common utterances answered instantly, never the slow brain.

Critically also asserts the new instant patterns do NOT swallow real requests
that genuinely need the brain (the ^...$ anchoring must hold).
"""
from __future__ import annotations

import pytest

from intent_router import Intent, detect_intent, execute_intent

pytestmark = pytest.mark.ci_safe


def _name(text: str) -> str | None:
    i = detect_intent(text, log_miss=False)
    return i.name if i else None


@pytest.mark.parametrize("text,expected", [
    # time paraphrases that previously fell through
    ("do you have the time", "time_query"),
    ("give me the time", "time_query"),
    ("what hour is it", "time_query"),
    ("what time is it now", "time_query"),
    # date paraphrases
    ("show me the date", "date_query"),
    ("tell me today's date", "date_query"),
    ("what's today", "date_query"),
    ("what day is it today", "date_query"),
    # greetings
    ("hiya", "greeting"),
    ("g'day", "greeting"),
    ("how's it going", "greeting"),
    # thanks / acknowledgements
    ("thanks", "acknowledgement"),
    ("thank you so much", "acknowledgement"),
    ("cheers", "acknowledgement"),
    ("ty", "acknowledgement"),
    ("got it", "acknowledgement"),
    ("okay", "acknowledgement"),
    ("cool", "acknowledgement"),
    ("perfect", "acknowledgement"),
    ("sounds good", "acknowledgement"),
    ("no worries", "acknowledgement"),
    # presence checks
    ("are you there", "status_check"),
    ("you there?", "status_check"),
    ("can you hear me", "status_check"),
    ("still there", "status_check"),
    ("zoe?", "status_check"),
])
def test_instant_intents_match(text, expected):
    assert _name(text) == expected, f"{text!r} -> {_name(text)!r}, expected {expected}"


@pytest.mark.parametrize("text", [
    # acknowledgement words that LEAD a real request must NOT be canned-replied
    "okay add milk to my shopping list",
    "thanks but what's the weather tomorrow",
    "great idea, let's build a budget tracker",
    "cool can you set a timer for ten minutes",
    "perfect, remind me at 5pm",
    # presence-ish words that are real requests
    "are you free tomorrow at 3",
    "is it going to rain today",
    "are you able to book a table",
    # time/date planning that needs reasoning, not a clock read
    "what's the best time to leave for the airport",
    "what's today's weather",
])
def test_real_requests_not_short_circuited(text):
    n = _name(text)
    assert n not in ("acknowledgement", "status_check"), f"{text!r} wrongly caught as {n}"
    # also must not be reduced to a bare clock read
    assert n not in ("time_query", "date_query"), f"{text!r} -> {n}"


@pytest.mark.asyncio
@pytest.mark.parametrize("intent,expect_in", [
    (Intent("acknowledgement", {"kind": "thanks"}), {"You're welcome!", "Anytime.", "Happy to help.", "No worries."}),
    (Intent("acknowledgement", {"kind": "ack"}), {"Got it.", "Sure thing.", "Okay.", "No problem."}),
    (Intent("status_check", {}), {"I'm here.", "Still here.", "Listening.", "Ready when you are."}),
])
async def test_canned_replies(intent, expect_in):
    reply = await execute_intent(intent, user_id="family-admin")
    assert reply in expect_in, f"{intent.name}/{intent.slots} -> {reply!r}"
