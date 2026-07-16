"""Open-domain creative intent routing."""

import pytest

from intent_router import detect_intent, execute_intent

pytestmark = pytest.mark.ci_safe


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
        ("what is the best time to go", "best_time_to_leave"),
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


@pytest.mark.parametrize("text", ["what is the best time to start", "what is the best time to arrive"])
def test_unobserved_time_planning_verbs_do_not_use_arrival_clarification(text: str):
    intent = detect_intent(text)

    assert intent is None or intent.name != "time_planning_clarification"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "response"),
    [
        ("what is the best time to leave", "What time do you need to arrive?"),
        ("what is the meeting time plus travel time", "I need the actual times before I can work that out."),
    ],
)
async def test_ambiguous_time_clarification_executes_as_question(text: str, response: str):
    intent = detect_intent(text)

    assert await execute_intent(intent) == response


@pytest.mark.parametrize("text", ["hi there how are you", "hey zoe how are you?", "hello how are you today"])
def test_greeting_wellbeing_phrases_route_to_fast_greeting(text: str):
    intent = detect_intent(text)

    assert intent is not None
    assert intent.name == "greeting"


@pytest.mark.asyncio
async def test_greeting_wellbeing_executes_without_open_domain_agent():
    intent = detect_intent("hi there how are you")

    response = await execute_intent(intent)

    assert response.startswith(("Hi", "Good"))
    # The greeting has time-of-day variants (morning/afternoon/evening/night), each
    # of which offers assistance in slightly different words. Assert against the full
    # family so the test is deterministic regardless of wall-clock time.
    offers_assistance = any(
        phrase in response.lower()
        for phrase in ("help", "do for you", "what do you need", "what do you want")
    )
    assert offers_assistance, response
