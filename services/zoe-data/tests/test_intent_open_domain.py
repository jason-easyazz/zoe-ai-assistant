"""Open-domain creative intent routing."""

import pytest

from intent_router import detect_intent, execute_intent


@pytest.mark.parametrize("text", ["Tell me a joke.", "Tell me a joke", "Tell me another joke.", "make me laugh", "do you have any jokes?", "have you got any jokes?", "know any good jokes?"])
def test_joke_requests_route_to_open_domain_agent(text: str):
    intent = detect_intent(text)

    assert intent is not None
    assert intent.name == "extend_capability"
    assert intent.slots == {"raw": text}


@pytest.mark.parametrize("text", ["Say exactly: Zoe chat integration ok", "Say exactly Zoe chat integration ok"])
def test_say_exactly_routes_to_open_domain_agent(text: str):
    intent = detect_intent(text)

    assert intent is not None
    assert intent.name == "extend_capability"
    assert intent.slots == {"raw": text}


def test_bare_say_exactly_does_not_route_to_open_domain_agent():
    assert detect_intent("say exactly") is None


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("what is the best time to leave", "best_time_to_leave"),
        ("the best time to head out", "best_time_to_leave"),
        ("what is the meeting time plus travel time", "time_math"),
    ],
)
def test_ambiguous_time_phrases_route_to_clarification(text: str, kind: str):
    intent = detect_intent(text)

    assert intent is not None
    assert intent.name == "time_planning_clarification"
    assert intent.slots == {"kind": kind}


def test_exact_clock_queries_still_route_to_clock_intents():
    assert detect_intent("what time is it").name == "time_query"
    assert detect_intent("what is the date").name == "date_query"


@pytest.mark.asyncio
async def test_ambiguous_time_clarification_executes_as_question():
    intent = detect_intent("what is the best time to leave")

    response = await execute_intent(intent)

    assert response == "What time do you need to arrive?"
