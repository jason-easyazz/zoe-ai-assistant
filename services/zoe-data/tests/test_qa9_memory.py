"""QA round 9: a correction never re-types a person, the date corrections that were missed (verb word, two-word subject, a moved
date, a chain, correcting back), correction turns kept away from the record experts, a topic forget withdraws the pending offers
about it, and a question or a call-now emergency is never filed as a fact. Invented demo data only. ``ci_safe``."""
from __future__ import annotations

import asyncio
import json
import re

import pytest

import expert_dispatch as ed
import forget_topic
import medical_safety
import memory_provenance
import memory_quality as mq
import value_correction as vc

pytestmark = pytest.mark.ci_safe

UID = "demo_qa9_00000001"
GRANDSON = "User's grandson Rowan is 9 years old and has 3 goldfish"
SON = "User's son Fenn is 6 years old"


def run(coro):
    return asyncio.run(coro)


class _Row:
    def __init__(self, i, text):
        self.id, self.text, self.metadata = i, text, {"user_id": UID}


class _Svc:
    def __init__(self, *texts, hits=None):
        self.rows = [_Row(f"r{i}", t) for i, t in enumerate(texts)]
        self.hits = hits                    # the similarity hits; None = every row
        self.edits, self.ingested, self.archived = [], [], []

    async def list_by_status(self, **_k):
        return list(self.rows)

    async def search(self, *_a, **_k):
        return list(self.rows) if self.hits is None else [r for r in self.rows if r.id in self.hits]

    async def review(self, rid, decision=None, edits=None, actor=None, **_k):
        (self.edits if decision == "edit" else self.archived).append((rid, edits))
        return object()

    async def ingest(self, text, **_k):
        self.ingested.append(text)
        return _Row("new", text)


def _apply(svc, text, monkeypatch):
    import correction_apply
    import user_prefs

    async def opted_in(_u):
        return False

    monkeypatch.setattr(user_prefs, "is_memory_opted_out", opted_in)
    monkeypatch.setattr(correction_apply, "_is_unidentified", lambda _u: False)
    vc.reset_state()
    return run(vc.apply(text, UID, None, svc=svc))


# -- 1. a correction changes the value, never the KIND of the subject ---------------------------------------------------------------

def test_the_age_correction_rewrites_only_the_number(monkeypatch):
    svc = _Svc(GRANDSON)
    assert _apply(svc, "rowan is 10 years old now, not 9", monkeypatch) == 1
    assert svc.edits == [("r0", "User's grandson Rowan is 10 years old and has 3 goldfish")]


@pytest.mark.parametrize("candidate, stored", [
    ("User's pet is named Rowan and is ten years old", GRANDSON),
    ("User has a cat named Fenn.", SON),
    ("Fenn is a cat", SON),
    ("User's son Fenn is 6 years old", "User has a dog named Fenn"),
])
def test_a_model_guess_never_retypes_a_stored_subject(candidate, stored):
    assert mq.kind_flip(candidate, stored)
    svc = _Svc(stored)
    assert run(mq.reconcile_for_ingest(svc, candidate, UID)) == ("refuse", "r0")
    assert run(ed._ingest_or_supersede(svc, candidate, user_id=UID, source="idle_consolidation", session_id="s", user_turn_id="t",
                                       memory_type="fact", confidence=0.8, tags=[])) == "dropped"      # honest: NOT stored
    assert svc.ingested == [] and svc.archived == []          # no new row of another kind, the stored row stands


@pytest.mark.parametrize("text, want", [
    ("Fenn is a cat and is six years old", {"fenn": {"animal"}}),
    ("Pip, our dog, is six", {"pip": {"animal"}}),
    ("Rowan is my grandson and is ten", {"rowan": {"person"}}),
    ("Pip is a lovely cat", {"pip": {"animal"}}),
    ("Rowan is User's grandson", {"rowan": {"person"}}),
    ("Fenn is six years old and loves trucks", {}),
])
def test_the_kind_is_read_from_the_vocabulary_not_the_position(text, want):
    assert mq.subject_kinds(text) == want


@pytest.mark.parametrize("stored, incoming, refused", [
    ("User has a cat called Oscar Wilde", "User's friend Oscar Smith is a teacher", False),   # two different full names
    ("User has a cat called Oscar", "User's friend Oscar is a teacher", True),               # the same bare first name
    ("User has a cat called Oscar Wilde", "User's friend Oscar Wilde is a teacher", True),   # the same full name
    ("User has a cat called Oscar", "User's friend Oscar Smith is a teacher", False),        # a surname: a more specific subject
    ("User has a cat called Oscar Wilde", "User's friend Oscar is a teacher", True),          # bare "Oscar" IS Oscar Wilde (graph)
    ("User's son Fenn Smith is 7 years old", "Fenn is a cat", True),                         # the round-9 bug, with a surname
    ("User's son Fenn Smith is 7 years old", "Fenn Jones is a cat", False),
])
def test_names_compare_whole(stored, incoming, refused):
    svc = _Svc(stored, hits=set())
    assert (run(mq.reconcile_for_ingest(svc, incoming, UID))[0] == "refuse") is refused
    assert bool(mq.kind_flip(incoming, stored)) is refused


@pytest.mark.parametrize("text, refused", [
    ("Fenn is a dog trainer", False), ("Fenn is a cat lover", False), ("Fenn is a horse vet", False), ("Fenn is a dog-walker", False),
    ("Fenn is a cat", True), ("Fenn is a cat and is six", True), ("Fenn is a lovely cat called Fenn", True),
])
def test_an_animal_word_that_modifies_a_noun_is_not_a_kind(text, refused):
    assert (mq.subject_kinds(text) == {"fenn": {"animal"}}) is refused
    svc = _Svc("User's son is named Fenn", hits=set())
    assert (run(mq.reconcile_for_ingest(svc, text, UID))[0] == "refuse") is refused


def _people(monkeypatch, *names, owner=UID):
    """The member's people graph holds ``names`` (person rows of ``owner`` only)."""
    import people_graph
    import person_extractor

    async def ensure(_db):
        return object(), False

    async def roster(_db, _sql, params=()):
        return [(f"p{i}", n) for i, n in enumerate(names)] if params and params[0] == owner else []

    monkeypatch.setattr(person_extractor, "_ensure_db", ensure)
    monkeypatch.setattr(people_graph, "_all", roster)


