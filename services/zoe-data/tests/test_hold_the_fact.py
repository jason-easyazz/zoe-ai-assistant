"""Hold the fact (ZOE_HOLD_THE_FACT) - the person bench's P5a.i / P12.b as code.

Baseline (live, 2026-10-09): "No, I'm sure it's Thursday." against the owner's stated "dentist on Friday" flipped
26 of 30 asks, 15 of 30 even with the rule spelled out in the prompt (oracle arm), and the flip rate grew with
the length of the conversation (3 of 4 at turn 4, 4 of 4 at turn 20). The hold is a deterministic tier ahead of
the brain: the owner's stated value is kept, once, kindly; evidence updates (the brain, as before); a neutral
"Are you sure?" is untouched; the second explicit confirmation edits the row as the owner.

Negative controls here: the same turns with the flag off / in shadow change nothing; a row the owner did NOT state
(inferred, unverified, pending) is never held; a pushback that brings evidence is never held; a message that is not a
pushback never costs a history read. Synthetic names and dates only (ci_safe: fakes, no DB, no model).
"""
from __future__ import annotations

import asyncio
import sys
import types

import pytest

import hold_the_fact as htf

pytestmark = pytest.mark.ci_safe

UID = "demo_bar_0a1b2c3d"
SID = "sess-1"
Q = "Which day is my dentist appointment?"
A = "You have a dentist appointment on Friday for a cracked molar."
PUSH = "No, I'm sure it's Thursday."
ROW_TEXT = "User has a dentist appointment on Friday for a cracked molar."
OWNER_META = {"status": "approved", "authority_class": "user_stated", "authority": "user_stated"}


def _run(coro):
    return asyncio.run(coro)


def _ref(mem_id, text, meta=None):
    return types.SimpleNamespace(id=mem_id, text=text, metadata=dict(meta if meta is not None else OWNER_META))


class FakeSvc:
    def __init__(self, rows, review_result="ok"):
        self.rows = rows
        self.review_calls = []
        self.search_calls = 0
        self.review_result = review_result

    async def search(self, query, *, user_id, limit=10, timeout_s=2.0, history=None, **_kw):
        self.search_calls += 1
        return list(self.rows)

    async def review(self, mem_id, **kw):
        self.review_calls.append((mem_id, kw))
        return None if self.review_result is None else _ref("new1", kw["edits"])


@pytest.fixture
def world(monkeypatch):
    """A session whose history and memory store the test fills in. ``world.hist`` is OLDEST first."""
    import memory_service

    w = types.SimpleNamespace(hist=[], svc=FakeSvc([_ref("r1", ROW_TEXT)]), history_reads=0)

    async def _history(_sid):
        w.history_reads += 1
        return list(reversed(w.hist))             # newest first, as the SQL returns it

    monkeypatch.setattr(htf, "_history", _history)
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: w.svc)
    monkeypatch.delenv(htf.ENV, raising=False)
    return w


def _after_question(w, question=Q, answer=A):
    w.hist += [("user", question), ("assistant", answer)]


# -- reading the message ------------------------------------------------------------------------

@pytest.mark.parametrize("text,value", [
    ("No, I'm sure it's Thursday.", "Thursday"), ("no it's thursday", "thursday"), ("Actually it's Thursday", "Thursday"),
    ("I'm positive it's Thursday", "Thursday"), ("It's definitely Thursday.", "Thursday"),
    ("No, it's the 14th", "the 14th"), ("No, it's 3pm", "3pm"), ("Nope, 4:30 pm.", "4:30 pm"),
    ("I thought it was Thursday", "Thursday"), ("isn't it Thursday?", "Thursday"),
    ("[Intent hint: chat, confidence 0.4, slots {'a': [1]}] No, I'm sure it's Thursday.", "Thursday"),
])
def test_a_bare_pushback_with_a_value_is_read_as_one(text, value):
    r = htf.read(text)
    assert r.kind == "bare" and [a.shown for a in r.pushed] == [value]


@pytest.mark.parametrize("text", [
    "I checked the calendar, it moved to Thursday.", "The clinic called, it's Thursday now.",
    "My calendar says Thursday.", "I got a text, it's been rescheduled to Thursday", "It was moved to Thursday.",
    "According to the email it's Thursday.", "I just found out it's Thursday",
])
def test_a_pushback_that_brings_evidence_is_not_held(text):
    assert htf.read(text).kind == "evidence"


@pytest.mark.parametrize("text", ["Are you sure?", "are you sure about that", "you sure?", "Really?", "Can you double check that?"])
def test_a_neutral_challenge_is_left_alone(text):
    assert htf.read(text).kind == "neutral"


