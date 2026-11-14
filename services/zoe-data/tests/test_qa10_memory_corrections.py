"""QA round 10: a correction or a re-filing never leaves the subject with LESS than it had. Correcting back, a moved date, a chain of
moves and a restatement are driven through the same calls the live path makes (``value_correction.apply`` on the correction turn,
then the post-turn person extractor and the idle pass over the session with the model's mined facts as fixed strings). In-memory
doubles only: no database, no model, no network. Invented demo data. ``ci_safe``."""
from __future__ import annotations

import asyncio
import itertools

import pytest

import expert_dispatch as ed
import memory_quality as mq
import value_correction as vc

pytestmark = pytest.mark.ci_safe

UID = "demo_qa10_00000001"


def run(coro):
    return asyncio.run(coro)


class _Row:
    def __init__(self, i, text, **meta):
        self.id, self.text = i, text
        self.metadata = {"user_id": UID, "status": "approved", **meta}


class _Store:
    """A member's rows that the writers really change: an edit retires the row and writes its successor, a supersede retires the
    old row only when the successor is a served row (the real ``_supersede_by_sync`` contract). Every replacement is recorded."""

    def __init__(self, *texts, **meta):
        self.rows = [_Row(f"r{i}", t, **meta) for i, t in enumerate(texts)]
        self.replaced = []              # (old text, new text) for every row that stopped being served
        self.ingest_status = "approved"

    def live(self):
        return [r for r in self.rows if r.metadata["status"] == "approved"]

    async def list_by_status(self, **_k):
        return self.live()

    async def search(self, *_a, **_k):
        return self.live()

    async def list_by_entity(self, _uid, ids, **_k):
        return [r for r in self.live() if r.metadata.get("entity_id") in ids]

    async def get(self, rid):
        return next((r for r in self.rows if r.id == rid), None)

    async def review(self, rid, decision=None, edits=None, **_k):
        row = await self.get(rid)
        if row is None or row.metadata["status"] != "approved":
            return None
        row.metadata["status"] = "superseded" if decision == "edit" else "archived"
        if decision != "edit":
            self.replaced.append((row.text, ""))
            return row
        new = _Row(f"r{len(self.rows)}", edits, **{k: v for k, v in row.metadata.items() if k != "status"})
        self.rows.append(new)
        self.replaced.append((row.text, edits))
        return new

    async def ingest(self, text, **_k):
        new = _Row(f"r{len(self.rows)}", text)
        new.metadata["status"] = self.ingest_status
        self.rows.append(new)
        return new

    async def supersede_by(self, _uid, old_id, new_id, **_k):
        old, new = await self.get(old_id), await self.get(new_id)
        if old is None or new is None or old.metadata["status"] != "approved" or new.metadata["status"] != "approved":
            return False
        old.metadata["status"] = "superseded"
        self.replaced.append((old.text, new.text))
        return True


@pytest.fixture
def quiet(monkeypatch):
    import correction_apply
    import user_prefs

    async def opted_in(_u):
        return False

    monkeypatch.setattr(user_prefs, "is_memory_opted_out", opted_in)
    monkeypatch.setattr(correction_apply, "_is_unidentified", lambda _u: False)
    vc.reset_state()
    yield
    vc.reset_state()


def _correct(store, said):
    return run(vc.apply(said, UID, None, svc=store))


def _idle(store, mined):
    """The idle pass's write of one mined fact (memory_idle_consolidation -> expert_dispatch._ingest_or_supersede)."""
    return run(ed._ingest_or_supersede(store, mined, user_id=UID, source="idle_consolidation", session_id=None,
                                       user_turn_id=None, memory_type="fact", confidence=0.8, tags=["idle"]))


def _person(store, monkeypatch, name, kind, value):
    """The post-turn person extractor's write of one mined (name, kind, value) (person_extractor._ingest_to_mempalace)."""
    import memory_service
    import person_extractor as pe

    monkeypatch.setattr(memory_service, "get_memory_service", lambda: store)
    return run(pe._ingest_to_mempalace(f"{name}: {value}", UID, name, f"slug:{name.lower()}", pattern_type=kind))


def _texts(store, word):
    return [r.text for r in store.live() if word.lower() in r.text.lower()]


# -- R1: correcting back never replaces the age with "next month" -------------------------------------------------------------------

def test_correcting_back_keeps_the_age_beside_a_relative_birthday(quiet, monkeypatch):
    # the model filed the earlier "he had a birthday" turn's age under kind birthday (it has no age kind)
    store = _Store("Tobin: turned 7", entity_id="slug:tobin", entity_type="person", pattern_type="birthday")
    assert _correct(store, "actually tobin is 6 after all, i got mixed up, his birthday is next month") == 1
    assert _texts(store, "tobin") == ["Tobin: turned 6"]
    _person(store, monkeypatch, "Tobin", "birthday", "next month")          # the mined birthday of the same turn
    assert "Tobin: turned 6" in _texts(store, "tobin")                      # the age is still stored ...
    assert "Tobin: next month" in _texts(store, "tobin")                    # ... and "next month" beside it, never in its place
    assert store.replaced == [("Tobin: turned 7", "Tobin: turned 6")]


def test_a_real_value_change_of_the_same_kind_still_supersedes(quiet, monkeypatch):
    store = _Store("Tobin: 3rd of March", entity_id="slug:tobin", entity_type="person", pattern_type="birthday")
    _person(store, monkeypatch, "Tobin", "birthday", "5th of March")
    assert _texts(store, "tobin") == ["Tobin: 5th of March"]


def test_a_new_age_in_words_still_supersedes(quiet, monkeypatch):
    store = _Store("Tobin: turned 6", entity_id="slug:tobin", entity_type="person", pattern_type="birthday")
    _person(store, monkeypatch, "Tobin", "birthday", "turned seven")
    assert _texts(store, "tobin") == ["Tobin: turned seven"]


