"""QA round 9: a correction never re-types a person, the date corrections that were missed (verb word, two-word subject, a moved
date, a chain, correcting back), correction turns kept away from the record experts, a topic forget withdraws the pending offers
about it, and a question or a call-now emergency is never filed as a fact. Invented demo data only. ``ci_safe``."""
from __future__ import annotations

import asyncio
import json

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
    def __init__(self, *texts):
        self.rows = [_Row(f"r{i}", t) for i, t in enumerate(texts)]
        self.edits, self.ingested, self.archived = [], [], []

    async def list_by_status(self, **_k):
        return list(self.rows)

    async def search(self, *_a, **_k):
        return list(self.rows)

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
    assert run(mq.reconcile_for_ingest(svc, candidate, UID)) == ("skip", "r0")
    assert run(ed._ingest_or_supersede(svc, candidate, user_id=UID, source="idle_consolidation", session_id="s", user_turn_id="t",
                                       memory_type="fact", confidence=0.8, tags=[])) == "skip"
    assert svc.ingested == [] and svc.archived == []          # no new row of another kind, the stored row stands


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


def test_the_withdraw_is_best_effort_and_reaches_the_forget(monkeypatch):
    import pending_suggestions

    async def down(*_a, **_k):
        raise RuntimeError("no pool")

    monkeypatch.setattr(pending_suggestions, "withdraw_matching", down)
    assert run(forget_topic._withdraw_offers(UID, forget_topic.topic_groups("my sister"), [])) == 0
    import inspect

    assert "_withdraw_offers(" in inspect.getsource(forget_topic.forget)


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


def test_a_call_now_emergency_is_off_the_record(monkeypatch):
    monkeypatch.delenv("ZOE_MEDICAL_SAFETY", raising=False)
    assert medical_safety.detect(FALL).cls == "red_call"
    assert medical_safety.guarded_text(FALL) is True
    assert memory_provenance.is_off_record(UID, FALL) is True
    assert memory_provenance.claim_turn("demo_qa9_00000002", FALL) is True       # the transcript row is flagged off the record
    assert medical_safety.guarded_text("my phone fell over") is False
    assert memory_provenance.is_off_record(UID, "i fell asleep on the couch") is False
    monkeypatch.setenv("ZOE_MEDICAL_SAFETY", "shadow")
    assert medical_safety.guarded_text(FALL) is False                            # only what the floor ENFORCES
