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
    "source?",
    "any links?",
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
    # BARE nouns are ordinary requests, not push-back (regression: these matched)
    "link",
    "links",
    "source",
    "citation",
    "send me the link",
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


def _tools(*names):
    return [{"type": "function", "function": {"name": n, "parameters": {}}} for n in names]


def test_helper_forces_web_search_only():
    """A required tool call must be satisfiable ONLY by web_search — with
    web_browse also offered the model could 'comply' by browsing an invented URL
    instead of actually searching."""
    msg, tools, choice = zoe_agent.apply_verification_challenge(
        "are you sure?", "user msg", _tools("web_search", "web_browse", "calendar_add"), "auto")
    assert choice == "required"
    assert [t["function"]["name"] for t in tools] == ["web_search"]
    assert zoe_agent._VERIFY_DIRECTIVE in msg


def test_helper_is_a_noop_for_ordinary_messages():
    tools_in = _tools("web_search", "calendar_add")
    msg, tools, choice = zoe_agent.apply_verification_challenge(
        "add milk to the shopping list", "user msg", tools_in, "auto")
    assert msg == "user msg" and tools == tools_in and choice == "auto"


def test_helper_degrades_when_web_search_absent():
    """Never force a tool call with nothing usable to call (e.g. creative-writing
    turns strip the tool list entirely)."""
    msg, tools, choice = zoe_agent.apply_verification_challenge(
        "are you sure?", "user msg", [], "auto")
    assert msg == "user msg" and tools == [] and choice == "auto"


def test_both_chat_paths_apply_the_challenge():
    """REGRESSION: the logic first shipped only in the buffered path, so
    'are you sure?' did nothing on the STREAMING path the UI actually uses."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(zoe_agent))
    calls = sum(
        1 for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "apply_verification_challenge"
    )
    # Count real CALL nodes, not text matches — a substring count also matches the
    # `def` line, which made an earlier version of this test pass even with the
    # streaming call deleted (caught by a negative control).
    assert calls >= 2, (
        f"apply_verification_challenge is called {calls}x; it must be called from "
        "BOTH chat prompt-assembly paths (buffered AND streaming)"
    )


def test_force_tool_threshold_tracks_always_on_count():
    """REGRESSION: the force-tool limit was a bare `<= 6` chosen when there were
    3 always-on tools. Adding web_browse lengthened every skill's list, which
    would silently drop skills sitting at the limit back to tool_choice='auto'."""
    assert zoe_agent._FORCE_TOOL_MAX == len(zoe_agent._ALWAYS_ON_TOOLS) + 3


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


def test_bare_nouns_need_a_qualifier_or_question_mark():
    """REGRESSION: 'link' / 'source' / 'citation' alone are ordinary requests.
    They only read as a challenge with a qualifier ('got a source') or a '?'."""
    f = zoe_agent._is_verification_challenge
    for bare in ("link", "links", "source", "sources", "citation"):
        assert f(bare) is False, bare
    for challenge in ("link?", "source?", "got a source?", "any links?", "the citation?"):
        assert f(challenge) is True, challenge


def test_voice_exclusion_is_documented_not_accidental():
    """Voice deliberately does not force verification (latency + spoken citations
    + the replay gate). Keep that rationale in the code so it isn't 'fixed' blindly."""
    import inspect
    doc = inspect.getdoc(zoe_agent.apply_verification_challenge) or ""
    assert "VOICE IS DELIBERATELY EXCLUDED" in doc