# -- R2 / R3: a moved date keeps the event; a chain's second move behaves like the first ---------------------------------------------

def test_a_mined_restatement_without_the_date_never_retires_the_event(quiet):
    store = _Store("User's weaving class is on the 3rd of December")
    assert _correct(store, "weaving class got moved to the 10th of december") == 1
    for mined in ("User's weaving class was moved", "User's weaving class is next month"):
        assert mq.classify_against_existing(mined, [("x", "User's weaving class is on the 10th of December")])[0] == "update"
        _idle(store, mined)
        assert "User's weaving class is on the 10th of December" in _texts(store, "weaving")


def test_a_chain_of_moves_keeps_one_dated_event(quiet):
    store = _Store("User's weaving class is on the 3rd of December")
    assert _correct(store, "weaving class got moved to the 10th of december") == 1
    assert _correct(store, "weaving moved again, its on the 17th of december now") == 1
    assert _texts(store, "weaving") == ["User's weaving class is on the 17th of December"]
    _idle(store, "Weaving moved again on the seventeenth")                  # the shape the model mined from the chain turn
    assert "User's weaving class is on the 17th of December" in _texts(store, "weaving")


def test_a_moved_date_that_matches_no_row_writes_and_deletes_nothing(quiet):
    store = _Store("User's weaving class is on the 3rd of December", "User got married on the 2nd of February 1998")
    assert _correct(store, "the knitting circle got moved to the 10th of december") == 0
    assert store.replaced == [] and len(store.rows) == 2


def test_a_successor_that_is_not_served_never_retires_the_old_row(quiet, monkeypatch):
    store = _Store("User's weaving class is on the 10th of December")
    store.ingest_status = "pending"                                         # written, but not a served row
    # retire_supersede: the real supersede_by keeps its own walls; the writer must not even ask (write, verify, retire)
    asked = []

    async def no_supersede(*a, **_k):
        asked.append(a)
        return True

    monkeypatch.setattr(store, "supersede_by", no_supersede)
    _idle(store, "User's weaving class is on the 17th of December")
    assert asked == [] and "User's weaving class is on the 10th of December" in _texts(store, "weaving")


# -- the invariant, over (age, date, amount, schedule) x (correct, correct back, move, move again, restate) ----------------------------

_CASES = {
    "age": ("Tobin: turned 6", "tobin", {
        "correct": "tobin is 7 not 6", "back": "actually tobin is 6 after all, his birthday is next month",
        "move": "tobin got moved to the 10th of december", "again": "tobin moved again, its on the 17th of december now",
        "restate": "tobin is 8 now"}),
    "date": ("User's car rego is due on the 9th of December", "car rego", {
        "correct": "the car rego is due on the 16th of december not the 9th", "back": "the car rego is due on the 9th of december not the 16th",
        "move": "car rego got moved to the 10th of december", "again": "car rego moved again, its on the 17th of december now",
        "restate": "the car rego is due on the 20th of december now"}),
    "amount": ("User's caravan fund is 1500 dollars", "caravan fund", {
        "correct": "the caravan fund is 1800 dollars not 1500", "back": "the caravan fund is 1500 dollars not 1800",
        "move": "caravan fund got moved to the 10th of december", "again": "caravan fund moved again, its on the 17th now",
        "restate": "the caravan fund is 2100 dollars now"}),
    "schedule": ("User's weaving class is on the 3rd of December", "weaving", {
        "correct": "the weaving class is on the 5th of december not the 3rd", "back": "the weaving class is on the 3rd of december not the 5th",
        "move": "weaving class got moved to the 10th of december", "again": "weaving moved again, its on the 17th of december now",
        "restate": "weaving class is on the 20th of december now"}),
}


@pytest.mark.parametrize("attr", sorted(_CASES))
def test_no_sequence_leaves_the_subject_with_less(attr, quiet, monkeypatch):
    row, word, said = _CASES[attr]
    noun = word.split()[-1] if attr != "age" else "Tobin"
    for seq in itertools.permutations(said, 3):
        vc.reset_state()
        store = _Store(row, entity_id="slug:tobin", entity_type="person", pattern_type="birthday")
        for op in seq:
            _correct(store, said[op])
            # what the model may mine from the same turn, with the value missing
            _idle(store, f"User's {word} is next month")
            _idle(store, f"User's {word} was moved")
            if attr == "age":
                _person(store, monkeypatch, "Tobin", "birthday", "next month")
            valued = [t for t in _texts(store, noun) if vc.quantities(t)]
            assert valued, (seq, op, _texts(store, noun))
            # a row that held a value was only ever replaced by one that holds a value
            assert all(vc.quantities(new) for old, new in store.replaced if vc.quantities(old)), (seq, op, store.replaced)


@pytest.mark.parametrize("old, new, drops", [
    ("Tobin: turned 6", "Tobin: next month", True),
    ("Tobin: 6", "Tobin's birthday is on the 3rd of March", True),       # a different unit is not the value
    ("User's weaving class is on the 10th of December", "User's weaving class was moved", True),
    ("Tobin is 6 years old", "", True),
    ("Tobin: turned 6", "Tobin: turned 7", False),
    ("Tobin: turned 6", "Tobin: turned seven", False),                     # the new age in words still replaces
    ("Tobin is six years old", "Tobin is seven years old", False),
    ("User has saved 1500 dollars for the caravan", "User has 1800 saved for the caravan", False),
    ("User's weaving class is on the 10th of December", "Weaving moved again on the seventeenth", False),
    ("User likes tea", "User likes coffee", False),                       # no number: never judged
])
def test_drops_value(old, new, drops):
    assert vc.drops_value(old, new) is drops