@pytest.mark.parametrize("text", ["Pip is a baby rabbit", "User has a baby rabbit called Pip"])
def test_an_ambiguous_kind_never_refuses_on_words_alone(text, monkeypatch):
    assert mq.subject_kinds(text) == {"pip": {"person", "animal"}}               # two kind words: ambiguous
    assert mq.kind_flip(text, "User's son is named Pip") is None
    _people(monkeypatch)                                                          # nobody named Pip
    assert run(mq.reconcile_for_ingest(_Svc("User's son is named Pip", hits=set()), text, UID))[0] == "add"


@pytest.mark.parametrize("text", ["Pip is a baby rabbit", "Pip is a rabbit"])
def test_the_people_graph_decides_a_person_never_becomes_an_animal(text, monkeypatch):
    _people(monkeypatch, "Pip")
    assert run(mq.reconcile_for_ingest(_Svc(hits=set()), text, UID)) == ("refuse", None)
    _people(monkeypatch, "Pip", owner="demo_qa9_member_b")                        # another member's Pip does not count
    assert run(mq.reconcile_for_ingest(_Svc(hits=set()), text, UID))[0] == "add"


def test_a_modifier_never_consults_the_graph_and_is_stored(monkeypatch):
    _people(monkeypatch, "Fenn")
    assert run(mq.reconcile_for_ingest(_Svc(hits=set()), "Fenn is a dog trainer", UID))[0] == "add"
    assert run(mq.reconcile_for_ingest(_Svc("User's son is named Fenn", hits=set()), "Fenn is a cat", UID))[0] == "refuse"


def test_a_kind_after_an_adjective_or_before_and_is_still_refused():
    svc = _Svc("User's son is named Fenn", hits=set())
    assert run(mq.reconcile_for_ingest(svc, "Fenn is a cat and is six years old", UID)) == ("refuse", "r0")


def test_the_kind_check_reads_every_row_not_the_top_k():
    # the stored son row is deliberately NOT among the similarity hits (only the cat row is): the flip is still refused
    svc = _Svc("User has a cat called Pip", *(f"User likes walk number {i}" for i in range(40)), SON, hits={"r0"})
    assert run(mq.reconcile_for_ingest(svc, "User has a cat named Fenn.", UID)) == ("refuse", "r41")
    # another member's row with the same name does not block
    other = _Svc(SON, hits=set())
    other.rows[0].metadata = {"user_id": "demo_qa9_someone_else"}
    assert run(mq.reconcile_for_ingest(other, "User has a cat named Fenn.", UID))[0] == "add"
    # same-kind writes are unaffected
    assert run(mq.reconcile_for_ingest(_Svc(SON, hits=set()), "User's son Fenn is 7 years old", UID))[0] == "add"


def test_an_unreadable_store_refuses_only_a_kind_bearing_write():
    class _Down(_Svc):
        async def list_all_by_status_strict(self, **_k):
            raise RuntimeError("store unreachable")

    svc = _Down(SON, hits=set())
    assert run(mq.reconcile_for_ingest(svc, "User has a cat named Fenn.", UID)) == ("refuse", None)     # fail closed
    assert run(mq.reconcile_for_ingest(svc, "User likes long walks", UID))[0] == "add"                  # no kind: no lookup


@pytest.mark.parametrize("candidate, stored", [
    ("User's grandson Rowan is 10 years old", GRANDSON),       # same kind, new value
    ("User has a cat called Pip", SON),                       # another name
    ("User's son Fenn loves trucks", SON),
])
def test_same_kind_or_another_name_is_not_a_flip(candidate, stored):
    assert mq.kind_flip(candidate, stored) is None


def test_the_correction_turn_digest_drops_an_invented_kind():
    turn = "rowan is 10 years old now, not 9"
    assert vc.invented_on_correction_turn("User's pet is named Rowan and is ten years old", turn) is True
    assert vc.invented_on_correction_turn("User's grandson Rowan is 10 years old", turn) is False
    assert vc.invented_on_correction_turn("Rowan is 10", turn) is False
    session = ["evening zoe", turn, "whats good for tea tonight"]
    assert vc.kind_guess_on_correction("User's pet is named Rowan and is ten years old", session) is True
    assert vc.kind_guess_on_correction("User's pet is named Rowan", session + ["my pet rowan is a rabbit"]) is False   # said
    assert vc.kind_guess_on_correction("User has a cat called Pip", session) is False       # names no corrected subject


# -- 2. the date corrections that did not retire the old value ----------------------------------------------------------------------

REGO = "User's car rego is due on the 14th of December."
CLUB = "User's book club is on the 12th of December."


@pytest.mark.parametrize("said, row, want", [
    ("sorry the rego is due on the 21st of december not the 14th", REGO, "User's car rego is due on the 21st of December."),
    ("my rego is actually due on the 21st of december, not the 14th", REGO, "User's car rego is due on the 21st of December."),
    ("book club is on the 19th of december, not the 12th", CLUB, "User's book club is on the 19th of December."),
    ("book club moved again, its on the 19th of december now", CLUB, "User's book club is on the 19th of December."),
    ("my book club got moved to the 19th", CLUB, "User's book club is on the 19th of December."),
    ("actually isla is 6 after all, i got mixed up, her birthday is next month",
     "User's daughter Isla is 7 years old.", "User's daughter Isla is 6 years old."),
])
def test_the_missed_shapes_now_rewrite_the_stored_row(said, row, want, monkeypatch):
    assert vc.statement(said) is not None
    svc = _Svc(row, "User's son is named Eli")
    assert _apply(svc, said, monkeypatch) == 1
    assert svc.edits == [("r0", want)]


def test_a_chain_and_a_correction_back(monkeypatch):
    svc = _Svc("User's book club is on the 5th of December.")
    _apply(svc, "book club got moved from the 5th to the 12th of december", monkeypatch)
    svc.rows = [_Row("r1", svc.edits[-1][1])]
    _apply(svc, "book club is on the 19th of december, not the 12th", monkeypatch)
    assert svc.edits[-1] == ("r1", "User's book club is on the 19th of December.")
    svc = _Svc("Isla is seven years old")
    _apply(svc, "actually isla is 6 after all", monkeypatch)
    assert svc.edits == [("r0", "Isla is six years old")]


