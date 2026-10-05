"""The gates that sit beside the authority wall.

* MCP: an MCP agent acts FOR a member but is not the member - every MCP review / forget passes
  ``origin="mcp"`` (a model writer, rank 1) with the account only as the acting member, so an MCP
  edit can never overwrite a ``user_stated`` row (it used to arrive as ``actor=<user id>`` =
  ``user_confirmed``).
* Person merge: closes / re-points people rows and relationship edges whoever stated them, so it is
  a user (or admin) action, enforced at the entry point.
* Edges: ``person_extractor._write_relationship`` -> ``_edge_may_change`` is the people-graph half of
  the same rule (``test_memory_authority_people.py`` pins the stamps).
* Affect gate (docs/governance/emotional-safety-note.md section 6; owner product decision 2026-10-05):
  a RECORD of how someone seems (an ``emotional_moment`` row, a feeling in a row's metadata) is kept
  for every household member INCLUDING children, never for guests, no stored consent row.
  ``ZOE_AFFECT_CONSENT_GATE=household`` (DEFAULT); ``members`` = adults only; ``optin`` = a stored
  persona mode (the consent record) is required; ``off`` allows. The tests below name the mode they
  exercise; only the ``test_the_default_*`` ones rely on the env being unset.

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


# ── affect gate ──────────────────────────────────────────────────────────────

def _persona(monkeypatch, *, mode="companion", minor=False, fail=False):
    import persona_layer

    async def load(uid, db=None):
        if fail:
            raise RuntimeError("db down")
        return persona_layer.MemberMode(mode=mode, minor=minor)

    monkeypatch.setattr(persona_layer, "load_member_mode", load)


def _gate(monkeypatch, mode):
    monkeypatch.setenv("ZOE_AFFECT_CONSENT_GATE", mode)


def emo(svc, user=UID, **kw):
    return asyncio.run(svc.ingest("User was proud of the 5k finish.", user_id=user, source="digest",
                                  memory_type="emotional_moment", status="approved", **kw))


GUESTS = ["guest", "voice-guest", "anonymous", "voice-daemon"]


def test_the_default_is_household_and_keeps_a_minors_emotional_moment(svc, monkeypatch):
    """Owner decision 2026-10-05: env unset = household. A child's emotional moment is kept."""
    assert ma.affect_gate_mode() == "household"
    _persona(monkeypatch, mode="kid", minor=True)
    ref = emo(svc)
    assert ref is not None and ref.metadata["memory_type"] == "emotional_moment"


def test_the_default_needs_no_consent_row(svc, monkeypatch):
    """No member_modes row (UNSET) is still a member under the default - no stored consent needed."""
    assert ma.affect_gate_mode() == "household"
    _persona(monkeypatch, mode="unset")
    assert emo(svc) is not None


@pytest.mark.parametrize("guest", GUESTS)
@pytest.mark.parametrize("mode", [None, "household", "members", "optin"])
def test_guests_never_get_an_affective_record_in_any_mode(svc, monkeypatch, guest, mode, caplog):
    """The default (None = env unset) and every explicit mode: a guest sentinel is refused."""
    caplog.set_level(logging.INFO, logger=memory_service.logger.name)
    if mode:
        _gate(monkeypatch, mode)
    _persona(monkeypatch)
    assert emo(svc, user=guest) is None and not svc._col.rows
    assert "AFFECT_NOT_STORED" in caplog.text


def test_household_refuses_when_the_member_lookup_fails(svc, monkeypatch):
    """Unknown / failed lookup is closed under the default (and explicit household)."""
    _persona(monkeypatch, fail=True)
    assert emo(svc, user="member-c") is None and not svc._col.rows
    _gate(monkeypatch, "household")
    assert emo(svc, user="member-c") is None


def test_household_keeps_adults_and_children_alike(svc, monkeypatch):
    _gate(monkeypatch, "household")
    for persona in ({"mode": "companion"}, {"mode": "unset"}, {"mode": "kid", "minor": True},
                    {"mode": "helper", "minor": True}):
        _persona(monkeypatch, **persona)
        assert emo(svc, user=f"member-{persona['mode']}") is not None, persona


def test_members_mode_is_adults_only_without_a_consent_row_and_fails_open(svc, monkeypatch):
    _gate(monkeypatch, "members")
    _persona(monkeypatch, mode="unset")
    assert emo(svc) is not None                               # no consent row needed
    _persona(monkeypatch, mode="kid", minor=True)
    assert emo(svc, user="member-b") is None                  # minors refused in this stricter mode
    _persona(monkeypatch, fail=True)
    assert emo(svc, user="member-c") is not None              # a DB blip is not a ban here


def test_optin_mode_requires_a_stored_consent_row_and_fails_closed(svc, monkeypatch):
    _gate(monkeypatch, "optin")
    _persona(monkeypatch, mode="unset")
    assert emo(svc) is None                                   # no consent row: not kept
    _persona(monkeypatch, mode="companion")
    assert emo(svc) is not None                               # the stored mode is the consent
    _persona(monkeypatch, mode="kid", minor=True)
    assert emo(svc, user="member-b") is None                  # minors refused
    _persona(monkeypatch, fail=True)
    assert emo(svc, user="member-c") is None                  # lookup failed: closed


