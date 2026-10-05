"""Regression pack for the review of #1868 (Opus: 8 inline findings, Codex: 3) - one test group per
finding, each with the probe the reviewer ran and a break-the-fix control.

  1  supports(): a sentence about SOMEONE ELSE / a negation / a question is not the user's statement
  2  legacy_class_basis: the owner's own account id in reviewed_by is a PERSON (user_confirmed)
  3  idle consolidation: the supersede is ONE operation judged on the new row, never a bare archive
  4  a held-back write is visible (review queue), asked about ONCE (offer), expires LOGGED (30 d);
     short answers and first-person present shapes are read; brain_tool carries the user's turn
  5  user_unverified has a hook (speaker verdict) and its exposure is pinned
  6  approve honours origin (an admin approving another member's candidate)
  7  (matrix) lives in test_memory_authority_matrix.py - the order and policy are literals
  8  proposals/manual, sweep default actor, edge caller class, session ids, backfill env

Synthetic data, fake Chroma (ci_safe).
"""
from __future__ import annotations

import asyncio
import importlib
import inspect
import logging
import os

import pytest

import memory_authority as ma
import memory_service
from memory_service import MemoryService
from test_memory_authority import _Col

pytestmark = pytest.mark.ci_safe

UID = "jason"


@pytest.fixture
def svc(monkeypatch):
    for k in ("ZOE_MEMORY_AUTHORITY", "ZOE_AFFECT_CONSENT_GATE"):
        monkeypatch.delenv(k, raising=False)
    s = MemoryService(data_dir="/nonexistent/zoe-test-memory-authority-review")
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


def put(svc, text, source="voice_fact", **kw):
    return asyncio.run(svc.ingest(text, user_id=UID, source=source, status="approved", **kw))


def status(svc, mem_id):
    return svc._col.rows[mem_id][1]["status"]


def recalled(svc):
    return [r.text for r in svc._metadata_read(UID, 50)]


# ── 1. supports(): the user speaking about THEMSELF, affirmatively ───────────

NOT_THE_USERS_STATEMENT = [
    ("User lives in Perth.", "my sister lives in Perth"),
    ("User's birthday is 12 March.", "my wife's birthday is 12 March"),
    ("User works at Acme.", "my mum works at Acme"),
    ("User is allergic to nuts.", "my son is allergic to nuts"),
    ("User lives in Perth.", "I don't live in Perth"),                 # negation the fact does not share
    ("User lives in Perth.", "do I live in Perth?"),                    # a question
    ("User lives in Perth.", "I used to live in Perth"),                # past, the fact is present
    ("User lives in Perth.", "I wish I lived in Perth"),                # a wish
    ("User lives in Perth.", "if I moved I would live in Perth"),       # hypothetical
    ("User lives in Perth.", "I think my brother lives in Perth"),      # a relative, even with an "I"
    ("User lives in Perth.", "am I living in Perth"),
]


@pytest.mark.parametrize("fact,turn", NOT_THE_USERS_STATEMENT)
def test_supports_rejects_what_is_not_the_users_own_affirmative_statement(fact, turn):
    assert not ma.supports(fact, turn)


@pytest.mark.parametrize("fact,turn", [
    ("User lives in Perth.", "I moved to Perth last month"),
    ("User lives in Perth.", "no, I live in Perth now"),                # a leading "no," is not a negation
    ("User's sister lives in Perth.", "my sister lives in Perth"),      # the fact names the relation
    ("User's brother is named Tomás.", "My brother Tomas is driving down from Porto"),
    ("User's dog is named Teddy.", "my dog Teddy needs a walk"),       # pets are not people-relations
    ("User no longer plays tennis.", "I don't play tennis any more"),   # the fact shares the negation
    ("User used to live in Perth.", "I used to live in Perth"),
    ("User is 41 years old.", "i'm 41 now"),
    ("User works at Acme.", "I started at Acme last week"),
    ("User lives in Perth.", "I'm in Perth now"),
    ("User works at Acme.", "I'm at Acme now"),
])
def test_supports_reads_first_person_present_and_change_shapes(fact, turn):
    assert ma.supports(fact, turn)


