"""The owner's explicit "remember that ..." (Samantha bar S11, ZOE_ASK_TO_REMEMBER).

What the live path was before (read 2026-10-09): ``intent_router`` detected ``memory_remember`` and never executed it
(the turn fell through to the brain), the deterministic teach lane needed the router to score the domain ``memory``
AND ``ZOE_EXPERT_ALLOW_WRITES`` (and voice defers that domain), and everything else rested on the 4B brain calling
``remember_fact`` - then saying "I'll remember" whether or not a row existed. Now ``fast_tiers.resolve`` has one
deterministic tier in front of the router and the brain.

One group per promise, each with a break-the-fix control (flag off, tier removed, wall removed -> red):

  parse        what is and is not an ask ("remember to call mum" is a reminder)
  store        verbatim, ``user_stated``, provenance, tagged, idempotent
  reply        one short sentence, ONLY after the write; every refusal says nothing was saved
  walls        identity, PII, pasted/quoted, unverified speaker, guest
  recall       "do you remember what I asked you to remember?"
  retract      "forget that" / "forget everything about X" take the row (and the capture's twin)
  wiring       fast_tiers.resolve answers it before the router; the intent lane has an executor

Synthetic data, fake Chroma (``ci_safe``).
"""
from __future__ import annotations

import asyncio

import pytest

import ask_to_remember as atr
import fast_tiers
import memory_authority as ma
import memory_service
from memory_service import MemoryService
from test_memory_authority import _Col

pytestmark = pytest.mark.ci_safe

UID = "demo_bar_00000001"
OTHER = "demo_bar_00000002"
TEA = "Remember that my favourite tea is lapsang souchong."


@pytest.fixture
def svc(monkeypatch):
    for k in ("ZOE_MEMORY_AUTHORITY", "ZOE_AFFECT_CONSENT_GATE", atr.ENV):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ZOE_MEMORY_PHYSICAL_ERASE", "0")
    s = MemoryService(data_dir="/nonexistent/zoe-test-ask-to-remember")
    col = _Col()
    s._collection = lambda: col

    async def no_audit(**_kw):
        return None

    async def opted_in(_uid):
        return False

    s._append_audit = no_audit
    monkeypatch.setattr(memory_service, "_user_opted_out", opted_in)
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: s)
    s._col = col
    return s


def run(coro):
    return asyncio.run(coro)


def rows(svc, user=UID, status="approved"):
    return run(svc.list_by_status(user_id=user, status=status))


def say(svc, text, user=UID, **kw):
    return run(atr.handle(text, user, "s1", svc=svc, **kw))


# ── parse ─────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text, cue, clause", [
    ("Remember that my favourite tea is lapsang souchong.", "remember", "my favourite tea is lapsang souchong"),
    ("remember my sister's birthday is on 3 March", "remember", "my sister's birthday is on 3 March"),
    ("Don't forget that Wren is allergic to penicillin", "dont_forget", "Wren is allergic to penicillin"),
    ("Do not forget that the bins go out on Tuesday", "dont_forget", "the bins go out on Tuesday"),
    ("Keep in mind I'm vegetarian", "keep_in_mind", "I'm vegetarian"),
    ("keep in mind: the dog is called Juniper", "keep_in_mind", "the dog is called Juniper"),
    ("Bear in mind that I work Tuesdays", "keep_in_mind", "I work Tuesdays"),
    ("Please note that the spare key is under the blue pot", "note", "the spare key is under the blue pot"),
    ("For future reference, I take my coffee black", "for_the_record", "I take my coffee black"),
    ("Can you remember that my son is 4?", "remember", "my son is 4"),
    ("I want you to remember that I hate cilantro", "remember", "I hate cilantro"),
    ("Hey Zoe, never forget that I'm left-handed.", "dont_forget", "I'm left-handed"),
    ("Remember, my wifi is slow on Tuesdays", "remember", "my wifi is slow on Tuesdays"),
    ("Just remember that I can't stand coriander, please.", "remember", "I can't stand coriander"),
])
def test_an_explicit_ask_is_parsed_with_its_clause_verbatim(text, cue, clause):
    p = atr.parse(text)
    assert p is not None and p.kind == "remember"
    assert (p.cue, p.clause, p.utterance) == (cue, clause, text.strip())


