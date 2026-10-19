"""Caregiver curation BY VOICE (memories-space P7): "Zoe, remember that Mia\x27s swimming is on Tuesdays" from a curator in their OWN
panel identity (``memories_voice._curate`` -> ``memory_curation.by_voice``). VOICE-PATH (inside ``fast_tiers._provenance_tier``).

Synthetic members only (the fixture of ``test_memory_curation``). Walls, each with a control: a child, a guest, a rejected speaker,
the chat lane and a dry turn never curate (the existing teach path runs: ``None``); a sensitive note is refused aloud without the
opt-in; the confirmation never repeats the note; flag off is inert.
"""
from __future__ import annotations

import asyncio

import pytest

import memories_voice as mv
import memory_curation as mc
from test_memory_curation import ADULT, KID, KID2, PARENT, H, as_, env, rows_of  # noqa: F401 - env is the shared fixture

pytestmark = pytest.mark.ci_safe
PANEL = "panel-kitchen"


def say(text, member, *, verified=None, channel="voice", allow_writes=True, panel=PANEL):
    ctx = dict(panel_id=panel)
    if member is not None:
        ctx["scope_user"] = member
    if verified is not None:
        ctx["speaker_verified"] = verified
    out = asyncio.run(mv.tier(text, "guest", "s-voice", channel=channel, extra_ctx=ctx, allow_writes=allow_writes))
    return out.reply if out is not None else None


def test_a_curator_adds_a_note_for_a_child_by_voice(env):
    assert say("Zoe, remember that Mia\x27s swimming is on Tuesdays", PARENT) == "Okay, I\x27ll remember that for Mia."
    (row,) = rows_of(env, KID)
    md = row.metadata
    assert row.text == "User\x27s swimming is on Tuesdays" and md["origin"] == "curator"
    assert md["curated_via"] == "voice" and md["curated_category"] == "routines" and md["curated_by"] == PARENT
    assert not rows_of(env, PARENT), "never filed in the curator own memory"


def test_a_child_a_guest_a_rejected_voice_chat_and_a_dry_turn_never_curate(env):
    text = "remember that Leo\x27s swimming is on Tuesdays"
    assert say(text, KID) is None                                     # a child: their own teach path, unchanged
    assert say(text, None) is None                                    # nobody identified on the panel
    assert say(text, PARENT, verified=False) is None                  # the speaker gate said not the curator
    assert say(text, PARENT, channel="chat") is None                  # chat is not this tier
    assert say(text, PARENT, allow_writes=False) is None              # a dry turn writes nothing
    assert say(text, ADULT) is None                                   # an adult, not an admin
    assert not rows_of(env, KID2)
    assert say(text, PARENT) == "Okay, I\x27ll remember that for Leo."  # control: the curator
    assert len(rows_of(env, KID2)) == 1


def test_a_sensitive_note_is_refused_aloud_without_the_opt_in(env):
    out = say("remember that Mia is allergic to peanuts", PARENT)
    assert out == "Health notes for Mia are off. Turn them on first."
    assert not rows_of(env, KID)
    as_(env, PARENT, "admin").put(f"/api/memories/curation/members/{KID}/sensitive/health", json=dict(on=True), headers=H)
    out = say("remember that Mia is allergic to peanuts", PARENT)
    assert out == "Okay, I\x27ll remember that for Mia." and "peanut" not in out
    assert rows_of(env, KID)[0].metadata["curated_category"] == "health"


def test_not_a_curation_turn_falls_through(env):
    for text in ("remember that I have a dentist appointment on Friday", "remember to buy milk",
                 "remember that Mum\x27s birthday is in May", "what do you know about me?"):
        assert say(text, PARENT) is None, text
    assert not rows_of(env, KID) and not rows_of(env, PARENT)


def test_an_adult_only_by_their_own_opt_in(env):
    assert say("remember that Robin\x27s bins go out on Monday night", PARENT) is None      # not curatable: the teach path runs
    as_(env, ADULT, "user").put("/api/memories/curation/me/routines", json=dict(on=True), headers=H)
    assert say("remember that Robin\x27s bins go out every Monday night", PARENT) == "Okay, I\x27ll remember that for Robin."


def test_flag_off_is_inert(env, monkeypatch):
    monkeypatch.delenv("ZOE_MEMORIES_CURATORS")
    assert say("remember that Mia\x27s swimming is on Tuesdays", PARENT) is None
    assert not rows_of(env, KID)


def test_parse_and_category_are_pure():
    assert mc.parse_voice("Hey Zoe, can you remember that Mia’s teacher is Ms Lee.") == ("Mia", "Mia’s teacher is Ms Lee")
    assert mc.parse_voice("remember that I like tea") is None
    pol = mc.Policy(mc.Account("k", "Mia", "child", "child"), frozenset(mc.CATEGORIES))
    assert mc.guess_category("Mia\x27s teacher is Ms Lee", pol) == "school"
    assert mc.guess_category("Mia takes ventolin before sport", pol) == "medication"
    assert mc.guess_category("Mia is allergic to peanuts", pol) == "health"
