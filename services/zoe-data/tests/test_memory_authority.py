"""Memory fidelity: model inference never supersedes or contradicts what the USER said.

Generalises the identity incident (2026-10-05): the nightly digest read a day transcript
holding a speech-to-text fragment that named a third person, asserted a fact about the
owner from it, and its contradiction pass called ``MemoryService.review(edit)`` - which
superseded a row the owner had stated himself and carried that row's source/session forward.
The class is "who SAID a fact decides who may change it" (``memory_authority.py``). This pack
pins, with synthetic data only (no household text, no network, no model, ``ci_safe``):

  * provenance on every row (``authority`` / ``origin`` / ``turn_ref`` / ``model``) and that a
    supersede records the NEW writer, never the old row's source / session / excerpt;
  * the wall at the choke point (``ingest`` / ``review`` / ``supersede_by``): store -> recall;
    the user's correction wins; an inferred contradiction cannot win (the digest incident
    replayed with a generic attribute: the genuine row survives, a candidate appears,
    ``AUTHORITY_BLOCKED`` is logged with labels only); negation is user_stated and supersedes;
    "I used to" keeps history; a third-person speech-to-text fragment creates no user fact
    (a name becomes a PERSON candidate); users are isolated;
  * anchoring (``supports``) - the user's own turn must carry the subject, the value, the
    attribute and the first person;
  * the legacy backfill rule;
  * the synthetic bar-scenario fixtures replayed at the store level.

Every wall has a break-the-fix control (``ZOE_MEMORY_AUTHORITY=0`` reproduces the incident).
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import types
from pathlib import Path

import pytest

import identity_facts as idf
import memory_authority as ma
import memory_service
from memory_service import MemoryService

pytestmark = pytest.mark.ci_safe

UID = "member-a"
OTHER = "member-b"
FIXTURES = Path(__file__).parent / "fixtures" / "memory_authority_scenarios.json"


# ── fakes ─────────────────────────────────────────────────────────────────────

def _match(meta: dict, where) -> bool:
    if not where:
        return True
    for key, want in where.items():
        if key == "$and":
            if not all(_match(meta, w) for w in want):
                return False
        elif key == "$or":
            if not any(_match(meta, w) for w in want):
                return False
        elif meta.get(key) != want:
            return False
    return True


class _Col:
    """A Chroma stand-in that HONOURS ``where`` (status / user scoping matter here)."""

    def __init__(self):
        self.rows: dict[str, tuple[str, dict]] = {}

    def upsert(self, *, ids, documents, metadatas, **_kw):
        for i, d, m in zip(ids, documents, metadatas):
            self.rows[i] = (d, dict(m))

    def update(self, *, ids, metadatas, **_kw):
        for i, m in zip(ids, metadatas):
            self.rows[i] = (self.rows[i][0], dict(m))

    def get(self, *, ids=None, where=None, include=None, **_kw):
        keys = [i for i in ids if i in self.rows] if ids is not None else list(self.rows)
        keys = [k for k in keys if _match(self.rows[k][1], where)]
        return {"ids": keys, "documents": [self.rows[i][0] for i in keys],
                "metadatas": [dict(self.rows[i][1]) for i in keys]}


@pytest.fixture
def svc(monkeypatch):
    monkeypatch.delenv("ZOE_MEMORY_AUTHORITY", raising=False)
    s = MemoryService(data_dir="/nonexistent/zoe-test-memory-authority")
    col = _Col()
    s._collection = lambda: col

    async def no_audit(**_kw):
        return None

    async def opted_in(_uid):
        return False

    s._append_audit = no_audit
    monkeypatch.setattr(memory_service, "_user_opted_out", opted_in)
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: s)
    s._col = col
    return s


def put(svc, text, source="voice_fact", *, user=UID, status="approved", **kw):
    return asyncio.run(svc.ingest(text, user_id=user, source=source, status=status,
                                  confidence=0.9, **kw))


def edit(svc, mem_id, text, actor, **kw):
    return asyncio.run(svc.review(mem_id, decision="edit", edits=text, actor=actor, **kw))


def meta(svc, mem_id):
    return svc._col.rows[mem_id][1]


def status_of(svc, status, user=UID):
    return [r for r in asyncio.run(svc.list_by_status(user_id=user, status=status))]


def recalled(svc, user=UID):
    return [r.text for r in svc._metadata_read(user, 50)]


# ── 1. provenance on every row ────────────────────────────────────────────────

def test_explicit_user_paths_are_user_stated_and_stamped(svc):
    ref = put(svc, "User's dog is named Teddy.", "voice_fact", user_turn_id="t-1")
    m = meta(svc, ref.id)
    assert (m["authority"], m["origin"], m["authority_basis"]) == (
        ma.USER_STATED, "voice_fact", "explicit_source")
    assert m["turn_ref"] == "t-1" and "model" not in m


def test_model_writers_without_the_users_words_are_inferred_and_name_the_model(svc):
    ref = put(svc, "User likes quiet mornings.", "digest")
    m = meta(svc, ref.id)
    assert (m["authority"], m["origin"], m["authority_basis"]) == (
        ma.INFERRED, "digest", "no_user_evidence")
    assert m["model"]
    assert "model" not in meta(svc, put(svc, "User likes tea.", "chat_regex").id)  # regex: no model


def test_deterministic_extractor_over_the_users_turn_is_user_stated(svc):
    m = meta(svc, put(svc, "User's sister is named Juno.", "chat_regex").id)
    assert (m["authority"], m["authority_basis"]) == (ma.USER_STATED, "deterministic_user_turn")


def test_anchored_model_fact_is_user_stated_only_when_the_users_turn_supports_it(svc):
    ok = put(svc, "User lives in Hobart.", "turn_digest", source_excerpt="I moved to Hobart in May")
    bad = put(svc, "User lives in Perth.", "turn_digest", source_excerpt="Casey moved to Perth in May")
    assert meta(svc, ok.id)["authority"] == ma.USER_STATED
    # a plain first-person sentence of the turn ENTAILS it: the owner speaking (ZMB C1), user_stated POWER
    assert meta(svc, ok.id)["authority_class"] == ma.USER_STATED_DERIVED
    assert meta(svc, ok.id)["authority_basis"] == ma.VERBATIM_BASIS
    # supported but hedged: still the user's derived fact, no power over what they said before
    hedged = put(svc, "User lives in Cairns.", "turn_digest", source_excerpt="I moved to Cairns in May I think",
                 user_turn_id="h-1")
    assert meta(svc, hedged.id)["authority_basis"] == "anchored_user_turn"
    assert meta(svc, bad.id)["authority"] == ma.INFERRED
    assert meta(svc, bad.id)["authority_basis"] == "unanchored"


def test_a_model_writer_cannot_claim_authority_it_does_not_have(svc):
    ref = put(svc, "User likes quiet mornings.", "digest", authority="user_stated")
    assert meta(svc, ref.id)["authority"] == ma.INFERRED
    down = put(svc, "User's cat is named Pip.", "voice_fact", authority="inferred")
    assert meta(svc, down.id)["authority"] == ma.INFERRED
    conf = put(svc, "User's cat is named Pippa.", "review_ui", authority="user_confirmed")
    assert meta(svc, conf.id)["authority"] == ma.USER_CONFIRMED


def test_supersede_records_the_new_writer_not_the_old_rows_provenance(svc):
    """The lie in the incident: review(edit) carried source/session/excerpt forward."""
    old = put(svc, "User's dog is named Teddy.", "voice_fact", session_id="sess-old",
              source_excerpt="my dog is Teddy", user_turn_id="turn-old")
    new = edit(svc, old.id, "User's dog is named Teddy Bear.", "consolidation",
               session_id="sess-new", turn_ref="turn-new")
    # consolidation has no user text, but this edit is a refused rewrite of a user row:
    assert new is None
    # a person's edit is a legitimate supersede - and records THEIR provenance
    new = edit(svc, old.id, "User's dog is named Teddy Bear.", "review_ui", session_id="sess-new",
               turn_ref="turn-new")
    nm, om = meta(svc, new.id), meta(svc, old.id)
    assert (om["source"], om["session_id"], om["status"]) == ("voice_fact", "sess-old", "superseded")
    assert nm["source"] == nm["added_by"] == nm["origin"] == "review_ui"
    assert nm["session_id"] == "sess-new" and nm["turn_ref"] == "turn-new"
    assert nm["supersedes_id"] == old.id and om["superseded_by_id"] == new.id
    assert nm["authority"] == ma.USER_CONFIRMED and nm["authority_class"] == ma.USER_CONFIRMED
    assert "user_turn_id" not in nm


def test_an_inferred_supersede_does_not_inherit_the_old_excerpt_or_session(svc):
    old = put(svc, "User likes quiet mornings.", "synthesis", session_id="sess-old",
              source_excerpt="I like quiet mornings")
    assert meta(svc, old.id)["authority"] == ma.INFERRED
    new = edit(svc, old.id, "User likes quiet evenings.", "consolidation")
    nm = meta(svc, new.id)
    assert nm["source"] == nm["origin"] == "consolidation" and nm["authority"] == ma.INFERRED
    assert "session_id" not in nm and "source_excerpt" not in nm
    assert nm["model"]
    assert meta(svc, old.id)["source_excerpt"] == "I like quiet mornings"  # the old row keeps its own


# ── 2. the wall ───────────────────────────────────────────────────────────────

def test_store_then_recall(svc):
    put(svc, "User's dog is named Teddy.", "voice_fact")
    assert recalled(svc) == ["User's dog is named Teddy."]


def test_the_users_correction_wins(svc):
    old = put(svc, "User lives in Geraldton.", "voice_fact")
    new = edit(svc, old.id, "User lives in Perth.", "review_ui")
    assert new is not None and meta(svc, old.id)["status"] == "superseded"
    assert recalled(svc) == ["User lives in Perth."]
    # the regex extractor's correction path is user_stated by construction
    again = edit(svc, new.id, "User lives in Broome.", "chat_regex", source_excerpt="actually I live in Broome")
    assert again is not None and recalled(svc) == ["User lives in Broome."]
    # the per-turn digest reading the owner's OWN plain sentence ("I live in Darwin now") is the owner changing
    # their mind (ZMB C1): it updates, with the honest class `user_stated_derived` and the verbatim basis
    third = edit(svc, again.id, "User lives in Darwin.", "turn_digest",
                 source_excerpt="Big news - I have moved. I live in Darwin now.")
    assert third is not None and recalled(svc) == ["User lives in Darwin."]
    assert (third.metadata["authority_class"], third.metadata["authority_basis"]) == (
        ma.USER_STATED_DERIVED, ma.VERBATIM_BASIS)
    # a paraphrase the turn does NOT plainly entail (hedged here) is only a MODEL's reading of it: it parks as a
    # candidate until the person says yes - then it wins
    fourth = edit(svc, third.id, "User lives in Cairns.", "turn_digest",
                  source_excerpt="Cairns is where I live now I think")
    assert fourth is None and recalled(svc) == ["User lives in Darwin."]
    (cand,) = status_of(svc, "disputed")
    assert cand.metadata["authority_class"] == ma.USER_STATED_DERIVED
    asyncio.run(svc.review(cand.id, decision="approve", actor="review_ui"))
    assert recalled(svc) == ["User lives in Cairns."]


def test_inferred_edit_of_a_user_row_is_refused_and_leaves_a_candidate(svc, caplog):
    caplog.set_level(logging.INFO, logger=ma.logger.name)
    seed = put(svc, "User's dog is named Teddy.", "voice_fact")
    assert edit(svc, seed.id, "User's dog is named Rex.", "digest") is None
    assert meta(svc, seed.id)["status"] == "approved"
    cands = status_of(svc, "disputed")
    assert len(cands) == 1 and cands[0].text == "User's dog is named Rex."
    cm = cands[0].metadata
    assert (cm["contradicts_id"], cm["authority"], cm["origin"]) == (seed.id, ma.INFERRED, "digest")
    assert cm["authority_blocked"] in (True, "True", 1)
    assert "AUTHORITY_BLOCKED writer=digest kind=pet" in caplog.text
    assert "Teddy" not in caplog.text and "Rex" not in caplog.text  # labels only
    assert recalled(svc) == ["User's dog is named Teddy."]            # the candidate is never recalled


def test_inferred_ingest_that_contradicts_a_user_row_becomes_a_candidate(svc, caplog):
    caplog.set_level(logging.INFO, logger=ma.logger.name)
    seed = put(svc, "User lives in Geraldton.", "voice_fact")
    ref = put(svc, "User lives in Perth.", "digest")
    assert ref.metadata["status"] == "disputed" and ref.metadata["contradicts_id"] == seed.id
    assert meta(svc, seed.id)["status"] == "approved"
    assert "AUTHORITY_BLOCKED writer=digest kind=home" in caplog.text
    # a second extraction of the same fact does not stack a second candidate
    put(svc, "User lives in Perth.", "digest")
    assert len(status_of(svc, "disputed")) == 1


def test_inferred_fact_with_no_conflict_is_stored_normally(svc):
    put(svc, "User lives in Geraldton.", "voice_fact")
    ref = put(svc, "User's dog is named Rex.", "digest")
    assert ref.metadata["status"] == "approved" and ref.metadata["authority"] == ma.INFERRED


def test_inferred_archive_and_reject_of_a_user_row_are_refused(svc, caplog):
    caplog.set_level(logging.INFO, logger=ma.logger.name)
    seed = put(svc, "User lives in Geraldton.", "voice_fact")
    for decision in ("archive", "reject"):
        assert asyncio.run(svc.review(seed.id, decision=decision, actor="consolidation")) is None
    assert meta(svc, seed.id)["status"] == "approved"
    assert "AUTHORITY_BLOCKED writer=consolidation kind=home action=archive" in caplog.text
    # an UNKNOWN writer is rank 0 (fail-closed); the decay sweep is a model-class maintainer, so
    # it can no longer archive a never-recalled row the user said (audit V12) ...
    for actor in ("janitor", "decay_sweep"):
        assert asyncio.run(svc.review(seed.id, decision="archive", actor=actor)) is None
    assert meta(svc, seed.id)["status"] == "approved"
    # ... but it still retires a stale MODEL row; the account and the operator may archive anything
    model_row = put(svc, "User likes quiet mornings.", "synthesis")
    assert asyncio.run(svc.review(model_row.id, decision="archive", actor="decay_sweep")) is not None
    done = asyncio.run(svc.review(seed.id, decision="archive", actor=UID))
    assert done is not None and meta(svc, seed.id)["status"] == "archived"


def test_inferred_row_can_still_be_rewritten_by_inference(svc):
    """The wall protects the USER's rows - inference may still tidy its own."""
    old = put(svc, "User likes quiet mornings.", "digest")
    assert edit(svc, old.id, "User likes quiet evenings.", "consolidation") is not None
    assert meta(svc, old.id)["status"] == "superseded"


