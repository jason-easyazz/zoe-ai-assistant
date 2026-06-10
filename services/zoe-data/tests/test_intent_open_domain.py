"""Open-domain creative intent routing."""

import pytest

from intent_router import detect_intent


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
