"""An adult's fact is not changed by a child's correction (owner, 2026-11-15: "if an adult sets facts for a kid, they must be important
enough to stay"). ``memory_authority.author_class`` / ``may_correct``, ``value_correction.apply`` and the claims validator's rule 17.
Placeholder people only (Juno is the child, Ash the parent, Wren another adult). ``ci_safe``."""
from __future__ import annotations

import asyncio

import pytest

import correction_apply
import manner_block
import memory_authority as ma
import memory_claims_validate as vm
import user_prefs
import value_correction as vc
from test_memory_claims_validate import _ctx, _fail_rule
from test_qa4_memory_fixes import svc  # noqa: F401  (the in-memory MemoryService fixture)

pytestmark = pytest.mark.ci_safe

KID, PARENT, ADULT = "demo_ac_0000000c", "demo_ac_0000000a", "demo_ac_0000000e"
ROLES = {KID: "child", PARENT: "adult", ADULT: "adult"}


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _who(monkeypatch):
    async def opted_in(_u):
        return False

    async def is_adult(uid, db=None):
        role = ROLES.get(uid)
        if role == "boom":
            raise RuntimeError("lookup failed")
        return role == "adult"

    async def graph(_name, _uid):
        return "none"

    monkeypatch.setattr(user_prefs, "is_memory_opted_out", opted_in)
    monkeypatch.setattr(correction_apply, "_is_unidentified", lambda _u: False)
    monkeypatch.setattr(manner_block, "is_adult", is_adult)
    monkeypatch.setattr(vc, "person_status", graph)


def _curated(svc, text="Juno is 7", uid=KID):   # an adult's fact in the child's space: the curator writer + its stamps
    ref = run(svc.ingest(text, user_id=uid, source="curator", origin="curator", memory_type="fact", confidence=0.95,
                         status="approved", scope="personal", tags=["curated"],
                         metadata=dict(curated_by=PARENT, curated_by_name="Ash", curated_at="2026-11-15", curated_via="desktop",
                                       curated_category="other", curated_sensitive=False)))
    assert ref is not None and ref.metadata["authority_class"] == ma.CURATED
    return ref.id


def _own(svc, text, uid, **stamp):
    ref = run(svc.ingest(text, user_id=uid, source="chat_regex", status="approved"))
    assert ref is not None and ref.metadata["status"] == "approved"
    svc._collection().rows[ref.id][1].update(stamp)       # an explicit author stamp (no writer stamps one yet)
    return ref.id


def _rows(svc, uid, status):
    return {r.id: r for r in run(svc.list_by_status(user_id=uid, status=status, limit=50))}


SAY = "Juno is 8, not 7"


def test_a_childs_correction_of_an_adult_authored_fact_changes_nothing_and_is_kept_as_a_held_note(svc):
    rid = _curated(svc)
    assert run(vc.apply(SAY, KID, "s1", svc=svc)) == 0                            # nothing rewritten
    approved = _rows(svc, KID, "approved")
    assert [r.text for r in approved.values()] == ["Juno is 7"] and rid in approved   # the row stands, untouched
    held = _rows(svc, KID, "disputed")
    assert len(held) == 1
    (note,) = held.values()
    assert note.metadata["contradicts_id"] == rid and "8" in note.text            # kept for the adult, linked to the fact, never approved
    assert not _rows(svc, KID, "rejected")                                           # nothing superseded / archived


def test_the_hold_does_not_depend_on_the_authority_wall(svc, monkeypatch):
    monkeypatch.setenv("ZOE_MEMORY_AUTHORITY", "off")                                # NEGATIVE CONTROL for the review-only protection
    rid = _curated(svc)
    assert run(vc.apply(SAY, KID, "s1", svc=svc)) == 0
    assert _rows(svc, KID, "approved")[rid].text == "Juno is 7"


def test_a_child_authored_fact_is_corrected_by_the_child(svc):
    _own(svc, "Juno is 7", KID)
    assert run(vc.apply(SAY, KID, "s1", svc=svc)) == 1
    assert [r.text for r in _rows(svc, KID, "approved").values()] == ["Juno is 8"]
    assert not _rows(svc, KID, "disputed")


def test_a_row_that_records_no_author_is_not_protected(svc):
    """The gap, pinned: an ordinary own-space row carries no author class, so it is unprotected (and a child's own row anyway)."""
    _own(svc, "Juno is 7", KID)
    assert ma.author_class({"authority_class": "user_stated"}) is None
    assert run(vc.apply(SAY, KID, "s1", svc=svc)) == 1