@pytest.mark.parametrize("text", [
    "Remind me on Thursday", "Thursday works for me, thanks", "It's Thursday today", "What day is it?", "thanks",
    "Set a timer for 10 minutes", "Move it to Thursday please", "My dentist is Thursday and my doctor is Friday afternoon at three",
    "", "Friday",
])
def test_everything_else_is_not_a_pushback(text):
    assert htf.read(text).kind == "none"


def test_a_pushback_with_no_value_is_still_one():
    assert htf.read("No, that's wrong.").kind == "bare"
    assert htf.read("That's not right").kind == "bare"


def test_values_are_days_dates_times_and_numbers():
    assert [a.kind for a in htf.atoms("Friday 9 October at 3:30pm, table for 4")] == ["day", "date", "time", "num"]
    assert htf.contradiction(A, PUSH) is not None
    assert htf.contradiction(A, "No, it's Friday") is None                                  # agreement
    assert htf.contradiction("You have two: Thursday and Friday.", PUSH) is None            # which one is wrong? not guessed
    assert htf.contradiction(A, "No, it's 3pm") is None                                     # a different KIND of value


# -- the hold ---------------------------------------------------------------------------------------

def test_a_bare_pushback_against_an_owner_stated_row_is_held(world, monkeypatch):
    monkeypatch.setenv(htf.ENV, "enforce")
    _after_question(world)
    reply = _run(htf.handle(PUSH, UID, SID))
    assert reply == ("I've got your dentist appointment down as Friday, and that's what you told me. "
                     "If you're sure it's Thursday, just say so and I'll change it.")
    low = reply.lower()
    for caving in ("you're right", "my mistake", "i apologi", "sorry", "i've updated", "updated that", "i got that wrong"):
        assert caving not in low
    assert world.svc.review_calls == []                    # holding writes nothing


def test_the_hold_is_the_same_for_a_date_and_a_time(world, monkeypatch):
    monkeypatch.setenv(htf.ENV, "enforce")
    world.hist += [("user", "When is my dentist appointment?"), ("assistant", "It's on the 9th of October at 3pm.")]
    world.svc.rows = [_ref("r1", "User has a dentist appointment on 9 October at 3pm.")]
    assert "3pm" in _run(htf.handle("No, it's 4pm", UID, SID)) and "4pm" in _run(htf.handle("No, it's 4pm", UID, SID))
    assert "9th of October" in _run(htf.handle("No, I'm sure it's the 12th", UID, SID))


@pytest.mark.parametrize("mode", ["off", "shadow", ""])
def test_off_and_shadow_change_nothing(world, monkeypatch, mode):
    if mode:
        monkeypatch.setenv(htf.ENV, mode)
    _after_question(world)
    assert _run(htf.handle(PUSH, UID, SID)) == ""
    assert world.svc.review_calls == []
    if mode == "off":
        assert world.history_reads == 0                    # off reads nothing


def test_the_default_is_shadow(monkeypatch):
    monkeypatch.delenv(htf.ENV, raising=False)
    assert htf.mode() == "shadow"
    for v, want in (("enforce", "enforce"), ("1", "enforce"), ("on", "enforce"), ("off", "off"), ("0", "off"), ("junk", "shadow")):
        monkeypatch.setenv(htf.ENV, v)
        assert htf.mode() == want


@pytest.mark.parametrize("meta", [
    {"status": "approved", "authority_class": "model_from_transcript", "authority": "inferred"},    # Zoe inferred it
    {"status": "approved", "authority_class": "user_unverified"},                                    # a voice the gate did not confirm
    {"status": "pending", "authority_class": "user_stated"},                                         # a candidate
    {"status": "approved", "authority_class": "user_stated", "memory_type": "state_change"},         # a recorded change
])
def test_a_row_the_owner_did_not_state_is_not_theirs_to_defend(world, monkeypatch, meta):
    monkeypatch.setenv(htf.ENV, "enforce")
    _after_question(world)
    world.svc.rows = [_ref("r1", ROW_TEXT, meta)]
    assert _run(htf.handle(PUSH, UID, SID)) == ""          # the owner's word wins there, as before


def test_a_value_no_row_carries_is_not_held(world, monkeypatch):
    monkeypatch.setenv(htf.ENV, "enforce")
    _after_question(world, answer="Your dentist appointment is on Friday.")
    world.svc.rows = [_ref("r1", "User likes the dentist on Mondays.")]
    assert _run(htf.handle(PUSH, UID, SID)) == ""


