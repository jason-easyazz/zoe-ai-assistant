"""The owner's retraction / correction of their own fact retires the older row BY KEY, not by words.

Found by the agent of #1916 (day-sim 6n, 2026-10-08): after "Good news: I no longer get the migraines since I
switched to new glasses." the retraction landed approved as ``user_stated_derived`` but the older "User has been
getting migraines most afternoons lately" stayed APPROVED beside it (``superseded=0`` with the implicit-supersede
flag on). The day-sim passed only because the new row was served too.

Root cause (reproduced with the real ``run_turn_digest`` + ``MemoryService`` over a fake collection, no model):
``supersede_for_turn`` and the nightly pass paired rows with ``same_topic`` - the change must name at least half of the
OLD sentence's content words (``MIN_OLD_COVERAGE``). "User no longer gets migraines" names ONE of the old row's three
(migraine / afternoons / lately...), 1/3 < 1/2, so the pair was never made; with a longer wording ("... since switching
to new glasses") the extra words happened to tip another pairing. Which pairing happened therefore depended on how
much the 4B extractor wrote, not on whose fact it was. Class: a pairing decided by sentence overlap.

Fix: when the new row is the OWNER'S word (``memory_authority`` rank >= user_stated_derived) the pair is decided by the
(subject, attribute) key - ``memory_supersede.owner_key_match``: the same subject and either the same one-valued
attribute (home / birthday / age / job) or the same predicate and everything the retraction names. The older row is
retired by id (``superseded_by_id`` / ``invalid_at`` / ``expired_at``; never deleted; the two timelines of #1896).

Break-the-fix controls: with ``owner_key_match`` disabled the older row stays approved (the incident), in both the
turn lane and the nightly lane. Synthetic users and names only.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import importlib.util
import sys
from pathlib import Path

import pytest

import memory_authority as ma
import memory_digest
import memory_supersede as ms
from test_memory_implicit_supersede import UID, _by_text, _flag, _patch_llm, _svc  # noqa: F401

pytestmark = pytest.mark.ci_safe

T1 = "I've been getting migraines most afternoons lately and it's starting to worry me."
T2 = "Good news: I no longer get the migraines since I switched to new glasses."
OLD = "User has been getting migraines most afternoons lately"


def _drive(monkeypatch, fact, *, saying=T2, old=OLD, first=T1, old_type="profile"):
    _flag(monkeypatch, True)
    svc, col = _svc(monkeypatch)

    async def go():
        _patch_llm(monkeypatch, [{"type": old_type, "fact": old}])
        await memory_digest.run_turn_digest(UID, first, session_id="s-1")
        _patch_llm(monkeypatch, [{"type": "profile", "fact": fact}])
        return await memory_digest.run_turn_digest(UID, saying, session_id="s-1")

    res = asyncio.run(go())
    return svc, col, res


# ── the turn lane (run_turn_digest -> supersede_for_turn) ────────────────────────────────────────────

@pytest.mark.parametrize("fact", [
    "User no longer gets migraines",                                       # the incident wording (live: not paired)
    "User has stopped getting migraines",
    "User is no longer getting migraines",
    "User has stopped getting migraines since switching to new glasses",
    "User no longer gets migraines since switching to new glasses",
])
def test_the_owners_retraction_retires_the_older_row_by_id_whatever_the_wording(monkeypatch, fact):
    svc, col, res = _drive(monkeypatch, fact)
    old_id, old = _by_text(col, OLD)
    new_id, new = _by_text(col, fact)
    assert new["status"] == "approved" and new["authority_class"] == ma.USER_STATED_DERIVED
    assert old["status"] == "superseded", fact                       # the incident: it stayed approved
    assert old["superseded_by_id"] == new_id and new["supersedes_id"] == old_id
    assert isinstance(old["invalid_at"], float) and old["invalid_at"] == new["valid_from"]
    assert res["superseded"] == 1
    assert old_id in col.rows and col.rows[old_id][0] == OLD         # invalidated, never deleted: history is kept
    served = [r.text for r in asyncio.run(svc.list_by_status(user_id=UID, status="approved"))]
    assert OLD not in served and fact in served


def test_negative_control_without_the_key_pairing_the_older_row_stays_approved(monkeypatch):
    monkeypatch.setattr(ms, "owner_key_match", lambda *a, **k: "")      # the pre-fix behaviour
    fact = "User no longer gets migraines"
    svc, col, res = _drive(monkeypatch, fact)
    _, old = _by_text(col, OLD)
    assert old["status"] == "approved" and res["superseded"] == 0       # the incident
    served = [r.text for r in asyncio.run(svc.list_by_status(user_id=UID, status="approved"))]
    assert OLD in served and fact in served


def test_the_history_question_still_finds_the_retired_row(monkeypatch):
    fact = "User no longer gets migraines"
    svc, col, _ = _drive(monkeypatch, fact)
    old_id, old = _by_text(col, OLD)
    assert old["status"] == "superseded" and old["expired_at"] and old["invalid_at"]
    rows = asyncio.run(svc.list_by_status(user_id=UID, status="superseded"))
    assert [r.text for r in rows] == [OLD]


def test_another_persons_or_another_predicates_row_is_never_retired(monkeypatch):
    # a different subject (the mum), the same words
    svc, col, _ = _drive(monkeypatch, "User no longer gets migraines",
                         old="User's mum Ingrid has been getting migraines most afternoons lately",
                         first="My mum Ingrid has been getting migraines most afternoons lately.")
    assert _by_text(col, "User's mum Ingrid has been getting migraines most afternoons lately")[1]["status"] == "approved"
    # the same subject and the same object, but a different predicate (a specialist is not the symptom)
    svc, col, _ = _drive(monkeypatch, "User no longer gets migraines",
                         old="User sees a specialist about the migraines on Tuesdays",
                         first="I see a specialist about the migraines on Tuesdays.")
    assert _by_text(col, "User sees a specialist about the migraines on Tuesdays")[1]["status"] == "approved"
    # the same predicate, a different object
    svc, col, _ = _drive(monkeypatch, "User no longer gets migraines",
                         old="User has been getting headaches most afternoons lately",
                         first="I've been getting headaches most afternoons lately and it's starting to worry me.")
    assert _by_text(col, "User has been getting headaches most afternoons lately")[1]["status"] == "approved"


def test_a_model_guess_that_the_owner_did_not_say_retires_nothing(monkeypatch):
    # the extractor wrote an end the owner's turn does not state: parked/model class, never the key pairing
    svc, col, res = _drive(monkeypatch, "User no longer gets migraines", saying="Good news: the weather has cleared up.")
    assert _by_text(col, OLD)[1]["status"] == "approved"


# ── the key itself ────────────────────────────────────────────────────────────────────────────────────

END = ms.Cue("no longer", "end", ms.CUES[2].pattern, True)
SWAP = ms.Cue("moved to", "swap", ms.CUES[9].pattern, True)
CORRECTION = next(c for c in ms.CUES if c.name == "correction")


@pytest.mark.parametrize("new,old,cue,want", [
    ("User no longer gets migraines", OLD, END, "retraction"),
    ("User is no longer getting migraines", OLD, END, "retraction"),
    ("User quit smoking", "User smokes a pack a day while driving to the depot", END, "retraction"),
    ("User no longer plays squash", "User plays squash on Tuesdays at the club near the river", END, "retraction"),
    # a light verb needs its object; "quit smoking" is its own object
    ("User no longer gets them", OLD, END, ""),
    # not the same predicate / object / subject
    ("User no longer gets migraines", "User sees a specialist about the migraines on Tuesdays", END, ""),
    ("User no longer gets migraines", "User has been getting headaches most afternoons lately", END, ""),
    ("User no longer gets migraines", "User's mum has been getting migraines most afternoons lately", END, ""),
    ("User no longer gets migraines", "Dana has been getting migraines most afternoons lately", END, ""),
    # an end with no predicate (an object right after the cue) is the overlap rule's alone
    ("User dropped the half-marathon", "User has a half-marathon medal and a long training plan on the wall", END, ""),
    # a one-valued attribute slot, same subject
    ("User works at Acme", "User works at Bolt as a barista on weekdays and weekends", CORRECTION, "slot:job"),
    ("User's birthday is 7 August", "User's birthday is 8 July and they like cake", CORRECTION, "slot:birthday"),
    ("User is 45 years old", "User is 44 years old", CORRECTION, "slot:age"),
    ("User lives in Bendigo", "User lives in Ballarat near the lake", SWAP, "slot:home"),
    # a job is replaced only by an explicit change in the fact or a correction, not by any cue in the turn
    ("User works at Acme", "User works at Bolt", ms.Cue("change of plan", "swap", END.pattern, False), ""),
    # different subject / different attribute never pair
    ("User's friend Dana works at Acme", "User's friend Leo works at Bolt", CORRECTION, ""),
    ("User moved to Perth", "User works at a bakery", SWAP, ""),
    ("User lives in Bendigo and works at Acme", "User works at Bolt", SWAP, ""),
])
def test_owner_key_match(new, old, cue, want):
    assert ms.owner_key_match(new, old, cue) == want


def test_only_the_owners_word_qualifies():
    assert ms.is_owner_word({"authority_class": ma.USER_STATED_DERIVED})
    assert ms.is_owner_word({"authority_class": ma.USER_STATED})
    assert ms.is_owner_word({"authority_class": ma.USER_CONFIRMED})
    assert not ms.is_owner_word({"authority_class": ma.USER_UNVERIFIED})     # an unconfirmed panel voice
    assert not ms.is_owner_word({"authority_class": ma.MODEL_FROM_TURN})
    assert not ms.is_owner_word({"authority_class": ma.MODEL_FROM_TRANSCRIPT})


def test_an_unverified_panel_voice_cannot_retire_the_owners_row(monkeypatch):
    _flag(monkeypatch, True)
    svc, col = _svc(monkeypatch)
    fact = "User no longer gets migraines"

    async def go():
        _patch_llm(monkeypatch, [{"type": "profile", "fact": OLD}])
        await memory_digest.run_turn_digest(UID, T1, session_id="s-1", speaker_verified=True)
        _patch_llm(monkeypatch, [{"type": "profile", "fact": fact}])
        return await memory_digest.run_turn_digest(UID, T2, session_id="s-1", speaker_verified=False,
                                                   source="voice")

    asyncio.run(go())
    assert _by_text(col, OLD)[1]["status"] == "approved"


# ── the nightly lane (nightly_conflict_pass) ──────────────────────────────────────────────────────────

async def _seed_owner(svc, col, text, said, *, days_ago):
    ref = await svc.ingest(text, user_id=UID, source="turn_digest", memory_type="profile", confidence=0.82,
                           status="approved", source_excerpt=said, anchor_text=said, tags=["turn_digest"])
    when = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days_ago)
    doc, meta = col.rows[ref.id]
    meta.update(added_at=when.isoformat().replace("+00:00", "Z"), added_ts=when.timestamp())
    return ref.id


def _nightly(monkeypatch, fact=None):
    fact = fact or "User no longer gets migraines"
    _flag(monkeypatch, True)
    svc, col = _svc(monkeypatch)

    async def go():
        old = await _seed_owner(svc, col, OLD, T1, days_ago=3)
        new = await _seed_owner(svc, col, fact, T2, days_ago=1)
        assert col.rows[new][1]["authority_class"] == ma.USER_STATED_DERIVED
        return old, new, await ms.nightly_conflict_pass(svc, UID)

    return svc, col, asyncio.run(go())


def test_the_nightly_pass_retires_the_older_row_by_the_owners_key(monkeypatch):
    svc, col, (old, new, out) = _nightly(monkeypatch)
    assert out == {"pairs": 1, "superseded": 1}
    assert col.rows[old][1]["status"] == "superseded" and col.rows[old][1]["superseded_by_id"] == new
    assert col.rows[new][1]["status"] == "approved"


def test_negative_control_the_nightly_pass_without_the_key_leaves_it(monkeypatch):
    monkeypatch.setattr(ms, "owner_key_match", lambda *a, **k: "")
    svc, col, (old, new, out) = _nightly(monkeypatch)
    assert out == {"pairs": 0, "superseded": 0} and col.rows[old][1]["status"] == "approved"


def test_the_nightly_pass_never_pairs_a_model_class_row_by_key(monkeypatch):
    _flag(monkeypatch, True)
    svc, col = _svc(monkeypatch)

    async def go():
        old = await _seed_owner(svc, col, OLD, T1, days_ago=3)
        # the newer row is the model's reading of a transcript, no user evidence: not the owner's word
        ref = await svc.ingest("User no longer gets migraines", user_id=UID, source="digest",
                               memory_type="profile", confidence=0.82, status="approved", tags=["digest"])
        when = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)
        col.rows[ref.id][1].update(added_at=when.isoformat().replace("+00:00", "Z"), added_ts=when.timestamp())
        return old, await ms.nightly_conflict_pass(svc, UID)

    old, out = asyncio.run(go())
    assert col.rows[old][1]["status"] == "approved" and out["superseded"] == 0


# ── the day-sim seeds themselves (scenario 6 and 6n) ──────────────────────────────────────────────────

def _sim():
    perf = Path(__file__).resolve().parents[3] / "scripts" / "perf"
    sys.path.insert(0, str(perf))
    spec = importlib.util.spec_from_file_location("samantha_day_sim", perf / "samantha_day_sim.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["samantha_day_sim"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_day_sim_6n_seed_turns_leave_only_the_retraction_current(monkeypatch):
    say = _sim().SAY
    fact = "User no longer gets migraines"
    svc, col, res = _drive(monkeypatch, fact, saying=say["d2-migraine-neg"], first=say["d1-health"])
    assert _by_text(col, OLD)[1]["status"] == "superseded"
    assert [r.text for r in asyncio.run(svc.list_by_status(user_id=UID, status="approved"))] == [fact]


def test_day_sim_6_race_seed_turns_still_retire_the_half_marathon(monkeypatch):
    say = _sim().SAY
    _flag(monkeypatch, True)
    svc, col = _svc(monkeypatch)
    old_fact = "User is training for the Rottnest half-marathon in February"
    tomb = "User dropped the Rottnest half-marathon"
    swap = "User is doing the City to Surf 12k in August instead"

    async def go():
        _patch_llm(monkeypatch, [{"type": "event", "fact": old_fact}])
        await memory_digest.run_turn_digest(UID, say["d1-race"], session_id="s-1")
        _patch_llm(monkeypatch, [{"type": "event", "fact": tomb}, {"type": "event", "fact": swap}])
        return await memory_digest.run_turn_digest(UID, say["d2-race-swap"], session_id="s-1")

    res = asyncio.run(go())
    assert _by_text(col, old_fact)[1]["status"] == "superseded" and res["superseded"] == 1
    served = [r.text for r in asyncio.run(svc.list_by_status(user_id=UID, status="approved"))]
    assert old_fact not in served and swap in served
