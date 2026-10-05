"""The gates that sit beside the authority wall.

* MCP: an MCP agent acts FOR a member but is not the member - every MCP review / forget passes
  ``origin="mcp"`` (a model writer, rank 1) with the account only as the acting member, so an MCP
  edit can never overwrite a ``user_stated`` row (it used to arrive as ``actor=<user id>`` =
  ``user_confirmed``).
* Person merge: closes / re-points people rows and relationship edges whoever stated them, so it is
  a user (or admin) action, enforced at the entry point.
* Edges: ``person_extractor._write_relationship`` -> ``_edge_may_change`` is the people-graph half of
  the same rule (``test_memory_authority_people.py`` pins the stamps).
* Affect consent (docs/governance/emotional-safety-note.md section 6): a RECORD of how someone seems
  (an ``emotional_moment`` row, a feeling in a row's metadata) is kept for consenting adult members
  only - guests and children never. ``ZOE_AFFECT_CONSENT_GATE=members`` (default) refuses guests and
  members flagged as minors; ``optin`` also needs the member's stored persona mode; ``off`` allows.

Synthetic data, fake Chroma, fake persona lookup (ci_safe).
"""
from __future__ import annotations

import asyncio
import inspect
import logging

import pytest

import memory_authority as ma
import memory_service
from memory_service import MemoryService
from test_memory_authority import _Col

pytestmark = pytest.mark.ci_safe

UID = "member-a"


@pytest.fixture
def svc(monkeypatch):
    for k in ("ZOE_MEMORY_AUTHORITY", "ZOE_AFFECT_CONSENT_GATE"):
        monkeypatch.delenv(k, raising=False)
    s = MemoryService(data_dir="/nonexistent/zoe-test-memory-authority-gates")
    col = _Col()
    s._collection = lambda: col

    async def no_audit(**_kw):
        return None

    async def opted_in(_uid):
        return False

    s._append_audit = no_audit
    monkeypatch.setattr(memory_service, "_user_opted_out", opted_in)
    s._col = col
    return s


def put(svc, text, source="voice_fact", user=UID, **kw):
    return asyncio.run(svc.ingest(text, user_id=user, source=source, status="approved", **kw))


# ── MCP ──────────────────────────────────────────────────────────────────────

def test_an_mcp_edit_cannot_overwrite_a_user_stated_row(svc, caplog):
    caplog.set_level(logging.INFO, logger=ma.logger.name)
    seed = put(svc, "User's dog is named Teddy.")
    # exactly what mcp_server.memory_review calls: actor = the member, origin = "mcp"
    got = asyncio.run(svc.review(seed.id, decision="edit", edits="User's dog is named Rex.",
                                 actor=UID, origin="mcp"))
    assert got is None and svc._col.rows[seed.id][1]["status"] == "approved"
    (cand,) = asyncio.run(svc.list_by_status(user_id=UID, status="disputed"))
    assert cand.metadata["origin"] == "mcp" and cand.metadata["authority_class"] == ma.MODEL_FROM_TURN
    assert "AUTHORITY_BLOCKED writer=mcp kind=pet action=edit" in caplog.text
    # the archive/reject (memory_forget) and the approve routes are model-class too
    for decision in ("archive", "reject"):
        assert asyncio.run(svc.review(seed.id, decision=decision, actor=UID, origin="mcp")) is None
    assert asyncio.run(svc.review(cand.id, decision="approve", actor=UID, origin="mcp")) is None
    assert svc._col.rows[cand.id][1]["status"] == "disputed"
    # break-the-fix control: the OLD call shape (actor = user id, no origin) was user_confirmed
    old_shape = asyncio.run(svc.review(seed.id, decision="edit", edits="User's dog is named Rex.", actor=UID))
    assert old_shape is not None and svc._col.rows[seed.id][1]["status"] == "superseded"
    assert old_shape.metadata["authority_class"] == ma.USER_CONFIRMED


def test_mcp_server_passes_origin_on_every_review_and_forget_call():
    import mcp_server

    src = inspect.getsource(mcp_server)
    calls = [seg for seg in src.split("await svc.review(")[1:]]
    assert len(calls) >= 2
    for seg in calls:
        assert 'origin="mcp"' in seg.split(")\n", 1)[0] + seg[:400], seg[:120]


def test_an_mcp_added_fact_is_a_model_write(svc):
    put(svc, "User lives in Geraldton.")
    ref = put(svc, "User lives in Perth.", source="mcp")
    assert ref.metadata["authority_class"] == ma.MODEL_FROM_TURN and ref.metadata["status"] == "disputed"


# ── person merge ─────────────────────────────────────────────────────────────

def test_person_merge_is_a_user_action_enforced_at_the_entry_point(caplog):
    caplog.set_level(logging.INFO, logger=ma.logger.name)
    import person_merge

    for actor in ("digest", "consolidation", "person_extractor_llm", "mcp", "some_new_writer"):
        with pytest.raises(person_merge.PersonMergeError):
            # refused BEFORE any database work: db=None would raise AttributeError otherwise
            asyncio.run(person_merge.merge_person(None, UID, "a", "b", actor=actor))
    assert "AUTHORITY_BLOCKED writer=digest kind=relationship action=merge" in caplog.text


