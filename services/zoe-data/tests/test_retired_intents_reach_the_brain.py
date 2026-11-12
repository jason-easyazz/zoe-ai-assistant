"""Intents that promised work nothing does now reach the brain instead (gap audit 2026-11-13).

* ``recipe_search`` replied "Looking up a recipe for X." and nothing looked anything up.
* ``ha_full_setup`` / ``build_widget`` / ``build_page`` / ``self_improve`` expanded the user's words into instruction
  blocks for the retired OpenClaw agent (``openclaw_user_message``), which the brain then received in place of what the
  member said.
* ``a2a_federation_status`` read the deleted peer-agent registry.

Each utterance must now give NO fast intent (``None`` = the brain answers), and must not be swallowed by a later
pattern (the recipe and builder guards sit where the old intents were, so "get a recipe for scones" is not a list add).
Invented utterances only. ``ci_safe``.
"""
import asyncio

import pytest

import intent_router
from intent_router import detect_and_extract_intent, detect_intent

pytestmark = pytest.mark.ci_safe

TO_THE_BRAIN = [
    # recipes
    "find me a recipe for banana bread",
    "show me a recipe for lamb curry",
    "get a recipe for scones",
    "look up a recipe to make pad thai",
    "how do i make pancakes",
    "how can i bake sourdough",
    # Home Assistant setup
    "setup home automation",
    "please set up home assistant",
    "home assistant setup",
    "create an automation in home assistant",
    # builders / self-improvement
    "build a widget for the dashboard",
    "add a moon widget",
    "i want a new page for the garden",
    "improve yourself",
    "what are you struggling with",
    # the A2A federation
    "show me the agent federation status",
    "are hermes and openclaw online",
    "list connected agents",
]


@pytest.mark.parametrize("text", TO_THE_BRAIN)
def test_the_utterance_has_no_fast_intent(text):
    assert detect_intent(text) is None
    assert asyncio.run(detect_and_extract_intent(text, "member_1")) is None


def test_ordinary_commands_beside_the_guards_still_match():
    assert detect_intent("get milk").name == "list_add"
    assert detect_intent("add eggs to the shopping list").name == "list_add"


@pytest.mark.parametrize("name", ["recipe_search", "ha_full_setup", "build_widget", "build_page", "self_improve",
                                  "a2a_federation_status"])
def test_the_executor_has_no_branch_for_a_retired_intent(name):
    # Unknown to execute_intent: it declines (None) rather than claiming work.
    assert asyncio.run(intent_router.execute_intent(intent_router.Intent(name, {"query": "scones"}), "member_1")) is None


def test_no_retired_agent_message_expansion_is_left():
    for gone in ("openclaw_user_message", "HA_FULL_SETUP_OPENCLAW_MESSAGE", "_BUILD_WIDGET_OPENCLAW_MSG",
                 "_SELF_IMPROVE_OPENCLAW_MSG"):
        assert not hasattr(intent_router, gone), gone
