"""The owner's own correction "X, not Y" is the owner's word - it must retire Y, not be parked.

Regression 2026-10-07 (live main 4ceb35ec): day-sim "how's my mum" answered "...getting good care
in Ballarat" after "Actually, I got that wrong earlier - my mum lives in Bendigo, not Ballarat."
Trace (logs + a live repro with a synthetic user): the turn digest's "User's mum lives in Bendigo"
was classed ``model_from_turn`` because ``memory_authority._window_is_a_statement_about_the_user``
counted the "not" of ", not Ballarat" as a polarity mismatch with the fact. A model-class write
cannot contradict an earlier user-class row, so it was written ``disputed`` (AUTHORITY_BLOCKED
kind=home) and "User's mum Ingrid lives in Ballarat" stayed ``approved`` - served as current beside
the new value. Whether the reply asserted Ballarat then depended on the 4B's sampling.

A ", not <other value>" clause is a contrast naming the value being corrected away; a clause that
names the fact's own value, or a hedge ("not sure"), still counts as a denial.

Break-the-fix control: with ``_without_contrast`` disabled the correction is parked as a dispute
and the stale row stays approved (the incident). Real MemoryService over a fake collection, no
model, no DB; synthetic names only.
"""
from __future__ import annotations

import asyncio

import pytest

import memory_authority as ma
import memory_digest
from test_memory_implicit_supersede import UID, _by_text, _flag, _patch_llm, _svc  # noqa: F401
import memory_supersede as ms

pytestmark = pytest.mark.ci_safe

FIX = "Actually, I got that wrong earlier - my mum lives in Bendigo, not Ballarat."
SAID_BALLARAT = "My mum Ingrid lives in Ballarat, and she's recovering from a hip replacement."
OLD = "User's mum Ingrid lives in Ballarat"
NEW = "User's mum lives in Bendigo"
M = "User's mum lives in Bendigo"


@pytest.mark.parametrize("fact,said,want", [
    (M, FIX, True),
    (M, "My mum lives in Bendigo, not Ballarat.", True),
    ("User's birthday is 7 August", "My birthday is 7 August, not 8 July.", True),
    ("User is a nurse", "I am a nurse, not a doctor.", True),
    # the clause names the FACT's own value: that is a denial of it, not a contrast
    ("User's mum lives in Ballarat", "My mum lives in Bendigo, not Ballarat.", False),
    ("User's birthday is 8 July", "My birthday is 7 August, not 8 July.", False),
    ("User is a doctor", "I am a nurse, not a doctor.", False),
    # plain denials and hedges are unchanged
    ("User lives in Perth", "I don't live in Perth.", False),
    ("User lives in Perth", "I live in Perth, not in Perth.", False),
    ("User lives in Perth", "I live in Perth, not sure about it.", False),
    # review of #1913: a clause that points BACK at the fact is a denial of it, not a corrected-away value
    (M, "My mum lives near Bendigo, but not there.", False),
    (M, "My mum lives in Bendigo, not in it.", False),
    (M, "My mum lives in Bendigo, and not that place.", False),
    ("User's mum lives in Bendigo", "My mum lives in Bendigo, she doesn't live there.", False),
    # review of #1913: the contrast also reads after an ATTACHED dash (em / en / hyphen / double hyphen)
    (M, "My mum lives in Bendigo\u2014not Ballarat.", True),
    (M, "My mum lives in Bendigo\u2013not Ballarat.", True),
    (M, "My mum lives in Bendigo-not Ballarat.", True),
    (M, "My mum lives in Bendigo--not Ballarat.", True),
    (M, "My mum lives in Bendigo - not Ballarat.", True),
    ("User's mum lives in Ballarat", "My mum lives in Bendigo\u2014not Ballarat.", False),
    ("User's mum lives in Ballarat", "My mum lives in Bendigo-not Ballarat.", False),
    ("User lives in Perth", "I live in Perth\u2014not in Perth.", False),
    ("User lives in Perth", "I live in Perth\u2014not sure about it.", False),
    # review round 2 of #1913: a TIME qualifier is a denial for now, not a corrected-away value
    (M, "My mum lives in Bendigo, but not at the moment.", False),
    (M, "My mum lives in Bendigo but not right now.", False),
    (M, "My mum lives in Bendigo\u2014not this year.", False),
    # ... and a conjunction alone is a delimiter: speech-to-text carries no commas
    (M, "My mum lives in Bendigo but not Ballarat", True),
    (M, "My mum lives in Bendigo and not Ballarat", True),
    ("User is a nurse", "I am a nurse and not a doctor", True),
    ("User's mum lives in Ballarat", "My mum lives in Bendigo but not Ballarat", False),
    ("User is a doctor", "I am a nurse and not a doctor", False),
    (M, "My mum lives in Bendigo and not there", False),
    ("User lives in Perth", "I live in Perth and not sure about it", False),
])
def test_contrast_clause_supports_the_new_value_only(fact, said, want):
    assert ma.supports(fact, said) is want


# The retraction shape: "I've dropped / quit / stopped / cancelled X" is the owner's word that X is over -
# the same polarity as the fact "User no longer does X" (day-sim race swap: AUTHORITY_BLOCKED
# writer=turn_digest action=supersede). Tense must still agree: a live statement never supports an ended one.
RACE = "Change of plan: I've dropped the Harbourtown half-marathon. I'm doing the Lakeside 12k in August instead."