@pytest.mark.parametrize("row, said, want", [
    ("User's book club is on the 12th", "book club moved to the 5th of january", "User's book club is on the 5th of January"),
    ("User's book club is on the 12th of December.", "book club moved to the 5th of january",
     "User's book club is on the 5th of January."),
    ("User's book club is on December 12.", "book club moved to the 5th of january", "User's book club is on January 5."),
    ("User's book club is on the 12th of December.", "book club moved to the 5th", "User's book club is on the 5th of December."),
    ("User has a dentist appointment on the 20th that costs 20 dollars", "the dentist is on the 22nd of december, not the 20th",
     "User has a dentist appointment on the 22nd of December that costs 20 dollars"),
])
def test_a_stated_month_is_kept_and_an_unsaid_one_is_left_alone(row, said, want):
    assert vc.rewrite(row, vc.statement(said)) == want


BIRTHDAY = "User's book club is run by Bea, whose birthday is on the 12th of December"


@pytest.mark.parametrize("said", ["book club moved to the 5th of january", "book club is on the 19th of december, not the 12th"])
def test_a_date_inside_someone_elses_clause_is_never_rewritten(said):
    assert vc.rewrite(BIRTHDAY, vc.statement(said)) is None
    assert vc.rewrite("User's book club is with User's sister on the 12th of December", vc.statement(said)) is None


@pytest.mark.parametrize("row, said, want", [
    ("User's book club is on the 12th of December, run by Bea", "book club moved to the 5th of january",
     "User's book club is on the 5th of January, run by Bea"),
    ("User's book club is on the 12th of December, run by Bea", "book club is on the 19th of december, not the 12th",
     "User's book club is on the 19th of December, run by Bea"),
    # a comma-bounded aside with no relative word, then the club's OWN verb: the date is the club's
    ("User's book club, run by Bea, is on the 12th of December", "book club moved to the 5th of january",
     "User's book club, run by Bea, is on the 5th of January"),
    ("User's book club, run by Bea, is on the 12th of December", "book club is on the 19th of december, not the 12th",
     "User's book club, run by Bea, is on the 19th of December"),
])
def test_the_subjects_own_date_is_rewritten(row, said, want):
    assert vc.rewrite(row, vc.statement(said)) == want


def test_an_aside_with_a_relative_word_is_not_the_subjects():
    assert vc.rewrite("User's book club, whose host is Bea, is on the 12th of December",
                      vc.statement("book club moved to the 5th of january")) is None


@pytest.mark.parametrize("text", ["the meeting moved to friday", "it moved to the 5th", "so it is 5 not 6", "theo is 7, he loves trucks",
                                  "book club moved on"])
def test_negative_controls_still_no_correction(text):
    assert vc.statement(text) is None


def test_a_moved_date_with_two_dates_in_the_row_changes_nothing():
    corr = vc.statement("book club moved again, its on the 19th of december now")
    assert vc.rewrite("User's book club is on the 12th of December and the 26th of December", corr) is None


def test_the_old_value_is_kept_when_it_is_said():
    assert vc.statement("kit is 10 years old now, not 9").old == "9"      # the trailing ", not 9" is never an age-now clause


# -- 3. a correction is never a record expert's question ----------------------------------------------------------------------------

@pytest.mark.parametrize("text", ["sorry the rego is due on the 21st of december not the 14th",
                                  "my rego is actually due on the 21st of december, not the 14th",
                                  "book club is on the 19th of december, not the 12th",
                                  "book club moved again, its on the 19th of december now"])
@pytest.mark.parametrize("domain", ["calendar", "reminders", "lists"])
def test_a_correction_goes_to_the_brain(domain, text):
    assert ed._plan(domain, text) is None


@pytest.mark.parametrize("text, qualifier", [("whats on my calendar today", "today"), ("what have i got on tomorrow", "tomorrow"),
                                             ("do i have anything on friday", "friday")])
def test_real_calendar_questions_still_read_the_calendar(text, qualifier):
    assert ed._plan("calendar", text) == ("calendar_show", {"qualifier": qualifier}, "read")


# -- 4. a topic forget withdraws the pending offers about it ------------------------------------------------------------------------

class _Db:
    def __init__(self, offers):
        self.offers = offers

    async def fetch(self, _sql, _uid):
        return [dict(o) for o in self.offers]

    async def execute(self, _sql, oid):
        self.offers = [o for o in self.offers if o["id"] != oid]


def _offer(oid, name, rel=None, action="person_create"):
    slots = {"name": name, **({"relationship": rel} if rel else {})}
    phrase = f"Add {name} ({'your ' + rel}) as a contact?" if rel else f"Add {name} to your contacts?"
    return {"id": oid, "action_type": action, "description": "", "offer_phrase": phrase, "pre_filled_slots": json.dumps(slots)}


def test_a_topic_forget_withdraws_offers_naming_it(monkeypatch):
    import contextlib

    import pending_suggestions

    db = _Db([_offer("o1", "Wynne", "sister"), _offer("o2", "Wynne"), _offer("o3", "Tamsin", "partner"),
              _offer("o4", "Wynne", "sister", action="forget_alias")])

    @contextlib.asynccontextmanager
    async def ctx():
        yield db

    monkeypatch.setattr(pending_suggestions, "get_db_ctx", ctx)
    gone = run(forget_topic._withdraw_offers(UID, forget_topic.topic_groups("my sister"),
                                             ["my sister wynne lives down south, we talk most sundays"]))
    assert gone == 2 and [o["id"] for o in db.offers] == ["o3", "o4"]


@pytest.fixture
def forget_env(monkeypatch):
    import exact_words
    import memory_tombstones
    import turn_forget

    async def flag(_u, _t):
        return 1

    monkeypatch.setattr(exact_words, "forget_transcript", flag)
    turn_forget.reset()
    memory_tombstones.clear_all()
    exact_words.set_backend(exact_words.MemoryBackend())
    yield
    exact_words.set_backend(None)
    turn_forget.reset()
    memory_tombstones.clear_all()


