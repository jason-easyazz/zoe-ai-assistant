"""classify_query request-frame contract (FIX-PACKET-2026-07-07 item 1).

The fast-path research classifier must fire only on an explicit
request/imperative research frame — never on bare topical substrings
("weekend", "best", "recipe", "price"...). Live bug: "I enjoy hiking on
weekends" and "What do I do on weekends?" were both hijacked into the
research-brief follow-up before the brain saw them.

ci_safe: ``research_evidence`` imports only stdlib plus the stdlib-only
``agent_safety`` module, so this collects in the slim GitHub lane.
"""

import pytest

pytestmark = pytest.mark.ci_safe  # GitHub-CI opt-in: runs in validate.yml's `-m ci_safe` lane

from research_evidence import classify_query


@pytest.mark.parametrize(
    "message",
    [
        # First-person statements — bare markers must not hijack (live bug).
        "I enjoy hiking on weekends",
        "I love a good pizza recipe",
        "I went to the bottle shop yesterday",
        "I'm happy with the deal I got on my flight",
        "I like to compare notes with my sister",
        "I look up to her",  # "look up" frame must not fire on "look up to"
        "look up to your elders",  # imperative opener also guarded against "look up to"
        # Self-recall questions about the user's own life — memory turf.
        "What do I do on weekends?",
        "what did I say about the weekend?",
        "when did I book my flight to Bali?",
        "where do I usually get coffee?",
        # Conversational asks without a research frame.
        "Can you help me tidy this note?",
        "that price seems fine, thanks",
        "",
    ],
)
def test_non_research_messages_stay_general(message):
    assert classify_query(message) == "general"


@pytest.mark.parametrize(
    "message",
    [
        # Packet true positives.
        "find me the cheapest flight to Bali",
        "best bottle shop deals this weekend",
        "compare NBN plans",
        # Bare "best X near Y" elliptical ask — deliberately research: a
        # message *opening* with best/cheapest/top is a search-style query,
        # while mid-sentence "best" ("I went to the best bottle shop") is not.
        "best pizza near Geraldton",
        # Frame variants.
        "search for events in Geraldton this weekend",
        "look up flight prices to Bali",
        "can you find me a good pizza recipe",
        "could you recommend a plumber",
        "recommend a good bottle shop near me",
        "where can I buy cheap cat food",
        "what's the cheapest NBN plan with unlimited data",
        # Request verb wins over a first-person opener.
        "I love it, but can you find me a cheaper one?",
    ],
)
def test_research_requires_request_frame(message):
    assert classify_query(message) == "research"


@pytest.mark.parametrize(
    "message",
    [
        "what is the capital of France",
        "What is photosynthesis",
        "who is the premier of WA",
    ],
)
def test_short_factual_questions_stay_simple_factual(message):
    assert classify_query(message) == "simple_factual"
