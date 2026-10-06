"""The conflict pass keys on the NAMED PERSON and the ATTRIBUTE (bake-off verification X1 / X2, 2026-10-06).

X1: ``subject_key`` ignored a friend's name, so "User's friend Dana lives in Hobart" and "User's friend Leo
lives in Perth" were one subject with two homes and the nightly pass retired the older (5 of 20 friend facts on a
real run). X2: a friend's "moved to X" retired the same friend's JOB whenever the friend's name + the word
"friend" were enough shared "topic" (2 of 3 held-out seeds). Both are user-stated memory loss.

Synthetic names only; the store is a list of ``MemoryRef`` rows (pure), plus one pass over a fake service. Each
behavioural test has a negative control: the pre-fix matcher put back (names ignored / the subject's words counted
as topic / no attribute guard) turns it red.
"""
from __future__ import annotations

import asyncio
import datetime as dt

import pytest

import memory_supersede as ms
from memory_service import MemoryRef

pytestmark = pytest.mark.ci_safe  # pure matchers + a fake service; no DB, no model, no network


def _ref(i, text, days_ago, mtype="fact", status="approved"):
    when = dt.datetime(2026, 9, 30, tzinfo=dt.timezone.utc) - dt.timedelta(days=days_ago)
    return MemoryRef(id=f"r{i}", text=text, metadata={
        "status": status, "memory_type": mtype, "added_at": when.isoformat()})


def _pairs(rows):
    return sorted((n.id, o.id, why) for n, o, why in ms.conflict_pairs(rows))


# ── X1: two named friends are two subjects ────────────────────────────────────────

def test_two_friends_homes_both_survive():
    rows = [_ref(1, "User's friend Dana lives in Hobart.", 20),
            _ref(2, "User's friend Leo lives in Perth.", 10)]
    assert _pairs(rows) == []


def test_a_friends_second_home_retires_only_that_friends_first():
    rows = [_ref(1, "User's friend Dana lives in Hobart.", 20),
            _ref(2, "User's friend Leo lives in Perth.", 10),
            _ref(3, "User's friend Dana lives in Cork.", 1)]
    assert _pairs(rows) == [("r3", "r1", "home")]            # Leo's Perth is not touched


def test_many_friends_many_homes_none_retired():
    rows = [_ref(i, f"User's friend {n} lives in {p}.", 30 - i)
            for i, (n, p) in enumerate([("Dana", "Hobart"), ("Leo", "Perth"), ("Ines", "Cork"),
                                        ("Tove", "Ghent"), ("Ravi", "Bergen")], start=1)]
    assert _pairs(rows) == []


def test_the_owners_own_move_still_retires_the_owners_old_home_and_only_that():
    rows = [_ref(1, "User lives in Hobart.", 20),
            _ref(2, "User's friend Dana lives in Hobart.", 15),
            _ref(3, "User's friend Leo lives in Perth.", 10),
            _ref(4, "User moved to Cork.", 1)]
    assert _pairs(rows) == [("r4", "r1", "home")]


def test_a_unique_relation_correction_need_not_repeat_the_name():
    """"User's mum lives in Bendigo" corrects "User's mum Ingrid lives in Ballarat": one mum."""
    rows = [_ref(1, "User's mum Ingrid lives in Ballarat.", 20),
            _ref(2, "User's mum lives in Bendigo.", 1)]
    assert _pairs(rows) == [("r2", "r1", "home")]


def test_an_unnamed_friend_is_not_a_named_friend():
    """A non-unique relation: the name is what tells the people apart, so a one-sided name is NOT a match (a
    retirement is never a guess about who a fact is about)."""
    rows = [_ref(1, "User's friend Dana lives in Hobart.", 20),
            _ref(2, "User's friend lives in Perth.", 1)]
    assert _pairs(rows) == []


def test_names_are_read_whole_never_as_substrings():
    rows = [_ref(1, "User's friend Ana lives in Hobart.", 20),
            _ref(2, "User's friend Anabel lives in Perth.", 10),
            _ref(3, "User's friend Dana lives in Cork.", 8),
            _ref(4, "User's friend Dan lives in Ghent.", 1)]
    assert _pairs(rows) == []