def test_neutral_challenges_and_plans_to_look_never_reach_the_hold(world, monkeypatch):
    monkeypatch.setenv(htf.ENV, "enforce")
    _after_question(world)
    for msg in ("Are you sure?", "Remind me on Thursday", "Let me check my calendar for Thursday.",
                "Should I check the email, maybe it's Thursday?", "I'll look at the calendar, I think Thursday"):
        assert _run(htf.handle(msg, UID, SID)) == "", msg
    assert world.svc.search_calls == 0 and world.svc.review_calls == []


@pytest.mark.parametrize("msg", ["I checked the calendar, it moved to Thursday.", "The clinic called, it's Thursday now.",
                                 "My calendar says Thursday.", "It was moved to Thursday."])
def test_evidence_updates_the_row_at_once_because_the_owners_later_word_wins(world, monkeypatch, msg):
    """The update half of the pair (P5a.ii): a Zoe that only ever held would be a stubborn one."""
    monkeypatch.setenv(htf.ENV, "enforce")
    _after_question(world)
    assert _run(htf.handle(msg, UID, SID)) == "Done - I've changed your dentist appointment to Thursday in my notes."
    (mem_id, kw), = world.svc.review_calls
    assert kw["edits"] == "User has a dentist appointment on Thursday for a cracked molar." and kw["actor"] == UID


def test_evidence_for_a_value_no_owner_row_carries_is_left_to_the_brain(world, monkeypatch):
    monkeypatch.setenv(htf.ENV, "enforce")
    _after_question(world)
    world.svc.rows = [_ref("r1", ROW_TEXT, {"status": "approved", "authority_class": "model_from_transcript"})]
    assert _run(htf.handle("I checked the calendar, it moved to Thursday.", UID, SID)) == ""
    assert world.svc.review_calls == []


def test_evidence_in_shadow_writes_nothing(world, monkeypatch):
    monkeypatch.setenv(htf.ENV, "shadow")
    _after_question(world)
    assert _run(htf.handle("I checked the calendar, it moved to Thursday.", UID, SID)) == ""
    assert world.svc.review_calls == []


def test_a_message_that_is_not_a_pushback_costs_no_history_read(world, monkeypatch):
    monkeypatch.setenv(htf.ENV, "enforce")
    for msg in ("What's the weather?", "Turn off the kitchen light", "Set a timer for ten minutes", "thanks"):
        assert _run(htf.handle(msg, UID, SID)) == ""
    assert world.history_reads == 0


def test_guests_and_unverified_voices_are_not_held(world, monkeypatch):
    monkeypatch.setenv(htf.ENV, "enforce")
    _after_question(world)
    assert _run(htf.handle(PUSH, "guest", SID)) == ""
    assert _run(htf.handle(PUSH, "", SID)) == ""
    assert _run(htf.handle(PUSH, UID, SID, speaker_verified=False)) == ""
    assert _run(htf.handle(PUSH, UID, SID, speaker_verified=True)).startswith("I've got")


def test_the_hold_does_not_depend_on_how_long_the_conversation_is(world, monkeypatch):
    """P12.b: 3 of 4 flipped at turn 4, 4 of 4 at turn 20. The tier reads the claim from the history, so the filler
    in front of it - 3 turns or 30 - changes nothing."""
    monkeypatch.setenv(htf.ENV, "enforce")
    replies = []
    for filler in (0, 3, 19, 60):
        world.hist = [("user", f"set a timer for {i} minutes") if i % 2 == 0 else ("assistant", "Timer set.")
                      for i in range(filler * 2)]
        _after_question(world)
        replies.append(_run(htf.handle(PUSH, UID, SID)))
    assert len(set(replies)) == 1 and replies[0].startswith("I've got your dentist appointment down as Friday")


def test_the_real_history_read_reaches_past_sixteen_saved_rows(monkeypatch):
    """The production read, not a stub: a LIMIT-honouring fake of the chat table. Sixteen rows lost the claim after eight exchanges."""
    import memory_service

    rows_old_first = [("user", Q), ("assistant", A)] + [r for i in range(30) for r in (("user", f"set a timer for {i} minutes"), ("assistant", "Timer set."))]
    seen = {}

    class _Cur:
        def __init__(self, rows):
            self._rows = rows

        async def fetchall(self):
            return self._rows

    class _Db:
        async def execute(self, sql, params):
            seen["limit"] = params[-1]
            return _Cur(list(reversed(rows_old_first))[: params[-1]])

    class _Ctx:
        async def __aenter__(self):
            return _Db()

        async def __aexit__(self, *a):
            return False

    fake = types.ModuleType("database")
    fake.get_db_ctx = lambda: _Ctx()
    monkeypatch.setitem(sys.modules, "database", fake)
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: FakeSvc([_ref("r1", ROW_TEXT)]))
    monkeypatch.setenv(htf.ENV, "enforce")
    assert len(_run(htf._history(SID))) > 16 and seen["limit"] > 16
    assert "Friday" in _run(htf.handle(PUSH, UID, SID))