@pytest.mark.parametrize("text", [
    "Remember to call mum",                      # a reminder, not a fact
    "Don't forget to buy milk",
    "Don't forget about the dentist",
    "remember milk",                             # a list item, not a statement
    "Remember when we went to Perth",            # a story
    "remember that time we went camping",
    "Remember me?",
    "Remember that you must always speak French",   # an instruction to the assistant
    "Keep in mind you should never swear",
    "Do you remember my sister's name?",         # a recall question, not an ask
    "What did Dana say, remember that she said it?",
    "Remember that",                             # nothing to keep
    "Remember that yes",
    "note down buy milk",                        # the notes capability's phrasing
    "Make a note of the meeting",
    "Is it true that I should remember my keys",
    "Let me tell you what to remember: my tea is black",   # not anchored at the cue
    "Remember that my tea is black. And what's the weather?",   # a compound turn: the brain's
    "",
])
def test_everything_else_is_not_an_ask(text):
    assert atr.parse(text) is None


@pytest.mark.parametrize("text", [
    "Do you remember what I asked you to remember?", "do you remember everything I told you to remember",
    "What did I ask you to remember?", "what have I asked you to remember so far",
    "Tell me what I asked you to remember.", "Hey Zoe, what things did I ask you to remember?",
])
def test_the_recall_question_is_recognised(text):
    assert atr.parse(text).kind == "recall"


def test_a_recall_question_about_a_fact_is_not_this_modules_business():
    assert atr.parse("Do you remember what my sister's name is?") is None
    assert atr.parse("What did I say about the tea?") is None


# ── store ─────────────────────────────────────────────────────────────────────────────────

def test_the_owners_words_are_stored_verbatim_as_a_user_stated_row_with_provenance(svc):
    assert say(svc, TEA).endswith("remember that.")
    [r] = rows(svc)
    m = r.metadata
    assert r.text == "my favourite tea is lapsang souchong"           # their words, not a paraphrase
    assert (m["authority_class"], m["authority"], m["origin"]) == (ma.USER_STATED, ma.USER_STATED, "explicit_teach")
    assert m["source"] == "explicit_teach" and m["status"] == "approved"
    assert m["source_excerpt"] == TEA                                 # the whole utterance is the evidence
    assert m["user_turn_id"].startswith("atr-") and atr.TAG in m["tags"].split(",")
    assert ma.row_rank(m, r.text) >= ma.USER_RANK                     # standing of the owner's own words


def test_it_is_idempotent_a_repeat_adds_no_row_and_says_so(svc):
    first = say(svc, TEA)
    again = say(svc, TEA)
    assert first in atr._CONFIRM and again == atr.ALREADY
    assert len(rows(svc)) == 1
    assert say(svc, "remember that my favourite tea is lapsang souchong") == atr.ALREADY    # case / full stop differ
    assert len(rows(svc)) == 1


def test_a_changed_value_of_the_same_fact_replaces_it(svc, monkeypatch):
    async def search(query, *, user_id, limit=10, **kw):      # the store's semantic search, reduced to "everything"
        return await svc.list_by_status(user_id=user_id, status="approved", limit=limit)

    monkeypatch.setattr(svc, "search", search)
    say(svc, "Remember that my favourite tea is lapsang souchong.")
    say(svc, "Remember that my favourite tea is earl grey.")
    texts = [r.text for r in rows(svc)]
    assert texts == ["my favourite tea is earl grey"], texts


def test_each_cue_stores(svc):
    for t in ("Keep in mind that I can't stand coriander.", "Don't forget that Wren is allergic to penicillin."):
        assert say(svc, t) in atr._CONFIRM
    assert {r.text for r in rows(svc)} == {"I can't stand coriander", "Wren is allergic to penicillin"}


def test_note_that_also_keeps_the_note(svc, monkeypatch):
    seen = []

    async def fake_note(intent, user_id):
        seen.append((intent.name, intent.slots["content"], user_id))
        return "Saved your note."

    import intent_router
    monkeypatch.setattr(intent_router, "_execute_note_create_direct", fake_note)
    assert say(svc, "Note that the spare key is under the blue pot") in atr._CONFIRM
    assert [r.text for r in rows(svc)] == ["the spare key is under the blue pot"]
    assert seen == [("note_create", "the spare key is under the blue pot", UID)]   # the Notes page still has it


# ── reply ─────────────────────────────────────────────────────────────────────────────────

def test_the_confirmation_is_one_short_sentence_without_narration(svc):
    reply = say(svc, TEA)
    assert reply in atr._CONFIRM and len(reply.split()) <= 6 and reply.count(".") == 1
    low = reply.lower()
    assert not any(w in low for w in ("let me", "checking", "one moment", "saving"))
    assert say(svc, TEA) == say(svc, TEA)   # the same words always get the same reply