def test_a_first_name_and_the_full_name_are_the_same_person_but_two_surnames_are_not():
    same = [_ref(1, "Dana Whitfield lives in Hobart.", 20), _ref(2, "Dana lives in Perth.", 1)]
    assert _pairs(same) == [("r2", "r1", "home")]
    apart = [_ref(1, "Dana Whitfield lives in Hobart.", 20), _ref(2, "Dana Okonkwo lives in Perth.", 1)]
    assert _pairs(apart) == []
    other_first = [_ref(1, "Dana Whitfield lives in Hobart.", 20), _ref(2, "Whitfield lives in Perth.", 1)]
    assert _pairs(other_first) == []


def test_possessive_and_relation_forms_agree_on_the_person():
    rows = [_ref(1, "User's friend Dana lives in Hobart.", 20),
            _ref(2, "User's friend Dana's sister lives in Perth.", 10)]
    assert _pairs(rows) == []                                  # the friend and her sister are not one subject


@pytest.mark.parametrize("text,names", [
    ("User's friend Dana lives in Hobart.", {"dana"}),
    ("User lives in Hobart.", set()),
    ("User's mum lives in Bendigo.", set()),
    ("Dana's job is baker.", {"dana"}),
    ("Dana Whitfield works at a bakery.", {"dana whitfield"}),
    ("User's friends Dana and Leo live in Hobart.", {"dana", "leo"}),
    ("User's friend Dana has two kids Mika and Biscuit.", {"dana"}),     # a list of dependents extends the fact
    ("Her birthday is in May.", set()),
    ("My dad's name is Neil.", set()),
])
def test_subject_names(text, names):
    assert ms.subject_names(text) == names


# ── X2: a move is about the home, never the job ───────────────────────────────────

@pytest.mark.parametrize("job", ["works at a bookbinder", "works at a bakery", "works as a nurse",
                                 "works at the harbour office", "works in Perth"])
def test_a_friends_move_never_retires_the_same_friends_job(job):
    rows = [_ref(1, f"User's friend Ines {job}.", 20),
            _ref(2, "User's friend Ines lives in Hobart.", 10),
            _ref(3, "User's friend Ines moved to Perth.", 1)]
    assert _pairs(rows) == [("r3", "r2", "home")]              # the job row r1 is never a victim


@pytest.mark.parametrize("job", ["works at a bookbinder", "works in Cork"])
def test_the_owners_move_never_retires_the_owners_job(job):
    rows = [_ref(1, f"User {job}.", 20), _ref(2, "User lives in Hobart.", 10),
            _ref(3, "User moved to Cork.", 1)]
    assert _pairs(rows) == [("r3", "r2", "home")]


def test_the_cue_path_is_per_attribute_too():
    """A swap cue retires a same-topic row only if it says the same THING about the person."""
    assert not ms.same_topic("User's friend Ines moved to Perth.", "User's friend Ines works in Perth.")
    assert ms.same_topic("User no longer works at the bank.", "User works at the bank.")
    assert ms.same_topic("User's friend Ines no longer works at the bank.", "User's friend Ines works at the bank.")
    # the subject's own words are not topic: a friend and a name are not a shared subject matter
    assert not ms.same_topic("User's friend Ines moved to Perth.", "User's friend Ines plays chess.")


def test_attributes_of():
    assert ms.attributes_of("User's friend Ines moved to Perth.") == {"home"}
    assert ms.attributes_of("User's friend Ines works at a bakery.") == {"job"}
    assert ms.attributes_of("Ines's birthday is in May.") == {"birthday"}
    assert ms.attributes_of("User plays chess.") == frozenset()
    assert ms.same_attribute("User plays chess.", "User works at a bakery.")        # unclassified: not decided here


# ── the nightly pass end to end over a fake service ───────────────────────────────

class _FakeSvc:
    def __init__(self, rows):
        self.rows = rows
        self.retired = []

    async def list_by_status(self, **_k):
        return [r for r in self.rows if r.metadata.get("status") == "approved"]

    async def supersede_by(self, user_id, old_id, new_id, **_k):
        row = next(r for r in self.rows if r.id == old_id)
        row.metadata["status"] = "superseded"
        self.retired.append((old_id, new_id))
        return True