def test_an_unrecognised_value_falls_to_the_strictest_gate_not_the_default(monkeypatch):
    for typo in ("hosuehold", "adults", "strict", "1", "true"):
        monkeypatch.setenv("ZOE_AFFECT_CONSENT_GATE", typo)
        assert ma.affect_gate_mode() == "optin", typo
    for raw, want in (("household", "household"), (" Members ", "members"), ("optin", "optin"),
                      ("off", "off"), ("0", "off")):
        monkeypatch.setenv("ZOE_AFFECT_CONSENT_GATE", raw)
        assert ma.affect_gate_mode() == want, raw
    monkeypatch.delenv("ZOE_AFFECT_CONSENT_GATE")
    assert ma.affect_gate_mode() == "household"


def test_off_is_the_break_the_fix_control(svc, monkeypatch):
    _gate(monkeypatch, "off")
    _persona(monkeypatch)
    assert emo(svc, user="guest") is not None


def test_a_fact_with_a_feeling_keeps_it_for_a_minor_by_default_and_strips_it_in_members(svc, monkeypatch):
    _persona(monkeypatch, mode="kid", minor=True)
    kept = put(svc, "User has a swimming carnival on Friday.", source="turn_digest",
               metadata={"affect": "anxious"})
    assert kept.metadata.get("candidate_affect") == "anxious"          # household default
    _gate(monkeypatch, "members")
    stripped = put(svc, "User has a spelling test on Monday.", source="turn_digest",
                   metadata={"affect": "anxious"})
    assert stripped is not None and not any(k.endswith("affect") for k in stripped.metadata)
    _persona(monkeypatch)
    ok = put(svc, "User has a maths test on Monday.", source="turn_digest", metadata={"affect": "anxious"})
    assert ok.metadata.get("candidate_affect") == "anxious"


def test_a_guests_fact_never_carries_a_feeling_in_any_mode(svc, monkeypatch):
    _persona(monkeypatch)
    for mode in ("household", "members", "optin"):
        _gate(monkeypatch, mode)
        ref = put(svc, f"Visitor asked about parking ({mode}).", source="turn_digest", user="voice-guest",
                  metadata={"affect": "anxious"})
        assert ref is None or not any(k.endswith("affect") for k in ref.metadata), mode


def test_an_edit_keeps_a_minors_affective_row_by_default_but_members_refuses(svc, monkeypatch):
    _persona(monkeypatch)
    row = emo(svc)
    _persona(monkeypatch, mode="kid", minor=True)
    assert asyncio.run(svc.review(row.id, decision="edit", edits="User was proud of the 10k finish.",
                                  actor="review_ui")) is not None   # household default keeps it
    _gate(monkeypatch, "members")
    _persona(monkeypatch)
    row2 = emo(svc, user="member-d")
    _persona(monkeypatch, mode="kid", minor=True)
    assert asyncio.run(svc.review(row2.id, decision="edit", edits="User was proud of the 10k finish.",
                                  actor="review_ui")) is None


# ── review(edit) with a feeling in the metadata (Codex #1868 r4) ─────────────

def _edit_with_affect(svc, row):
    return asyncio.run(svc.review(row.id, decision="edit", edits="User has a dentist visit on Monday.",
                                  actor="turn_digest", metadata={"affect": "anxious", "valence": "-0.4"}))


def test_an_edit_attaches_a_feeling_for_a_minor_and_an_unconsented_member_by_default(svc, monkeypatch):
    for persona in ({"mode": "kid", "minor": True}, {"mode": "unset"}):
        _persona(monkeypatch)
        row = put(svc, f"User has a dentist visit on Friday ({persona['mode']}).", source="turn_digest")
        _persona(monkeypatch, **persona)
        new = _edit_with_affect(svc, row)
        assert new is not None and new.metadata.get("candidate_affect") == "anxious", persona


@pytest.mark.parametrize("gate,persona", [
    ("members", {"mode": "kid", "minor": True}),
    ("optin", {"mode": "kid", "minor": True}),
    ("optin", {"mode": "unset"}),
    ("optin", {"fail": True}),
    ("household", {"fail": True}),
])
def test_an_edit_cannot_attach_a_feeling_where_the_gate_refuses(svc, monkeypatch, gate, persona):
    """The fact is edited, the feeling is stripped - not persisted as candidate_affect."""
    _gate(monkeypatch, gate)
    _persona(monkeypatch)
    row = put(svc, "User has a dentist visit on Friday.", source="turn_digest")
    _persona(monkeypatch, **persona)
    new = _edit_with_affect(svc, row)
    assert new is not None and new.text == "User has a dentist visit on Monday."
    assert not any(k.endswith(("affect", "valence", "intensity")) for k in new.metadata), new.metadata


def test_an_edit_does_not_carry_forward_a_feeling_the_gate_no_longer_allows(svc, monkeypatch):
    _gate(monkeypatch, "optin")
    _persona(monkeypatch)
    row = put(svc, "User has a dentist visit on Friday.", source="turn_digest", metadata={"affect": "anxious"})
    assert row.metadata.get("candidate_affect") == "anxious"
    _persona(monkeypatch, mode="unset")                        # consent withdrawn (optin mode)
    new = asyncio.run(svc.review(row.id, decision="edit", edits="User has a dentist visit on Monday.",
                                 actor="turn_digest"))
    assert new is not None and "candidate_affect" not in new.metadata


def test_a_member_keeps_the_feeling_on_an_edit(svc, monkeypatch):
    _persona(monkeypatch)
    row = put(svc, "User has a dentist visit on Friday.", source="turn_digest")
    new = _edit_with_affect(svc, row)
    assert new.metadata.get("candidate_affect") == "anxious"