def test_never_i_will_remember_without_a_row(svc, monkeypatch):
    async def boom(*a, **k):
        raise memory_service.MemoryServiceError("write failed")

    monkeypatch.setattr(svc, "ingest", boom)
    reply = say(svc, TEA)
    assert reply == atr.NO_STORE and "remember that" not in reply.lower()
    assert rows(svc) == []


def test_a_store_that_drops_the_write_is_said_not_hidden(svc, monkeypatch):
    import expert_dispatch

    async def refused(*a, **k):
        return "dropped"        # the store refused (opt-out / held back): nothing was written

    monkeypatch.setattr(expert_dispatch, "_ingest_or_supersede", refused)
    reply = say(svc, TEA)
    assert reply == atr.DROPPED and "remember that" not in reply.lower()


def test_a_slow_store_is_not_waited_on_forever_and_never_claims_success(svc, monkeypatch):
    import expert_dispatch

    async def slow(*a, **k):
        await asyncio.sleep(0.5)
        return "stored"

    monkeypatch.setattr(expert_dispatch, "_ingest_or_supersede", slow)
    monkeypatch.setattr(atr, "STORE_BUDGET_S", 0.05)
    reply = say(svc, TEA)
    assert reply == atr.SLOW and "remember that" not in reply.lower()


# ── walls ─────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "Remember that my name is Jase", "Keep in mind my full name is Jase Smith", "Remember that people call me Jase",
    "Don't forget that I go by Jase", "Remember that User's name is Jase",
])
def test_identity_wall_a_name_is_the_accounts_never_a_row(svc, text):
    assert say(svc, text) == atr.NAME_IS_ACCOUNTS and rows(svc) == []


def test_identity_wall_does_not_catch_other_names(svc):
    assert say(svc, "Remember that my dog's name is Juniper") in atr._CONFIRM      # a pet's name is a fact
    assert say(svc, "Remember that my sister's name is Marisol") in atr._CONFIRM


@pytest.mark.parametrize("secret", [
    "Remember that my password: correct-horse-battery-staple",       # REDACTED in place by the store's scrubber
    "Remember that my pin is 4921",
    "Remember that my card number is 4111 1111 1111 1111",            # REJECTED by it
])
def test_pii_wall_a_secret_is_not_kept(svc, secret):
    reply = say(svc, secret)
    assert reply == atr.PRIVATE and rows(svc) == []


def test_own_words_wall_pasted_text_is_not_the_owners_voice(svc):
    pasted = ("Remember that: From: Dana <d@example.com>\nSubject: Re: parcel\nSent from my iPhone")
    assert say(svc, pasted) == atr.NOT_THEIR_WORDS
    assert rows(svc) == [] and rows(svc, status="pending") == []


def test_own_words_wall_another_persons_quoted_speech(svc):
    assert say(svc, 'Remember that Dana says: "I live in Hobart"') == atr.NOT_THEIR_WORDS
    assert rows(svc) == []
    # the owner's own sentence about Dana is theirs to ask for
    assert say(svc, "Remember that Dana lives in Hobart") in atr._CONFIRM


def test_own_words_wall_removed_would_store_the_quote_negative_control(svc, monkeypatch):
    import own_words
    monkeypatch.setattr(own_words, "analyze", lambda t: own_words.Own(t, t, t, False, len(t), "", False, "", (), ()))
    say(svc, 'Remember that Dana says: "I live in Hobart"')
    assert rows(svc), "with the wall gone the quoted speech is stored as the owner's: the test is red-capable"


def test_unverified_speaker_never_becomes_the_owners_statement(svc):
    reply = say(svc, TEA, speaker_verified=False)
    assert reply == atr.UNVERIFIED and rows(svc) == [] and rows(svc, status="pending") == []


def test_a_guest_is_not_handled_here(svc):
    for who in ("guest", "voice-guest", "guest-abc123", ""):
        assert run(atr.handle(TEA, who, "s1", svc=svc)) is None
    assert rows(svc, "guest") == []