def test_supersede_by_refuses_an_inferred_successor(svc, caplog):
    caplog.set_level(logging.INFO, logger=ma.logger.name)
    old = put(svc, "User lives in Geraldton.", "voice_fact")
    inferred = put(svc, "User lives in Perth.", "digest", status="pending")
    assert asyncio.run(svc.supersede_by(UID, old.id, inferred.id, actor="implicit_supersede")) is False
    assert meta(svc, old.id)["status"] == "approved"
    assert "AUTHORITY_BLOCKED writer=digest kind=home action=supersede" in caplog.text
    # a model's paraphrase of the user's turn that the turn does not PLAINLY entail (user_stated_derived)
    # is still not a direct statement
    derived = put(svc, "User lives in Perth.", "turn_digest", source_excerpt="Perth is where I live now",
                  user_turn_id="t-2", status="pending")
    assert meta(svc, derived.id)["authority_class"] == ma.USER_STATED_DERIVED
    assert meta(svc, derived.id)["authority_basis"] == "anchored_user_turn"
    assert asyncio.run(svc.supersede_by(UID, old.id, derived.id, actor="implicit_supersede")) is False
    # ...but the owner's own plain sentence read by the per-turn digest has user_stated POWER (ZMB C1)
    plain = put(svc, "User lives in Perth.", "turn_digest", source_excerpt="I live in Perth now",
                user_turn_id="t-2b", status="pending")
    assert (meta(svc, plain.id)["authority_class"], meta(svc, plain.id)["authority_basis"]) == (
        ma.USER_STATED_DERIVED, ma.VERBATIM_BASIS)
    assert ma.row_class(meta(svc, plain.id)) == ma.USER_STATED
    stated = put(svc, "User lives in Perth now.", "chat_regex", user_turn_id="t-3")
    assert asyncio.run(svc.supersede_by(UID, old.id, stated.id, actor="implicit_supersede")) is True
    assert meta(svc, old.id)["status"] == "superseded"