@pytest.mark.parametrize("fact,said,want", [
    ("User is no longer doing the Harbourtown half-marathon", RACE, True),
    ("User dropped the Harbourtown half-marathon", RACE, True),
    ("User no longer plays squash", "I stopped playing squash.", True),
    ("User no longer plays squash", "I quit playing squash.", True),
    ("User no longer plays squash", "I cancelled my squash membership.", False),   # a membership is not the sport
    ("User cancelled their gym membership", "I cancelled my gym membership.", True),
    ("User no longer has a gym membership", "I cancelled my gym membership.", True),
    ("User no longer plays squash", "I no longer play squash.", True),
    # the other direction stays closed: a live statement does not support the end of the state, and back
    ("User is no longer doing the Harbourtown half-marathon", "I'm doing the Harbourtown half-marathon.", False),
    ("User is doing the Harbourtown half-marathon", RACE.split(". ")[0] + ".", False),
    ("User plays squash", "I quit squash.", False),
    ("User lives in Perth", "I live in Perth.", True),
    # review round 2 of #1913: the NEGATION of an end-state verb is not the end itself
    ("User dropped the Harbourtown half-marathon", "I haven't dropped the Harbourtown half-marathon.", False),
    ("User cancelled the gym membership", "I haven't cancelled the gym membership.", False),
    ("User quit squash", "I didn't quit squash.", False),
    ("User no longer does the Harbourtown half-marathon", "I haven't dropped the Harbourtown half-marathon.", False),
    ("User has not dropped the Harbourtown half-marathon", "I haven't dropped the Harbourtown half-marathon.", True),
    ("User dropped the Harbourtown half-marathon", "I dropped the Harbourtown half-marathon.", True),
    # review round 3 of #1913: the digest words a denial with the BASE form ("did not drop")
    ("User did not drop the Harbourtown half-marathon", "I haven't dropped the Harbourtown half-marathon.", True),
    ("User did not cancel the gym membership", "I haven't cancelled the gym membership.", True),
    ("User did not quit squash", "I didn't quit squash.", True),
    ("User did not leave Perth", "I didn't leave Perth.", True),
    ("User did not drop the Harbourtown half-marathon", "I dropped the Harbourtown half-marathon.", False),
    ("User dropped the Harbourtown half-marathon", "I did not drop the Harbourtown half-marathon.", False),
    # review round 4 of #1913: the negation binds to ITS verb (same clause, before it)
    ("User did not drop the Harbourtown marathon", "I did not enjoy the Harbourtown marathon, so I dropped it.", False),
    ("User dropped the Harbourtown marathon", "I did not enjoy the Harbourtown marathon, so I dropped it.", True),
    ("User dropped the Harbourtown marathon", "I didn't enjoy the Harbourtown marathon and I dropped it.", True),
    # ... and a bare action verb in the fact is the CLAIM, never an optional paraphrase word
    ("User plans to stop treatment", "I plan to continue treatment.", False),
    ("User will cancel the booking", "I will keep the booking.", False),
    ("User expects a drop in price", "I expect an increase in price.", False),
    ("User did not stop treatment", "I plan to continue treatment.", False),
    ("User plans to stop treatment", "I plan to stop treatment.", True),
    ("User will cancel the booking", "I will cancel the booking.", True),
    ("User expects a drop in price", "I expect a drop in price.", True),
])
def test_retraction_verbs_are_the_owners_word_about_their_own_fact(fact, said, want):
    assert ma.supports(fact, said) is want


def test_the_correction_is_the_owners_own_derived_statement():
    r = ma.resolve_write("turn_digest", NEW, anchor_text=FIX, user_id=UID)
    assert r.cls == ma.USER_STATED_DERIVED and r.power >= ma.DERIVED_RANK


def _run(monkeypatch):
    _flag(monkeypatch, True)
    svc, col = _svc(monkeypatch)
    _patch_llm(monkeypatch, [{"type": "profile", "fact": NEW}])

    async def go():
        old = await svc.ingest(OLD, user_id=UID, source="turn_digest", memory_type="profile", confidence=0.82,
                               status="approved", source_excerpt=SAID_BALLARAT, anchor_text=SAID_BALLARAT,
                               user_turn_id="t-1")
        assert col.rows[old.id][1]["authority_class"] == ma.USER_STATED_DERIVED   # the seed is the owner's word
        res = await memory_digest.run_turn_digest(UID, FIX, session_id="s-fix")
        return old.id, res

    old, res = asyncio.run(go())
    return svc, col, old, res


def test_the_correction_retires_the_stale_home_and_the_packet_serves_only_the_new_one(monkeypatch):
    svc, col, old, res = _run(monkeypatch)
    new_id, new = _by_text(col, NEW)
    assert new["status"] == "approved"                              # not parked as a dispute
    assert col.rows[old][1]["status"] == "superseded"               # kept, never deleted
    assert col.rows[old][1]["superseded_by_id"] == new_id
    assert res["superseded"] == 1
    served = [r.text for r in asyncio.run(svc.list_by_status(user_id=UID, status="approved"))]
    assert served == [NEW]                                          # Ballarat is never served as current


def test_negative_control_without_the_contrast_rule_the_stale_home_stays_current(monkeypatch):
    monkeypatch.setattr(ma, "_without_contrast", lambda win, fact: win)   # the pre-fix behaviour
    svc, col, old, res = _run(monkeypatch)
    _, new = _by_text(col, NEW)
    assert new["status"] == "disputed"                              # the incident: AUTHORITY_BLOCKED kind=home
    assert col.rows[old][1]["status"] == "approved"                 # ... and Ballarat is still served
    served = [r.text for r in asyncio.run(svc.list_by_status(user_id=UID, status="approved"))]
    assert served == [OLD]