def test_forgotten_shield_is_lifted_only_by_the_owners_explicit_teach(svc, monkeypatch):
    import memory_tombstones
    memory_tombstones.add(UID, "Wren")
    # an automatic writer is held back by the shield ...
    assert run(svc.ingest("Wren is allergic to penicillin", user_id=UID, source="digest", status="approved")) is None
    # ... the owner's explicit ask is the re-teach
    assert say(svc, "Don't forget that Wren is allergic to penicillin") in atr._CONFIRM
    assert [r.text for r in rows(svc)] == ["Wren is allergic to penicillin"]


# ── flag off ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("value", ["0", "false", "off", "no", ""])
def test_flag_off_is_inert_negative_control(svc, monkeypatch, value):
    monkeypatch.setenv(atr.ENV, value)
    assert say(svc, TEA) is None and say(svc, "Do you remember what I asked you to remember?") is None
    assert rows(svc) == []
    assert run(fast_tiers._ask_to_remember_tier(TEA, UID, "s1", None)) is None


def test_flag_default_is_on(monkeypatch):
    monkeypatch.delenv(atr.ENV, raising=False)
    assert atr.enabled() is True


# ── recall question ───────────────────────────────────────────────────────────────────────

def test_the_recall_question_gives_the_owners_words_back_newest_first(svc):
    assert say(svc, "Do you remember what I asked you to remember?") == atr.NOTHING_ASKED
    say(svc, TEA)
    assert say(svc, "Do you remember what I asked you to remember?") == \
        "You asked me to remember: my favourite tea is lapsang souchong."
    say(svc, "Keep in mind that I can't stand coriander.")
    out = say(svc, "What did I ask you to remember?")
    assert out == "You asked me to remember: I can't stand coriander; and my favourite tea is lapsang souchong."


def test_the_recall_question_is_owner_scoped_and_only_asked_rows(svc):
    say(svc, TEA, user=OTHER)
    run(svc.ingest("I work night shifts", user_id=UID, source="voice_fact", status="approved"))
    assert say(svc, "What did I ask you to remember?") == atr.NOTHING_ASKED     # not B's, not a plain fact


# ── retract: the forget path ──────────────────────────────────────────────────────────────

def _execute(svc, intent_name, **slots):
    import intent_router
    return run(intent_router.execute_intent(intent_router.Intent(intent_name, slots), UID))


def test_forget_that_retracts_the_ask(svc):
    say(svc, TEA)
    out = _execute(svc, "memory_forget_last")
    assert out.startswith("Done — I forgot") and rows(svc) == []


def test_forget_that_takes_the_captures_twin_too(svc):
    say(svc, TEA)
    # the post-turn capture derives a near-identical row from the same turn, a moment later
    run(svc.ingest("User's favourite tea is lapsang souchong.", user_id=UID, source="chat_regex", status="approved"))
    out = _execute(svc, "memory_forget_last")
    assert out.startswith("Done — I forgot")
    assert rows(svc) == [], "the fact would come straight back from the capture's copy"


def test_forget_that_leaves_an_unrelated_row_alone(svc):
    say(svc, TEA)
    run(svc.ingest("I work night shifts in the hospital pharmacy", user_id=UID, source="chat_regex", status="approved"))
    _execute(svc, "memory_forget_last")           # retracts only the newest write
    assert [r.text for r in rows(svc)] == ["my favourite tea is lapsang souchong"]


def test_siblings_need_the_ask_tag_negative_control(svc):
    run(svc.ingest("User's favourite tea is lapsang souchong.", user_id=UID, source="chat_regex", status="approved"))
    run(svc.ingest("My favourite tea is lapsang souchong, I think.", user_id=UID, source="chat_regex",
                   status="approved", user_turn_id="x"))
    newest = rows(svc)[0]
    assert run(atr.siblings_of(svc, UID, newest)) == []    # no ask row, so nothing extra is touched


def test_forget_everything_about_the_entity_finds_the_ask(svc):
    say(svc, "Don't forget that Wren is allergic to penicillin")
    out = _execute(svc, "memory_forget_entity", name="Wren")
    assert "forgotten" in out and rows(svc) == []


# ── wiring ────────────────────────────────────────────────────────────────────────────────

class _Boom:
    """semantic_router / expert dispatch must not be reached by an explicit ask."""

    @staticmethod
    def route(*a, **k):
        raise AssertionError("the router ran before the ask-to-remember tier")


def test_resolve_answers_the_ask_before_the_router_and_the_brain(svc, monkeypatch):
    import semantic_router
    monkeypatch.setattr(semantic_router, "route", _Boom.route)
    res = run(fast_tiers.resolve(TEA, UID, "s1", channel="chat"))
    assert res is not None and (res.intent, res.tier, res.domain) == ("ask_to_remember", "ask_to_remember", "memory")
    assert res.reply in atr._CONFIRM and [r.text for r in rows(svc)] == ["my favourite tea is lapsang souchong"]