def test_a_short_elliptical_answer_needs_the_question_it_answers():
    q = "Where do you live now?"
    assert not ma.supports("User lives in Perth.", "no, Perth now")                # alone: nothing
    assert ma.supports("User lives in Perth.", "no, Perth now", q)
    assert ma.supports("User's name is Alex.", "it's Alex", "What's your name?")
    assert not ma.supports("User lives in Perth.", "no, Perth now", "Anything else?")   # no "?" about the attribute
    assert not ma.supports("User lives in Perth.", "Casey is in Perth now and I know it is lovely", q)  # long: not an answer


def test_the_probe_end_to_end_a_relatives_sentence_cannot_supersede_the_owners_row(svc):
    """The reviewer's probe: owner row (chat_regex) then a digest edit anchored on "my sister
    lives in Perth" used to return a user_stated row and supersede the owner's."""
    old = put(svc, "User lives in Sydney.", "chat_regex")
    got = asyncio.run(svc.review(old.id, decision="edit", edits="User lives in Perth.", actor="turn_digest",
                                 anchor_text="my sister lives in Perth"))
    assert got is None and status(svc, old.id) == "approved" and recalled(svc) == ["User lives in Sydney."]
    # control: the anchor check removed -> the incident reproduces (the row WOULD have won)
    real = ma.supports
    ma.supports = lambda *a, **k: True
    try:
        win = asyncio.run(svc.review(old.id, decision="edit", edits="User lives in Perth.", actor="turn_digest",
                                     anchor_text="my sister lives in Perth"))
    finally:
        ma.supports = real
    assert win is None  # a derived paraphrase never overrides a DIRECT row even with a perfect anchor
    derived = put(svc, "User lives in Alice Springs.", "turn_digest", source_excerpt="I live in Alice Springs",
                  user_turn_id="d1")
    ma.supports = lambda *a, **k: True
    try:
        broke = asyncio.run(svc.review(derived.id, decision="edit", edits="User lives in Perth.",
                                       actor="turn_digest", anchor_text="my sister lives in Perth"))
    finally:
        ma.supports = real
    assert broke is not None and status(svc, derived.id) == "superseded"


# ── 2. legacy: the account id in reviewed_by is a person ──────────────────────

def test_owner_in_reviewed_by_is_user_confirmed_not_a_model():
    for src in ("turn_digest", "chat_regex", "digest", "consolidation"):
        meta = {"source": src, "reviewed_by": UID, "user_id": UID, "status": "approved"}
        assert ma.legacy_class_basis(meta, "User lives in Perth.")[0] in (ma.USER_CONFIRMED, ma.USER_STATED), src
    cls, basis = ma.legacy_class_basis({"source": "turn_digest", "reviewed_by": UID, "user_id": UID}, "x")
    assert (cls, basis) == (ma.USER_CONFIRMED, "legacy_reviewed_by_person")
    # an admin / any other non-model label is a person too; a MODEL reviewer is not
    assert ma.legacy_class_basis({"source": "digest", "reviewed_by": "admin-x", "user_id": UID})[0] == ma.USER_CONFIRMED
    assert ma.legacy_class_basis({"source": "chat_regex", "reviewed_by": "digest", "user_id": UID})[0] \
        == ma.MODEL_FROM_TRANSCRIPT


def test_rows_the_owner_approved_before_the_pr_are_protected_end_to_end(svc):
    md = MemoryService._build_metadata(
        user_id=UID, source="turn_digest", session_id=None, user_turn_id=None, memory_type="fact",
        confidence=0.8, status="approved", tags=[], entity_type=None, entity_id=None, expires_at=None, text="x")
    md["reviewed_by"] = UID                                      # unstamped legacy row the owner approved
    svc._col.upsert(ids=["legacy"], documents=["User lives in Sydney."], metadatas=[md])
    assert asyncio.run(svc.review("legacy", decision="edit", edits="User lives in Perth.", actor="digest")) is None
    assert status(svc, "legacy") == "approved"