@pytest.mark.asyncio
async def test_person_merge_by_the_account_still_works_and_off_is_the_control(monkeypatch):
    import person_merge
    from test_person_merge import _open_db, _rows, _seed_person

    db = await _open_db()
    try:
        await _seed_person(db, "src", user_id=UID, name="Al", is_partial=1)
        await _seed_person(db, "dst", user_id=UID, name="Alice")
        out = await person_merge.merge_person(db, UID, "src", "dst", actor=UID)
        assert out
        assert (await _rows(db, "SELECT deleted FROM people WHERE id='src'"))[0]["deleted"] == 1
    finally:
        await db.close()
    monkeypatch.setenv("ZOE_MEMORY_AUTHORITY", "off")
    db = await _open_db()
    try:
        await _seed_person(db, "src", user_id=UID, name="Al", is_partial=1)
        await _seed_person(db, "dst", user_id=UID, name="Alice")
        assert await person_merge.merge_person(db, UID, "src", "dst", actor="digest")  # the control
    finally:
        await db.close()


def test_the_merge_endpoint_passes_the_account_as_actor():
    from routers import people

    assert "actor=user_id" in inspect.getsource(people.merge_person_endpoint)


# ── edges: the exact function that closes Postgres edges ──────────────────────

def test_the_edge_writer_calls_the_authority_rule():
    import person_extractor

    src = inspect.getsource(person_extractor._write_relationship)
    assert "_edge_may_change(" in src                         # the supersede branch asks first
    assert 'authority=_edge_authority_for(source, text), origin=source' in inspect.getsource(person_extractor.process_text)
    assert "may_override(" in inspect.getsource(person_extractor._edge_may_change)


# ── affect consent ───────────────────────────────────────────────────────────

def _persona(monkeypatch, *, mode="companion", minor=False, fail=False):
    import persona_layer

    async def load(uid, db=None):
        if fail:
            raise RuntimeError("db down")
        return persona_layer.MemberMode(mode=mode, minor=minor)

    monkeypatch.setattr(persona_layer, "load_member_mode", load)


def emo(svc, user=UID, **kw):
    return asyncio.run(svc.ingest("User was proud of the 5k finish.", user_id=user, source="digest",
                                  memory_type="emotional_moment", status="approved", **kw))


@pytest.mark.parametrize("guest", ["guest", "voice-guest", "anonymous", "voice-daemon"])
def test_guests_never_get_an_affective_record(svc, monkeypatch, guest, caplog):
    caplog.set_level(logging.INFO, logger=memory_service.logger.name)
    _persona(monkeypatch)
    assert emo(svc, user=guest) is None and not svc._col.rows
    assert "AFFECT_NOT_STORED" in caplog.text


def test_a_minor_never_gets_an_affective_record(svc, monkeypatch):
    _persona(monkeypatch, mode="kid", minor=True)
    assert emo(svc) is None and not svc._col.rows


def test_a_consenting_adult_keeps_it(svc, monkeypatch):
    _persona(monkeypatch)
    ref = emo(svc)
    assert ref is not None and ref.metadata["memory_type"] == "emotional_moment"


def test_a_fact_with_a_feeling_keeps_the_fact_not_the_feeling_for_a_minor(svc, monkeypatch):
    _persona(monkeypatch, mode="kid", minor=True)
    ref = put(svc, "User has a swimming carnival on Friday.", source="turn_digest",
              metadata={"affect": "anxious"})
    assert ref is not None and not any(k.endswith("affect") for k in ref.metadata)
    _persona(monkeypatch)
    ok = put(svc, "User has a maths test on Monday.", source="turn_digest", metadata={"affect": "anxious"})
    assert ok.metadata.get("candidate_affect") == "anxious"


def test_an_edit_cannot_make_a_minor_row_affective(svc, monkeypatch):
    _persona(monkeypatch)
    row = emo(svc)
    _persona(monkeypatch, mode="kid", minor=True)
    assert asyncio.run(svc.review(row.id, decision="edit", edits="User was proud of the 10k finish.",
                                  actor="review_ui")) is None


def test_optin_mode_requires_the_stored_persona_mode_and_fails_closed(svc, monkeypatch):
    monkeypatch.setenv("ZOE_AFFECT_CONSENT_GATE", "optin")
    _persona(monkeypatch, mode="unset")
    assert emo(svc) is None                                   # no opt-in row
    _persona(monkeypatch, mode="companion")
    assert emo(svc) is not None
    _persona(monkeypatch, fail=True)
    assert emo(svc, user="member-c") is None                  # lookup failed: closed


def test_default_mode_fails_open_on_a_lookup_error_but_off_allows_guests(svc, monkeypatch):
    _persona(monkeypatch, fail=True)
    assert emo(svc) is not None                               # members mode: a DB blip is not a ban
    monkeypatch.setenv("ZOE_AFFECT_CONSENT_GATE", "off")      # the break-the-fix control
    _persona(monkeypatch)
    assert emo(svc, user="guest") is not None