def test_a_user_approving_the_candidate_is_the_correction(svc):
    seed = put(svc, "User lives in Geraldton.", "voice_fact")
    cand = put(svc, "User lives in Perth.", "digest")
    # inference cannot approve the candidate against the user's row
    assert asyncio.run(svc.review(cand.id, decision="approve", actor="consolidation")) is None
    assert meta(svc, cand.id)["status"] == "disputed"
    ok = asyncio.run(svc.review(cand.id, decision="approve", actor="review_ui"))
    assert ok.metadata["authority"] == ma.USER_CONFIRMED
    assert meta(svc, cand.id)["status"] == "approved" and meta(svc, seed.id)["status"] == "superseded"
    assert recalled(svc) == ["User lives in Perth."]


def test_a_rejected_candidate_stays_rejected_and_the_user_row_stays_approved(svc):
    seed = put(svc, "User lives in Geraldton.", "voice_fact")
    cand = put(svc, "User lives in Perth.", "digest")
    asyncio.run(svc.review(cand.id, decision="reject", actor="review_ui"))
    assert put(svc, "User lives in Perth.", "digest") is None  # not resurrected
    assert meta(svc, seed.id)["status"] == "approved" and meta(svc, cand.id)["status"] == "rejected"


def test_negation_is_user_stated_and_supersedes_history_kept(svc, monkeypatch):
    """"I don't play tennis any more": the user's own words retire the row - and the old
    row is KEPT (superseded + invalid_at), never deleted. (Written by a direct user writer;
    a model's paraphrase of it waits for the person's yes - next test.)"""
    monkeypatch.setenv("ZOE_MEMORY_IMPLICIT_SUPERSEDE", "1")
    import memory_supersede

    old = put(svc, "User plays tennis on Saturdays.", "voice_fact")
    new = put(svc, "User no longer plays tennis.", "voice_fact", memory_type="state_change",
              tags=["state_change"])
    assert meta(svc, new.id)["authority"] == ma.USER_STATED
    out = asyncio.run(memory_supersede.supersede_for_turn(svc, UID, "no longer", [new]))
    assert out["superseded"] == 1
    om = meta(svc, old.id)
    assert om["status"] == "superseded" and om["superseded_by_id"] == new.id and om["invalid_at"]
    assert asyncio.run(svc.get(old.id)).text == "User plays tennis on Saturdays."