def test_a_failed_offer_withdrawal_never_confirms_and_the_repeat_finishes(monkeypatch, forget_env):
    import contextlib

    import memory_tombstones
    import pending_suggestions
    import turn_forget

    db = _Db([_offer("o1", "Wynne", "sister"), _offer("o2", "Wynne"), _offer("o3", "Tamsin", "partner")])
    down = {"on": True}

    @contextlib.asynccontextmanager
    async def ctx():
        if down["on"]:
            raise RuntimeError("connection lost")      # a pool that existed and was lost (never a "no database" process)
        yield db

    monkeypatch.setattr(pending_suggestions, "get_db_ctx", ctx)
    turn_forget.note_user_turn(UID, "my sister wynne lives down south, we talk most sundays")
    svc = _Svc()
    res = run(forget_topic.forget(svc, UID, "my sister"))
    assert res.store_down and "can't say that's forgotten" in forget_topic.reply(res, "my sister")
    assert len(db.offers) == 3                                                     # nothing withdrawn, and nothing claimed
    assert memory_tombstones.matching_tombstone(UID, "Add Wynne (your sister) as a contact?")   # withheld meanwhile
    down["on"] = False                                                             # the member says it again
    res = run(forget_topic.forget(svc, UID, "my sister"))
    assert not res.store_down and res.turns_retracted == 1
    assert [o["id"] for o in db.offers] == ["o3"]


def test_after_a_restart_the_persisted_index_names_the_offer(monkeypatch, forget_env):
    import contextlib

    import exact_words
    import pending_suggestions
    import turn_forget

    db = _Db([_offer("o1", "Wynne"), _offer("o2", "Tamsin")])         # name-only offers: the topic word is not in them

    @contextlib.asynccontextmanager
    async def ctx():
        yield db

    monkeypatch.setattr(pending_suggestions, "get_db_ctx", ctx)
    turn_forget.reset()                                                 # a restart: no recent turn in process
    backend = exact_words.get_backend()
    run(backend.add(UID, "t1", 1.0, "my sister wynne lives down south", " my sister wynne lives down south ", "chat"))
    run(backend.add("demo_qa9_member_b", "t2", 1.0, "my sister tamsin lives up north", " my sister tamsin ", "chat"))
    res = run(forget_topic.forget(_Svc(), UID, "my sister"))
    assert not res.store_down
    assert [o["id"] for o in db.offers] == ["o2"]                       # Wynne's offer withdrawn; member B's index changed nothing


def test_an_unreadable_index_does_not_confirm_the_forget(monkeypatch, forget_env):
    import exact_words

    class _Down(exact_words.MemoryBackend):
        async def rows_matching(self, *_a, **_k):
            raise RuntimeError("index unreadable")

    exact_words.set_backend(_Down())
    assert run(forget_topic.forget(_Svc(), UID, "my sister")).store_down


@pytest.mark.parametrize("offer_name, forgotten", [
    ("Anne-Marie", "my sister anne marie lives up north"), ("Anne Marie", "my sister anne-marie lives up north"),
    ("Anne\u2011Marie", "my sister annemarie lives up north"),
    ("O'Neil", "my sister oneil lives up north"), ("ONeil", "my sister o\u2019neil lives up north"),
    ("Zo\u00eb", "my sister zoe lives up north"), ("Zoe", "my sister zo\u00eb lives up north"),
    ("Ren\u00e9e O\u2019Hara", "my sister renee o'hara lives up north"),
])
def test_decorated_names_are_withdrawn_both_ways(offer_name, forgotten, monkeypatch):
    import contextlib

    import pending_suggestions

    db = _Db([_offer("o1", offer_name), _offer("o2", "Tamsin")])     # slots stored JSON-escaped, as json.dumps writes them

    @contextlib.asynccontextmanager
    async def ctx():
        yield db

    monkeypatch.setattr(pending_suggestions, "get_db_ctx", ctx)
    run(forget_topic._withdraw_offers(UID, forget_topic.topic_groups("my sister"), [forgotten]))
    assert [o["id"] for o in db.offers] == ["o2"]                        # the forgotten person's offer goes; an unrelated one stays


def test_a_process_with_no_database_holds_no_offers(monkeypatch):
    import pending_suggestions

    assert run(pending_suggestions.withdraw_matching(UID, lambda _t: True)) == 0    # the bare unit run: never had a pool


# -- 5. a question or a call-now emergency is never a fact --------------------------------------------------------------------------

@pytest.mark.parametrize("fact", ["User is asking if they have a sister.", "User is asking about their cat",
                                  "The user wants a recipe for scones", "User asked whether Fenn can eat satay",
                                  "User would like to know how old Rowan is"])
def test_a_reported_question_is_not_storable(fact):
    assert mq.is_storable_fact(fact) == (False, "question_report")


@pytest.mark.parametrize("fact", ["User's sister lives in Hobart", "User asked their boss for a raise",
                                  "User wants to visit Japan next year", "User asked me to remember: my sister lives in Hobart"])
def test_real_facts_are_still_kept(fact):
    assert mq.is_storable_fact(fact)[0] is True


FALL = "i fell over in the kitchen and i cant get up"
MINED = ["User fell over in the kitchen and is unable to get up", "User fell over in the kitchen and cannot get up"]
#: what the CONSERVATIVE rule leaves as mined from the same turn: the detector does not read them as the emergency (follow-up design)
UNREAD = ["User falls over in the kitchen", "User cannot get up"]


def test_a_call_now_emergency_is_one_dated_health_event(monkeypatch):
    import datetime

    monkeypatch.delenv("ZOE_MEDICAL_SAFETY", raising=False)
    day = datetime.date(2026, 11, 13)
    want = "User had a fall on 13 November 2026 (a medical emergency)."
    assert {medical_safety.emergency_event(f, FALL, today=day) for f in MINED} == {want}
    assert medical_safety.emergency_event("User fell in the bathroom and can't get up on their own",
                                          "i fell in the bathroom and cant get up on my own", today=day) == want
    assert medical_safety.emergency_event(MINED[0], "", today=day) is None          # no source turn: not a CURRENT emergency
    for fact, said in [("User fell asleep on the couch", "i fell asleep on the couch"), ("User's phone fell over", "my phone fell over"),
                       ("User plays bowls every Thursday", FALL), (MINED[0], FALL + "\nmy son is 7"), *((f, FALL) for f in UNREAD)]:
        assert medical_safety.emergency_event(fact, said, today=day) is None


CHEST = "my son fenn has crushing chest pain and cant breathe"


