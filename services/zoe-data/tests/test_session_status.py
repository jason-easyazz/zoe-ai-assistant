"""session_status: "who is logged in / am I signed in" is answered from the resolved identity of the turn.

Live 2026-10-14: a spoken who-is-logged-in question was routed to the lists domain and the brain said it had no access. These
tests pin the deterministic voice-lane answer: a member hears their OWN name, a guest hears that nobody is signed in and never a
member's name, and look-alikes (a service login, a history question, any other "who") are untouched.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import session_status as ss  # noqa: E402

pytestmark = pytest.mark.ci_safe

POSITIVE = [
    "who is logged in",
    "who's logged in",
    "who's signed in on this panel",
    "who is signed in on the panel right now",
    "hey zoe, who's currently logged in?",
    "who is logged in here",
    "who is signed in to this device",
    "who are logged in",
    "who's using this panel",
    "who is using the screen right now",
    "am I logged in",
    "am I signed in?",
    "am I still signed in on this panel",
    "am i currently logged in right now",
    "is anyone signed in",
    "is somebody logged in here",
    "who am I signed in as",
    "who am I logged in as right now",
    "who am I",
    "who are you talking to",
    "who are you talking to right now",
    "which account am I using",
    "what profile is this",
    "whose account is this",
    "tell me who's logged in",
    "can you tell me who's signed in on this panel",
]

NEGATIVE = [
    "who is the singer",
    "who sings this song",
    "log in to Spotify",
    "sign me in",
    "sign me out",
    "who's logged in to Spotify",
    "am I logged in to Netflix",
    "am I signed in to my email",
    "who logged in last night",
    "who signed the declaration of independence",
    "who am I meeting tomorrow",
    "do you know who I am",
    "who is at the door",
    "who's on the phone",
    "who's coming to dinner",
    "is anyone home",
    "is anyone coming tomorrow",
    "what's my name",
    "what is the weather",
    "who is signed up for the newsletter",
    "",
    "who is logged in to the router and what is its address and also please tell me about the weather in perth tomorrow thanks",
]


@pytest.mark.parametrize("text", POSITIVE)
def test_invented_phrasings_are_recognised(text):
    assert ss.question_kind(text), text


@pytest.mark.parametrize("text", NEGATIVE)
def test_hard_negatives_are_left_alone(text):
    assert ss.question_kind(text) == "", text


def _patch_identity(monkeypatch, names):
    import identity_facts as idf

    async def resolve(uid, db=None, *, budget_s=None):
        name = names.get(uid)
        return idf.Identity(user_id=uid, name=name, account_name=name) if name else None

    monkeypatch.setattr(idf, "resolve_identity", resolve)


@pytest.mark.asyncio
async def test_member_hears_their_own_name(monkeypatch):
    _patch_identity(monkeypatch, {"u1": "Maya"})
    reply = await ss.answer("who is logged in", "u1", channel="voice", extra_ctx={"scope_user": "u1", "panel_id": "p1"})
    assert reply == "You're signed in as Maya."
    assert "Maya" in (await ss.answer("am I signed in", "u1", channel="voice", extra_ctx={"scope_user": "u1"}))


@pytest.mark.asyncio
async def test_guest_hears_nobody_is_signed_in_and_no_names(monkeypatch):
    _patch_identity(monkeypatch, {"u1": "Maya", "u2": "Theo"})
    for ctx in ({"scope_user": None, "panel_id": "p1"}, {"scope_user": "guest"}, {"scope_user": ""}):
        reply = await ss.answer("who's signed in on this panel", "guest", channel="voice", extra_ctx=ctx)
        assert reply == ss.GUEST_REPLY
        assert "Maya" not in reply and "Theo" not in reply
    # the identity of the TURN wins: a registered user_id on the call does not make a guest turn a member
    reply = await ss.answer("who is logged in", "u1", channel="voice", extra_ctx={"scope_user": None})
    assert reply == ss.GUEST_REPLY and "Maya" not in reply


@pytest.mark.asyncio
async def test_without_a_scope_key_the_account_id_is_the_identity(monkeypatch):
    """The replay harness passes no scope_user: a real account is a member, a sentinel is a guest."""
    _patch_identity(monkeypatch, {"u1": "Maya"})
    assert await ss.answer("who am I", "u1", channel="voice", extra_ctx=None) == "You're signed in as Maya."
    assert await ss.answer("who am I", "voice-guest", channel="voice", extra_ctx=None) == ss.GUEST_REPLY
    assert await ss.answer("who am I", "guest", channel="voice", extra_ctx={"panel_id": "p1"}) == ss.GUEST_REPLY


@pytest.mark.asyncio
async def test_other_channels_and_other_questions_return_none(monkeypatch):
    _patch_identity(monkeypatch, {"u1": "Maya"})
    for channel in ("chat", "telegram", "livekit", None):
        assert await ss.answer("who is logged in", "u1", channel=channel, extra_ctx={"scope_user": "u1"}) is None
    assert await ss.answer("who is the singer", "u1", channel="voice", extra_ctx={"scope_user": "u1"}) is None
    assert await ss.answer("log in to Spotify", "u1", channel="voice", extra_ctx={"scope_user": "u1"}) is None


@pytest.mark.asyncio
async def test_a_lookup_failure_never_breaks_the_turn(monkeypatch):
    import identity_facts as idf
    import voice_session as vs

    async def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(idf, "resolve_identity", boom)
    monkeypatch.setattr(vs, "display_name", boom)
    assert await ss.answer("who is logged in", "u1", channel="voice", extra_ctx={"scope_user": "u1"}) == "You're signed in."


@pytest.mark.asyncio
async def test_voice_session_time_left_is_mentioned_only_for_that_member(monkeypatch):
    import voice_session as vs

    _patch_identity(monkeypatch, {"u1": "Maya", "u2": "Theo"})
    monkeypatch.setenv("ZOE_VOICE_SESSION", "1")
    monkeypatch.setenv("ZOE_VOICE_SESSION_TTL_S", "300")
    clock = [1000.0]
    monkeypatch.setattr(vs, "_clock", lambda: clock[0])
    monkeypatch.setattr(vs, "_arm_timer", lambda *a, **k: None)
    vs.reset()
    try:
        vs._open_or_renew("p1", "u1", clock[0], "test")
        clock[0] += 60
        assert abs(vs.seconds_left("p1") - 240) < 1
        reply = await ss.answer("who is logged in", "u1", channel="voice", extra_ctx={"scope_user": "u1", "panel_id": "p1"})
        assert reply.startswith("You're signed in as Maya.") and "4 minutes left" in reply
        # another member (PIN session) on the same panel does not borrow the voice session's clock
        other = await ss.answer("who is logged in", "u2", channel="voice", extra_ctx={"scope_user": "u2", "panel_id": "p1"})
        assert other == "You're signed in as Theo."
        clock[0] += 400
        assert vs.seconds_left("p1") is None
        assert await ss.answer("who is logged in", "u1", channel="voice", extra_ctx={"scope_user": "u1", "panel_id": "p1"}) == "You're signed in as Maya."
    finally:
        vs.reset()


@pytest.mark.asyncio
async def test_seconds_left_is_none_when_the_flag_is_off(monkeypatch):
    import voice_session as vs

    monkeypatch.delenv("ZOE_VOICE_SESSION", raising=False)
    assert vs.seconds_left("p1") is None


@pytest.mark.asyncio
async def test_the_fast_tier_wrapper_answers_before_the_router_and_leaves_other_turns(monkeypatch):
    """The hook fires in fast_tiers.resolve (the wrapper every lane uses) before any domain routing."""
    import fast_tiers as ft

    async def none(*a, **k):
        return None

    for name in ("_distress_tier", "_feedback_tier", "_provenance_tier"):
        monkeypatch.setattr(ft, name, none)
    import self_model as sm
    import voice_enrollment as ve

    monkeypatch.setattr(sm, "tier", none)
    monkeypatch.setattr(ve, "tier", none)
    _patch_identity(monkeypatch, {"u1": "Maya"})
    reached = []

    async def core(text, user_id, session_id, **kw):
        reached.append(text)
        return None

    monkeypatch.setattr(ft, "_resolve_core", core)
    res = await ft.resolve("who is logged in", "u1", "s1", channel="voice",
                           extra_ctx={"scope_user": "u1", "panel_id": "p1"}, phase="conversation")
    assert res is not None and res.reply == "You're signed in as Maya." and res.tier == "session_status"
    assert reached == []
    res = await ft.resolve("who's logged in", "guest", "s1", channel="voice",
                           extra_ctx={"scope_user": None, "panel_id": "p1"}, phase="conversation")
    assert res.reply == ss.GUEST_REPLY
    for text in ("who is the singer", "log in to Spotify"):
        assert await ft.resolve(text, "u1", "s1", channel="voice", extra_ctx={"scope_user": "u1"}, phase="conversation") is None
    assert reached == ["who is the singer", "log in to Spotify"]
    # the domain half of a split run never repeats the conversation tiers
    reached.clear()
    assert await ft.resolve("who is logged in", "u1", "s1", channel="voice", extra_ctx={"scope_user": "u1"}, phase="domain") is None
    assert reached == ["who is logged in"]


def test_voice_lane_hands_the_turn_scope_identity_to_the_conversation_tiers():
    """The scope identity (never the raw effective user) is what the tier answers from."""
    src = (ROOT / "routers" / "voice_tts.py").read_text()
    i = src.index('phase="conversation",')
    assert '"scope_user": _scope_identity_user' in src[i - 400:i]
