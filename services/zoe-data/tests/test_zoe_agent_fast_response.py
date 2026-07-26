from __future__ import annotations

from datetime import datetime, timezone
import types

import pytest

import memory_service
import zoe_agent

pytestmark = pytest.mark.ci_safe


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


@pytest.mark.asyncio
async def test_mempalace_emotional_offset_metadata_gets_age_label(monkeypatch) -> None:
    added_at = datetime.now(timezone.utc).isoformat()
    ref = types.SimpleNamespace(
        text="Jason felt proud after fixing the calendar.",
        metadata={
            "memory_type": "emotional_moment",
            "tags": "emotional",
            "added_at": added_at,
        },
    )

    class FakeMemoryService:
        async def load_for_prompt(self, user_id, limit=20):
            return [ref]

    zoe_agent._USER_FACTS_CACHE.clear()
    monkeypatch.setattr(memory_service, "is_guest_memory_user", lambda user_id: False)
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: FakeMemoryService())

    packet = await zoe_agent._mempalace_load_user_facts("jason")

    assert "## Recent emotional moments:" in packet
    assert "- [today] Jason felt proud after fixing the calendar." in packet