def test_a_model_paraphrased_negation_waits_for_the_persons_yes(svc, monkeypatch):
    monkeypatch.setenv("ZOE_MEMORY_IMPLICIT_SUPERSEDE", "1")
    import memory_supersede

    old = put(svc, "User plays tennis on Saturdays.", "voice_fact")
    new = put(svc, "User no longer plays tennis.", "turn_digest", memory_type="state_change",
              tags=["state_change"], source_excerpt="I do not play tennis on Saturdays any more I think")
    assert new.metadata["authority_class"] == ma.USER_STATED_DERIVED and new.metadata["status"] == "disputed"
    out = asyncio.run(memory_supersede.supersede_for_turn(svc, UID, "no longer", [new]))
    assert out["superseded"] == 0 and meta(svc, old.id)["status"] == "approved"
    asyncio.run(svc.review(new.id, decision="approve", actor="review_ui"))
    assert meta(svc, old.id)["status"] == "superseded" and meta(svc, new.id)["authority_class"] == ma.USER_CONFIRMED


def test_an_unanchored_model_negation_cannot_retire_a_user_row(svc, monkeypatch, caplog):
    """The same shape from a model with no user words behind it (break-the-fix control
    for the test above: the retirement comes from the user's anchor, not from the shape)."""
    caplog.set_level(logging.INFO, logger=ma.logger.name)
    monkeypatch.setenv("ZOE_MEMORY_IMPLICIT_SUPERSEDE", "1")
    import memory_supersede

    old = put(svc, "User plays tennis on Saturdays.", "voice_fact")
    new = put(svc, "User no longer plays tennis.", "turn_digest", memory_type="state_change",
              tags=["state_change"])  # no excerpt / anchor
    assert new.metadata["status"] == "disputed" and new.metadata["authority"] == ma.INFERRED
    out = asyncio.run(memory_supersede.supersede_for_turn(svc, UID, "no longer", [new]))
    assert out["superseded"] == 0 and meta(svc, old.id)["status"] == "approved"
    assert "AUTHORITY_BLOCKED writer=turn_digest" in caplog.text