# ── 3. idle consolidation: one operation, judged on the new row ───────────────

def test_idle_consolidation_over_a_direct_row_is_held_not_half_applied(svc):
    import expert_dispatch

    old = put(svc, "User lives in Sydney.", "chat_regex")
    out = asyncio.run(expert_dispatch._ingest_or_supersede(
        svc, "User lives in Perth.", user_id=UID, source="idle_consolidation", session_id="s1",
        user_turn_id="idle:1", memory_type="fact", confidence=0.8, tags=["idle"],
        anchor_text="I moved to Perth in March"))
    assert out == "dropped"                                       # held back as a candidate
    assert status(svc, old.id) == "approved" and recalled(svc) == ["User lives in Sydney."]
    (cand,) = asyncio.run(svc.list_by_status(user_id=UID, status="disputed"))
    assert cand.metadata["contradicts_id"] == old.id


def test_idle_consolidation_over_a_model_row_supersedes_in_one_operation(svc, monkeypatch):
    import expert_dispatch

    old = put(svc, "User lives in Sydney.", "digest")             # a model row

    async def reconcile(_svc, text, user_id, **k):                  # the fake store has no search
        return "update", old.id

    monkeypatch.setattr("memory_quality.reconcile_for_ingest", reconcile)
    out = asyncio.run(expert_dispatch._ingest_or_supersede(
        svc, "User lives in Perth.", user_id=UID, source="idle_consolidation", session_id="s1",
        user_turn_id="idle:2", memory_type="fact", confidence=0.8, tags=["idle"],
        anchor_text="I moved to Perth in March"))
    assert out == "stored" and status(svc, old.id) == "superseded"
    assert recalled(svc) == ["User lives in Perth."]               # ONE current fact, not two
    assert svc._col.rows[old.id][1]["superseded_by_id"]


# ── 4. a held-back write is visible, asked about once, and expires logged ─────

def _candidate(svc):
    old = put(svc, "User is 40 years old.", "chat_regex")
    cand = put(svc, "User is 41 years old.", "digest")
    assert cand.metadata["status"] == "disputed"
    return old, cand


def test_disputes_are_listed_in_the_review_queue_with_the_text_they_dispute(svc, monkeypatch):
    from routers import memories

    old, cand = _candidate(svc)
    monkeypatch.setattr(memories, "_svc", lambda: svc)

    async def allow(*a, **k):
        return None

    monkeypatch.setattr(memories, "require_feature_access", allow)
    out = asyncio.run(memories.list_review_queue(limit=50, user={"user_id": UID}, db=None))
    (item,) = [i for i in out["items"] if i.get("dispute")]
    assert item["id"] == cand.id and item["contradicts_id"] == old.id
    assert item["contradicts_text"] == "User is 40 years old." and item["content"] == "User is 41 years old."


def test_a_review_that_was_held_back_is_a_409_not_a_crash(svc, monkeypatch):
    from fastapi import HTTPException

    from routers import memories

    monkeypatch.setattr(memories, "_svc", lambda: svc)

    async def allow(*a, **k):
        return None

    monkeypatch.setattr(memories, "require_feature_access", allow)
    old = put(svc, "User lives in Sydney.", "chat_regex")
    body = memories.MemoryReviewBody(action="edit", content="User lives in Perth.", note=None)
    # a non-admin owner editing is the account: allowed (200) ...
    ok = asyncio.run(memories.review_memory(old.id, body, user={"user_id": UID, "role": "member"}, db=None))
    assert ok["content"] == "User lives in Perth."
    # ... an empty change that a wall refuses (opt-out / identity / authority) answers 409
    monkeypatch.setattr(svc, "review", lambda *a, **k: asyncio.sleep(0, result=None))
    with pytest.raises(HTTPException) as e:
        asyncio.run(memories.review_memory(old.id, body, user={"user_id": UID, "role": "member"}, db=None))
    assert e.value.status_code == 409


