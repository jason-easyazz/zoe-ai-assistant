"""The person-half guards through the REAL stack pieces (Jetson lane: unmarked, not ci_safe).

The ci_safe tests (test_hold_the_fact / test_ask_when_ambiguous / test_clean_goodbye / test_person_half_policies) fake the
store and the history. These drive the real ``MemoryService`` (over the fake Chroma the authority tests use), the real
``memory_authority`` stamps, the real ``fast_tiers.resolve`` and the real Flue seam ``run_flue_brain_streaming`` (only the
sidecar turn is stubbed), and check what the wiring promises:

* an owner-stated row is held against a bare pushback, a model-inferred row is not;
* the confirmed update is a REAL superseding edit by the owner's account (``user_confirmed``): the new value is recalled,
  the old row is not, nothing is deleted;
* the held turn's extractors are skipped (routers/chat.py), a normal turn's are not;
* the ambiguous question is asked before the router / any tool, and the short answer reaches the brain as the original
  request with the full name;
* a farewell that the brain hooks is cleaned at the seam, every other turn streams byte-identical.
"""
from __future__ import annotations

import asyncio

import pytest

import ask_when_ambiguous as awa
import clean_goodbye as cg
import fast_tiers
import hold_the_fact as htf
import memory_authority as ma
import memory_service
import zoe_flue_client as zc
from memory_service import MemoryService
from test_memory_authority import _Col

UID = "demo_bar_0a1b2c3d"
SID = "wiring-1"
Q = "Which day is my dentist appointment?"
A = "You have a dentist appointment on Friday for a cracked molar."
PUSH = "No, I'm sure it's Thursday."
ROW = "User has a dentist appointment on Friday for a cracked molar."


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def svc(monkeypatch):
    for k in ("ZOE_MEMORY_AUTHORITY", "ZOE_AFFECT_CONSENT_GATE", htf.ENV, awa.ENV, cg.ENV):
        monkeypatch.delenv(k, raising=False)
    s = MemoryService(data_dir="/nonexistent/zoe-test-person-half")
    col = _Col()
    s._collection = lambda: col

    async def no_audit(**_kw):
        return None

    async def opted_in(_uid):
        return False

    async def search(query, *, user_id, limit=10, **_kw):          # the semantic index is Chroma's; the rows are the real ones
        return s._metadata_read(user_id, 50)

    s._append_audit = no_audit
    s.search = search
    monkeypatch.setattr(memory_service, "_user_opted_out", opted_in)
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: s)
    s._col = col
    return s


def _put(s, text, source="voice_fact"):
    return _run(s.ingest(text, user_id=UID, source=source, status="approved", confidence=0.9))


def _history(monkeypatch, *rows):
    """rows are OLDEST first."""
    async def h(_sid):
        return list(reversed(rows))

    monkeypatch.setattr(htf, "_history", h)


def _resolve(text, sid=SID):
    import expert_dispatch
    import semantic_router

    return _run(fast_tiers.resolve(text, UID, sid, channel="chat", router_decision=None))


@pytest.fixture(autouse=True)
def _router_off(monkeypatch):
    import expert_dispatch
    import semantic_router

    monkeypatch.setattr(expert_dispatch, "is_enabled", lambda: True)
    monkeypatch.setattr(semantic_router, "is_enabled", lambda: False)         # past the person-half tier nothing answers


# -- hold the fact: the real authority ladder, the real edit -----------------------------------------

def test_an_owner_stated_row_is_held_through_fast_tiers_resolve(svc, monkeypatch):
    monkeypatch.setenv(htf.ENV, "enforce")
    _put(svc, ROW, "voice_fact")
    _history(monkeypatch, ("user", Q), ("assistant", A))
    res = _resolve(PUSH)
    assert res is not None and res.tier == "hold_the_fact" and res.intent == "hold_the_fact"
    assert res.reply.startswith("I've got your dentist appointment down as Friday, and that's what you told me.")
    assert [r.text for r in svc._metadata_read(UID, 50)] == [ROW]             # nothing written by holding


def test_a_row_a_model_inferred_is_not_theirs_to_defend(svc, monkeypatch):
    monkeypatch.setenv(htf.ENV, "enforce")
    _put(svc, ROW, "digest")                                                  # the nightly transcript writer: inferred
    assert ma.row_authority(svc._metadata_read(UID, 5)[0].metadata) == ma.INFERRED
    _history(monkeypatch, ("user", Q), ("assistant", A))
    assert _resolve(PUSH) is None                                             # the owner's word wins: on to the brain


def test_the_confirmation_is_a_real_superseding_edit_by_the_owner(svc, monkeypatch):
    monkeypatch.setenv(htf.ENV, "enforce")
    old = _put(svc, ROW, "voice_fact")
    hold = htf.hold_reply("your dentist appointment", "Friday", "Thursday")
    _history(monkeypatch, ("user", Q), ("assistant", A), ("user", PUSH), ("assistant", hold))
    res = _resolve("Yes, I'm sure.")
    assert res is not None and res.reply == "Done - I've changed your dentist appointment to Thursday in my notes."
    live = [r.text for r in svc._metadata_read(UID, 50)]
    assert live == ["User has a dentist appointment on Thursday for a cracked molar."]       # Friday is no longer recalled
    new = svc._metadata_read(UID, 5)[0]
    assert ma.row_class(new.metadata, new.text) == ma.USER_CONFIRMED                          # the owner, confirming
    assert svc._col.rows[old.id][1]["status"] == "superseded"                                 # kept, not deleted