def test_i_used_to_keeps_history(svc, monkeypatch):
    monkeypatch.setenv("ZOE_MEMORY_IMPLICIT_SUPERSEDE", "1")
    import memory_supersede

    old = put(svc, "User lives in Perth.", "voice_fact")
    new = put(svc, "User used to live in Perth.", "voice_fact", memory_type="state_change",
              tags=["state_change"])
    assert meta(svc, new.id)["authority"] == ma.USER_STATED
    asyncio.run(memory_supersede.supersede_for_turn(svc, UID, "used to", [new]))
    om = meta(svc, old.id)
    assert om["status"] == "superseded" and om["superseded_by_id"] == new.id
    assert "User lives in Perth." not in recalled(svc)            # no longer a current fact
    assert asyncio.run(svc.get(old.id)).text == "User lives in Perth."  # the history survives


def test_users_are_isolated(svc):
    put(svc, "User lives in Geraldton.", "voice_fact", user=UID)
    ref = put(svc, "User lives in Perth.", "digest", user=OTHER)
    assert ref.metadata["status"] == "approved"           # UID's protected row is not OTHER's
    assert status_of(svc, "disputed", OTHER) == [] and status_of(svc, "disputed", UID) == []
    put(svc, "User's dog is named Teddy.", "voice_fact", user=UID)
    cand = put(svc, "User's dog is named Rex.", "digest", user=UID)
    assert cand.metadata["status"] == "disputed"
    assert recalled(svc, OTHER) == ["User lives in Perth."]


def test_identity_stays_with_the_account(svc):
    """#1866 stays the special case: inference can never write the name at all, and the
    answer is the account's whatever the store holds."""
    assert put(svc, "User's name is Mika Vale.", "digest") is None
    ident = idf.build_identity(UID, username="zed", users_name="zed")
    assert ident is not None and ident.name == "Zed"
    assert recalled(svc) == []


# ── 3. the digest incident, replayed on a GENERIC attribute ───────────────────

def _digest(svc, monkeypatch, *, transcript, facts, seed_id):
    import memory_digest

    async def todays(*a, **k):
        return transcript

    async def extract(_text):
        return facts

    async def always_contradiction(*a, **k):
        return True

    async def search(*a, **k):
        r = svc._col.rows[seed_id]
        return [types.SimpleNamespace(id=seed_id, text=r[0])]

    async def no_blob(*a, **k):
        return ""

    stub = types.ModuleType("zoe_agent")
    stub._mempalace_load_user_facts = no_blob
    stub._invalidate_user_facts_cache = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "zoe_agent", stub)
    monkeypatch.setattr(memory_digest, "_load_todays_messages", todays)
    monkeypatch.setattr(memory_digest, "_extract_facts_with_gemma", extract)
    monkeypatch.setattr(memory_digest, "_is_contradiction", always_contradiction)

    async def no_emotions(*a, **k):  # the emotional pass is a SECOND model call: never from a test
        return 0

    monkeypatch.setattr(memory_digest, "_emotional_memory_pass", no_emotions)
    svc.search = search
    return asyncio.run(memory_digest.run_memory_digest(UID))


STT = ("okay so that was the plan for tomorrow and then uh Rex is coming over I think "
       "and we will see what happens with the weather and the shopping later on tonight ")