def test_one_question_is_queued_for_a_fresh_candidate_and_not_twice(svc, monkeypatch):
    import memory_disputes
    import pending_suggestions

    old, cand = _candidate(svc)
    stored = []

    async def store(user_id, session_id, suggestions):
        stored.extend(suggestions)
        return len(suggestions)

    asked = {"n": 0}

    async def already(user_id, candidate_id):
        return asked["n"] > 0

    monkeypatch.setattr(pending_suggestions, "store_suggestions", store)
    monkeypatch.setattr(memory_disputes, "_already_asked", already)
    n = asyncio.run(memory_disputes.queue_questions(svc, UID, "s1", "i'm 41 now", fresh=[cand]))
    assert n == 1 and stored[0]["action_type"] == "memory_dispute"
    assert stored[0]["pre_filled_slots"] == {"candidate_id": cand.id, "old_id": old.id}
    assert "User is 40 years old" in stored[0]["offer_phrase"] and "User is 41 years old" in stored[0]["offer_phrase"]
    asked["n"] = 1                                                   # already open: not asked again
    assert asyncio.run(memory_disputes.queue_questions(svc, UID, "s1", "i'm 41 now", fresh=[cand])) == 0
    # the next time the topic comes up (no fresh candidate), the open dispute is asked about
    asked["n"] = 0
    stored.clear()
    n = asyncio.run(memory_disputes.queue_questions(svc, UID, "s2", "how old am i, years old"))
    assert n == 1
    # ... and nothing is queued with the wall off or without a session
    assert asyncio.run(memory_disputes.queue_questions(svc, UID, "", "x", fresh=[cand])) == 0


def test_yes_applies_the_candidate_no_keeps_the_old_row(svc):
    import memory_disputes

    old, cand = _candidate(svc)
    assert asyncio.run(memory_disputes.resolve(UID, cand.id, accept=True, svc=svc)) is True
    assert status(svc, cand.id) == "approved" and status(svc, old.id) == "superseded"
    assert svc._col.rows[cand.id][1]["authority_class"] == ma.USER_CONFIRMED
    old2 = put(svc, "User lives in Sydney.", "chat_regex")
    cand2 = put(svc, "User lives in Perth.", "digest")
    assert asyncio.run(memory_disputes.resolve(UID, cand2.id, accept=False, svc=svc)) is True
    assert status(svc, cand2.id) == "rejected" and status(svc, old2.id) == "approved"
    assert asyncio.run(memory_disputes.resolve("someone-else", cand2.id, accept=True, svc=svc)) is False


def test_pending_suggestions_executes_and_dismisses_the_dispute_offer():
    import pending_suggestions

    src = inspect.getsource(pending_suggestions)
    assert 'action == "memory_dispute"' in src and "memory_disputes.resolve" in src
    assert 'r["action_type"] == "memory_dispute"' in src and "accept=False" in src


def test_an_unanswered_dispute_expires_to_a_logged_stale_dispute_never_a_deletion(svc, caplog):
    import memory_disputes

    caplog.set_level(logging.INFO, logger=memory_disputes.logger.name)
    old, cand = _candidate(svc)
    n = asyncio.run(memory_disputes.expire_stale(svc, UID, now=cand.metadata["added_ts"] + 29 * 86400))
    assert n == 0 and status(svc, cand.id) == "disputed"            # inside the TTL
    n = asyncio.run(memory_disputes.expire_stale(svc, UID, now=cand.metadata["added_ts"] + 31 * 86400))
    assert n == 1 and status(svc, cand.id) == "rejected"             # still in the palace, status says why
    assert "stale dispute" in svc._col.rows[cand.id][1]["review_note"]
    assert status(svc, old.id) == "approved"
    assert "STALE_DISPUTE user=jason" in caplog.text


