"""QA round 8: a topic forget said BEFORE the capture filed the fact holds, and the fall / cannot-get-up turn gets the number and a way
to get someone in the house. Invented demo data only. ``ci_safe``."""
from __future__ import annotations

import asyncio

import pytest

import forget_topic
import intent_router
import medical_safety as ms
import memory_service
from memory_service import MemoryService
from test_memory_authority import _Col

pytestmark = pytest.mark.ci_safe

UID = "demo_qa8_00000001"
SISTER_SAID = "my sister Odelle lives out past the ranges and we talk most sundays"
SISTER_FACT = "User has a sister named Odelle who lives out past the ranges"
LONELY_SAID = "honestly i've been feeling really lonely since the move"
LONELY_FACT = "User has been feeling lonely since moving house"


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def svc(monkeypatch):
    for k in ("ZOE_MEMORY_AUTHORITY", "ZOE_AFFECT_CONSENT_GATE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ZOE_MEMORY_PHYSICAL_ERASE", "0")
    s = MemoryService(data_dir="/nonexistent/zoe-test-qa8-memory")
    col = _Col()
    s._collection = lambda: col

    async def no_audit(**_kw):
        return None

    async def opted_in(_uid):
        return False

    s._append_audit = no_audit
    monkeypatch.setattr(memory_service, "_user_opted_out", opted_in)
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: s)
    import exact_words
    import turn_forget

    flagged: list = []

    async def flag(user_id, text):
        flagged.append((user_id, text))
        return 1

    monkeypatch.setattr(exact_words, "forget_transcript", flag)
    turn_forget.reset()
    exact_words.set_backend(exact_words.MemoryBackend())
    s.flagged = flagged
    yield s
    exact_words.set_backend(None)
    turn_forget.reset()


def _live(svc):
    return [r.text for r in run(svc.list_by_status(user_id=UID, status="approved"))]


def _digest_files(svc, said, fact):
    """What the turn digest does minutes later: write the fact from that turn, inside that turn's capture scope."""
    import turn_forget

    with turn_forget.capture_scope(UID, said):
        return run(svc.ingest(fact, user_id=UID, source="turn_digest", source_excerpt=said))


# ── 1. the forget shapes people actually say ─────────────────────────────────────────────────────────────────────────

def test_forget_that_i_told_you_about_is_a_topic_forget():
    assert forget_topic.parse("forget that I told you about my sister") == "my sister"
    assert forget_topic.parse("please forget that i just mentioned about my sister") == "my sister"
    d = intent_router.detect_intent("forget that I told you about my sister", log_miss=False)
    assert d.name == "memory_forget_entity" and d.slots == {"topic": "my sister"}


def test_being_is_not_a_topic_word():
    topic = forget_topic.parse("forget what I said about being lonely")
    assert forget_topic.topic_groups(topic) == [frozenset({"lonely"})]
    assert forget_topic.row_matches(forget_topic.topic_groups(topic), LONELY_SAID)


@pytest.mark.parametrize("text", ["forget that I said hi there", "forget that", "forget that it rained",
                                  "don't forget that I told you about the bins"])
def test_negative_controls_stay_where_they_were(text):
    assert forget_topic.parse(text) is None


# ── 2. the race: stated -> forgotten before the digest -> the digest runs ─────────────────────────────────────────────

@pytest.mark.parametrize("forget_words, said, fact", [
    ("forget that I told you about my sister", SISTER_SAID, SISTER_FACT),
    ("forget what I said about being lonely", LONELY_SAID, LONELY_FACT),
])
def test_a_forget_before_filing_holds_when_the_digest_runs(svc, forget_words, said, fact):
    import turn_forget

    turn_forget.note_user_turn(UID, said)
    turn_forget.note_user_turn(UID, "what's for dinner tonight")
    d = intent_router.detect_intent(forget_words, log_miss=False)
    assert d.name == "memory_forget_entity" and d.slots.get("topic")
    turn_forget.note_user_turn(UID, forget_words)
    reply = run(intent_router.execute_intent(d, UID))
    assert "forgotten" in reply.lower()                                       # never "nothing saved" about what WAS said
    assert "couldn't find" not in reply and "nothing saved" not in reply.lower() and "noted" not in reply.lower()
    assert (UID, said) in svc.flagged                                         # the saved transcript turn is off the record
    assert _digest_files(svc, said, fact) is None                             # the late capture is refused
    assert _live(svc) == []                                                   # not stored, so never recalled


def test_an_unrelated_turn_still_files(svc):
    import turn_forget

    turn_forget.note_user_turn(UID, SISTER_SAID)
    turn_forget.note_user_turn(UID, LONELY_SAID)
    run(forget_topic.execute(UID, "my sister"))
    assert _digest_files(svc, LONELY_SAID, LONELY_FACT) is not None
    assert _live(svc) == [LONELY_FACT]


def test_restating_after_the_forget_can_be_remembered_again(svc):
    import turn_forget

    turn_forget.note_user_turn(UID, SISTER_SAID)
    turn_forget.note_user_turn(UID, "forget that I told you about my sister")
    run(forget_topic.execute(UID, "my sister"))
    turn_forget.note_user_turn(UID, SISTER_SAID)                              # said again, deliberately: a new turn
    assert _digest_files(svc, SISTER_SAID, SISTER_FACT) is not None
    assert _live(svc) == [SISTER_FACT]


def test_nothing_said_and_nothing_stored_still_says_nothing_changed(svc):
    reply = run(forget_topic.execute(UID, "my cousin's wedding"))
    assert "nothing was changed" in reply and "forgotten" not in reply.lower()


