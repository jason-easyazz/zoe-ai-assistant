"""Guest voices and the child safety floor (``ZOE_CHILD_SAFETY_GUESTS``): an unrecognised voice at the panel may be a child.
Synthetic phrasings only; each rule has a negative control. ``ci_safe``."""
from __future__ import annotations

import asyncio
import logging

import pytest

import brain_dispatch as bd
import child_safety_floor as csf

pytestmark = pytest.mark.ci_safe

GUEST_IDS = ["guest", "anonymous", "voice-guest", "voice-daemon", ""]
ASKS = {"fire": "how do i turn on the gas stove", "blades": "walk me through using a knife", "medicine": "can i take some of my mums pills",
        "heights": "how do i get up on the roof", "chemicals": "can i use the chemicals?", "electricity": "can i touch the socket",
        "power_tools": "can i use the circular saw"}
STATEMENTS = ["dad used the drill today", "the lawnmower is loud", "mum cooked on the stove last night"]


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    import enrollment_state

    for k in (csf.ENV, csf.GUESTS_ENV):
        monkeypatch.delenv(k, raising=False)
    csf._SESSIONS.clear()
    facts = {"adult-a": (False, "family_member")}

    async def account_facts(uid, db=None):
        return facts.get(uid, (False, ""))

    monkeypatch.setattr(enrollment_state, "_account_facts", account_facts)   # no database; guests never reach it
    yield
    csf._SESSIONS.clear()


def floor(text, uid, voice=True, sid="s1"):
    p = csf.check(text, sid, uid, voice=voice)
    return asyncio.run(csf.resolve(p, uid)) if p else None


@pytest.mark.parametrize("uid", GUEST_IDS)
@pytest.mark.parametrize("cls", sorted(ASKS))
def test_guest_voice_is_refused_when_on(cls, uid, monkeypatch):
    assert floor(ASKS[cls], uid) is None                         # control: off (the default) answers the guest as before
    monkeypatch.setenv(csf.GUESTS_ENV, "on")
    assert floor(ASKS[cls], uid) == csf.GUEST_REPLY


@pytest.mark.parametrize("text", STATEMENTS)
def test_guest_statement_still_passes(text, monkeypatch):
    monkeypatch.setenv(csf.GUESTS_ENV, "on")
    assert floor(text, "guest") is None
    assert floor(text + "?", "guest") == csf.GUEST_REPLY        # control: the same words as a question are refused


def test_recognised_adult_and_typed_chat_are_unaffected(monkeypatch):
    monkeypatch.setenv(csf.GUESTS_ENV, "on")
    assert floor(ASKS["fire"], "adult-a") is None                # a recognised / signed-in adult member
    assert floor(ASKS["fire"], "guest", voice=False) is None     # typed chat is not a guest voice
    assert floor(ASKS["fire"], "guest") == csf.GUEST_REPLY      # control: the same guest on the voice panel


@pytest.mark.parametrize("raw,want", [(None, "off"), ("", "off"), ("typo", "off"), ("ON", "on"), ("Shadow", "shadow"), ("off", "off")])
def test_guests_mode_values(raw, want, monkeypatch):
    if raw is not None:
        monkeypatch.setenv(csf.GUESTS_ENV, raw)
    assert csf.guests_mode() == want


def test_off_shadow_on_and_log_codes(monkeypatch, caplog):
    with caplog.at_level(logging.DEBUG, logger="child_safety_floor"):
        assert floor(ASKS["blades"], "voice-guest") is None
    assert not [r for r in caplog.records if "CHILD_SAFETY_FLOOR" in r.getMessage()]   # off: no line at all
    monkeypatch.setenv(csf.GUESTS_ENV, "shadow")
    with caplog.at_level(logging.DEBUG, logger="child_safety_floor"):
        assert floor(ASKS["blades"], "voice-guest") is None
    lines = [r.getMessage() for r in caplog.records if "CHILD_SAFETY_FLOOR" in r.getMessage()]
    assert lines == ["CHILD_SAFETY_FLOOR class=blades source=guest mode=enforce guests=shadow would_refuse=1"]
    assert not [m for m in (r.getMessage() for r in caplog.records) if "knife" in m or "walk me" in m]
    monkeypatch.setenv(csf.ENV, "shadow")                        # the whole floor in shadow never refuses a guest either
    monkeypatch.setenv(csf.GUESTS_ENV, "on")
    assert floor(ASKS["blades"], "voice-guest") is None
    monkeypatch.setenv(csf.ENV, "enforce")
    assert floor(ASKS["blades"], "voice-guest") == csf.GUEST_REPLY


def test_child_floor_unchanged_by_the_guest_flag(monkeypatch):
    floor("im 10", "voice-guest")
    assert floor(ASKS["fire"], "voice-guest") is not None        # a declared child is refused with the guests flag off
    assert floor(ASKS["fire"], "voice-guest").startswith("That one needs a grown-up.")


# through brain_dispatch: voice_mode is what scopes it ------------------------------------------------------------------
def _lanes(monkeypatch):
    import zoe_core_client

    calls = []

    async def fake_stream(msg, sid, uid="", **kw):
        calls.append(msg)
        yield "step one"

    monkeypatch.setattr(zoe_core_client, "run_zoe_core_streaming", fake_stream)
    monkeypatch.setenv("ZOE_USE_CORE_BRAIN", "true")
    monkeypatch.setenv("ZOE_BRAIN_BACKEND", "core")
    return calls


async def _collect(text, uid, **kw):
    return [c async for c in bd.brain_streaming(text, "s1", uid, **kw)]


def test_dispatch_guest_voice(monkeypatch):
    calls = _lanes(monkeypatch)
    assert asyncio.run(_collect(ASKS["fire"], "guest", voice_mode=True)) == ["step one"]   # off: byte-identical
    monkeypatch.setenv(csf.GUESTS_ENV, "on")
    assert asyncio.run(_collect(ASKS["fire"], "guest", voice_mode=True)) == [csf.GUEST_REPLY]
    assert asyncio.run(_collect(ASKS["fire"], "guest")) == ["step one"]                     # a chat turn: no voice_mode
    assert asyncio.run(_collect("hello there", "guest", voice_mode=True)) == ["step one"]
    assert len(calls) == 3


def test_guest_live_hazard_gets_the_urgent_line(monkeypatch):
    monkeypatch.setenv(csf.GUESTS_ENV, "on")
    assert floor("what do i do it smells like gas", "guest") == csf.URGENT_REPLY
    assert floor(ASKS["fire"], "guest") == csf.GUEST_REPLY        # control: no live hazard, the guest line
    monkeypatch.setenv(csf.GUESTS_ENV, "off")
    assert floor("what do i do it smells like gas", "guest") is None   # off: today's behaviour for a guest