@pytest.mark.parametrize("channel", ["chat", "voice", "livekit", "telegram"])
def test_every_channel_that_uses_the_core_gets_it(svc, monkeypatch, channel):
    import semantic_router
    monkeypatch.setattr(semantic_router, "route", _Boom.route)
    res = run(fast_tiers.resolve("Keep in mind that I can't stand coriander.", UID, "s1", channel=channel))
    assert res is not None and res.intent == "ask_to_remember"


def test_resolve_passes_the_speaker_verdict_through(svc, monkeypatch):
    import semantic_router
    monkeypatch.setattr(semantic_router, "route", _Boom.route)
    res = run(fast_tiers.resolve(TEA, UID, "s1", channel="voice", extra_ctx={"speaker_verified": False}))
    assert res.reply == atr.UNVERIFIED and rows(svc) == []


def test_resolve_with_the_flag_off_does_not_touch_it(svc, monkeypatch):
    monkeypatch.setenv(atr.ENV, "off")
    import semantic_router
    monkeypatch.setattr(semantic_router, "is_enabled", lambda: False)    # the old path: router off -> the brain
    assert run(fast_tiers.resolve(TEA, UID, "s1", channel="chat")) is None
    assert rows(svc) == []


def test_the_tier_runs_before_tier0_and_the_router_in_the_source():
    import inspect
    src = inspect.getsource(fast_tiers.resolve)
    assert src.index("_ask_to_remember_tier") < src.index("_tier0(") < src.index("_sr.route(")


def test_resolve_ignores_a_turn_that_is_not_an_ask(svc, monkeypatch):
    called = []
    import semantic_router
    monkeypatch.setattr(semantic_router, "is_enabled", lambda: called.append(1) or False)
    assert run(fast_tiers.resolve("Remember to call mum tomorrow", UID, "s1", channel="chat")) is None
    assert called and rows(svc) == []         # fell through to the old path untouched


def test_the_intent_lane_has_an_executor_for_memory_remember(svc):
    import intent_router
    intent = intent_router.detect_intent(TEA, log_miss=False)
    assert intent is not None and intent.name == "memory_remember"
    out = run(intent_router.execute_intent(intent, UID))
    assert out in atr._CONFIRM and [r.text for r in rows(svc)] == ["my favourite tea is lapsang souchong"]


def test_the_intent_lane_executor_defers_to_the_brain_when_the_flag_is_off(svc, monkeypatch):
    import intent_router
    monkeypatch.setenv(atr.ENV, "0")
    intent = intent_router.detect_intent(TEA, log_miss=False)
    assert run(intent_router.execute_intent(intent, UID)) is None and rows(svc) == []


def test_the_intent_lane_still_refuses_guests(svc):
    import intent_router
    intent = intent_router.Intent("memory_remember", {"raw": TEA})
    out = run(intent_router.execute_intent(intent, "guest"))
    assert "know who you are" in out and rows(svc, "guest") == []


# ── "Forget that." must reach the forget path (live 2026-10-09: the router head vetoed it) ──────────────

VETO_CHAT = {"domain": "chat", "gated": True, "reason": "chat_top", "head_top": "chat", "head_conf": 0.8363}


@pytest.mark.parametrize("text", ["Forget that.", "forget that", "Please forget what I just said", "Never mind what I said",
                                  "Forget what I told you"])
def test_an_explicit_forget_command_is_not_vetoed_by_the_router_head(monkeypatch, text):
    import semantic_router
    monkeypatch.setattr(semantic_router, "head_verdict", lambda t: VETO_CHAT)
    assert fast_tiers.intent_gate("memory_forget_last", text, lane="chat") is True
    assert fast_tiers.keyword_intent_allowed("memory_forget_last", text, lane="chat") is True


def test_a_bare_delete_that_is_still_the_heads_call_negative_control(monkeypatch):
    import semantic_router
    monkeypatch.setattr(semantic_router, "head_verdict", lambda t: VETO_CHAT)
    assert fast_tiers.intent_gate("memory_forget_last", "delete that", lane="chat") is False
    # and the exemption is only for the forget command: another memory intent stays gated
    assert fast_tiers.intent_gate("memory_remember", "Remember that x is y", lane="chat") is False