# ── 3. a fall and cannot get up ──────────────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def au(monkeypatch):
    for k in (ms.ENV, "ZOE_HOUSEHOLD_COUNTRY", "ZOE_LOCATION_COUNTRY", "ZOE_DISTRESS_LINES_FILE", "ZOE_INTERCOM"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ZOE_HOUSEHOLD_COUNTRY", "AU")


@pytest.mark.parametrize("text", [
    "i fell over in the kitchen and i can't get up",
    "I've fallen and I can't get up",
    "mum fell in the bathroom and she cant get up",
    "dad had a fall and he cannot get up off the floor",
    "i'm on the floor and i can't get up",
    "I slipped in the shower and can not stand up",
])
def test_a_fall_and_cannot_get_up_is_a_call_now(text):
    assert (ms.detect(text).cls, ms.detect(text).key) == ("red_call", "fall")


@pytest.mark.parametrize("text", [
    "my phone fell over", "I fell asleep", "the stock market fell", "i fell asleep and can't get up early",
    "I fell behind and can't get up to speed", "the vase fell and i can't get up there", "I fell over yesterday but I'm fine now",
    "i can't get up early tomorrow", "if i fall i can't get up", "I fell but I can get up",
])
def test_negative_controls_are_not_a_fall(text):
    assert ms.detect(text).cls == ""


def test_the_fall_reply_names_000_and_how_to_get_someone(au):
    out = run(ms.handle("i fell over in the kitchen and i can't get up", "demo-user"))
    assert out.startswith("Ring 000 now.")
    assert "call out to someone in the house" in out
    assert "I have" not in out and "I've called" not in out and "I've alerted" not in out   # never claims an alert
    assert len(out) < 260                                                     # short enough to speak


def test_other_call_now_symptoms_get_the_same_way_to_get_someone(au):
    for text in ("i can't breathe", "my chest hurts", "i'm bleeding badly"):
        out = run(ms.handle(text, "demo-user"))
        assert out.startswith("Ring 000 now.") and out.endswith("call out to someone in the house.")


def test_the_intercom_phrase_is_offered_only_when_it_would_work(au, monkeypatch):
    import intercom

    monkeypatch.setattr(intercom, "_quiet_now", lambda now=None: False)
    panels = [{"panel_id": "p-kitchen"}, {"panel_id": "p-hall"}]

    async def speaker_panels(db):
        return list(panels)

    monkeypatch.setattr(intercom, "_speaker_panels", speaker_panels)
    import contextlib

    import db_compat

    @contextlib.asynccontextmanager
    async def no_db():
        yield None

    monkeypatch.setattr(db_compat, "get_compat_db", no_db)
    text = "i fell over in the kitchen and i can't get up"
    assert "tell everyone I need help" not in run(ms.handle(text, "demo-user", voice=True))        # intercom off (default)
    monkeypatch.setenv("ZOE_INTERCOM", "on")
    on = run(ms.handle(text, "demo-user", voice=True))
    assert "tell everyone I need help" in on
    assert intercom.parse("tell everyone I need help", []) is not None                             # the phrase IS an intercom request
    assert "tell everyone" not in run(ms.handle(text, "demo-user", voice=False))                  # the chat lane has no intercom
    assert "tell everyone" not in run(ms.handle(text, "guest", voice=True))                       # guests can't use it
    monkeypatch.setattr(intercom, "_quiet_now", lambda now=None: True)
    assert "tell everyone" not in run(ms.handle(text, "demo-user", voice=True))                   # quiet hours hold the intercom
    monkeypatch.setattr(intercom, "_quiet_now", lambda now=None: False)
    del panels[1]                                                                                  # a one-panel house: no OTHER panel
    alone = run(ms.handle(text, "demo-user", voice=True))
    assert "tell everyone" not in alone and alone.endswith("call out to someone in the house.")   # never a promise nothing can keep


@pytest.mark.parametrize("text", ["I've fallen, and I can't get up", "I fell over in the kitchen. I can't get up.",
                                  "I slipped; now I cannot stand up!", "I had a fall, I can't get up off the floor.",
                                  # a physical tail after "get up" is still a fall (the exclusions are non-physical phrases only)
                                  "I fell and I can't get up on my own", "I fell over and can't get up in the bathroom",
                                  "I fell and I can't get up at all", "I fell and I can't get up the stairs"])
def test_a_spoken_fall_with_punctuation_still_gets_the_number(au, text):
    """Speech-to-text transcripts carry commas and full stops between the clauses."""
    assert ms.detect(text).key == "fall" and run(ms.handle(text, "demo-user")).startswith("Ring 000 now.")


@pytest.mark.parametrize("text", ["My phone fell over. I can't get up early tomorrow.", "I fell asleep, and I couldn't get up to speed.",
                                  "The stock market fell. I can't get up there.", "I fell behind. I can't get up to date.",
                                  "i fell and cant get up for work", "I fell and I can't get up before nine",
                                  "I tripped and couldn't get up in time for the bus"])
def test_punctuated_talk_that_is_not_a_fall_is_left_alone(text):
    assert ms.detect(text).key != "fall"


def test_a_child_still_gets_the_grown_up_line_first(au, monkeypatch):
    import distress_handoff

    async def minor(_uid):
        return True

    monkeypatch.setattr(distress_handoff, "_is_minor", minor)
    assert run(ms.handle("i fell and i can't get up", "kid")).startswith("Tell a grown-up")


def test_distress_is_still_the_distress_hand_off():
    assert ms.detect("i feel like i can't go on anymore").cls == ""