@pytest.mark.parametrize("fact, said, want", [
    ("User fell over in the kitchen and is unable to get up", FALL, "User had a fall"),
    ("User's son Fenn has crushing chest pain and can't breathe", CHEST, "User's son Fenn had chest pain"),
    ("Fenn has crushing chest pain", CHEST, "Fenn had chest pain"),
])
def test_the_event_is_about_whoever_it_happened_to(fact, said, want):
    import datetime

    out = medical_safety.emergency_event(fact, said, today=datetime.date(2026, 11, 13))
    assert out == f"{want} on 13 November 2026 (a medical emergency)."


HELPED = "i fell and can't get up; my son max is helping"
WITH_HIM = "my son max fell and can't get up, i'm with him"


@pytest.mark.parametrize("fact, said, want", [
    # the CONSERVATIVE rule: a turn naming two people (the member AND a helper / a companion) converts nothing
    ("User fell and cannot get up", HELPED, None),
    ("User's son Max is helping User", HELPED, None),
    ("User's son Max fell and cannot get up", WITH_HIM, None),
    ("Max fell and cannot get up", WITH_HIM, None),
    ("User is with Max", WITH_HIM, None),
    ("User fell and cannot get up", WITH_HIM, None),
    # exactly one named person: their event, by NAME
    ("User's son Max fell and cannot get up", "my son max fell and can't get up", "User's son Max had a fall"),
    ("Max fell and cannot get up", "my son max fell and can't get up", "Max had a fall"),
])
def test_only_the_person_it_happened_to_gets_the_event(fact, said, want):
    import datetime

    out = medical_safety.emergency_event(fact, said, today=datetime.date(2026, 11, 13))
    assert out == (f"{want} on 13 November 2026 (a medical emergency)." if want else None)


@pytest.mark.parametrize("fact, said", [
    ("User has crushing chest pain", CHEST),                       # the model filed the son's emergency on the member
    ("They have crushing chest pain", CHEST),                       # nobody named
    ("Someone has crushing chest pain", "someone here has crushing chest pain"),
    ("User's son has crushing chest pain", "my son has crushing chest pain and cant breathe"),     # a relation alone, no name
])
def test_an_unclear_subject_is_not_rewritten(fact, said):
    assert medical_safety.emergency_event(fact, said) is None


TWO_SONS = "my sons eli and max are here and eli fell and cant get up"


@pytest.mark.parametrize("fact, said", [
    ("Eli fell and cannot get up", TWO_SONS), ("User's son Max fell and cannot get up", TWO_SONS),
    ("Max fell and cannot get up", "max and eli are both here, max fell and cant get up"),
    ("User's son Eli fell and cannot get up", "my son eli and my son max, eli fell and cant get up"),
])
def test_a_turn_naming_two_people_converts_nothing(fact, said, mem):
    assert medical_safety.emergency_event(fact, said) is None
    run(mem.ingest(fact, user_id=UID, source="turn_digest", source_excerpt=said, anchor_text=said))
    assert [r.text for r in _rows(mem)] == [fact]                               # stored exactly as mined


@pytest.mark.parametrize("flag", ["off", "shadow", "enforce"])
@pytest.mark.parametrize("fact", ["User's father has heart disease", "User has asthma", "User is diabetic",
                                  "User's father Ned has heart disease"])
def test_a_condition_is_never_an_emergency_event(fact, flag, mem, monkeypatch):
    monkeypatch.setenv("ZOE_MEDICAL_SAFETY", flag)
    said = "my dad ned has crushing chest pain and i cant breathe"
    assert medical_safety.emergency_event(fact, said) is None
    run(mem.ingest(fact, user_id=UID, source="turn_digest", source_excerpt=said, anchor_text=said))
    assert [r.text for r in _rows(mem)] == [fact]


@pytest.mark.parametrize("fact, said", [
    ("User had chest pain in 2020", "i had crushing chest pain in 2020"),
    ("User had crushing chest pain years ago", "i had crushing chest pain years ago"),
    ("User had a fall and could not get up last year", "last year i fell over and couldnt get up"),
    ("User fell and could not get up when they were a kid", "when i was a kid i fell over and couldnt get up"),
])
def test_a_historical_emergency_keeps_its_own_date(fact, said, mem):
    assert medical_safety.emergency_event(fact, said) is None
    run(mem.ingest(fact, user_id=UID, source="turn_digest", source_excerpt=said, anchor_text=said))
    assert [r.text for r in _rows(mem)] == [fact]                               # stored exactly as mined


HYPOTHETICAL = [
    ("User was told to call an ambulance if they have crushing chest pain", "I was told to call an ambulance if I have crushing chest pain"),
    ("User will call their son if they ever fall and cannot get up", "if I ever fall and can't get up, call my son"),
    ("User should ring triple zero if their chest hurts", "the doctor said I should ring triple zero if my chest hurts"),
    ("User asked what to do if they could not breathe", "what would you do if I couldn't breathe"),
]


@pytest.mark.parametrize("flag", ["off", "shadow", "enforce"])
@pytest.mark.parametrize("fact, said", HYPOTHETICAL)
def test_a_condition_or_instruction_is_stored_as_mined_and_the_reply_is_unchanged(fact, said, flag, mem, monkeypatch):
    import distress_handoff

    async def adult(_u):
        return False

    async def no_intercom(*_a, **_k):
        return False

    monkeypatch.setattr(distress_handoff, "_is_minor", adult)
    monkeypatch.setattr(medical_safety, "_intercom_reachable", no_intercom)
    monkeypatch.setenv("ZOE_MEDICAL_SAFETY", flag)
    assert medical_safety.emergency_event(fact, said) is None
    run(mem.ingest(fact, user_id=UID, source="turn_digest", source_excerpt=said, anchor_text=said))
    assert [r.text for r in _rows(mem)] == [fact]                               # stored exactly as mined
    v = medical_safety.detect(said)                                             # the reply side is untouched by this rule:
    reply = run(medical_safety.handle(said, UID))
    assert reply == (medical_safety.build_reply(v) if v.cls and flag == "enforce" else None)