def test_digest_replay_generic_attribute_genuine_row_survives(svc, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    seed = put(svc, "User's dog is named Teddy.", "voice_fact")
    out = _digest(svc, monkeypatch, transcript=STT,
                  facts=[{"fact": "User's dog is named Rex.", "type": "profile"}], seed_id=seed.id)
    assert out["superseded"] == 0
    assert meta(svc, seed.id)["status"] == "approved"
    assert not any("Rex" in d and m["status"] == "approved" for d, m in svc._col.rows.values())
    cands = status_of(svc, "disputed")
    assert len(cands) == 1 and cands[0].metadata["contradicts_id"] == seed.id
    assert "AUTHORITY_BLOCKED writer=digest kind=pet" in caplog.text
    ours = "\n".join(r.getMessage() for r in caplog.records if r.name == ma.logger.name)
    assert ours and "Teddy" not in ours and "Rex" not in ours      # labels only
    assert out["new"] == 0                                           # a candidate is not a stored fact


def test_digest_replay_break_the_fix_control(svc, monkeypatch):
    """With the wall off the SAME replay reproduces the incident: the genuine row is
    superseded and the new row's provenance LOOKS like the user's (it is the digest's)."""
    monkeypatch.setenv("ZOE_MEMORY_AUTHORITY", "0")
    monkeypatch.setenv("ZOE_DIGEST_OBSERVATION_GATE", "off")      # the gate is the second wall: a model's claim the owner's words do not carry is held first
    seed = put(svc, "User's dog is named Teddy.", "voice_fact")
    out = _digest(svc, monkeypatch, transcript=STT,
                  facts=[{"fact": "User's dog is named Rex.", "type": "profile"}], seed_id=seed.id)
    assert out["superseded"] == 1 and meta(svc, seed.id)["status"] == "superseded"
    new = meta(svc, meta(svc, seed.id)["superseded_by_id"])
    assert new["authority"] == ma.INFERRED and new["origin"] == "digest"   # stamped honestly even then
    assert new["source"] == "digest"                                        # no carried-forward lie


def test_digest_replay_the_observation_gate_alone_holds_the_incident_with_the_wall_off(svc, monkeypatch):
    """Two walls, each enough alone: with the authority wall OFF the nightly digest's unsupported "Rex" is still not stored as a
    fact and never touches the owner's row - the observation gate holds it (pending) before it reaches the contradiction pass."""
    monkeypatch.setenv("ZOE_MEMORY_AUTHORITY", "0")
    seed = put(svc, "User's dog is named Teddy.", "voice_fact")
    out = _digest(svc, monkeypatch, transcript=STT,
                  facts=[{"fact": "User's dog is named Rex.", "type": "profile"}], seed_id=seed.id)
    assert out["superseded"] == 0 and meta(svc, seed.id)["status"] == "approved"
    assert out.get("observations_held") == 1
    assert [d for d, m in svc._col.rows.values() if m["status"] == "approved"] == ["User's dog is named Teddy."]
    assert [d for d, m in svc._col.rows.values() if m["status"] == "pending"] == ["User's dog is named Rex."]


def test_third_person_fragment_creates_no_user_fact_and_a_person_candidate(svc, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    seed = put(svc, "User lives in Geraldton.", "voice_fact")
    _digest(svc, monkeypatch, transcript="okay " * 5 + "uh Mika Vale is coming over I think " + "and so on " * 8,
            facts=[{"fact": "User's name is Mika Vale.", "type": "profile"}], seed_id=seed.id)
    approved = [d for d, m in svc._col.rows.values() if m["status"] == "approved"]
    assert approved == ["User lives in Geraldton."]
    persons = [r for r in status_of(svc, "pending") if r.metadata.get("entity_type") == "person_pending"]
    assert len(persons) == 1 and "Mika Vale" in persons[0].text and "name is" not in persons[0].text
    assert persons[0].metadata["authority"] == ma.INFERRED
    assert "PERSON_CANDIDATE writer=digest" in caplog.text and "Mika" not in caplog.text


def test_a_name_the_user_really_claimed_makes_no_person_candidate(svc):
    assert put(svc, "User's name is Mika Vale.", "digest", anchor_text="hi, my name is Mika Vale") is None
    assert status_of(svc, "pending") == [] and status_of(svc, "disputed") == []


def test_third_person_move_is_not_a_user_attribute(svc, monkeypatch):
    seed = put(svc, "User lives in Geraldton.", "voice_fact")
    _digest(svc, monkeypatch, transcript="okay " * 5 + "Casey moved to Hobart last month and she loves it " + "so on " * 8,
            facts=[{"fact": "User lives in Hobart.", "type": "profile"}], seed_id=seed.id)
    assert meta(svc, seed.id)["status"] == "approved"
    assert [r.text for r in status_of(svc, "disputed")] == ["User lives in Hobart."]


def test_digest_that_reads_the_users_own_words_is_derived_and_cannot_overwrite_a_direct_row(
        svc, monkeypatch):
    """The anchor makes the digest's fact `user_stated_derived`: it beats model classes and
    other derived rows, but NEVER a direct statement - a paraphrase of an older sentence in a
    day's transcript must not overwrite what the person said directly (both would be
    'user_stated' and the newer would win). It waits for the person's yes."""
    seed = put(svc, "User lives in Geraldton.", "voice_fact")
    out = _digest(svc, monkeypatch, transcript="okay " * 5 + "Big news, I moved to Hobart last week " + "so on " * 8,
                  facts=[{"fact": "User lives in Hobart.", "type": "profile"}], seed_id=seed.id)
    assert out["superseded"] == 0 and meta(svc, seed.id)["status"] == "approved"
    (cand,) = status_of(svc, "disputed")
    assert (cand.metadata["authority_class"], cand.metadata["authority_basis"]) == (
        ma.USER_STATED_DERIVED, "anchored_user_turn")
    assert cand.metadata["origin"] == "digest" and cand.metadata["model"]  # honest about WHO wrote it
    # ...and over another DERIVED row (or any model row) the same digest does win
    derived_seed = put(svc, "User lives in Alice Springs.", "turn_digest", user_turn_id="d1",
                       source_excerpt="Alice Springs is where I live")
    assert derived_seed.metadata["authority_class"] == ma.USER_STATED_DERIVED
    new = edit(svc, derived_seed.id, "User lives in Hobart.", "digest",
               anchor_text="Big news, I moved to Hobart last week")
    assert new is not None and meta(svc, derived_seed.id)["status"] == "superseded"


def test_the_digest_transcript_is_user_turns_only():
    import inspect
    import memory_digest

    assert "cm.role = 'user'" in inspect.getsource(memory_digest._load_todays_messages)


# ── 4. flag, kill switch, nothing else changes ────────────────────────────────

def test_kill_switch_keeps_provenance_but_drops_the_wall(svc, monkeypatch):
    monkeypatch.setenv("ZOE_MEMORY_AUTHORITY", "0")
    seed = put(svc, "User lives in Geraldton.", "voice_fact")
    ref = put(svc, "User lives in Perth.", "digest")
    assert ref.metadata["status"] == "approved" and ref.metadata["authority"] == ma.INFERRED
    assert meta(svc, seed.id)["status"] == "approved" and meta(svc, seed.id)["authority"] == ma.USER_STATED


# ── 5. anchoring ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("fact,turn", [
    ("User's name is Mika Vale.", "hi, my name is Mika Vale"),
    ("User's name is Mika Vale.", "call me Mika Vale"),
    ("User lives in Perth.", "I moved to Perth last month"),
    ("User lives in Perth.", "okay. I live in Perth these days"),
    ("User's dog is named Teddy.", "my dog Teddy needs a walk"),
    ("User's brother is named Tomás.", "My brother Tomas is driving down from Porto on Saturday"),
    ("User's birthday is 15 March 1980.", "my birthday is 15/03/1980"),
    ("User is allergic to penicillin.", "I'm allergic to penicillin"),
    ("User no longer plays tennis.", "I don't play tennis any more"),
    ("Casey's birthday is 4 May.", "Casey's birthday is on the 4th of May"),
])
def test_supports_the_users_own_words(fact, turn):
    assert ma.supports(fact, turn)