def test_the_turn_digest_and_the_weekly_pass_call_the_dispute_machinery():
    import memory_digest

    assert "memory_disputes.queue_questions" in inspect.getsource(memory_digest.run_turn_digest)
    assert "memory_disputes" in inspect.getsource(memory_digest.run_weekly_consolidation)


def test_brain_tool_explicit_remember_carries_the_users_turn(svc, monkeypatch):
    import intent_router
    import memory_digest

    put(svc, "User lives in Sydney.", "chat_regex")

    async def turn(user_id, **k):
        return "remember that I live in Perth now"

    monkeypatch.setattr(memory_digest, "latest_user_turn", turn)
    reply = asyncio.run(intent_router.execute_intent(
        intent_router.Intent("memory_store", {"text": "User lives in Perth now."}), UID))
    assert "remember" in reply.lower()
    row = [m for d, m in svc._col.rows.values() if d == "User lives in Perth now."][0]
    assert row["status"] == "approved" and row["authority_class"] == ma.USER_STATED
    assert row["origin"] == "explicit_teach" and row["source"] == "brain_tool"
    # not an imperative, or the turn does not support the fact -> the model's paraphrase stays rank 1
    for said in ("I think I might move to Perth", "remember that I like tea"):
        async def other(user_id, _s=said, **k):
            return _s

        monkeypatch.setattr(memory_digest, "latest_user_turn", other)
        out = asyncio.run(intent_router.execute_intent(
            intent_router.Intent("memory_store", {"text": "User lives in Perth."}), UID))
        assert "haven't changed it" in out or "may already be stored" in out
    assert not [1 for d, m in svc._col.rows.values() if d == "User lives in Perth." and m["status"] == "approved"]


# ── 5. user_unverified: the hook and its exposure ─────────────────────────────

def test_an_unverified_speaker_self_fact_is_user_unverified(svc):
    for w in ("voice_fact", "voice_regex", "voice"):
        r = ma.resolve_write(w, "User's dog is named Rex.", speaker_verified=False)
        assert (r.cls, r.rank) == (ma.USER_UNVERIFIED, 2), w
    # the lane reporting nothing (today) or a match leaves the class alone; chat is not the panel
    assert ma.resolve_write("voice_fact", "User's dog is named Rex.").cls == ma.USER_STATED
    assert ma.resolve_write("voice_fact", "User's dog is named Rex.", speaker_verified=True).cls == ma.USER_STATED
    assert ma.resolve_write("chat_regex", "User's dog is named Rex.", speaker_verified=False).cls == ma.USER_STATED
    assert ma.resolve_write("voice_fact", "Casey's dog is named Rex.", speaker_verified=False).cls == ma.USER_STATED
    # a model fact anchored on an unverified voice turn is unverified too
    d = ma.resolve_write("voice_turn_digest", "User lives in Perth.", anchor_text="I live in Perth",
                         speaker_verified=False)
    assert d.cls == ma.USER_UNVERIFIED


def test_an_unverified_voice_turn_cannot_overwrite_the_owners_rows(svc):
    owner = put(svc, "User's dog is named Teddy.", "review_ui")           # user_confirmed
    typed = put(svc, "User lives in Sydney.", "chat_regex")
    ref = put(svc, "User's dog is named Rex.", "voice_fact", speaker_verified=False)
    assert ref.metadata["authority_class"] == ma.USER_UNVERIFIED and ref.metadata["status"] == "disputed"
    assert status(svc, owner.id) == "approved"
    got = asyncio.run(svc.review(typed.id, decision="edit", edits="User lives in Perth.", actor="voice_fact",
                                 speaker_verified=False))
    assert got is None and status(svc, typed.id) == "approved"
    same = asyncio.run(svc.review(typed.id, decision="edit", edits="User lives in Perth.", actor="voice_fact"))
    assert same is not None        # exposure: with NO verdict (today's voice daemon) the panel is a direct user