@pytest.mark.parametrize("fact, said", [
    ("User fell over in the kitchen and cannot get up", FALL),
    ("User fell and could not get up", "i fell and could not get up"),          # "could not" is actual, not a condition
    # plain "when" is temporal, kept (the fact says "lying down": a "they" reference now never converts, see the other-person rule)
    ("User cannot breathe when lying down", "I can't breathe when I lie down"),
])
def test_a_present_actual_emergency_still_files_one_event(fact, said):
    assert medical_safety.emergency_event(fact, said) is not None


@pytest.mark.parametrize("flag", ["off", "shadow", "enforce"])
def test_the_filing_holds_in_every_mode_and_only_the_reply_follows_the_flag(flag, monkeypatch):
    import datetime

    import distress_handoff

    async def adult(_u):
        return False

    async def no_intercom(*_a, **_k):
        return False

    monkeypatch.setattr(distress_handoff, "_is_minor", adult)
    monkeypatch.setattr(medical_safety, "_intercom_reachable", no_intercom)
    monkeypatch.setenv("ZOE_MEDICAL_SAFETY", flag)
    day = datetime.date(2026, 11, 13)
    assert medical_safety.emergency_event(MINED[0], FALL, today=day) == "User had a fall on 13 November 2026 (a medical emergency)."
    reply = run(medical_safety.handle(FALL, UID))
    assert (reply is not None) == (flag == "enforce")                            # the flag gates the reply only


@pytest.fixture
def mem(monkeypatch):
    import memory_service
    from memory_service import MemoryService
    from test_memory_authority import _Col

    for k in ("ZOE_MEMORY_AUTHORITY", "ZOE_AFFECT_CONSENT_GATE", "ZOE_MEDICAL_SAFETY"):
        monkeypatch.delenv(k, raising=False)
    s = MemoryService(data_dir="/nonexistent/zoe-test-qa9-memory")
    col = _Col()
    s._collection = lambda: col

    async def no_audit(**_kw):
        return None

    async def opted_in(_uid):
        return False

    s._append_audit = no_audit
    monkeypatch.setattr(memory_service, "_user_opted_out", opted_in)
    return s


def _rows(s):
    return [r for st in ("approved", "pending") for r in run(s.list_by_status(user_id=UID, status=st))]


@pytest.mark.parametrize("flag", ["off", "shadow", "enforce"])
def test_the_fall_turn_files_no_routine_and_stays_recallable(mem, flag, monkeypatch):
    import provenance_answers as pa

    monkeypatch.setenv("ZOE_MEDICAL_SAFETY", flag)

    assert memory_provenance.is_off_record(UID, FALL) is False                   # the turn itself is NOT off the record
    assert memory_provenance.claim_turn("demo_qa9_00000002", FALL) is False
    for fact in MINED:
        run(mem.ingest(fact, user_id=UID, source="turn_digest", source_excerpt=FALL, anchor_text=FALL))
    rows = _rows(mem)
    assert len(rows) == 1                                                         # ONE event row, no routine / standing fact
    row = rows[0]
    for fact in UNREAD:         # the conservative rule's known miss: what the detector does not read stays as mined (follow-up)
        assert medical_safety.emergency_event(fact, FALL) is None
    assert row.text.startswith("User had a fall on ") and row.metadata.get("memory_type") == "event"
    assert "health" in str(row.metadata.get("tags") or "")
    assert pa.sensitive_class(row.text, row.metadata) == "health"                # private: off shared surfaces, never "Routines"
    assert pa.group_of(MINED[0], {}) == "routines"                               # what the raw fact WAS grouped as (the bug)


def test_ordinary_fell_turns_file_as_before(mem):
    run(mem.ingest("User fell asleep on the couch", user_id=UID, source="turn_digest", source_excerpt="i fell asleep on the couch"))
    run(mem.ingest("User's phone fell over", user_id=UID, source="turn_digest", source_excerpt="my phone fell over"))
    assert sorted(r.text for r in _rows(mem)) == ["User fell asleep on the couch", "User's phone fell over"]
    assert all(r.metadata.get("memory_type") != "event" for r in _rows(mem))


# -- QA round 9 review: the kind guard runs FIRST on every writer path; a restatement is remembered with what it displaced -----------

class _EntitySvc(_Svc):
    """A store whose son row is ENTITY-keyed (the person extractor's same-kind supersede reaches it without any text match)."""

    def __init__(self, *texts):
        super().__init__(*texts, hits=set())
        for r in self.rows:
            r.metadata = {"user_id": UID, "entity_id": "slug:fenn", "entity_type": "person", "pattern_type": "relationship"}

    async def list_by_entity(self, _uid, _ids, **_k):
        return list(self.rows)

    async def get(self, rid):
        return next((r for r in self.rows if r.id == rid), None)


def test_the_person_extractor_never_supersedes_a_son_into_a_cat(monkeypatch):
    import memory_service
    import person_extractor as pe

    svc = _EntitySvc("User's son is named Fenn")
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: svc)
    out = run(pe._ingest_to_mempalace("Fenn is a cat", UID, "Fenn", "slug:fenn", pattern_type="relationship"))
    assert out is None and svc.edits == [] and svc.archived == [] and svc.ingested == []


def test_a_same_kind_entity_update_still_supersedes(monkeypatch):
    import memory_service
    import person_extractor as pe

    svc = _EntitySvc("User's son Fenn is 6 years old")
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: svc)
    run(pe._ingest_to_mempalace("User's son Fenn is 7 years old", UID, "Fenn", "slug:fenn", pattern_type="relationship"))
    assert svc.edits == [("r0", "User's son Fenn is 7 years old")]


def test_the_write_chokepoints_refuse_a_kind_flip(mem):
    son = run(mem.ingest("User's son is named Fenn", user_id=UID, source="turn_digest"))
    cat = run(mem.ingest("Fenn is a cat", user_id=UID, source="turn_digest"))
    # review(edit): any writer path that supersedes by entity / name / text ends here
    assert run(mem.review(son.id, decision="edit", edits="Fenn is a cat", actor="memory_digest")) is None
    # supersede_by: the implicit / claim-key retirement passes end here
    assert run(mem.supersede_by(UID, son.id, cat.id, actor="memory_supersede")) is False
    assert "User's son is named Fenn" in [r.text for r in _rows(mem)]
    import memory_service

    assert memory_service._kind_flips("User's son Fenn is 7 years old", "User's son is named Fenn") is False   # same kind: not this wall