def test_evidence_updates_through_resolve_and_a_neutral_challenge_does_not(svc, monkeypatch):
    monkeypatch.setenv(htf.ENV, "enforce")
    _put(svc, ROW, "voice_fact")
    _history(monkeypatch, ("user", Q), ("assistant", A))
    assert _resolve("Are you sure?") is None
    res = _resolve("I checked the calendar, it moved to Thursday.")
    assert res is not None and "Thursday" in res.reply and res.reply.startswith("Done")
    assert [r.text for r in svc._metadata_read(UID, 50)] == ["User has a dentist appointment on Thursday for a cracked molar."]


def test_shadow_is_the_default_and_does_nothing_through_resolve(svc, monkeypatch):
    _put(svc, ROW, "voice_fact")
    _history(monkeypatch, ("user", Q), ("assistant", A))
    assert _resolve(PUSH) is None
    assert [r.text for r in svc._metadata_read(UID, 50)] == [ROW]


def test_the_held_turn_is_not_mined_for_the_claim_it_declined(monkeypatch):
    import routers.chat as chat

    mined = []

    async def fake(*a, **k):
        mined.append(a)
        return []

    import memory_digest
    import memory_extractor
    import person_extractor
    import person_extractor_llm

    monkeypatch.setattr(memory_extractor, "extract_and_ingest", fake)
    monkeypatch.setattr(memory_digest, "run_turn_digest", fake)
    monkeypatch.setattr(person_extractor, "process_text", fake)
    monkeypatch.setattr(person_extractor_llm, "process_text_llm", fake)
    hold = htf.hold_reply("your dentist appointment", "Friday", "Thursday")
    assert _run(chat._persist_memory_candidates_impl(UID, SID, PUSH, hold)) is True and mined == []
    _run(chat._persist_memory_candidates_impl(UID, SID, "I'm going to Hobart on Monday.", "Nice, enjoy Hobart."))
    assert mined                                                              # an ordinary turn is mined as ever


def test_the_voice_lane_skips_a_held_turn_too(monkeypatch):
    import routers.voice_tts as vt
    import memory_digest
    import memory_extractor
    import person_extractor
    import person_extractor_llm

    mined = []

    async def fake(*a, **k):
        mined.append(a)
        return []

    monkeypatch.setattr(memory_extractor, "extract_and_ingest", fake)
    monkeypatch.setattr(memory_digest, "run_turn_digest", fake)
    monkeypatch.setattr(person_extractor, "process_text", fake)
    monkeypatch.setattr(person_extractor_llm, "process_text_llm", fake)
    _run(vt._run_voice_memory_passes(PUSH, htf.hold_reply("your dentist appointment", "Friday", "Thursday"), UID, SID))
    assert mined == []
    _run(vt._run_voice_memory_passes("I'm going to Hobart on Monday.", "Nice, enjoy Hobart.", UID, SID))
    assert mined


# -- ask when ambiguous -----------------------------------------------------------------------------

def test_the_question_comes_before_the_router_and_the_answer_reaches_the_brain_complete(svc, monkeypatch):
    monkeypatch.setenv(awa.ENV, "enforce")
    awa.forget_roster()
    people = [awa.Candidate("1", "Marisol Vance", "sister"), awa.Candidate("2", "Marisol Okafor", "colleague")]

    async def load(_u):
        return people

    monkeypatch.setattr(awa, "_load_people", load)
    res = _resolve("Tell me about Marisol.", sid="amb-1")
    assert res is not None and res.tier == "ask_when_ambiguous" and res.domain == "people"
    assert res.reply == "Which Marisol do you mean: Marisol Vance, your sister, or Marisol Okafor, your colleague?"
    seen = {}

    async def fake_turn(message, session_id, user_id="", **kw):
        seen["message"] = message
        yield "Marisol Vance is your sister."

    monkeypatch.setattr(zc, "_run_flue_brain_streaming_turn", fake_turn)
    out = _run(_collect(zc.run_flue_brain_streaming("the sister", "amb-1", UID)))
    assert seen["message"] == "Tell me about Marisol Vance." and out == ["Marisol Vance is your sister."]


# -- a clean goodbye at the seam --------------------------------------------------------------------

async def _collect(agen):
    return [d async for d in agen]


def test_the_seam_cleans_a_hooked_goodbye_and_leaves_every_other_turn_alone(monkeypatch):
    monkeypatch.setenv(cg.ENV, "enforce")

    async def fake_turn(message, session_id, user_id="", **kw):
        yield "Good evening. "
        yield "How can I help you settle in for the night?" if "night" in message else "Sunny, 22 degrees. How else can I help?"

    monkeypatch.setattr(zc, "_run_flue_brain_streaming_turn", fake_turn)
    assert _run(_collect(zc.run_flue_brain_streaming("night Zoe", "s-1", UID))) == ["Night. Sleep well."]
    assert _run(_collect(zc.run_flue_brain_streaming("what's the weather", "s-2", UID))) == [
        "Good evening. ", "Sunny, 22 degrees. How else can I help?"]
    monkeypatch.setenv(cg.ENV, "shadow")
    assert _run(_collect(zc.run_flue_brain_streaming("night Zoe", "s-3", UID))) == [
        "Good evening. How can I help you settle in for the night?"]               # shadow: logged, not changed
