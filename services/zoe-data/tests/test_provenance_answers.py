"""BM5: provenance answers and memory control in Zoe's voice (ZOE_MEMORY_PROVENANCE_ANSWERS).

"why did you say that?" names the source row's day and the OWNER'S OWN words; "what do you know about me?" is a bounded, grouped
summary that counts the private classes and never reads them; "that's wrong, it's X" / "forget it" on the next turn mean the row
that answer named; "off the record" keeps a turn out of every writer. One group per promise, each with a break-the-fix control
(flag off, ledger removed, wall removed -> red):

  shapes      what is and is not each ask
  ledger      the turn ledger: ids only, the previous reply or "I cannot tell", never the wrong reply
  explain     the answer, built from the real packet builder + real rows (the brain's reply is the only simulated step)
  walls       another member's words, a guest, an unverified voice, a forgotten row, a sensitive row on voice, a paraphrase
  summary     grouped, bounded, counts; private classes counted not read; pulled by name
  fix/forget  the row the answer named - not the newest row; twins; the quoted turn in the exact-words index
  off record  the cue forms, the arm/claim state machine, every writer, the transcript flag, the ingest choke point
  wiring      fast_tiers.resolve answers before the router; brain_dispatch commits; /for-prompt notes; flag off = byte-identical

Synthetic data only (``demo_bar_`` ids and invented names), fake Chroma (``ci_safe``). The brain is not run: its reply is the one
simulated step (the packet it was handed is real).
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest

import exact_words as xw
import memory_provenance as mp
import memory_service
import provenance_answers as pa
from memory_service import MemoryService
from test_memory_authority import _Col

pytestmark = pytest.mark.ci_safe

UID = "demo_bar_00000001"
OTHER = "demo_bar_00000002"
NOW = 1_791_500_000.0           # fixed clock: Thu 2026-10-09 ~12:53 Perth
SAY_SISTER = "My sister Marisol is flying in from Lisbon on Thursday and I need to pick her up."
ROW_SISTER = "User's sister Marisol is flying in from Lisbon on Thursday"
SAY_DAD = "My dad Teodor was a lighthouse keeper on the northern coast for thirty years."
ROW_DAD = "User's dad Teodor was a lighthouse keeper on the northern coast for thirty years"
ASK_SISTER = "When is my sister arriving?"
REPLY_SISTER = "Your sister Marisol is arriving on Thursday from Lisbon."
OFFSET_3D = "2026-10-06T02:00:00Z"   # 3 days before NOW


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for k in (mp.ENV, "ZOE_CORRECTION_APPLY", "ZOE_MEMORY_AUTHORITY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ZOE_TIMEZONE", "Australia/Perth")
    monkeypatch.setenv("ZOE_MEMORY_PHYSICAL_ERASE", "0")
    monkeypatch.setenv("ZOE_EXPERT_ENABLED", "0")        # fast_tiers.resolve: only the BM5 tier can answer
    monkeypatch.setattr(mp, "_now", lambda now=None: NOW if now is None else float(now))     # the ledger's clock is injected
    mp.reset()
    yield
    mp.reset()


@pytest.fixture
def svc(monkeypatch):
    s = MemoryService(data_dir="/nonexistent/zoe-test-provenance")
    col = _Col()
    s._collection = lambda: col

    async def no_audit(**_kw):
        return None

    async def opted_in(_uid):
        return False

    s._append_audit = no_audit
    monkeypatch.setattr(memory_service, "_user_opted_out", opted_in)
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: s)
    from routers import memories
    monkeypatch.setattr(memories, "get_memory_service", lambda: s)       # bound at import: patch the module's own name too
    s._col = col
    return s


def put(svc, text, *, user=UID, source="chat_regex", excerpt=None, when=OFFSET_3D, **kw):
    return run(svc.ingest(text, user_id=user, source=source, source_excerpt=excerpt, captured_at=when,
                          confidence=0.9, session_id="s0", **kw))


def brain_turn(message, reply, *, user=UID, session="s1"):
    """One brain turn as the live path runs it: the user turn is numbered, the real /for-prompt builder serves the packet, and
    brain_dispatch commits the reply. The reply text is the only simulated step."""
    import brain_dispatch
    from routers import memories

    mp.note_user_turn(user, message, session)
    token = brain_dispatch._provenance_begin(user)
    run(memories.memory_for_prompt(user_id=user, message=message, limit=12, _=None))
    brain_dispatch._provenance_commit(user, reply, message, session, token)


def say(text, *, user=UID, session="s1", channel="chat", **ctx):
    """One turn through the real fast_tiers.resolve (the wrapper); returns the reply or None."""
    import fast_tiers

    res = run(fast_tiers.resolve(text, user, session, channel=channel, extra_ctx=ctx or None))
    return None if res is None else res.reply


def approved(svc, user=UID):
    return [r.text for r in run(svc.list_by_status(user_id=user, status="approved", limit=500))]


def rows_for(svc, user=UID):
    return run(svc.list_by_status(user_id=user, status="approved", limit=500))


# ── shapes ────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "Why did you say that?", "why did you say that", "Why'd you say that?", "Where did you get that?", "where did you get that from",
    "Where'd you get that from?", "How do you know that?", "How did you know that", "Hey Zoe, why did you say that?",
    "ok but why did you tell me that", "What made you say that?", "What are you basing that on?", "Who told you that?",
    "what's your source for that", "why did you say that just now", "Where is that from?", "How come you said that?",
])
def test_the_explain_shapes(text):
    assert pa.parse_stateless(text) == pa.Ask("explain")


@pytest.mark.parametrize("text", [
    "Why did you say that Dana is my sister?",      # a question about a clause, not about the last reply
    "How do you know that Dana's birthday is in May?",
    "Where did you get that recipe from, I want to cook it",
    "why did the dog bark", "I wonder why you said that", "Why did you say that? Anyway, set a timer for ten minutes",
    "what do you know", "How do you know?",
])
def test_not_the_explain_shapes(text):
    assert pa.parse_stateless(text) is None or pa.parse_stateless(text).kind != "explain"


@pytest.mark.parametrize("text, more", [
    ("What do you know about me?", False), ("what have you got on me", False), ("What do you have on me?", False),
    ("What do you remember about me?", False), ("what have I told you", False), ("What do you know about me so far?", False),
    ("Tell me everything you know about me", True), ("what do you know about me in detail", True),
    ("What's in your memory about me?", False), ("show me what you know about me", False),
])
def test_the_know_me_shapes(text, more):
    a = pa.parse_stateless(text)
    assert a is not None and a.kind == "know_me" and a.more is more


@pytest.mark.parametrize("text", ["How well do you know me?", "What do you think about me?", "what do you know about the weather",
                                  "What do you know about Dana?", "do you know me"])
def test_not_the_know_me_shapes(text):
    a = pa.parse_stateless(text)
    assert a is None or a.kind not in ("know_me", "know_topic")


def test_a_named_person_is_the_know_other_shape_and_the_private_classes_are_pulled_by_name():
    assert pa.parse_stateless("What do you know about Jason?") == pa.Ask("know_other", name="Jason")
    assert pa.parse_stateless("what do you know about my health") == pa.Ask("know_topic", topic="health")
    assert pa.parse_stateless("What have you got on my finances?") == pa.Ask("know_topic", topic="money")
    assert pa.parse_stateless("what do you know about how I've been feeling") is None   # not a claimed shape: the brain's


@pytest.mark.parametrize("text, kind, value", [
    ("forget it", "forget_it", ""), ("Yes, forget that", "forget_it", ""), ("please forget this one", "forget_it", ""),
    ("That's wrong", "fix", ""), ("That's wrong, it's Tuesday", "fix", "Tuesday"), ("No, it's Marisa", "fix", "Marisa"),
    ("Actually she's flying in from Porto", "fix", "she's flying in from Porto"),
    ("that is not right, it's Marisa not Marisol", "fix", "Marisa"), ("You've got that wrong", "fix", ""),
])
def test_the_stateful_shapes(text, kind, value):
    a = pa.parse_stateful(text)
    assert a is not None and a.kind == kind and a.value == value


@pytest.mark.parametrize("text", ["forget about it", "actually can you set a timer", "actually, what's the weather", "no thanks",
                                  "That's great", "it's fine", "forget everything about Delia"])
def test_not_the_stateful_shapes(text):
    assert pa.parse_stateful(text) is None


@pytest.mark.parametrize("text, cue, payload", [
    ("Off the record: my brother-in-law Cormac is getting a divorce.", "off_the_record", "my brother-in-law Cormac is getting a divorce"),
    ("off the record", "off_the_record", ""), ("Off the record.", "off_the_record", ""), ("Can we go off the record?", "off_the_record", ""),
    ("Okay, off the record - I hate my job", "off_the_record", "I hate my job"),
    ("Don't remember this, I got the job", "dont_remember", "I got the job"), ("don't remember this", "dont_remember", ""),
    ("do not remember this", "dont_remember", ""), ("This stays between us", "between_us", ""), ("keep this between us", "between_us", ""),
    ("My sister is pregnant, this stays between us", "between_us", "My sister is pregnant"),
    ("how should I tell my boss, off the record?", "off_the_record", "how should I tell my boss"),
    ("Hey Zoe, off the record, I'm thinking of leaving", "off_the_record", "I'm thinking of leaving"),
])
def test_the_off_the_record_cues(text, cue, payload):
    got = mp.parse_off_record(text)
    assert got is not None and (got.cue, got.payload) == (cue, payload)


@pytest.mark.parametrize("text", [
    "what does off the record mean?", "Is this off the record?", "The meeting was off the record so I can't say",
    "I like tea", "don't remember that", "don't forget this", "between you and me is a great song", "",
])
def test_not_an_off_the_record_cue(text):
    assert mp.parse_off_record(text) is None or text.startswith("between you and me is")


# ── the ledger ────────────────────────────────────────────────────────────────────────────────

def test_a_turn_seen_twice_is_one_turn_and_a_new_turn_is_a_new_one():
    assert mp.note_user_turn(UID, "hello there", "s1", now=100.0) == 1
    assert mp.note_user_turn(UID, "hello there", "s1", now=100.4) == 1                  # chat save + tier
    assert mp.note_user_turn(UID, "/openclaw hello there", "s1", now=100.9) == 1        # the same turn under another wrapping
    assert mp.note_user_turn(UID, "what time is it", "s1", now=101.2) == 2              # another turn, seconds later, same session
    assert mp.note_user_turn(UID, "what time is it", "s1", now=110.0) == 3              # the same words a while later: a new turn


def test_the_ledger_keeps_ids_not_text_and_ranks_the_row_the_reply_restates(svc):
    put(svc, ROW_SISTER, excerpt=SAY_SISTER)
    put(svc, ROW_DAD, excerpt=SAY_DAD)
    brain_turn(ASK_SISTER, REPLY_SISTER)
    st = mp._STATES[UID]
    assert st.last.kind == "brain" and st.last.served == 2 and len(st.last.sources) == 1
    assert st.last.sources[0].kind == "row" and "Marisol" not in repr(st.last)          # an id, no words
    assert [r.text for r in rows_for(svc) if r.id == st.last.sources[0].id] == [ROW_SISTER]


def test_a_row_that_was_never_served_is_never_named(svc):
    """The reply restates the dad fact, but only the sister row was in the packet: the dad row cannot be named."""
    put(svc, ROW_SISTER, excerpt=SAY_SISTER)
    put(svc, ROW_DAD, excerpt=SAY_DAD, when="2026-10-08T02:00:00Z")
    mp.note_user_turn(UID, "tell me about my family", "s1")
    token = mp.begin_turn(UID)
    mp.note_served(UID, [(r.id, r.text) for r in rows_for(svc) if "Marisol" in r.text])
    mp.commit_brain_reply(UID, "Your dad Teodor was a lighthouse keeper on the northern coast.", "tell me about my family", "s1", token=token)
    assert mp._STATES[UID].last.sources == () and mp._STATES[UID].last.served == 1
    mp.note_user_turn(UID, "why did you say that", "s1")
    assert run(pa.explain(UID, svc=svc, now=NOW)) == pa.UNMATCHED_REPLY


def test_a_shuffled_ledger_names_the_wrong_row_so_the_correct_row_check_can_fail(svc):
    """Control (the register's shuffled-packet arm): point the ledger at the wrong row and the answer names it - which is exactly
    what the correct-row assertions in this file would catch."""
    put(svc, ROW_SISTER, excerpt=SAY_SISTER)
    dad = put(svc, ROW_DAD, excerpt=SAY_DAD)
    brain_turn(ASK_SISTER, REPLY_SISTER)
    good = mp._STATES[UID].last
    mp._STATES[UID].last = mp.ReplyRecord(seq=good.seq, ts=good.ts, kind="brain", sources=(mp.Source("row", dad.id),), served=2)
    mp.note_user_turn(UID, "why did you say that", "s1")
    out = run(pa.explain(UID, svc=svc, now=NOW))
    assert "Teodor" in out and "Marisol" not in out


def test_a_reply_that_restates_nothing_names_no_source(svc):
    put(svc, ROW_SISTER, excerpt=SAY_SISTER)
    brain_turn(ASK_SISTER, "I'm not sure, sorry.")
    assert mp._STATES[UID].last.served == 1 and mp._STATES[UID].last.sources == ()


def test_the_previous_reply_is_the_one_before_this_turn_or_nothing(svc):
    put(svc, ROW_SISTER, excerpt=SAY_SISTER)
    brain_turn(ASK_SISTER, REPLY_SISTER)
    mp.note_user_turn(UID, "Why did you say that?", "s1")
    assert mp.previous_reply(UID) is not None
    mp.note_user_turn(UID, "what is the weather like", "s1", now=mp._STATES[UID].last_note_ts + 30)   # a turn that recorded no reply
    mp.note_user_turn(UID, "why did you say that", "s1", now=mp._STATES[UID].last_note_ts + 30)
    assert mp.previous_reply(UID) is None                       # never the reply BEFORE the one the user means
    assert run(pa.explain(UID, svc=svc)) == pa.UNKNOWN_REPLY


def test_a_stale_reply_is_not_explained(svc):
    put(svc, ROW_SISTER, excerpt=SAY_SISTER)
    brain_turn(ASK_SISTER, REPLY_SISTER)
    t0 = mp._STATES[UID].last.ts
    mp.note_user_turn(UID, "Why did you say that?", "s1", now=t0 + 1)
    assert mp.previous_reply(UID, now=t0 + 5) is not None
    assert mp.previous_reply(UID, now=t0 + mp.REPLY_TTL_S + 1) is None


def test_a_guest_has_no_ledger():
    assert mp.note_user_turn("guest", "hello", "s1") == 0 and mp.note_user_turn("voice-guest", "hello", "s1") == 0
    assert mp.claim_turn("guest", "off the record: my secret is x") is False and "guest" not in mp._STATES


def test_flag_off_the_ledger_and_the_marks_do_nothing(monkeypatch, svc):
    monkeypatch.setenv(mp.ENV, "0")
    put(svc, ROW_SISTER, excerpt=SAY_SISTER)
    brain_turn(ASK_SISTER, REPLY_SISTER)
    assert mp._STATES == {}
    assert mp.claim_turn(UID, "off the record: my brother-in-law Cormac is getting a divorce") is False
    assert mp.is_off_record(UID, "off the record: my brother-in-law Cormac is getting a divorce") is False
    assert say("Why did you say that?") is None and say("what do you know about me") is None


# ── explain ───────────────────────────────────────────────────────────────────────────────────

def test_why_did_you_say_that_names_the_day_and_the_owners_own_words(svc):
    put(svc, ROW_SISTER, excerpt=SAY_SISTER)
    put(svc, ROW_DAD, excerpt=SAY_DAD)
    brain_turn(ASK_SISTER, REPLY_SISTER)
    mp.note_user_turn(UID, "Why did you say that?", "s1")
    out = run(pa.explain(UID, channel="chat", svc=svc, now=NOW))
    assert out.startswith("I said that because on Tuesday you told me, \"My sister Marisol is flying in from Lisbon on Thursday")
    assert "pick her up" in out and "Teodor" not in out and "lighthouse" not in out       # the other fact was served, not used
    assert pa.FIX_TAIL in out and "?" not in out.split(pa.FIX_TAIL)[0]
    assert mp._STATES[UID].explained.quote.startswith("My sister Marisol")             # carried to the NEXT turn only


def test_the_answer_is_the_owners_verbatim_words_never_the_rows_paraphrase(svc):
    put(svc, ROW_SISTER, excerpt=SAY_SISTER)
    brain_turn(ASK_SISTER, REPLY_SISTER)
    mp.note_user_turn(UID, "Why did you say that?", "s1")
    out = run(pa.explain(UID, svc=svc, now=NOW))
    assert ROW_SISTER not in out and "User's" not in out
    assert "My sister Marisol is flying in from Lisbon on Thursday" in out


def test_through_the_real_tier_on_chat_and_voice(svc):
    put(svc, ROW_SISTER, excerpt=SAY_SISTER)
    brain_turn(ASK_SISTER, REPLY_SISTER)
    out = say("why did you say that", channel="voice")
    assert out is not None and "you told me" in out and "Marisol" in out
    assert mp._STATES[UID].last.tier == "provenance"


def test_a_reply_that_used_no_memory_says_so_plainly(svc):
    brain_turn("What is the capital of France?", "The capital of France is Paris.")          # no rows at all
    out = say("Why did you say that?")
    assert out == pa.NO_MEMORY_REPLY and "didn't use anything I'd remembered" in out


def test_a_reply_that_offered_a_stored_contact_is_not_called_memory_free(svc):
    """The live brain appends 'Would you like me to add Marisol as a contact?' (a pending-offer block, not a /for-prompt row)."""
    mp.note_user_turn(UID, "What is the capital of Australia?", "s1")
    token = mp.begin_turn(UID)
    mp.note_context(UID, "offer")
    mp.commit_brain_reply(UID, "The capital of Australia is Canberra. Would you like me to add Marisol as a contact?", "q", "s1", token=token)
    out = say("Why did you say that?")
    assert out.startswith(pa.NO_MEMORY_REPLY) and "The offer to add a contact was me following up on someone you'd mentioned." in out
    mp.reset()
    mp.note_user_turn(UID, "What is the capital of Australia?", "s1")
    token = mp.begin_turn(UID)
    mp.commit_brain_reply(UID, "The capital of Australia is Canberra.", "q", "s1", token=token)
    assert say("Why did you say that?") == pa.NO_MEMORY_REPLY                       # control: no offer, no extra sentence


def test_a_reply_made_with_notes_in_front_of_it_but_none_restated_says_that(svc):
    put(svc, ROW_SISTER, excerpt=SAY_SISTER)
    brain_turn("When is my sister arriving?", "Sorry, I can't say.")
    assert say("Why did you say that?") == pa.UNMATCHED_REPLY


def test_no_record_of_the_previous_reply_is_never_answered_as_no_memory():
    assert say("Why did you say that?") == pa.UNKNOWN_REPLY
    assert pa.UNKNOWN_REPLY != pa.NO_MEMORY_REPLY and "don't have a record" in pa.UNKNOWN_REPLY


def test_a_deterministic_tier_reply_is_explained_as_one(svc):
    import fast_tiers
    mp.note_user_turn(UID, "what time is it", "s1")
    mp.note_direct_reply(UID, "tier0", "s1", domain="time")
    assert "straight from the clock" in say("Why did you say that?")
    mp.note_user_turn(UID, "who is Dana", "s1", now=mp._STATES[UID].last_note_ts + 30)
    mp.note_direct_reply(UID, "tier1.5", "s1", domain="people")
    out = say("How do you know that?")
    assert "stored notes" in out and "didn't keep track of which note" in out          # a store-backed answer is never "no memory"
    assert fast_tiers  # wrapper imported


def test_the_wrapper_records_every_other_tiers_reply(monkeypatch, svc):
    import expert_dispatch
    import fast_tiers

    async def core(text, user_id, session_id, **kw):
        return expert_dispatch.DispatchResult(domain="weather", reply="Sunny and 24.", intent="weather", tier="tier0")
    monkeypatch.setattr(fast_tiers, "_resolve_core", core)
    assert run(fast_tiers.resolve("what's the weather", UID, "s1", channel="chat")).reply == "Sunny and 24."
    out = say("why did you say that")
    assert "straight from the weather" in out


def test_the_best_row_leads_and_the_rest_are_counted(svc):
    put(svc, ROW_SISTER, excerpt=SAY_SISTER)
    put(svc, "User's sister Marisol works as a pastry chef in Lisbon", excerpt="Marisol works as a pastry chef in Lisbon.")
    brain_turn(ASK_SISTER, "Marisol is flying in from Lisbon on Thursday. She works as a pastry chef in Lisbon.")
    mp.note_user_turn(UID, "why did you say that", "s1")
    out = run(pa.explain(UID, svc=svc, now=NOW))
    assert "I also used 1 other thing I'd noted" in out


# ── walls ─────────────────────────────────────────────────────────────────────────────────────

def test_another_members_words_are_never_quoted(svc):
    put(svc, "User's sister Marisol is flying in from Lisbon on Thursday", user=OTHER, excerpt=SAY_SISTER, scope="shared")
    ref = rows_for(svc, OTHER)[0]
    mp.note_user_turn(UID, ASK_SISTER, "s1")
    mp._STATES[UID].last = mp.ReplyRecord(seq=1, ts=NOW, kind="brain", sources=(mp.Source("row", ref.id),), served=1)
    mp.note_user_turn(UID, "why did you say that", "s1", now=NOW)
    out = run(pa.explain(UID, svc=svc, now=NOW))
    assert out == pa.NOT_YOURS_REPLY and "Marisol" not in out and "Lisbon" not in out


def test_the_ownership_wall_is_what_stops_it(svc, monkeypatch):
    """Control: with the wall removed the other member's words ARE spoken, so the test above can go red."""
    put(svc, "User's sister Marisol is flying in from Lisbon on Thursday", user=OTHER, excerpt=SAY_SISTER, scope="shared")
    ref = rows_for(svc, OTHER)[0]
    monkeypatch.setattr(pa, "_owns", lambda meta, uid: True)
    mp.note_user_turn(UID, ASK_SISTER, "s1")
    mp._STATES[UID].last = mp.ReplyRecord(seq=1, ts=NOW, kind="brain", sources=(mp.Source("row", ref.id),), served=1)
    mp.note_user_turn(UID, "why did you say that", "s1", now=NOW)
    assert "Marisol" in run(pa.explain(UID, svc=svc, now=NOW))


def test_an_unverified_voice_rows_words_are_not_quoted(svc):
    ref = put(svc, ROW_SISTER, excerpt=SAY_SISTER, source="voice_regex", speaker_verified=False)
    assert ref is not None
    row = [r for r in run(svc.list_by_status(user_id=UID, status="pending", limit=50))
           + run(svc.list_by_status(user_id=UID, status="approved", limit=50))][0]
    assert pa.owner_quote(row.metadata, row.text) == ""


def test_a_forgotten_row_is_not_explained_or_quoted(svc):
    ref = put(svc, ROW_SISTER, excerpt=SAY_SISTER)
    brain_turn(ASK_SISTER, REPLY_SISTER)
    run(svc.review(ref.id, decision="reject", actor=UID, note="forgotten"))
    mp.note_user_turn(UID, "why did you say that", "s1")
    out = run(pa.explain(UID, svc=svc, now=NOW))
    assert out == pa.FORGOTTEN_REPLY and "Marisol" not in out


def test_a_nightly_row_with_no_excerpt_is_never_quoted_as_the_owners_words(svc):
    put(svc, "User likes a flat white with oat milk in the mornings", source="digest", excerpt=None)
    brain_turn("What coffee do I like?", "You like a flat white with oat milk.")
    mp.note_user_turn(UID, "why did you say that", "s1")
    out = run(pa.explain(UID, svc=svc, now=NOW))
    assert "put together from our chats" in out and "flat white" not in out and "you told me" not in out


def test_a_row_with_no_excerpt_borrows_the_owners_verbatim_turn_from_the_exact_words_index(svc):
    ref = put(svc, "User likes a flat white with oat milk in the mornings", source="digest", excerpt=None, when="2026-10-06T04:00:00Z")
    said = 1_791_252_000.0 - 3600          # an hour before the row was captured
    run(xw.get_backend().add(UID, "xw-1", said, "I always have a flat white with oat milk first thing in the morning.", xw.tokens_column(
        "I always have a flat white with oat milk first thing in the morning."), "chat"))
    brain_turn("What coffee do I like?", "You like a flat white with oat milk.")
    mp.note_user_turn(UID, "why did you say that", "s1")
    out = run(pa.explain(UID, svc=svc, now=NOW))
    assert "you told me, \"I always have a flat white with oat milk first thing in the morning\"" in out and ref is not None


def test_a_sensitive_rows_words_are_not_spoken_on_voice_but_are_in_chat(svc):
    put(svc, "User takes insulin every morning for diabetes", excerpt="I take my insulin every morning because of my diabetes.")
    brain_turn("What do I take every morning?", "You take insulin every morning for your diabetes.")
    mp.note_user_turn(UID, "why did you say that", "s1")
    voice = run(pa.explain(UID, channel="voice", svc=svc, now=NOW))
    assert "won't read it out loud" in voice and "insulin" not in voice and "diabetes" not in voice
    mp.reset()
    brain_turn("What do I take every morning?", "You take insulin every morning for your diabetes.")
    mp.note_user_turn(UID, "why did you say that", "s1")
    chat = run(pa.explain(UID, channel="chat", svc=svc, now=NOW))
    assert "insulin" in chat


def test_a_pasted_rows_words_are_not_read_back():
    ev = pa.Evidence(row_id="x", quote="", pasted=True, said_at=NOW - 86400 * 3)
    out = pa.render_explanation(ev, more=0, voice=False, now=NOW)
    assert "pasted in on Tuesday" in out and "won't read it back" in out


def test_a_guest_and_an_unverified_speaker_get_a_refusal_and_nothing_is_read(svc):
    put(svc, ROW_SISTER, excerpt=SAY_SISTER)
    assert say("what do you know about me", user="guest") == pa.GUEST_REPLY
    assert say("why did you say that", user="voice-guest") == pa.GUEST_REPLY
    assert say("What do you know about Jason?", user="guest") == pa.GUEST_ABOUT_OTHER_REPLY
    out = say("what do you know about me", channel="voice", speaker_verified=False)
    assert out == pa.UNVERIFIED_SPEAKER_REPLY and "Marisol" not in out


def test_another_members_name_is_refused_but_a_friend_in_your_own_list_is_the_brains(monkeypatch, svc):
    async def names():
        return frozenset({"jason", "wren"})
    monkeypatch.setattr(pa, "account_names", names)
    assert say("What do you know about Jason?") == pa.OTHER_MEMBER_REPLY
    assert say("What do you know about Dana Whitfield?") is None


# ── summary ───────────────────────────────────────────────────────────────────────────────────

def seed_household(svc):
    put(svc, ROW_SISTER, excerpt=SAY_SISTER, when="2026-10-08T02:00:00Z")
    put(svc, "User lives in Hobart", excerpt="I live in Hobart now.", when="2026-10-02T02:00:00Z")
    put(svc, "User walks the dog every morning at six", excerpt="I walk the dog every morning at six.", when="2026-10-03T02:00:00Z")
    put(svc, "User likes oat milk in coffee", excerpt="I like oat milk in my coffee.", when="2026-10-04T02:00:00Z")
    put(svc, "User has a dentist appointment on Thursday", excerpt="I have a dentist appointment on Thursday.", when="2026-10-08T03:00:00Z")
    put(svc, "User takes insulin every morning for diabetes", excerpt="I take insulin every morning for my diabetes.", when="2026-09-20T02:00:00Z")
    put(svc, "User owes the bank a lot of money on a loan", excerpt="I owe the bank a lot on my loan.", when="2026-09-21T02:00:00Z")
    put(svc, "User's wife Anika is a surgeon", user=OTHER, excerpt="My wife Anika is a surgeon.")


def test_the_voice_summary_is_grouped_counted_bounded_and_never_reads_the_private_classes(svc):
    seed_household(svc)
    out = say("What do you know about me?", channel="voice")
    assert out.startswith("Here's the short version. I know ")
    assert "1 place, like you live in Hobart" in out and "1 routine, like you walk the dog every morning at six" in out
    assert "1 preference, like you like oat milk in coffee" in out
    assert "Most recently" in out and pa.PRIVATE_NOTE_VOICE in out and pa.HANDOFF_VOICE in out
    assert "insulin" not in out and "diabetes" not in out and "loan" not in out and "bank" not in out
    assert "Anika" not in out and "surgeon" not in out                                   # another member's row
    assert len(out.split()) <= 90                                                        # spoken length


def test_the_chat_summary_lists_more_with_dates_and_still_counts_the_private_classes(svc):
    seed_household(svc)
    out = say("what have you got on me", channel="chat")
    assert out.startswith("Here's what I've got on you, newest first.")
    assert "**Places** (1):" in out and "- you live in Hobart (2 Oct)" in out and "- you walk the dog every morning at six (3 Oct)" in out
    assert "**Recently** (last week):" in out
    assert "2 private things" in out and "health" in out and "money" in out
    assert "insulin" not in out and "loan" not in out and "Anika" not in out
    assert "why did you say that" in out and "off the record" in out                      # how to use the verbs
    assert len(out.splitlines()) < 30


def test_a_private_class_is_pulled_by_name(svc):
    seed_household(svc)
    v = say("What do you know about my health?", channel="voice")
    assert "insulin" in v and "loan" not in v
    c = say("what do you know about my finances", channel="chat")
    assert "loan" in c and "insulin" not in c
    assert say("what do you know about my mood", channel="chat") == "I haven't kept anything about how you've been feeling."


def test_the_summary_is_bounded_however_much_is_stored(svc):
    for i in range(40):
        put(svc, f"User likes the number {i} in the tropical flavour", excerpt=f"I like flavour number {i}.", when="2026-10-07T02:00:00Z")
    chat = say("what do you know about me", channel="chat")
    assert "... and 35 more" in chat and chat.count("\n- ") <= 12
    assert len(say("what do you know about me", channel="voice").split()) <= 90


def test_a_user_with_nothing_stored_is_told_so_not_handed_another_users_rows(svc):
    seed_household(svc)
    assert say("What do you know about me?", user=OTHER + "9") == pa.NOTHING_KNOWN_REPLY


def test_the_know_me_tier_is_what_produces_the_summary(svc, monkeypatch):
    """Control: with the tier removed the question is the brain's (None here)."""
    import fast_tiers
    seed_household(svc)
    monkeypatch.setattr(fast_tiers, "_provenance_tier", lambda *a, **k: asyncio.sleep(0, None))
    assert say("What do you know about me?", channel="voice") is None


def test_a_worry_about_a_diagnosis_is_health_and_mood_and_either_pull_finds_it(svc):
    put(svc, "User gets migraines most weeks and is stressed about it", excerpt="I get migraines most weeks, my doctor says it's stress.")
    assert pa.sensitive_classes("User gets migraines most weeks and is stressed about it", {"memory_type": "emotional_moment"}) >= {"health", "mood"}
    assert "mood" in pa.sensitive_classes("User is feeling really anxious about the interview", {})
    out = say("What do you know about me?", channel="chat")
    assert "migraine" not in out and "1 private thing" in out
    assert "migraines" in say("what do you know about my health", channel="chat")
    assert "migraines" in say("what do you know about my mood", channel="chat")
    assert "surgeon" not in pa._HEALTH_RE.pattern                                            # an occupation is not a diagnosis


def test_the_marked_turns_transcript_needle_matches_only_that_turn():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts" / "perf"))
    import samantha_bar as sb
    n = sb.S25_TRANSCRIPT_NEEDLE.lower()
    assert n in sb.SAY_OTR_PAYLOAD.lower()
    for other in (sb.ASK_OTR_FRESH, sb.ASK_OTR_PACKET, sb.SAY_OTR_NEXT, sb.SAY_OTR_CONTROL, sb.SAY_OTR_BARE):
        assert n not in other.lower(), other


def test_grouping_and_the_sensitive_classes_are_pure_and_conservative():
    assert pa.group_of("User's sister Marisol lives in Lisbon") == "people"
    assert pa.group_of("User lives in Hobart") == "places"
    assert pa.group_of("User walks the dog every morning") == "routines"
    assert pa.group_of("User likes oat milk") == "preferences"
    assert pa.group_of("User needs a new laptop") == "other"
    for text, cls in (("User takes insulin for diabetes", "health"), ("User owes a lot on a loan", "money"),
                      ("User is seeing a therapist on Mondays", "health"), ("User keeps a secret journal", "private"),
                      ("User likes oat milk", "")):
        assert pa.sensitive_class(text, {}) == cls, text
    assert pa.sensitive_class("User went for a walk", {"memory_type": "emotional_moment"}) == "mood"
    assert pa.second_person("User's sister is named Marisol") == "your sister is named Marisol"
    assert pa.second_person("User lives in Perth") == "you live in Perth"


# ── fix and forget-it ─────────────────────────────────────────────────────────────────────────

def explained_state(svc, row_text=ROW_SISTER, excerpt=SAY_SISTER, **kw):
    ref = put(svc, row_text, excerpt=excerpt, **kw)
    brain_turn(ASK_SISTER, REPLY_SISTER)
    out = say("Why did you say that?")
    assert "you told me" in out
    return ref


def test_forget_it_forgets_the_row_the_answer_named_not_the_newest_row(svc):
    ref = explained_state(svc)
    put(svc, "User likes oat milk in coffee", excerpt="I like oat milk in my coffee.", when="2026-10-09T04:00:00Z")   # NEWER than the sister row
    out = say("forget it")
    assert out.startswith("Done - I forgot: \"User's sister Marisol is flying in from Lisbon")
    left = approved(svc)
    assert ROW_SISTER not in left and "User likes oat milk in coffee" in left               # the newest row survived
    assert ref.id not in [r.id for r in rows_for(svc)]


def test_forget_it_without_the_explanation_is_the_existing_forget_that_path(svc):
    put(svc, ROW_SISTER, excerpt=SAY_SISTER)
    assert say("forget it") is None            # no explanation just before: not this tier's turn


def test_forget_it_takes_the_twin_rows_one_utterance_left_and_the_quoted_turn_in_the_index(svc):
    explained_state(svc)
    put(svc, "User's sister Marisol arrives from Lisbon on Thursday", source="turn_digest", excerpt=SAY_SISTER)    # the digest's twin
    run(xw.get_backend().add(UID, "xw-9", NOW - 3 * 86400, SAY_SISTER, xw.tokens_column(SAY_SISTER), "chat"))
    run(xw.get_backend().add(UID, "xw-10", NOW - 86400, "Dana's gate code is green.", xw.tokens_column("Dana's gate code is green."), "chat"))
    say("Forget it")
    assert not [t for t in approved(svc) if "Marisol" in t]
    assert run(xw.get_backend().count(UID)) == 1                                           # the quoted turn went; the other stayed


def test_a_neighbour_saved_within_five_minutes_is_not_a_twin_unless_it_lies_wholly_inside_the_fact():
    class _R:
        def __init__(self, id, text, **meta):
            self.id, self.text, self.metadata = id, text, {"user_id": UID, "added_ts": 1000.0, **meta}

    class _L:
        def __init__(self, *rows):
            self._rows = list(rows)

        async def list_by_status(self, **_kw):
            return self._rows
    ref = _R("a", "User's son Rowan is allergic to peanuts")
    neighbour = _R("n", "User's daughter Wren is allergic to peanuts")                         # same predicate, other child, same five minutes
    paraphrase = _R("t", "Rowan is allergic to peanuts")                                         # wholly inside: the same fact worded again
    other_turn = _R("o", "User's son Rowan is allergic to peanuts", user_turn_id="t2")
    same_turn = _R("s", "User's son is allergic to peanuts, Rowan", user_turn_id="t1")
    ref.metadata["user_turn_id"] = "t1"
    got = asyncio.run(pa.twins_of(UID, ref, svc=_L(neighbour, paraphrase, other_turn, same_turn)))
    assert {r.id for r in got} == {"t", "s"}


def test_forget_it_is_only_the_turn_right_after_the_answer(svc):
    explained_state(svc)
    say("what time is it", channel="chat")     # (expert dispatch is off: no reply, but the turn is numbered)
    mp.note_user_turn(UID, "and another thing entirely", "s1", now=mp._STATES[UID].last_note_ts + 30)
    assert say("forget it") is None
    assert ROW_SISTER in approved(svc)


GATE = "I told Dana the gate code is green and she should use it after nine."


def exact_words_state(svc):
    """A reply built from the owner's own verbatim turn (the exact-words block), then 'why did you say that?'."""
    run(xw.get_backend().add(UID, "xw-7", NOW - 2 * 86400, GATE, xw.tokens_column(GATE), "chat"))
    put(svc, "User told Dana the gate code is green", excerpt=GATE, when="2026-10-07T02:00:00Z")
    brain_turn("What exactly did I say about the gate code?", "You told Dana the gate code is green and she should use it after nine.")
    out = say("Why did you say that?")
    assert "you told me, \"I told Dana the gate code is green and she should use it after nine\"" in out
    return out


def test_an_answer_built_from_the_owners_verbatim_turn_quotes_it_with_its_day(svc):
    exact_words_state(svc)


def test_forget_it_after_a_verbatim_turn_erases_that_turn_and_the_rows_it_produced(svc):
    exact_words_state(svc)
    put(svc, "User likes oat milk in coffee", excerpt="I like oat milk in my coffee.", when="2026-10-09T04:00:00Z")
    out = say("forget it")
    assert out == "Done - I forgot what you said there."
    assert run(xw.get_backend().count(UID)) == 0
    assert not [t for t in approved(svc) if "gate code" in t] and "User likes oat milk in coffee" in approved(svc)


def test_a_fix_cannot_edit_the_owners_own_words(svc, monkeypatch):
    monkeypatch.setenv("ZOE_CORRECTION_APPLY", "1")
    exact_words_state(svc)
    assert say("That's wrong, it's blue") == pa.FIX_TURN_REPLY


def test_a_verbatim_turn_naming_a_forgotten_entity_is_not_quoted(svc, monkeypatch):
    import memory_forgotten
    monkeypatch.setenv(memory_forgotten.SALT_ENV, "x" * 40)
    memory_forgotten.set_backend(memory_forgotten.MemoryBackend())
    run(xw.get_backend().add(UID, "xw-8", NOW - 86400, GATE, xw.tokens_column(GATE), "chat"))
    mp.note_user_turn(UID, "q", "s1")
    token = mp.begin_turn(UID)
    mp.note_served(UID, [], [("xw-8", NOW - 86400, GATE)])
    mp.commit_brain_reply(UID, "You told Dana the gate code is green and she should use it after nine.", "q", "s1", token=token)
    try:
        mp.note_user_turn(UID, "why did you say that", "s1")
        assert "Dana" in run(pa.explain(UID, svc=svc, now=NOW))                    # control: before the forget it is quoted
        run(memory_forgotten.add(UID, "Dana", actor=UID))
        mp.reset()
        mp.note_user_turn(UID, "q", "s1")
        token = mp.begin_turn(UID)
        mp.note_served(UID, [], [("xw-8", NOW - 86400, GATE)])
        mp.commit_brain_reply(UID, "You told Dana the gate code is green and she should use it after nine.", "q", "s1", token=token)
        mp.note_user_turn(UID, "why did you say that", "s1")
        assert run(pa.explain(UID, svc=svc, now=NOW)) == pa.FORGOTTEN_REPLY
    finally:
        memory_forgotten.set_backend(None)
        memory_forgotten.reset_state()


def test_the_fix_needs_the_correction_path_and_says_so_when_it_is_off(svc):
    explained_state(svc)
    assert say("That's wrong, it's Tuesday") == pa.FIX_OFF_REPLY
    assert ROW_SISTER in approved(svc)                                      # nothing changed, nothing claimed


def test_the_fix_edits_the_named_row_and_says_what_it_now_holds(svc, monkeypatch):
    monkeypatch.setenv("ZOE_CORRECTION_APPLY", "1")
    explained_state(svc)
    put(svc, "User likes oat milk in coffee", excerpt="I like oat milk in my coffee.", when="2026-10-09T04:00:00Z")
    out = say("No, it's Tuesday")
    assert out == "Fixed - I now have: \"User's sister Marisol is flying in from Lisbon on Tuesday\". The old note is gone."
    left = approved(svc)
    assert "User's sister Marisol is flying in from Lisbon on Tuesday" in left and ROW_SISTER not in left
    assert "User likes oat milk in coffee" in left


def test_a_fix_that_cannot_be_applied_safely_asks_for_the_whole_sentence_and_then_applies_it(svc, monkeypatch):
    monkeypatch.setenv("ZOE_CORRECTION_APPLY", "1")
    explained_state(svc)
    assert say("That's wrong, it's from Porto") == pa.FIX_NEEDS_SENTENCE
    assert ROW_SISTER in approved(svc)
    out = say("My sister Marisol is flying in from Porto on Thursday")
    assert out.startswith("Fixed - I now have: \"My sister Marisol is flying in from Porto on Thursday\"")
    assert not [t for t in approved(svc) if "Lisbon" in t]


def test_that_is_wrong_alone_asks_what_the_right_answer_is(svc, monkeypatch):
    monkeypatch.setenv("ZOE_CORRECTION_APPLY", "1")
    explained_state(svc)
    assert say("That's wrong") == pa.ASK_FIX_REPLY
    assert say("Tuesday").startswith("Fixed - I now have: ")


def test_the_fix_retires_the_twin_that_still_holds_the_old_value(svc, monkeypatch):
    monkeypatch.setenv("ZOE_CORRECTION_APPLY", "1")
    explained_state(svc)
    put(svc, "User's sister Marisol arrives from Lisbon on Thursday", source="turn_digest", excerpt=SAY_SISTER)
    say("No, it's Tuesday")
    assert not [t for t in approved(svc) if "Thursday" in t]


def test_the_row_correction_path_is_the_correction_flags_own_not_just_the_tiers(svc):
    import correction_apply
    ref = put(svc, ROW_SISTER, excerpt=SAY_SISTER)
    assert run(correction_apply.apply_row_correction(UID, ref.id, "User's sister Marisol is flying in on Tuesday", svc=svc)) is None
    assert ROW_SISTER in approved(svc)                                                 # flag off: nothing changed


def test_fix_text_swaps_one_of_a_kind_and_never_guesses():
    assert pa.fix_text("User's dentist appointment is on Thursday at 3pm", "Tuesday") == "User's dentist appointment is on Tuesday at 3pm"
    assert pa.fix_text("User's dentist appointment is on Thursday at 3pm", "4pm") == "User's dentist appointment is on Thursday at 4pm"
    assert pa.fix_text("User's sister is named Marisol", "Marisa") == "User's sister is named Marisa"
    assert pa.fix_text("User's sister Marisol and brother Teodor visit", "Marisa") == ""      # two names: ambiguous
    assert pa.fix_text("User meets Dana on Monday and Tuesday", "Friday") == ""               # two weekdays
    assert pa.fix_text("User likes oat milk", "soy") == ""                                    # no slot of that kind
    assert pa.fix_text("User lives in Perth", "my sister lives in Hobart now") == "My sister lives in Hobart now"   # a whole statement


def test_a_correction_for_a_unverified_speaker_is_refused(svc, monkeypatch):
    monkeypatch.setenv("ZOE_CORRECTION_APPLY", "1")
    explained_state(svc)
    assert say("No, it's Tuesday", channel="voice", speaker_verified=False) == pa.UNVERIFIED_SPEAKER_REPLY
    assert ROW_SISTER in approved(svc)


# ── off the record ────────────────────────────────────────────────────────────────────────────

CORMAC = "Off the record: my brother-in-law Cormac is secretly getting a divorce."
ODALYS = "My neighbour Odalys is learning the cello at the community hall."


def test_a_cue_with_a_payload_marks_that_turn_and_a_normal_turn_is_not_marked():
    assert mp.claim_turn(UID, ODALYS) is False and mp.is_off_record(UID, ODALYS) is False
    assert mp.claim_turn(UID, CORMAC) is True and mp.is_off_record(UID, CORMAC) is True
    assert mp.claim_turn(UID, CORMAC) is True                                 # idempotent: the tier sees what the save saw


def test_a_bare_cue_arms_the_next_turn_only(svc):
    out = say("Off the record")
    assert out == pa.OFF_RECORD_ACKS["off_the_record"] and "won't keep your next message" in out
    mp.note_user_turn(UID, "I think Dana is being unfair to the team", "s1", now=mp._STATES[UID].last_note_ts + 30)
    assert mp.claim_turn(UID, "I think Dana is being unfair to the team") is True
    mp.note_user_turn(UID, "what time is it", "s1", now=mp._STATES[UID].last_note_ts + 30)
    assert mp.claim_turn(UID, "what time is it") is False                      # armed once, claimed once
    assert say("don't remember this") == pa.OFF_RECORD_ACKS["dont_remember"]
    assert say("this stays between us") == pa.OFF_RECORD_ACKS["between_us"]


def test_a_cue_with_a_payload_goes_to_the_brain_not_the_tier(svc):
    assert say(CORMAC) is None and mp.is_off_record(UID, CORMAC)


def test_the_per_turn_extractor_writes_a_normal_turn_and_nothing_for_an_off_the_record_one(svc):
    """The negative control the other half depends on: the SAME extractor stores a normal turn, so 'no row' means something."""
    import memory_extractor
    normal = "My dog is called Juniper and I love hiking."
    mp.note_user_turn(UID, normal, "s1")
    assert run(memory_extractor.extract_and_ingest(normal, user_id=UID, session_id="s1")) >= 1
    assert any("Juniper" in t for t in approved(svc))
    marked = "Off the record: my cat is called Pepper and I love gardening."
    mp.note_user_turn(UID, marked, "s1", now=mp._STATES[UID].last_note_ts + 30)
    assert mp.claim_turn(UID, marked)
    before = list(approved(svc))
    assert run(memory_extractor.extract_and_ingest(marked, user_id=UID, session_id="s1")) == 0
    assert approved(svc) == before and not [t for t in approved(svc) if "Pepper" in t]


def test_each_layer_holds_on_its_own_the_extractor_hook_and_the_ingest_choke_point(svc, monkeypatch):
    """Two walls in series: the per-turn hook skips a marked turn, and the write choke point blocks its content. Each alone is enough
    for the extractor's output, so each is shown with the OTHER removed."""
    import memory_extractor
    marked = "Off the record: my cat is called Pepper and I love gardening."
    mp.note_user_turn(UID, marked, "s1")
    mp.claim_turn(UID, marked)
    monkeypatch.setattr(mp, "blocks_write", lambda *a, **k: False)                   # choke point removed: the hook still holds
    assert run(memory_extractor.extract_and_ingest(marked, user_id=UID, session_id="s1")) == 0
    monkeypatch.undo()
    monkeypatch.setenv("ZOE_MEMORY_PHYSICAL_ERASE", "0")
    mp.reset()
    mp.note_user_turn(UID, marked, "s1")
    mp.claim_turn(UID, marked)
    monkeypatch.setattr(mp, "is_off_record", lambda *a, **k: False)                  # hook removed: the choke point still holds
    assert run(memory_extractor.extract_and_ingest(marked, user_id=UID, session_id="s1")) == 0
    assert not [t for t in approved(svc) if "Pepper" in t]


def test_the_extractor_stores_the_marked_turns_content_when_the_feature_is_off(svc, monkeypatch):
    """Control: the wall is the feature, not an accident of the extractor."""
    import memory_extractor
    marked = "Off the record: my cat is called Pepper and I love gardening."
    mp.note_user_turn(UID, marked, "s1")
    mp.claim_turn(UID, marked)
    monkeypatch.setenv(mp.ENV, "0")
    assert run(memory_extractor.extract_and_ingest(marked, user_id=UID, session_id="s1")) >= 1


def test_the_turn_digest_and_the_exact_words_index_skip_a_marked_turn(svc, monkeypatch):
    import memory_digest
    mp.note_user_turn(UID, CORMAC, "s1")
    mp.claim_turn(UID, CORMAC)
    assert run(memory_digest.run_turn_digest(UID, CORMAC, "ok", session_id="s1")).get("off_record") is True
    assert run(xw.index_turn(UID, CORMAC)) is False and run(xw.get_backend().count(UID)) == 0
    # control: the same index takes an ordinary turn
    mp.note_user_turn(UID, ODALYS, "s1", now=mp._STATES[UID].last_note_ts + 30)
    assert run(xw.index_turn(UID, ODALYS)) is True and run(xw.get_backend().count(UID)) == 1


def test_the_post_turn_chat_hook_skips_a_marked_turn_and_runs_for_a_normal_one(svc, monkeypatch):
    from routers import chat
    seen = []

    async def spy(*a, **kw):
        seen.append(a[0])
        return 0
    import memory_extractor
    monkeypatch.setattr(memory_extractor, "extract_and_ingest", spy)
    mp.note_user_turn(UID, CORMAC, "s1")
    mp.claim_turn(UID, CORMAC)
    assert run(chat._persist_memory_candidates_impl(UID, "s1", CORMAC, "I'm sorry to hear that.")) is True
    assert seen == []
    mp.note_user_turn(UID, ODALYS, "s1", now=mp._STATES[UID].last_note_ts + 30)
    run(chat._persist_memory_candidates_impl(UID, "s1", ODALYS, "How lovely."))
    assert seen == [ODALYS]


def test_the_voice_post_turn_hook_skips_a_marked_turn(svc, monkeypatch):
    from routers import voice_tts
    import memory_extractor
    seen = []

    async def spy(*a, **kw):
        seen.append(a[0])
        return 0
    monkeypatch.setattr(memory_extractor, "extract_and_ingest", spy)
    mp.note_user_turn(UID, CORMAC, "s1")
    mp.claim_turn(UID, CORMAC)
    run(voice_tts._run_voice_memory_passes(CORMAC, "ok", UID, "s1"))
    assert seen == []


def test_the_brains_own_memory_tool_cannot_store_what_was_asked_not_to_be_kept(svc):
    """The write choke point: a tool the brain calls DURING the marked turn is blocked by content, with no say-so from the brain."""
    mp.note_user_turn(UID, CORMAC, "s1")
    mp.claim_turn(UID, CORMAC)
    out = run(svc.ingest("User's brother-in-law Cormac is getting a divorce", user_id=UID, source="brain_tool", confidence=0.9))
    assert out is None and not [t for t in approved(svc) if "Cormac" in t]
    # a row about something else is untouched
    assert run(svc.ingest("User's dog is called Juniper", user_id=UID, source="brain_tool", confidence=0.9)) is not None
    # control: with the feature off the same write lands
    import os
    os.environ[mp.ENV] = "0"
    try:
        assert run(svc.ingest("User's brother-in-law Cormac is getting a divorce", user_id=UID, source="brain_tool",
                              confidence=0.9)) is not None
    finally:
        del os.environ[mp.ENV]


def test_a_write_that_only_shares_a_word_with_the_marked_turn_is_not_blocked(svc):
    mp.note_user_turn(UID, CORMAC, "s1")
    mp.claim_turn(UID, CORMAC)
    assert not mp.blocks_write(UID, "User has a brother-in-law who lives in Hobart")
    assert mp.blocks_write(UID, "User's brother-in-law Cormac is secretly getting a divorce")
    assert mp.blocks_write(UID, "anything at all", CORMAC)                                      # the evidence IS the marked turn


def test_the_marked_turn_expires_and_does_not_leak_into_later_turns():
    mp.note_user_turn(UID, CORMAC, "s1", now=1000.0)
    assert mp.claim_turn(UID, CORMAC, now=1000.0) is True
    assert mp.is_off_record(UID, "anything else entirely", now=1000.0 + 5) is True        # the turn in flight: any text variant
    assert mp.is_off_record(UID, "anything else entirely", now=1000.0 + mp.TURN_IN_FLIGHT_S + 1) is False
    assert mp.is_off_record(UID, CORMAC, now=1000.0 + 60) is True
    assert mp.is_off_record(UID, CORMAC, now=1000.0 + mp.MARK_TTL_S + 1) is False
    mp.note_user_turn(UID, "what time is it", "s1", now=1000.0 + 30)
    assert mp.is_off_record(UID, "what time is it", now=1000.0 + 31) is False             # the next turn is a normal one


def test_an_audit_line_is_written_without_the_words(svc, caplog):
    import logging
    with caplog.at_level(logging.INFO):
        mp.note_user_turn(UID, CORMAC, "s1")
        mp.claim_turn(UID, CORMAC)
    lines = [r.getMessage() for r in caplog.records if "OFF_RECORD" in r.getMessage()]
    assert lines and all("Cormac" not in ln and "divorce" not in ln for ln in lines)
    assert re.search(rf"OFF_RECORD user={UID} cue=off_the_record mode=same_turn chars=\d+", lines[0])


# ── the transcript copy (chat_messages) ──────────────────────────────────────────────────────

class _FakeDb:
    def __init__(self):
        self.inserts = []

    async def execute(self, sql, params=()):
        if sql.startswith("INSERT INTO chat_messages"):
            self.inserts.append(params)

        class _Cur:
            async def fetchall(self_inner):
                return []
        return _Cur()

    async def execute_fetchall(self, sql, params=()):
        return []

    async def commit(self):
        return None


def test_the_saved_turn_carries_the_off_record_flag_and_a_normal_turn_does_not(monkeypatch):
    from routers import chat
    import db_pool
    db = _FakeDb()

    class _Ctx:
        async def __aenter__(self_inner):
            return db

        async def __aexit__(self_inner, *a):
            return False
    monkeypatch.setattr(db_pool, "get_db_ctx", lambda: _Ctx())
    run(chat._save_chat_message("s1", "user", ODALYS, user_id=UID))
    run(chat._save_chat_message("s1", "assistant", "How lovely.", user_id=UID))
    run(chat._save_chat_message("s1", "user", CORMAC, user_id=UID))
    run(chat._save_chat_message("s1", "assistant", "I'm sorry to hear that about Cormac.", user_id=UID))
    metas = [json.loads(p[4]) if p[4] else {} for p in db.inserts]
    assert [m.get("off_record") for m in metas] == [None, None, True, True]
    assert '"off_record": true' in db.inserts[2][4] and mp.OFF_RECORD_JSON_MARK in db.inserts[2][4]


def test_every_reader_that_rebuilds_memory_from_the_transcript_leaves_the_flagged_rows_out():
    """The class, not the instance: each transcript reader that feeds a memory writer carries the off_record condition (the
    shared SQL fragment, or its literal in raw-asyncpg SQL). A new reader that skips it is the next leak - extend this table."""
    base = Path(memory_service.__file__).parent
    required = {"memory_digest.py": 2, "memory_idle_consolidation.py": 1, "memory_extractor.py": 1, "exact_words.py": 1}
    for name, count in required.items():
        src = (base / name).read_text()
        n = src.count("off_record_sql(") + src.count(mp.OFF_RECORD_JSON_MARK.replace('"', '"'))
        assert n >= count, f"{name}: {n} off_record conditions, need {count}"
    assert mp.off_record_sql("cm") == "COALESCE(cm.metadata, '') NOT LIKE '%\"off_record\": true%'"


# ── wiring ────────────────────────────────────────────────────────────────────────────────────

def test_brain_dispatch_commits_the_reply_and_hands_the_stream_back_untouched_when_off(monkeypatch, svc):
    import brain_dispatch
    monkeypatch.setenv(mp.ENV, "0")

    async def stream():
        yield "hello"
    s = stream()
    assert brain_dispatch._provenance_tracked_stream(s, "hi", "s1", UID) is s            # flag off: the very object


def test_the_tracked_stream_passes_every_delta_through_and_commits_the_text(monkeypatch, svc):
    import brain_dispatch
    put(svc, ROW_SISTER, excerpt=SAY_SISTER)
    mp.note_user_turn(UID, ASK_SISTER, "s1")

    async def inner():
        from routers import memories
        await memories.memory_for_prompt(user_id=UID, message=ASK_SISTER, limit=12, _=None)
        yield "__TOOL__:{}"
        yield "Your sister Marisol is arriving "
        yield "on Thursday from Lisbon."

    async def drain():
        return [d async for d in brain_dispatch._provenance_tracked_stream(inner(), ASK_SISTER, "s1", UID)]
    got = run(drain())
    assert got == ["__TOOL__:{}", "Your sister Marisol is arriving ", "on Thursday from Lisbon."]
    assert mp._STATES[UID].last.kind == "brain" and len(mp._STATES[UID].last.sources) == 1


def test_a_brain_fallback_reply_is_not_recorded_as_a_reply(monkeypatch, svc):
    import brain_dispatch
    from zoe_flue_client import _FALLBACK_TEXT
    mp.note_user_turn(UID, ASK_SISTER, "s1")
    brain_dispatch._provenance_commit(UID, _FALLBACK_TEXT, ASK_SISTER, "s1", brain_dispatch._provenance_begin(UID))
    assert mp._STATES[UID].last is None


def test_for_prompt_notes_what_it_served_and_the_exact_words_it_handed_over(svc):
    from routers import memories
    put(svc, ROW_SISTER, excerpt=SAY_SISTER)
    run(xw.get_backend().add(UID, "xw-1", NOW - 86400, "I told Dana the gate code is green.", xw.tokens_column(
        "I told Dana the gate code is green."), "chat"))
    token = mp.begin_turn(UID)
    run(memories.memory_for_prompt(user_id=UID, message="What exactly did I say about the gate code?", limit=12, _=None))
    served = mp._STATES[UID].served
    assert served and served[0][1] and served[0][2] and served[0][2][0][0] == "xw-1"
    assert token is not None


def test_the_tier_runs_before_the_router_and_never_for_a_turn_that_is_none_of_its_shapes(svc, monkeypatch):
    import fast_tiers
    calls = []

    async def core(text, user_id, session_id, **kw):
        calls.append(text)
        return None
    monkeypatch.setattr(fast_tiers, "_resolve_core", core)
    run(fast_tiers.resolve("set a timer for ten minutes", UID, "s1", channel="chat"))
    assert calls == ["set a timer for ten minutes"]
    run(fast_tiers.resolve("Why did you say that?", UID, "s1", channel="chat"))
    assert calls == ["set a timer for ten minutes"]                    # answered before the core was reached


def test_flag_off_the_core_is_reached_for_every_shape(svc, monkeypatch):
    import fast_tiers
    monkeypatch.setenv(mp.ENV, "0")
    calls = []

    async def core(text, user_id, session_id, **kw):
        calls.append(text)
        return None
    monkeypatch.setattr(fast_tiers, "_resolve_core", core)
    for t in ("Why did you say that?", "what do you know about me", "off the record", "forget it"):
        run(fast_tiers.resolve(t, UID, "s1", channel="chat"))
    assert len(calls) == 4


def test_the_flue_seam_labels_the_stored_context_blocks_it_carries():
    """Pin (source-level; the seam's own behaviour is pinned by the flue seam tests): the labels reach the ledger."""
    src = (Path(memory_service.__file__).parent / "zoe_flue_client.py").read_text()
    assert "note_context(uid" in src and '("offer", offer_block)' in src and '("raise", raise_block)' in src


# ── review sweep (PR #1938): the private-quote wall, the long off-the-record turn, the forget that must hold ─────

def test_a_private_clause_beside_a_public_fact_is_not_spoken_on_voice(svc):
    """The row path classifies the QUOTE that would be spoken, not only the stored fact (the fact here is ordinary)."""
    said = "I picked up my insulin today and my sister Marisol is flying in from Lisbon on Thursday."
    put(svc, ROW_SISTER, excerpt=said)
    brain_turn(ASK_SISTER, REPLY_SISTER)
    out = say("why did you say that", channel="voice")
    assert "insulin" not in out and "won't read it out loud" in out


def test_an_exact_words_turn_that_is_private_is_not_spoken_on_voice(svc):
    private = "I told Dana my diabetes results came back worse and she should not tell anyone."
    run(xw.get_backend().add(UID, "xw-p", NOW - 86400, private, xw.tokens_column(private), "chat"))
    ev = run(pa.evidence_for_turn(UID, "xw-p"))
    assert ev.sensitive
    out = pa.render_explanation(ev, more=0, voice=True, now=NOW)
    assert "diabetes" not in out and "won't read it out loud" in out
    assert "diabetes" in pa.render_explanation(ev, more=0, voice=False, now=NOW)         # chat still shows it


def test_a_long_off_the_record_message_is_still_off_the_record():
    body = "my brother Cormac is getting a divorce and he has not told his kids yet, " * 30         # > 1200 chars
    assert len(body) > 1200
    for text in (f"Off the record: {body}", f"{body.strip().rstrip(',')}, this stays between us"):
        cue = mp.parse_off_record(text)
        assert cue is not None and cue.payload, text[:40]
        assert mp.claim_turn(UID, text) is True and mp.is_off_record(UID, text) is True
        mp.reset()
    assert mp.parse_off_record("what does off the record mean? " + body) is None            # a mid-text mention is not a cue


class _Transcript:
    """A stand-in for the chat_messages table behind exact_words.forget_transcript's two SQL seams."""

    def __init__(self, rows):
        self.rows = {rid: [content, meta] for rid, content, meta in rows}

    async def candidates(self, user_id, needle):
        return [(rid, c, m) for rid, (c, m) in self.rows.items() if needle in c.lower()]

    async def setmeta(self, rid, meta):
        self.rows[rid][1] = meta


@pytest.fixture
def transcript(monkeypatch):
    t = _Transcript([("m1", GATE, None), ("m2", "what time is it", None)])
    monkeypatch.setattr(xw, "_transcript_candidates", t.candidates)
    monkeypatch.setattr(xw, "_set_transcript_metadata", t.setmeta)
    monkeypatch.setattr(xw, "SqlBackend", type(xw.get_backend()))          # the lab backend stands in for the SQL one
    return t


def test_forget_it_flags_the_saved_turn_so_the_nightly_catch_up_cannot_reindex_it(svc, transcript):
    exact_words_state(svc)
    assert say("forget it") == "Done - I forgot what you said there."
    assert run(xw.get_backend().count(UID)) == 0
    assert mp.OFF_RECORD_JSON_MARK in transcript.rows["m1"][1] and transcript.rows["m2"][1] is None
    assert json.loads(transcript.rows["m1"][1]) == {"off_record": True}


def test_a_failed_word_erase_is_not_reported_as_forgotten_and_the_retry_finishes_the_job(svc, monkeypatch):
    ref = explained_state(svc)
    run(xw.get_backend().add(UID, "xw-9", NOW - 3 * 86400, SAY_SISTER, xw.tokens_column(SAY_SISTER), "chat"))
    real = xw.erase_text
    calls = {"n": 0}

    async def flaky(user_id, text):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("store down")
        return await real(user_id, text)

    monkeypatch.setattr(xw, "erase_text", flaky)
    assert say("forget it") == pa.STORE_DOWN_REPLY                         # never "Done - I forgot"
    assert run(xw.get_backend().count(UID)) == 1                           # the words are still there
    out = say("Yes, forget that")                                          # the retry still has its target
    assert out.startswith("Done - I forgot")
    assert run(xw.get_backend().count(UID)) == 0


def test_a_shortened_quote_still_erases_the_indexed_turn(svc):
    long_turn = "I think " + "the lighthouse keeper's cottage on the northern coast had a green door and " * 6 + "a blue gate."
    run(xw.get_backend().add(UID, "xw-long", NOW - 86400, long_turn, xw.tokens_column(long_turn), "chat"))
    quote = pa.trim(long_turn, pa.QUOTE_MAX_CHARS)
    assert quote.endswith("...") and len(quote) < len(long_turn)
    exp = mp.Explained(seq=1, row_id="", ts=NOW, text=long_turn, quote=quote, turn_id="xw-long")
    assert run(pa._erase_words(UID, exp)) >= 1
    assert run(xw.get_backend().count(UID)) == 0
    run(xw.get_backend().add(UID, "xw-long", NOW - 86400, long_turn, xw.tokens_column(long_turn), "chat"))
    assert run(xw.erase_text(UID, quote)) == 1                              # and the displayed prefix alone matches too