@pytest.mark.parametrize("fact,turn", [
    ("User's name is Mika Vale.", "uh Mika Vale is coming over, I think"),     # a name is not a claim
    ("User's name is Mika Vale.", "Mika Vale said her name is Mika Vale"),     # no first person
    ("User lives in Perth.", "Casey moved to Perth last month"),               # someone else
    ("User lives in Perth.", "I have never been to Perth"),                    # no home cue
    ("User's dog is named Teddy.", "uh Teddy is coming over I think"),         # no dog, no first person
    ("User is allergic to penicillin.", "Casey is allergic to penicillin"),
    ("User's birthday is 15 March 1980.", "my birthday is in March"),         # value missing
    ("User lives in Perth.", ""),
])
def test_does_not_support_what_the_user_did_not_say(fact, turn):
    assert not ma.supports(fact, turn)


def test_conflict_kinds_are_a_closed_vocabulary():
    cases = {
        ("User lives in Perth.", "User lives in Geraldton."): "home",
        ("User's dog is named Rex.", "User's dog is named Teddy."): "pet",
        ("User works at Acme.", "User works at Initech."): "work",
        ("User is 45 years old.", "User is 44 years old."): "age",
        ("User no longer works at Acme.", "User works at Acme."): "work",
    }
    for (new, old), kind in cases.items():
        assert ma.conflict_kind(new, old) == kind, (new, old)
    assert ma.conflict_kind("User likes tea.", "User likes coffee.") is None   # both can be true
    assert ma.conflict_kind("Casey lives in Perth.", "User lives in Geraldton.") is None  # other subject
    assert ma.kind_of("User's secret handshake is odd") == "other"


# ── 6. the backfill rule for rows written before provenance ──────────────────

@pytest.mark.parametrize("meta_, text, want", [
    ({"source": "voice_fact", "status": "approved"}, "x", ma.USER_STATED),
    ({"source": "review_ui", "status": "approved"}, "x", ma.USER_CONFIRMED),
    ({"source": "chat_regex", "status": "approved"}, "x", ma.USER_STATED),
    # the incident row: label carried forward from a regex row, but a MODEL reviewed it
    ({"source": "chat_regex", "reviewed_by": "digest", "status": "approved"}, "x", ma.INFERRED),
    ({"source": "digest", "status": "approved"}, "x", ma.INFERRED),
    ({"source": "consolidation", "status": "approved"}, "x", ma.INFERRED),
    ({"source": "digest", "reviewed_by": "review_ui", "status": "approved"}, "x", ma.USER_CONFIRMED),
    ({"source": "turn_digest", "status": "approved",
      "source_excerpt": "I live in Perth"}, "User lives in Perth.", ma.USER_STATED),
    ({"source": "turn_digest", "status": "approved",
      "source_excerpt": "Casey lives in Perth"}, "User lives in Perth.", ma.INFERRED),
    ({"source": "turn_digest", "status": "approved"}, "User lives in Perth.", ma.INFERRED),
    ({"source": "turn_digest", "reviewed_by": "turn_digest", "status": "approved",
      "source_excerpt": "I live in Perth"}, "User lives in Perth.", ma.INFERRED),
    ({"authority": "inferred", "source": "voice_fact"}, "x", ma.INFERRED),   # a stamp wins
])
def test_legacy_authority(meta_, text, want):
    assert ma.row_authority(meta_, text) == want


# ── 7. synthetic bar-scenario fixtures, replayed at the store level ───────────

def _scenarios():
    return json.loads(FIXTURES.read_text())["scenarios"]


def test_fixture_file_shape():
    doc = json.loads(FIXTURES.read_text())
    assert doc["synthetic"] is True
    ids = [s["id"] for s in doc["scenarios"]]
    assert ids and len(ids) == len(set(ids))
    for s in doc["scenarios"]:
        assert {"id", "title", "proves", "turns", "asks", "store"} <= set(s)
        for t in s["turns"]:
            assert len(t) == 3  # (user, session label, text) - the samantha_bar.py turn shape