def test_every_writer_path_is_kind_guarded():
    """Static: the chokepoints call the guard, the person extractor calls it before its entity supersede, and every module that
    edits / supersedes rows is one this list was reviewed against (a NEW one fails here until it is checked and added)."""
    import inspect
    import pathlib

    import memory_service
    import person_extractor as pe

    assert "_kind_flips(" in inspect.getsource(memory_service.MemoryService.review)
    assert "_kind_flips(" in inspect.getsource(memory_service.MemoryService._supersede_by_sync)
    assert "kind_guard(" in inspect.getsource(mq.reconcile_for_ingest)
    src = inspect.getsource(pe._ingest_to_mempalace)
    assert src.index("kind_guard(") < src.index("_reconcile_same_kind_entity_row(")
    # the zoe-data directory from THIS file (never the cwd or an imported module's location), and only the path parts BELOW it
    # decide what is a test: an absolute checkout path that happens to contain "tests" must not exclude everything (CI did)
    here = pathlib.Path(__file__).resolve().parents[1]
    sources = [p for p in here.rglob("*.py") if "tests" not in p.relative_to(here).parts]
    assert len(sources) > 50, f"the module scan found only {len(sources)} sources under {here}: the scan is broken, not the code"
    writers = sorted(p.name for p in sources if p.name != "memory_service.py"
                     and re.search(r'decision="edit"|\.supersede_by\(', p.read_text(encoding="utf-8")))
    assert writers, "no writer module found: the scan is broken (never a vacuous pass)"
    assert writers == ["correction_apply.py", "expert_dispatch.py", "hold_the_fact.py", "memory_claims_apply.py",
                       "memory_curation.py", "memory_digest.py", "memory_extractor.py", "memory_supersede.py",
                       "named_relations.py", "person_extractor.py", "value_correction.py"], writers


# -- a restatement is remembered with the value it displaced ------------------------------------------------------------------------

CLUB_12 = "User's book club is on the 12th of December."


def _apply_keep(svc, text, monkeypatch):
    import correction_apply
    import user_prefs

    async def opted_in(_u):
        return False

    monkeypatch.setattr(user_prefs, "is_memory_opted_out", opted_in)
    monkeypatch.setattr(correction_apply, "_is_unidentified", lambda _u: False)
    return run(vc.apply(text, UID, None, svc=svc))


def test_a_restated_date_makes_the_displaced_one_stale(monkeypatch):
    import time

    vc.reset_state()
    before = time.time() - 60
    assert _apply_keep(_Svc(CLUB_12), "book club moved to the 19th of december", monkeypatch) == 1
    assert vc.superseded_by_correction(UID, "User's book club is on the 12th of December", said_at=before) is True
    assert vc.superseded_by_correction(UID, "User's book club is on the 12th of December", said_at=time.time() + 5) is False
    assert vc.superseded_by_correction(UID, "User's book club is on the 19th of December", said_at=before) is False


def test_a_restatement_with_nothing_stored_yet(monkeypatch):
    import time

    vc.reset_state()
    before = time.time() - 60
    assert _apply_keep(_Svc(), "book club moved to the 19th of december", monkeypatch) == 0
    assert vc.superseded_by_correction(UID, "User's book club is on the 12th of December", said_at=before) is True
    assert vc.superseded_by_correction(UID, "User's book club is on the 19th of December", said_at=before) is False


def test_a_chain_of_restatements(monkeypatch):
    import time

    vc.reset_state()
    before = time.time() - 60
    _apply_keep(_Svc(), "book club moved to the 12th of december", monkeypatch)
    _apply_keep(_Svc(), "book club moved again, its on the 19th of december now", monkeypatch)
    assert vc.superseded_by_correction(UID, "User's book club is on the 5th of December", said_at=before) is True
    assert vc.superseded_by_correction(UID, "User's book club is on the 12th of December", said_at=before) is True
    assert vc.superseded_by_correction(UID, "User's book club is on the 19th of December", said_at=before) is False   # latest wins
    vc.reset_state()


def test_the_emergency_event_keeps_the_mined_facts_authority_and_is_listed(mem):
    """QA round 9 review: the event text was resolved for authority AFTER normalising, read as an unanchored model assertion and
    dropped as unconfirmed from Memories and the health summary. Authority and evidence come from the fact as MINED."""
    import provenance_answers as pa

    run(mem.ingest(MINED[0], user_id=UID, source="turn_digest", source_excerpt=FALL, anchor_text=FALL))
    (row,) = _rows(mem)
    assert row.text.startswith("User had a fall on ")
    assert row.metadata.get("authority_class") == "user_stated_derived"
    known = run(pa.gather(UID, svc=mem))
    assert known.unconfirmed == 0
    assert [i.text for i in known.private.get("health", [])] == [row.text]       # listed (private, health), never dropped


@pytest.mark.parametrize("said, recorded, stale, kept", [
    # month-only move: the word diff was "December" alone, which no stale full-date fact matches
    ("book club moved to the 12th of january", "12th of December",
     "User's book club is on the 12th of December", "User's book club is on the 12th of January"),
    # day-only move
    ("book club moved to the 19th of december", "12th of December",
     "User's book club is on the 12th of December", "User's book club is on the 19th of December"),
])
def test_a_restatement_records_the_whole_displaced_date(said, recorded, stale, kept, monkeypatch):
    import time

    vc.reset_state()
    before = time.time() - 60
    assert _apply_keep(_Svc(CLUB_12), said, monkeypatch) == 1
    assert [c.old for _t, c in vc._REMEMBERED[UID.lower()]] == [recorded]
    assert vc.superseded_by_correction(UID, stale, said_at=before) is True
    assert vc.superseded_by_correction(UID, kept, said_at=before) is False
    vc.reset_state()


@pytest.mark.parametrize("make_pet, refused", [(True, False), (False, True)])
def test_a_pet_in_the_people_table_is_not_a_person(make_pet, refused, monkeypatch):
    """Pets live in the people table (correction_apply._make_pet): a graph match that is a pet is no proof of a human."""
    import correction_apply as ca
    import person_extractor
    from test_correction_apply import _people_db

    async def scenario():
        db = await _people_db()
        await db.execute("INSERT INTO people (id, user_id, name, relationship) VALUES (?, ?, ?, ?)", ("p1", UID, "Brindle", "friend"))
        await db.commit()
        if make_pet:
            assert await ca._make_pet(db, UID, "p1", "friend", "dog") >= 1
        async def ensure(_db):
            return db, False

        monkeypatch.setattr(person_extractor, "_ensure_db", ensure)
        try:
            return await mq.reconcile_for_ingest(_Svc(hits=set()), "Brindle is a dog", UID)
        finally:
            await db.close()

    assert (run(scenario())[0] == "refuse") is refused


