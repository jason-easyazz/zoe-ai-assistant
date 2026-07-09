"""Deterministic propose-on-mention: 'my <rel> <Name>' / '<Name>, my <rel>' →
person_create proposals, reliably (the LLM detector is flaky on a 4B model).
"""
import pytest

pytestmark = pytest.mark.ci_safe

import latent_intent_detector as lid


@pytest.fixture(autouse=True)
def _not_a_contact(monkeypatch):
    async def _no(name, user_id):
        return False
    monkeypatch.setattr(lid, "_already_a_contact", _no)


async def _props(text):
    return await lid._deterministic_person_proposals(text, "u1")


@pytest.mark.parametrize("text,name,rel", [
    ("my brother Daniel just got a new job", "Daniel", "brother"),
    ("My niece Teneeka is coming to visit", "Teneeka", "niece"),
    ("i had lunch with my friend Sarah today", "Sarah", "friend"),
    ("Daniel, my brother, called earlier", "Daniel", "brother"),
    ("my colleague Priya Sharma is helping", "Priya Sharma", "colleague"),
    ("My Brother Daniel is visiting", "Daniel", "brother"),        # capitalised rel
    ("Daniel is my Brother", "Daniel", "brother"),                 # capitalised rel, name-first
])
@pytest.mark.asyncio
async def test_extracts_common_forms(text, name, rel):
    props = await _props(text)
    assert len(props) == 1
    assert props[0]["pre_filled_slots"] == {"name": name, "relationship": rel}
    assert props[0]["action_type"] == "person_create"


@pytest.mark.asyncio
async def test_dedups_within_turn():
    props = await _props("my brother Daniel and later Daniel, my brother again")
    assert len(props) == 1 and props[0]["pre_filled_slots"]["name"] == "Daniel"


@pytest.mark.asyncio
async def test_pronoun_rejected():
    # "my friend He" — He is a pronoun the guard drops
    assert await _props("my friend He is nice") == []


@pytest.mark.asyncio
async def test_no_relationship_word_no_proposal():
    assert await _props("the weather is nice today") == []


@pytest.mark.asyncio
async def test_skips_existing_contact(monkeypatch):
    async def _yes(name, user_id):
        return True
    monkeypatch.setattr(lid, "_already_a_contact", _yes)
    assert await _props("my brother Daniel") == []