def test_run_turn_digest_accepts_the_speaker_verdict():
    import memory_digest

    assert "speaker_verified" in inspect.signature(memory_digest.run_turn_digest).parameters


# ── 6. approve honours origin ─────────────────────────────────────────────────

def test_an_admin_approving_another_members_candidate_is_a_person(svc):
    old, cand = _candidate(svc)
    got = asyncio.run(svc.review(cand.id, decision="approve", actor="admin-jason", origin="admin"))
    assert got.metadata["authority_class"] == ma.USER_CONFIRMED and status(svc, old.id) == "superseded"
    # control: the same actor with no origin is an unknown label (rank 0) and is refused
    old2 = put(svc, "User lives in Sydney.", "chat_regex")
    cand2 = put(svc, "User lives in Perth.", "digest")
    assert asyncio.run(svc.review(cand2.id, decision="approve", actor="admin-jason")) is None
    assert status(svc, old2.id) == "approved"


# ── 8. the P2 notes ───────────────────────────────────────────────────────────

def test_sweep_soft_archive_default_actor_is_not_an_operator():
    assert inspect.signature(MemoryService.sweep_soft_archive).parameters["actor"].default == "decay_sweep"
    assert ma.writer_class("decay_sweep") == ma.MODEL_FROM_TURN


def test_the_edge_caller_passes_its_real_class():
    import person_extractor

    assert person_extractor._edge_authority_for("conversation", "Alice is Bob's sister") == "user_stated"
    assert person_extractor._edge_authority_for("voice", "Alice is Bob's sister") == "user_stated"
    for src in ("digest", "consolidation", "some_new_lane", "turn_digest"):
        assert person_extractor._edge_authority_for(src, "Alice is Bob's sister") == "inferred", src


@pytest.mark.asyncio
async def test_process_text_from_a_model_source_cannot_close_a_user_stated_edge(monkeypatch):
    import person_extractor
    from test_temporal_relationships import _current_edges, _open_db, _seed_people

    monkeypatch.setenv("ZOE_TEMPORAL_RELATIONSHIPS_ENABLED", "1")
    db = await _open_db()
    await db.execute("ALTER TABLE person_relationships ADD COLUMN authority TEXT")
    await db.execute("ALTER TABLE person_relationships ADD COLUMN origin TEXT")
    try:
        await _seed_people(db, "jason")
        await person_extractor._write_relationship("jason", "Alice", "Bob", "friend", "personal", db,
                                                   authority="user_stated")
        await person_extractor.process_text("Alice is Bob's sister", user_id="jason", source="digest", db=db)
        assert [e["rel_type"] for e in await _current_edges(db)] == ["friend"]
        await person_extractor.process_text("Alice is Bob's sister", user_id="jason", source="conversation", db=db)
        assert [e["rel_type"] for e in await _current_edges(db)] == ["sibling"]
    finally:
        await db.close()


def test_edits_keep_the_new_writers_session():
    import correction_apply
    import memory_extractor

    assert "session_id=session_id" in inspect.getsource(memory_extractor.extract_and_ingest)
    assert "session_id" in inspect.signature(correction_apply.apply_date_correction).parameters
    assert "session_id" in inspect.signature(correction_apply.apply_pet_correction).parameters
    assert "session_id=session_id" in inspect.getsource(correction_apply.maybe_apply)


def test_backfill_honours_mempalace_data_dir(monkeypatch, tmp_path):
    import importlib.util
    from pathlib import Path

    monkeypatch.setenv("MEMPALACE_DATA_DIR", str(tmp_path))
    script = Path(__file__).resolve().parents[3] / "scripts" / "maintenance" / "memory_authority_backfill.py"
    spec = importlib.util.spec_from_file_location("bf_env", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.DEFAULT_PALACE == str(tmp_path)