def test_nightly_pass_over_the_exact_x1_shape(monkeypatch):
    import open_loop_lifecycle

    async def no_loops(*_a, **_k):
        return None

    monkeypatch.setattr(open_loop_lifecycle, "resolve_for_supersede", no_loops)
    names = ["Dana", "Leo", "Ines", "Tove", "Ravi", "Oskar", "Priya", "Noor"]
    places = ["Hobart", "Perth", "Cork", "Ghent", "Bergen", "Lisbon", "Dunedin", "Tauranga"]
    rows = [_ref(i, f"User's friend {n} lives in {p}.", 40 - i) for i, (n, p) in enumerate(zip(names, places), 1)]
    svc = _FakeSvc(rows)
    out = asyncio.run(ms.nightly_conflict_pass(svc, "member-a"))
    assert out == {"pairs": 0, "superseded": 0} and svc.retired == []
    # the second statement of ONE friend retires that friend's first, nobody else's
    rows.append(_ref(99, "User's friend Leo lives in Cork.", 0))
    out = asyncio.run(ms.nightly_conflict_pass(svc, "member-a"))
    assert out == {"pairs": 1, "superseded": 1} and svc.retired == [("r2", "r99")]


# ── the same defect in the other two writers ──────────────────────────────────────

def test_per_turn_reconcile_never_updates_another_persons_attribute():
    """``classify_against_existing`` strips the subject from the attribute key: "Leo's birthday is ..." used to
    UPDATE (retire) "Dana's birthday is ..."; the owner's birthday likewise."""
    import memory_quality as mq

    assert mq.classify_against_existing("Leo's birthday is June 9.", [("x", "Dana's birthday is March 5.")]) == ("add", None)
    assert mq.classify_against_existing("Leo's job is nurse.", [("x", "Dana's job is baker.")]) == ("add", None)
    assert mq.classify_against_existing("My birthday is June 9.", [("x", "Dana's birthday is March 5.")]) == ("add", None)
    # the corrections the reconciler exists for still land: same person, same attribute, new value
    assert mq.classify_against_existing("Jessica's birthday is March 25.",
                                        [("x", "My friend Jessica's birthday is March 15.")]) == ("update", "x")
    assert mq.classify_against_existing("My dad's name is Tom.", [("x", "User's father's name is Neil.")]) == ("update", "x")


def test_authority_wall_conflict_kind_follows_the_named_person():
    """The authority wall holds back a model row that contradicts a user-stated one; a DIFFERENT friend's row
    contradicts nothing (and the same friend's still does)."""
    import memory_authority as ma

    assert ma.conflict_kind("User's friend Leo lives in Perth.", "User's friend Dana lives in Hobart.") is None
    assert ma.conflict_kind("User's friend Dana lives in Perth.", "User's friend Dana lives in Hobart.") == "home"
    assert ma.conflict_kind("User lives in Perth.", "User lives in Hobart.") == "home"


def test_weekly_contradiction_pass_never_asks_about_two_different_people(monkeypatch):
    import memory_digest

    asked = []

    async def judge(new, old):
        asked.append((new, old))
        return True

    monkeypatch.setattr(memory_digest, "_is_contradiction", judge)
    rows = [_ref(1, "User's friend Dana lives in Hobart.", 20), _ref(2, "User's friend Leo lives in Hobart.", 1)]

    class Svc(_FakeSvc):
        async def get(self, mid):
            return next((r for r in self.rows if r.id == mid), None)

        async def review(self, mid, **_k):
            raise AssertionError("two different friends must never be merged by the weekly pass")

    assert asyncio.run(memory_digest._resolve_contradictions(Svc(rows), "member-a")) == 0
    assert asked == []


# ── negative controls: the pre-fix matcher turns the tests red ────────────────────

def test_control_ignoring_the_name_brings_x1_back(monkeypatch):
    monkeypatch.setattr(ms, "subject_names", lambda text: frozenset())
    rows = [_ref(1, "User's friend Dana lives in Hobart.", 20), _ref(2, "User's friend Leo lives in Perth.", 10)]
    assert _pairs(rows) == [("r2", "r1", "home")]                 # the defect, reproduced


def test_control_counting_the_subjects_words_as_topic_brings_x2_back(monkeypatch):
    monkeypatch.setattr(ms, "_subject_tokens", lambda text: set())
    monkeypatch.setattr(ms, "same_attribute", lambda a, b: True)
    # the seed where the job's own words are few enough that "friend" + the name carry the overlap
    assert ms.same_topic("User's friend Ines moved to Perth.", "User's friend Ines works at a bookbinder.")


def test_control_without_the_attribute_guard_a_move_retires_a_job_in_the_new_city(monkeypatch):
    monkeypatch.setattr(ms, "same_attribute", lambda a, b: True)
    assert ms.same_topic("User's friend Ines moved to Perth.", "User's friend Ines works in Perth.")