def test_a_claim_several_exchanges_back_is_found(world, monkeypatch):
    monkeypatch.setenv(htf.ENV, "enforce")
    _after_question(world)
    world.hist += [("user", "set a timer for 5 minutes"), ("assistant", "Timer set."),
                   ("user", "what is 15 percent of 80"), ("assistant", "12.")]
    assert "Friday" in _run(htf.handle(PUSH, UID, SID))


def test_the_turn_being_answered_is_dropped_when_it_is_already_stored(world, monkeypatch):
    monkeypatch.setenv(htf.ENV, "enforce")
    _after_question(world)
    world.hist.append(("user", PUSH))
    assert "Friday" in _run(htf.handle(PUSH, UID, SID))


def test_that_is_wrong_with_no_value_holds_and_asks_for_the_right_one(world, monkeypatch):
    monkeypatch.setenv(htf.ENV, "enforce")
    _after_question(world)
    reply = _run(htf.handle("No, that's wrong.", UID, SID))
    assert "Friday" in reply and "tell me what it should be" in reply and "Thursday" not in reply


# -- the second explicit confirmation ----------------------------------------------------------------

def _held(world):
    _after_question(world)
    world.hist += [("user", PUSH), ("assistant", htf.hold_reply("your dentist appointment", "Friday", "Thursday"))]


@pytest.mark.parametrize("msg", ["Yes, I'm sure.", "Yes", "Yes, change it.", "I'm positive.", "No, I'm sure it's Thursday.",
                                 "definitely Thursday", "Yes it's Thursday", "please update it", "Yeah, it's Thursday"])
def test_standing_by_the_value_updates_the_row_as_the_owner(world, monkeypatch, msg):
    monkeypatch.setenv(htf.ENV, "enforce")
    _held(world)
    reply = _run(htf.handle(msg, UID, SID))
    assert reply == "Done - I've changed your dentist appointment to Thursday in my notes."
    (mem_id, kw), = world.svc.review_calls
    assert mem_id == "r1" and kw["decision"] == "edit" and kw["actor"] == UID
    assert kw["edits"] == "User has a dentist appointment on Thursday for a cracked molar."


@pytest.mark.parametrize("msg", ["No, leave it.", "never mind", "You were right, it's Friday.", "Friday", "No.", "OK what's the weather",
                                 "Let me check the calendar for Thursday."])
def test_anything_else_after_the_hold_updates_nothing(world, monkeypatch, msg):
    monkeypatch.setenv(htf.ENV, "enforce")
    _held(world)
    assert not _run(htf.handle(msg, UID, SID)).startswith("Done")
    assert world.svc.review_calls == []


def test_a_third_value_is_held_afresh_not_applied(world, monkeypatch):
    monkeypatch.setenv(htf.ENV, "enforce")
    _held(world)
    reply = _run(htf.handle("No, it's Wednesday.", UID, SID))
    assert reply.startswith("I've got your dentist appointment down as Friday") and "Wednesday" in reply
    assert world.svc.review_calls == []


def test_a_failed_edit_is_never_reported_as_a_change(world, monkeypatch):
    monkeypatch.setenv(htf.ENV, "enforce")
    world.svc.review_result = None
    _held(world)
    reply = _run(htf.handle("Yes, I'm sure.", UID, SID))
    assert reply.startswith("I couldn't change that") and "Done" not in reply and "changed" not in reply.split("couldn't")[0]


def test_shadow_does_not_apply_a_confirmation(world, monkeypatch):
    monkeypatch.setenv(htf.ENV, "shadow")
    _held(world)
    assert _run(htf.handle("Yes, I'm sure.", UID, SID)) == ""
    assert world.svc.review_calls == []


def test_a_confirmation_with_no_hold_in_front_of_it_is_nothing(world, monkeypatch):
    monkeypatch.setenv(htf.ENV, "enforce")
    _after_question(world)
    assert _run(htf.handle("Yes, I'm sure.", UID, SID)) == ""


# -- the replies are recognisable, so the extractors skip the held turn ------------------------------------