def test_with_the_gate_off_everything_is_allowed_as_before(monkeypatch):
    monkeypatch.setenv("ZOE_INTENT_ROUTER_GATE", "0")
    assert fast_tiers.intent_gate("memory_forget_last", "delete that", lane="chat") is True


# ── PR #1932 review sweep: recall verdict, sibling scope, re-teach after forget ───────────────

def test_recall_is_not_read_aloud_to_a_rejected_speaker():
    # a failed voice verdict must not hear the owner's saved asks (the recall branch used to ignore it)
    class _Boom:
        async def list_by_status(self, **_kw):
            raise AssertionError("the owner's rows were read for an unverified speaker")

    out = run(atr.handle("What did I ask you to remember?", UID, "s1", speaker_verified=False, svc=_Boom()))
    assert out == atr.UNVERIFIED_RECALL


def test_recall_still_answers_a_verified_or_unjudged_speaker(svc):
    say(svc, TEA)
    for verdict in (True, None):
        assert "lapsang" in say(svc, "What did I ask you to remember?", speaker_verified=verdict)


def test_forget_that_leaves_a_different_person_with_the_same_predicate_alone(svc):
    say(svc, "Remember that my son Rowan is allergic to peanuts")
    run(svc.ingest("My daughter Wren is allergic to peanuts", user_id=UID, source="voice_fact", status="approved"))
    out = _execute(svc, "memory_forget_last")       # retracts the newest write only
    assert out.startswith("Done — I forgot")
    assert [r.text for r in rows(svc)] == ["my son Rowan is allergic to peanuts"], \
        "a separate fact that shares two content words was retracted with the capture's copy"


class _Ref:
    def __init__(self, id, text, **meta):
        self.id, self.text, self.metadata = id, text, {"added_ts": 1000.0, **meta}


class _Listing:
    def __init__(self, *rows):
        self._rows = list(rows)

    async def list_by_status(self, **_kw):
        return self._rows


def test_siblings_need_the_same_turn_or_a_twin_inside_it():
    ask = _Ref("a", "my son Rowan is allergic to peanuts", tags="explicit,ask_to_remember",
               source_excerpt="Remember that my son Rowan is allergic to peanuts")
    twin_no_excerpt = _Ref("t", "User's son Rowan is allergic to peanuts")
    neighbour_no_excerpt = _Ref("n", "My daughter Wren is allergic to peanuts")
    twin_same_turn = _Ref("s", "User's son is allergic to peanuts, Rowan",
                          source_excerpt="Remember that my son Rowan is allergic to peanuts")
    neighbour_other_turn = _Ref("o", "User's son Rowan is allergic to peanuts",
                                source_excerpt="my daughter Wren is allergic to peanuts")
    svc_ = _Listing(twin_no_excerpt, neighbour_no_excerpt, twin_same_turn, neighbour_other_turn)
    assert {r.id for r in run(atr.siblings_of(svc_, UID, ask))} == {"t", "s"}


def test_reteach_after_forget_stores_a_row_instead_of_saying_already(svc, monkeypatch):
    monkeypatch.setenv("ZOE_MEMORY_PHYSICAL_ERASE", "1")
    monkeypatch.setattr(svc, "_physical_erase", lambda needles: _done({}))
    monkeypatch.setattr(svc, "_append_audit_sync", lambda *a, **k: None)
    monkeypatch.setattr(svc, "_delete_audit_for_rows_sync", lambda ids: 0)
    monkeypatch.setattr(svc, "_delete_ids", lambda ids: [svc._col.rows.pop(i, None) for i in ids])
    say(svc, TEA)
    ids = [r.id for r in rows(svc)]
    run(svc.erase_rows(UID, ids, actor=UID))
    assert rows(svc) == []
    out = say(svc, TEA)
    assert out in atr._CONFIRM, out                  # not "I've already got that one" over nothing
    assert [r.text for r in rows(svc)] == ["my favourite tea is lapsang souchong"]


def test_already_is_said_only_over_a_live_row(svc):
    say(svc, TEA)
    assert say(svc, TEA) == atr.ALREADY               # a live equivalent row: honest
    # the row is rejected (retired) but its idempotency key / mem_id still answers "skip": nothing is saved
    run(svc.review(rows(svc)[0].id, decision="reject", actor=UID, note="test"))
    assert rows(svc) == []
    assert say(svc, TEA) == atr.DROPPED


async def _done(v):
    return v