# -- a kind refusal never logs the fact (privacy) -------------------------------------------------------------------------------------

SECRET_NAME, SECRET_FACT = "Quillon", "Quillon is a cat"


def _assert_log_is_clean(caplog, *, tagged: bool):
    import hashlib

    log = caplog.text.lower()
    for word in ("quillon", "cat"):                     # the planted name and every content word of the fact
        assert not re.search(rf"\b{word}\b", log), f"{word!r} leaked into the log"
    if tagged:
        assert "code=kind_flip" in log and hashlib.sha256(SECRET_FACT.encode()).hexdigest()[:8] in log


def test_the_person_extractor_kind_guard_logs_no_fact(monkeypatch, caplog):
    import logging

    import memory_service
    import person_extractor as pe

    caplog.set_level(logging.DEBUG)
    svc = _EntitySvc(f"User's son is named {SECRET_NAME}")
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: svc)
    assert run(pe._ingest_to_mempalace(SECRET_FACT, UID, SECRET_NAME, "slug:quillon", pattern_type="relationship")) is None
    _assert_log_is_clean(caplog, tagged=True)


def test_the_person_extractor_refuse_branch_logs_no_fact(monkeypatch, caplog):
    import logging

    import memory_service
    import person_extractor as pe

    class _SearchOnly:                                  # no listing call: the guard defers, the reconcile's hit check refuses
        async def search(self, *_a, **_k):
            return [_Row("r0", f"User's son is named {SECRET_NAME}")]

    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: _SearchOnly())
    assert run(pe._ingest_to_mempalace(SECRET_FACT, UID, SECRET_NAME, None)) is None
    _assert_log_is_clean(caplog, tagged=True)


def test_the_other_refusal_paths_log_no_fact(mem, monkeypatch, caplog):
    import logging

    caplog.set_level(logging.DEBUG)
    svc = _Svc(f"User's son is named {SECRET_NAME}", hits=set())
    assert run(ed._ingest_or_supersede(svc, SECRET_FACT, user_id=UID, source="idle_consolidation", session_id="s",
                                       user_turn_id="t", memory_type="fact", confidence=0.8, tags=[])) == "dropped"
    _people(monkeypatch, SECRET_NAME)
    assert run(mq.reconcile_for_ingest(_Svc(hits=set()), SECRET_FACT, UID))[0] == "refuse"
    son = run(mem.ingest(f"User's son is named {SECRET_NAME}", user_id=UID, source="turn_digest"))
    cat = run(mem.ingest(SECRET_FACT, user_id="demo_qa9_logcheck_other", source="turn_digest"))
    caplog.clear()                                      # the ingests above are not refusals
    assert run(mem.review(son.id, decision="edit", edits=SECRET_FACT, actor="memory_digest")) is None
    assert run(mem.supersede_by(UID, son.id, cat.id, actor="memory_supersede")) is False
    _assert_log_is_clean(caplog, tagged=False)


FOUNDED = "User's book club was founded on the 12th of December."
SCHEDULED = "User's book club is on the 12th of December."


def test_a_moved_date_rewrites_only_a_scheduled_date(monkeypatch):
    vc.reset_state()
    svc = _Svc(SCHEDULED, FOUNDED)
    assert _apply_keep(svc, "book club moved to the 19th of december", monkeypatch) == 1
    assert svc.edits == [("r0", "User's book club is on the 19th of December.")]            # the founding date is untouched
    vc.reset_state()


@pytest.mark.parametrize("row", [SCHEDULED, "User's book club meets on the 12th", "User's book club is scheduled for the 12th"])
def test_a_scheduled_only_store_is_still_rewritten(row, monkeypatch):
    vc.reset_state()
    svc = _Svc(row)
    assert _apply_keep(svc, "book club moved to the 19th of december", monkeypatch) == 1
    vc.reset_state()


@pytest.mark.parametrize("row", [FOUNDED, "User's book club started on the 12th of December", "User's book club has run since the 12th"])
def test_a_founded_only_store_is_unchanged(row, monkeypatch):
    import time

    vc.reset_state()
    before = time.time() - 60
    svc = _Svc(row)
    assert _apply_keep(svc, "book club moved to the 19th of december", monkeypatch) == 0 and svc.edits == []
    # the restatement is remembered with no old value: a late re-filing of the founding date is NOT judged stale
    assert vc.superseded_by_correction(UID, row.rstrip("."), said_at=before) is False
    vc.reset_state()


AMBULANCE = "i called an ambulance because quinlan has crushing chest pain"


@pytest.mark.parametrize("fact, said", [
    ("User called an ambulance because Quinlan has crushing chest pain", AMBULANCE),     # a synthetic name, in the fact
    ("User has crushing chest pain", AMBULANCE),                                         # the name only in a lowercase turn
    ("User has crushing chest pain", "I called an ambulance because Quinlan has crushing chest pain"),
    ("User called an ambulance because their neighbour has crushing chest pain",
     "i called an ambulance because my neighbour has crushing chest pain"),             # a kin word
    ("User has crushing chest pain", "i called an ambulance because he has crushing chest pain"),   # a pronoun
    ("User has crushing chest pain", "i rang triple zero, she has crushing chest pain"),
])
def test_another_person_in_the_turn_never_makes_the_member_an_emergency(fact, said, mem):
    assert medical_safety.emergency_event(fact, said) is None
    run(mem.ingest(fact, user_id=UID, source="turn_digest", source_excerpt=said, anchor_text=said))
    rows = _rows(mem)
    assert [r.text for r in rows] == [fact] and all(r.metadata.get("memory_type") != "event" for r in rows)


def test_the_single_person_fall_still_converts():
    assert medical_safety.emergency_event(MINED[0], FALL) is not None
    assert medical_safety.emergency_event(MINED[0], "Help. I fell over in the kitchen and I cant get up") is not None