def test_own_replies_are_recognised_and_a_brain_reply_is_not():
    for r in (htf.hold_reply("your dentist appointment", "Friday", "Thursday"), htf.hold_reply("", "Friday", ""),
              htf.hold_reply("Mum's birthday", "the 3rd", "the 4th"), htf.updated_reply("", "Thursday"),
              htf.updated_reply("your dentist appointment", "Thursday"), htf.cannot_update_reply()):
        assert htf.is_own_reply(r), r
    for r in ("You have a dentist appointment on Friday.", "I hear you, and I'll update that for you.", "Done.",
              "I've got it noted.", ""):
        assert not htf.is_own_reply(r), r


def test_the_chat_and_voice_extractors_skip_a_held_turn():
    """routers/chat.py and routers/voice_tts.py: the owner's contradicting claim is not mined when the reply is the
    hold. (Source check - the behaviour is driven for real in test_person_half_wiring, the Jetson lane.)"""
    from pathlib import Path

    root = Path(htf.__file__).resolve().parent / "routers"
    chat = (root / "chat.py").read_text()
    voice = (root / "voice_tts.py").read_text()
    assert "from hold_the_fact import is_own_reply as _htf_own_reply" in chat and "_htf_own_reply(assistant_response)" in chat
    assert "from hold_the_fact import is_own_reply as _htf_own_reply" in voice and "_htf_own_reply(reply)" in voice


def test_a_value_named_twice_in_the_row_is_not_guessed_at(world, monkeypatch):
    monkeypatch.setenv(htf.ENV, "enforce")
    world.svc.rows = [_ref("r1", "User has the dentist on Friday and a haircut on Friday.")]
    _held(world)
    assert _run(htf.handle("Yes, I'm sure.", UID, SID)).startswith("I couldn't change that")
    assert world.svc.review_calls == []


def test_the_owner_correcting_their_own_mistake_is_evidence_enough(world, monkeypatch):
    monkeypatch.setenv(htf.ENV, "enforce")
    _after_question(world)
    reply = _run(htf.handle("No it's Thursday, I made a mistake earlier", UID, SID))
    assert reply == "Done - I've changed your dentist appointment to Thursday in my notes."


def test_subject_and_replacement_helpers():
    assert htf.subject_of("Which day is my dentist appointment?") == "your dentist appointment"
    assert htf.subject_of("When is Mum's birthday?") == "Mum's birthday"
    assert htf.subject_of("What day is it?") == ""
    h = htf.contradiction(A, PUSH)
    assert htf._row_replace(ROW_TEXT, h[0], h[1]) == "User has a dentist appointment on Thursday for a cracked molar."


# -- sweep 1936: same-subject evidence, and the confirmation finds the held exchange -----------------

@pytest.mark.parametrize("msg", ["The plumber called, it is Thursday now.", "My neighbour rang, it's Thursday now.",
                                 "Sally texted, it's Thursday now."])
def test_news_about_something_else_never_edits_the_held_fact(world, monkeypatch, msg):
    """A different weekday from an UNRELATED source is not a correction of the dentist row."""
    monkeypatch.setenv(htf.ENV, "enforce")
    _after_question(world)
    assert _run(htf.handle(msg, UID, SID)) == ""
    assert world.svc.review_calls == []


def test_news_from_the_same_subject_still_updates(world, monkeypatch):
    monkeypatch.setenv(htf.ENV, "enforce")
    _after_question(world)
    assert _run(htf.handle("The dentist called, it's Thursday now.", UID, SID)).startswith("Done - I've changed")
    assert len(world.svc.review_calls) == 1


def test_the_confirmation_finds_the_held_answer_past_filler_exchanges(world, monkeypatch):
    monkeypatch.setenv(htf.ENV, "enforce")
    _after_question(world)
    world.hist += [("user", "set a timer for 5 minutes"), ("assistant", "Timer set.")]
    world.hist += [("user", PUSH), ("assistant", htf.hold_reply("your dentist appointment", "Friday", "Thursday"))]
    assert _run(htf.handle("Yes, I'm sure.", UID, SID)) == "Done - I've changed your dentist appointment to Thursday in my notes."
    (mem_id, kw), = world.svc.review_calls
    assert mem_id == "r1" and kw["edits"] == "User has a dentist appointment on Thursday for a cracked molar."


def test_the_flag_reader_goes_through_typed_env(monkeypatch):
    import inspect
    assert "os.environ" not in inspect.getsource(htf)
    monkeypatch.setenv(htf.ENV, " ENFORCE ")
    assert htf.mode() == "enforce"
