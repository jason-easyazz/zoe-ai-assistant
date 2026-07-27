"""'Are you sure?' -> forced, cited web check.

When the user pushes back on a claim Zoe just made, she must go and CHECK it
against live sources and cite them — not restate herself more confidently.

The false-POSITIVE cases matter most: hijacking an ordinary message into a web
search would be worse than missing a challenge, so the detector is deliberately
narrow (short, standalone push-backs only).
"""
import pytest

import zoe_agent

pytestmark = pytest.mark.ci_safe


@pytest.mark.parametrize("msg", [
    "are you sure?",
    "Are you sure",
    "are you really sure?",
    "are u sure?",
    "you sure?",
    "sure?",
    "really?",
    "really??",
    "is that right?",
    "is that true?",
    "is that correct?",
    "that's not right",
    "prove it",
    "verify that",
    "back it up",
    "got a source?",
    "any sources?",
    "the link?",
    "citation?",
    "where did you get that?",
    "where did u hear that",
    "says who?",
    "according to what?",
])
def test_detects_a_challenge(msg):
    assert zoe_agent._is_verification_challenge(msg) is True, msg


@pytest.mark.parametrize("msg", [
    # "sure" used as agreement or as an ordinary verb — must NOT trigger
    "sure, go ahead",
    "sure thing",
    "make sure the front light is off",
    "can you make sure the door is locked",
    "I'm sure it's fine",
    # ordinary questions that merely resemble the pattern
    "is that the right room for the heater?",
    "really good idea, thanks",
    "what time is it?",
    "prove it was a great match wasn't it",
    # a long message that happens to contain doubt is a NEW question, not a challenge
    "are you sure that the flight to Bali is cheaper on Tuesday, because I was "
    "reading that prices change a lot in the school holidays and I want to book soon",
    # empty / junk
    "",
    "   ",
])
def test_does_not_hijack_ordinary_messages(msg):
    assert zoe_agent._is_verification_challenge(msg) is False, msg


def test_long_messages_are_never_challenges():
    """Length cap: a challenge is a short push-back; anything longer is a new ask."""
    assert zoe_agent._is_verification_challenge("are you sure?" + " x" * 100) is False


def test_directive_demands_a_search_and_a_citation():
    d = zoe_agent._VERIFY_DIRECTIVE
    assert "web_search" in d
    assert "cite" in d.lower()
    # must permit an honest "couldn't verify" rather than forcing a confident answer
    assert "could not verify" in d.lower()
    # and must forbid simply repeating the claim
    assert "not simply repeat" in d.lower() or "do not simply repeat" in d.lower()


def test_verification_tools_exist_to_be_forced():
    """The forced-tool narrowing only works if these are in the schema."""
    names = [
        t["function"]["name"]
        for t in getattr(zoe_agent, "TOOLS", getattr(zoe_agent, "_TOOLS", []))
        if isinstance(t, dict) and "function" in t
    ]
    assert "web_search" in names and "web_browse" in names
    # both are always-on, so they are present on every chat turn to be narrowed to
    assert "web_search" in zoe_agent._ALWAYS_ON_TOOLS
