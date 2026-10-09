"""The wave-1 voice batch composes: ONE session, ONE user, every tier and guard that landed together runs on its own turn
and none of them eats another's (the batch merged #1932 ask-to-remember, #1936 hold-the-fact / ask-when-ambiguous /
clean-goodbye, #1937 restraint and #1938 provenance answers on the same two seams: ``fast_tiers.resolve`` and
``zoe_flue_client.run_flue_brain_streaming``).

Order the batch promises (and this pins, with a break-the-fix control per link):

  resolve():  provenance wrapper -> conversation-quality -> pull -> person half (hold / ask-when-ambiguous)
              -> identity -> ask-to-remember -> router ...   and the wrapper RECORDS every other tier's reply, so
              "Why did you say that?" after a deterministic reply says so instead of explaining the wrong reply;
  seam:       clarification rewrite -> spoken mute (restraint) -> brain -> clean goodbye.

Synthetic names; fake Chroma; no network. Jetson lane (unmarked, like test_person_half_wiring).
"""
from __future__ import annotations

import asyncio

import pytest

import ask_to_remember as atr
import ask_when_ambiguous as awa
import clean_goodbye as cg
import fast_tiers
import hold_the_fact as htf
import memory_provenance as mp
import memory_service
import provenance_answers as pa
import restraint
import zoe_flue_client as zc
from test_person_half_wiring import A, PUSH, Q, ROW, UID, _history, _put, svc  # noqa: F401 - the real-service fixture
from test_restraint import env  # noqa: F401 - the real migrations on a SQLite file behind db_compat (the mute store)

SID = "wave1-compose"


def _run(coro):
    return asyncio.run(coro)


async def _collect(agen):
    return [d async for d in agen]


def _resolve(text, sid=SID):
    return _run(fast_tiers.resolve(text, UID, sid, channel="chat", router_decision=None))


@pytest.fixture(autouse=True)
def _wave1_env(monkeypatch, svc, env):          # after svc + env: their fixtures set/clear the flags they know
    import expert_dispatch
    import semantic_router

    monkeypatch.setattr(expert_dispatch, "is_enabled", lambda: True)
    monkeypatch.setattr(semantic_router, "is_enabled", lambda: False)          # past the tiers nothing answers
    for k in (atr.ENV, "ZOE_MEMORY_PROVENANCE_ANSWERS"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv(htf.ENV, "enforce")
    monkeypatch.setenv(awa.ENV, "enforce")
    monkeypatch.setenv(cg.ENV, "enforce")
    monkeypatch.setenv("ZOE_RESTRAINT", "enforce")
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: svc)      # env swapped in its own stub
    mp.reset()
    awa.forget_roster()
    yield
    mp.reset()


def test_one_session_runs_ask_to_remember_hold_provenance_and_the_seam_guards_in_turn(svc, monkeypatch):
    # 1. the owner's explicit ask is the deterministic write tier's, and the wrapper notes who answered
    saved = _resolve("Remember that my favourite tea is lapsang souchong.")
    assert saved is not None and saved.tier == "ask_to_remember"
    assert [r.text for r in svc._metadata_read(UID, 50)] == ["my favourite tea is lapsang souchong"]

    # 2. an owner-stated fact is HELD against a bare pushback - the same wrapper, a different tier, no write
    _put(svc, ROW, "voice_fact")
    _history(monkeypatch, ("user", Q), ("assistant", A))
    held = _resolve(PUSH)
    assert held is not None and held.tier == "hold_the_fact"
    assert held.reply.startswith("I've got your dentist appointment down as Friday")

    # 3. "Why did you say that?" is the provenance tier's (it runs FIRST) and explains a deterministic reply as one
    why = _resolve("Why did you say that?")
    assert why is not None and why.tier == "provenance" and why.reply != pa.UNKNOWN_REPLY

    # 4. the person-half question tier still asks before the router
    people = [awa.Candidate("1", "Marisol Vance", "sister"), awa.Candidate("2", "Marisol Okafor", "colleague")]

    async def load(_u):
        return people

    monkeypatch.setattr(awa, "_load_people", load)
    awa.forget_roster()                                  # the earlier turns cached an empty roster (no contacts yet)
    ask = _resolve("Tell me about Marisol.")
    assert ask is not None and ask.tier == "ask_when_ambiguous"

    # 5. the seam, same session: a spoken mute is answered by code (the brain is never called), a farewell the brain
    #    hooks is cleaned, and an ordinary turn streams byte-identical
    calls = []

    async def brain(message, session_id, user_id="", **kw):
        calls.append(message)
        yield "Good evening. "
        yield "How can I help you settle in for the night?" if "night" in message else "Sunny, 22 degrees."

    monkeypatch.setattr(zc, "_run_flue_brain_streaming_turn", brain)
    assert _run(_collect(zc.run_flue_brain_streaming("stop mentioning the dentist", SID, UID))) == [restraint.ACK_MUTE]
    assert calls == []
    assert _run(_collect(zc.run_flue_brain_streaming("night Zoe", SID, UID))) == ["Night. Sleep well."]
    assert _run(_collect(zc.run_flue_brain_streaming("what's the weather", SID, UID))) == ["Good evening. ", "Sunny, 22 degrees."]
    assert calls == ["night Zoe", "what's the weather"]


def test_a_remember_ask_is_not_swallowed_by_the_hold_or_the_question_tier(svc, monkeypatch):
    """The person-half tier runs BEFORE ask-to-remember in resolve(); a plain 'remember that ...' must pass through it."""
    _put(svc, ROW, "voice_fact")
    _history(monkeypatch, ("user", Q), ("assistant", A))
    res = _resolve("Remember that my dentist is Dr Okonkwo.")
    assert res is not None and res.tier == "ask_to_remember"


def test_the_owners_remember_ask_twice_says_already_only_over_a_live_row(svc):
    first = _resolve("Remember that my favourite tea is lapsang souchong.")
    again = _resolve("Remember that my favourite tea is lapsang souchong.", sid="wave1-compose-2")
    assert first.tier == again.tier == "ask_to_remember" and again.reply == atr.ALREADY
    # break-the-row control: with no live approved row behind the skip the claim is withheld (#1932's _live_equivalent)
    for r in list(svc._col.rows.values()):
        r[1]["status"] = "archived"
    third = _resolve("Remember that my favourite tea is lapsang souchong.", sid="wave1-compose-3")
    assert third.reply != atr.ALREADY


def test_the_mute_check_reads_the_owners_own_words_not_the_clarification_rewrite(monkeypatch):
    """The seam resolves a pending clarification FIRST, but the spoken-mute check must see what the member said."""
    seen = {}

    def spy(message, user_id, session_id):
        return "Tell me about \"stop mentioning the dentist\" Marisol Vance."        # a rewrite that would trip the mute

    async def handle_turn(message, user_id, session_id):
        seen["mute_saw"] = message
        return ""

    async def brain(message, session_id, user_id="", **kw):
        seen["brain_saw"] = message
        yield "ok"

    monkeypatch.setattr(zc, "_resolve_clarification", spy)
    monkeypatch.setattr(restraint, "handle_turn", handle_turn)
    monkeypatch.setattr(zc, "_run_flue_brain_streaming_turn", brain)
    assert _run(_collect(zc.run_flue_brain_streaming("the sister", SID, UID))) == ["ok"]
    assert seen["mute_saw"] == "the sister" and seen["brain_saw"].startswith("Tell me about")
