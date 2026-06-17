from __future__ import annotations

import zoe_agent


def test_fast_response_answers_known_interesting_fact_topic() -> None:
    response = zoe_agent._check_fast_response("tell me something interesting about oceans")

    assert response is not None
    assert "oxygen" in response.lower()
    assert "phytoplankton" in response.lower()


def test_fast_response_unknown_interesting_fact_topic_falls_through() -> None:
    assert zoe_agent._check_fast_response("tell me something interesting about orbital harmonics") is None


def test_fast_response_generic_interesting_fact_uses_safe_default() -> None:
    response = zoe_agent._check_fast_response("give me a fun fact")

    assert response is not None
    assert "ocean" in response.lower()


def test_fast_response_answers_singular_ocean_topic() -> None:
    response = zoe_agent._check_fast_response("tell me a fun fact about ocean")

    assert response is not None
    assert "oxygen" in response.lower()



def test_fast_response_answers_zoe_identity_prompt() -> None:
    response = zoe_agent._check_fast_response("who are you")

    assert response is not None
    assert response.startswith("I'm Zoe")
    assert "local assistant" in response


def test_fast_response_answers_capability_prompt() -> None:
    response = zoe_agent._check_fast_response("what can you do")

    assert response is not None
    assert "reminders" in response.lower()
    assert "weather" in response.lower()


def test_fast_response_leaves_open_howto_to_model() -> None:
    assert zoe_agent._check_fast_response("how do i boil an egg") is None