@pytest.mark.parametrize("scn", _scenarios(), ids=lambda s: s["id"])
def test_bar_scenario_store_level(svc, scn):
    """Replay a scenario's turns as the writers would see them: the user's turns seed the
    store as user_stated; each model proposal is ingested with the day's user turns as its
    anchor; the expected outcome is pinned (approved texts, pending candidates, authority)."""
    spec = scn["store"]
    for text, source in spec["seed"]:
        put(svc, text, source)
    user_turns = "\n".join(t[2] for t in scn["turns"])
    for prop in spec["proposals"]:
        put(svc, prop["fact"], prop["writer"], anchor_text=user_turns)
    approved = sorted(d for d, m in svc._col.rows.values() if m["status"] == "approved")
    assert approved == sorted(spec["expect_approved"]), scn["id"]
    pending = sorted(d for d, m in svc._col.rows.values() if m["status"] in ("pending", "disputed"))
    assert pending == sorted(spec["expect_pending"]), scn["id"]
    for text, want in spec.get("expect_authority", {}).items():
        auths = {m["authority"] for d, m in svc._col.rows.values() if d == text}
        assert auths == {want}, (scn["id"], text)


# ── 8. more break-the-fix controls ────────────────────────────────────────────

def test_control_the_anchor_is_what_separates_a_derived_fact_from_a_guess(svc, monkeypatch):
    put(svc, "User lives in Geraldton.", "turn_digest", source_excerpt="Geraldton is where I live")  # derived
    guess = put(svc, "User lives in Hobart.", "turn_digest",
                source_excerpt="Casey moved to Hobart last month")
    assert guess.metadata["status"] == "disputed" and guess.metadata["authority"] == ma.INFERRED
    monkeypatch.setattr(ma, "supports", lambda *a, **k: True)       # break the anchor
    broken = put(svc, "User lives in Darwin.", "turn_digest",
                 source_excerpt="Casey moved to Darwin last month")
    assert broken.metadata["status"] == "approved"
    assert broken.metadata["authority_class"] == ma.USER_STATED_DERIVED


def test_control_archive_and_supersede_walls_are_the_authority_rule(svc, monkeypatch):
    seed = put(svc, "User lives in Geraldton.", "voice_fact")
    inferred = put(svc, "User lives in Perth.", "digest", status="pending")
    monkeypatch.setenv("ZOE_MEMORY_AUTHORITY", "0")
    assert asyncio.run(svc.supersede_by(UID, seed.id, inferred.id, actor="implicit_supersede")) is True
    other = put(svc, "User's dog is named Teddy.", "voice_fact")
    assert asyncio.run(svc.review(other.id, decision="archive", actor="consolidation")) is not None


def test_control_the_edit_wall_is_the_authority_rule(svc, monkeypatch):
    seed = put(svc, "User's dog is named Teddy.", "voice_fact")
    monkeypatch.setenv("ZOE_MEMORY_AUTHORITY", "0")
    new = edit(svc, seed.id, "User's dog is named Rex.", "digest")
    assert new is not None and meta(svc, seed.id)["status"] == "superseded"
    assert meta(svc, new.id)["origin"] == "digest" and meta(svc, new.id)["authority"] == ma.INFERRED


def test_the_edit_carries_no_user_turn_id_forward(svc):
    old = put(svc, "User's dog is named Teddy.", "voice_fact", user_turn_id="turn-old")
    assert meta(svc, old.id)["user_turn_id"] == "turn-old"
    new = edit(svc, old.id, "User's dog is named Teddy Bear.", "review_ui")
    assert "user_turn_id" not in meta(svc, new.id)


# ── 9. review round 1 (Codex on #1868) ────────────────────────────────────────

def test_default_manual_proposals_are_the_users_own_statements(svc):
    """POST /api/memories/proposals defaults source_type to "manual": the class must come from
    the route (origin="proposal"), never from a client-chosen label."""
    import inspect

    from routers import memories

    assert 'origin="proposal"' in inspect.getsource(memories)
    assert ma.writer_class("manual") == ma.USER_STATED
    ref = put(svc, "User prefers tea.", "manual", origin="proposal")
    assert ref.metadata["authority_class"] == ma.USER_STATED
    # a client-chosen label that is NOT on the allow-list cannot downgrade the route
    odd = put(svc, "User prefers oat milk.", "web-form-7", origin="proposal")
    assert odd.metadata["authority_class"] == ma.USER_STATED


@pytest.mark.parametrize("new,old", [
    ("User prefers coffee.", "User prefers tea."),
    ("User drives a Honda.", "User drives a Toyota."),
    ("User is vegan.", "User is vegetarian."),
    ("User is married.", "User is single."),
    ("User studies law.", "User studies nursing."),
])
def test_ordinary_distilled_shapes_contradict(svc, new, old):
    assert ma.conflict_kind(new, old)
    put(svc, old, "voice_fact")
    ref = put(svc, new, "digest")
    assert ref.metadata["status"] == "disputed"


@pytest.mark.parametrize("new,old", [
    ("User likes coffee.", "User likes tea."),
    ("User prefers green tea.", "User prefers tea."),     # a richer statement of the same thing
    ("User prefers tea.", "User prefers tea."),
    ("User drives a Honda.", "Casey drives a Toyota."),  # someone else
])
def test_ordinary_shapes_that_do_not_contradict(new, old):
    assert ma.conflict_kind(new, old) is None


def test_a_quote_stitched_across_two_user_turns_is_no_anchor():
    import memory_digest

    turns = "My dog is Teddy.\nRex is coming over."
    stitched = {"fact": "User's dog is named Rex.", "quote": "My dog is Teddy. Rex is coming over."}
    assert memory_digest.fact_anchor(stitched, turns) is None
    one = {"fact": "User's dog is named Teddy.", "quote": "my dog is teddy."}
    assert memory_digest.fact_anchor(one, turns) == "my dog is teddy."
