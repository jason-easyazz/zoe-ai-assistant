"""Two same-name people stored only as PLAIN memory rows (no person link, no contact): a correction whose old value matches ONE
row used to be applied silently (the ambiguity guard needed >= 2 matching rows). Invented names only. ``ci_safe``."""
from __future__ import annotations

import asyncio
import logging

import pytest

import value_correction as vc

pytestmark = pytest.mark.ci_safe

UID = "demo_plainrows_00000001"
SAID = "quorbel is 45 not 41"


def run(coro):
    return asyncio.run(coro)


class _Row:
    def __init__(self, i, text, person=None):
        self.id, self.text = i, text
        self.metadata = {"user_id": UID, **({"entity_type": "person", "entity_id": person} if person else {})}


class _Svc:
    def __init__(self, *rows):
        self.rows, self.edits = list(rows), []

    async def list_by_status(self, **_k):
        return list(self.rows)

    async def review(self, rid, decision=None, edits=None, **_k):
        self.edits.append((rid, edits))
        return object()


@pytest.fixture(autouse=True)
def _wiring(monkeypatch):
    import correction_apply
    import person_extractor as pe
    import user_prefs

    async def opted_in(_u):
        return False

    async def no_db(_db):
        return (None, False)

    monkeypatch.setattr(user_prefs, "is_memory_opted_out", opted_in)
    monkeypatch.setattr(correction_apply, "_is_unidentified", lambda _u: False)
    monkeypatch.setattr(pe, "_ensure_db", no_db)
    vc.reset_state()
    yield
    vc.reset_state()


@pytest.mark.parametrize("a,b", [
    ("Quorbel is 41", "Quorbel is 52"),                                   # two different current ages
    ("User's sister Quorbel is 41", "User's aunt Quorbel is 52 years old"),  # two relations
    ("Quorbel Tandry is 41", "Quorbel Vesk likes tea"),                    # two surnames
])
def test_two_plain_same_name_people_abstain_even_when_old_value_matches_one(a, b, caplog):
    caplog.set_level(logging.DEBUG)
    svc = _Svc(_Row("r0", a), _Row("r1", b))
    assert run(vc.apply(SAID, UID, None, svc=svc)) == 0
    assert svc.edits == []
    line = next(r.getMessage() for r in caplog.records if "VALUE_CORRECTION_AMBIGUOUS_SUBJECT" in r.getMessage())
    assert line == f"VALUE_CORRECTION_AMBIGUOUS_SUBJECT user={UID} rows=1 people=2"
    assert "quorbel" not in caplog.text.lower() and "41" not in caplog.text    # counts only


def test_negative_control_old_guard_alone_applies_silently(monkeypatch):
    monkeypatch.setattr(vc, "_plain_subjects", lambda _t, _s: 1)           # the pre-fix behaviour
    svc = _Svc(_Row("r0", "Quorbel is 41"), _Row("r1", "Quorbel is 52"))
    assert run(vc.apply(SAID, UID, None, svc=svc)) == 1 and svc.edits == [("r0", "Quorbel is 45")]


def test_one_plain_person_is_applied():
    svc = _Svc(_Row("r0", "Quorbel is 41"), _Row("r1", "Quorbel likes tea"))
    assert run(vc.apply(SAID, UID, None, svc=svc)) == 1
    assert svc.edits == [("r0", "Quorbel is 45")]


def test_two_rows_about_the_same_person_are_applied():
    svc = _Svc(_Row("r0", "Quorbel is 41"), _Row("r1", "Quorbel is 41 years old"))
    assert run(vc.apply(SAID, UID, None, svc=svc)) == 2


def test_a_relation_or_date_word_in_another_sentence_is_not_a_second_person():
    svc = _Svc(_Row("r0", "Quorbel is 41"), _Row("r1", "Quorbel's brother is Tandry"), _Row("r2", "Quorbel Monday swim"))
    assert run(vc.apply(SAID, UID, None, svc=svc)) == 1


def test_linked_people_ambiguity_still_abstains():
    svc = _Svc(_Row("r0", "Quorbel is 41", "person-a"), _Row("r1", "Quorbel is 41 years old", "person-b"))
    assert run(vc.apply(SAID, UID, None, svc=svc)) == 0 and svc.edits == []


@pytest.mark.parametrize("a,b", [
    ("quorbel tandry is 41", "quorbel vesk is 41"),                         # voice transcripts are lower-case
    ("Quorbel Tandry is 41", "quorbel vesk is 41"),                         # mixed case
    ("Quorbel Tandry-Vesk is 41", "Quorbel Tandry is 41"),                  # hyphenated surname folds to its own name
])
def test_surnames_are_read_case_insensitively(a, b):
    svc = _Svc(_Row("r0", a), _Row("r1", b))
    assert run(vc.apply(SAID, UID, None, svc=svc)) == 0 and svc.edits == []


def test_one_linked_person_and_one_plain_row_with_another_age_abstains(caplog):
    caplog.set_level(logging.DEBUG)
    svc = _Svc(_Row("r0", "Quorbel is 41", "person-a"), _Row("r1", "Quorbel is 52"))
    assert run(vc.apply(SAID, UID, None, svc=svc)) == 0 and svc.edits == []
    assert f"VALUE_CORRECTION_AMBIGUOUS_SUBJECT user={UID} rows=1 people=2" in caplog.text


def test_one_linked_person_and_a_plain_row_with_no_cue_is_one_person():
    svc = _Svc(_Row("r0", "Quorbel is 41", "person-a"), _Row("r1", "quorbel likes tea"))
    assert run(vc.apply(SAID, UID, None, svc=svc)) == 1


@pytest.mark.parametrize("other", [
    "quorbel likes tea", "quorbel and tandry went swimming", "quorbel swims, on monday", "quorbel plays chess",
    "quorbel in march booked a trip", "Quorbel Monday swim", "quorbel has a dentist visit", "Quorbel Swimming is on",
    "quorbel is my sister", "quorbel went to the shops, then home"])
def test_ordinary_words_after_a_name_are_not_surnames(other):
    svc = _Svc(_Row("r0", "quorbel is 41"), _Row("r1", other))
    assert run(vc.apply(SAID, UID, None, svc=svc)) == 1


def test_two_stale_values_for_one_person_over_abstain_by_design():
    svc = _Svc(_Row("r0", "Quorbel is 40"), _Row("r1", "Quorbel is 41"))
    assert run(vc.apply(SAID, UID, None, svc=svc)) == 0