@pytest.mark.parametrize("speaker", [PARENT, ADULT])
def test_an_adult_corrects_an_adult_authored_row_they_may_change(svc, speaker):
    """An adult may correct anything the rank rule lets them: an explicit adult-authored ordinary row (no curator class)."""
    _own(svc, "Juno is 7", speaker, author_class="adult")
    assert run(vc.apply(SAY, speaker, "s1", svc=svc)) == 1
    assert [r.text for r in _rows(svc, speaker, "approved").values()] == ["Juno is 8"]


def test_a_child_cannot_correct_an_explicitly_adult_authored_ordinary_row(svc):
    """The class check, not the rank wall: this row is user_stated (rank 4), which review() alone WOULD let a child's correction replace."""
    rid = _own(svc, "Juno is 7", KID, author_class="adult")
    assert run(vc.apply(SAY, KID, "s1", svc=svc)) == 0
    assert _rows(svc, KID, "approved")[rid].text == "Juno is 7" and len(_rows(svc, KID, "disputed")) == 1


@pytest.mark.parametrize("role", ["boom", None, "guest"])
def test_an_unknown_speaker_class_abstains(svc, role):
    ROLES[KID] = role                    # a failed lookup / no account / a guest: not positively an adult
    try:
        rid = _curated(svc)
        assert run(vc.apply(SAY, KID, "s1", svc=svc)) == 0
        assert _rows(svc, KID, "approved")[rid].text == "Juno is 7"
    finally:
        ROLES[KID] = "child"


def test_the_replacement_wording_leaks_nothing(svc):
    """value_correction returns a count and speaks no reply; the held note is a row, never text the child is handed back."""
    _curated(svc)
    out = run(vc.apply(SAY, KID, "s1", svc=svc))
    assert isinstance(out, int) and out == 0


def test_negative_control_without_the_gate_the_adults_explicit_row_is_rewritten(svc, monkeypatch):
    rid = _own(svc, "Juno is 7", KID, author_class="adult")
    monkeypatch.setattr(ma, "may_correct", lambda *_a, **_k: True)
    assert run(vc.apply(SAY, KID, "s1", svc=svc)) == 1
    assert [r.text for r in _rows(svc, KID, "approved").values()] == ["Juno is 8"]


# -- the predicate -------------------------------------------------------------------------------------------------------

def test_author_class_reads_positive_provenance_only():
    assert ma.author_class({"authority_class": ma.CURATED}) == "adult"
    assert ma.author_class({"authority_class": ma.USER_STATED, "author_class": "adult"}) == "adult"
    assert ma.author_class({"authority_class": ma.USER_STATED, "author_class": "child"}) == "child"
    assert ma.author_class({"authority_class": ma.USER_STATED, "author_class": "grown-up"}) is None
    assert ma.author_class({"authority_class": ma.USER_STATED}) is None and ma.author_class(None) is None
    assert ma.author_class({"authority_class": ma.CURATED, "author_class": "child"}) == "adult"   # curated wins
    for speaker, author, ok in ((True, "adult", True), (False, "adult", False), (None, "adult", False), (True, "child", True),
                                (False, "child", True), (None, None, True), (False, None, True)):
        assert ma.may_correct(speaker, author) is ok


# -- the claims validator, rule 17 ---------------------------------------------------------------------------------------

def _row(**kw):
    return vm.Row("r0", "x", authority=ma.USER_STATED, **kw)


def test_rule_17_an_adult_authored_row_needs_a_positively_adult_speaker():
    adult_row = _row(author="adult")
    vm.rule_17_may_supersede(adult_row, _ctx(speaker_adult=True))
    for speaker in (False, None):                                   # a child, a guest / unknown: abstain
        assert _fail_rule(vm.rule_17_may_supersede, adult_row, _ctx(speaker_adult=speaker)) == 17


def test_rule_17_a_child_authored_or_unrecorded_row_is_unchanged():
    for row in (_row(author="child"), _row()):
        for speaker in (True, False, None):
            vm.rule_17_may_supersede(row, _ctx(speaker_adult=speaker))


def test_rule_17_a_curated_row_is_an_adults_even_with_no_author_and_a_curator_needs_no_lookup():
    curated = vm.Row("r0", "x", authority=ma.CURATED)
    assert _fail_rule(vm.rule_17_may_supersede, curated, _ctx(speaker_adult=True)) == 17          # a member's own power (4) still loses
    vm.rule_17_may_supersede(curated, _ctx(speaker_rank=ma.RANK[ma.CURATED]))                      # a curator: adult by construction


def test_rule_17_negative_control_the_old_rule_let_a_child_through(monkeypatch):
    row, ctx = _row(author="adult"), _ctx(speaker_adult=False)
    assert _fail_rule(vm.rule_17_may_supersede, row, ctx) == 17
    monkeypatch.setattr(ma, "may_correct", lambda *_a, **_k: True)
    vm.rule_17_may_supersede(row, ctx)                               # without the clause (the old behaviour) a child's correction passes
