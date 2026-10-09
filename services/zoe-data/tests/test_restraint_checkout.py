"""The Jetson-lane half of the restraint tests (UNMARKED: runs in the full-directory lane, not the slim GitHub
lane, because it imports the real MemoryService and the full memory stack). The pure rules, the SQL, the
selector / brief / packet / seam wiring are ``tests/test_restraint.py`` (ci_safe).

What only the full stack can prove: a NEW memory row is stamped with its class by the real
``MemoryService._build_metadata``; the stamp survives the real review-edit key list's rules (an edited text
invalidates the label); a row written in any language is classed by its structure; and the real delete path
erases the member's restraint rows.
"""
from __future__ import annotations

import asyncio

import pytest

import restraint
from memory_service import MemoryRef, MemoryService


def _meta(text, **kw):
    base = dict(user_id="demo-a", source="voice", session_id=None, user_turn_id=None, memory_type="fact",
                confidence=0.9, status="approved", tags=[], entity_type=None, entity_id=None, expires_at=None,
                text=text)
    base.update(kw)
    return MemoryService._build_metadata(**base)


@pytest.fixture(autouse=True)
def _enforce(monkeypatch):
    monkeypatch.setenv("ZOE_RESTRAINT", "enforce")
    restraint._reset_state()
    yield
    restraint._reset_state()


def test_a_new_row_carries_its_class_in_its_metadata():
    md = _meta("I've got the dentist on Friday for a cracked molar")
    assert md["sensitivity"] == "health" and md["sensitivity_v"] == restraint.VERSION
    assert md["sensitivity_h"] == restraint.text_hash("I've got the dentist on Friday for a cracked molar")
    assert _meta("parcel pickup on Friday")["sensitivity"] == ""


def test_the_structured_fields_the_writer_set_decide_the_class():
    person = _meta("Hannah liebt Tennis", memory_type="person", entity_type="person", entity_id="p1")
    assert person["sensitivity"] == "other_member"
    feeling = _meta("Es war ein langer Tag", memory_type="emotional_moment", extra_metadata={"affect": "tired"})
    assert feeling["sensitivity"] == "affect"
    tagged = _meta("Zahnarzt am Freitag", tags=["dental"])
    assert tagged["sensitivity"] == "health"


def test_a_stamped_row_is_read_back_through_row_classes():
    md = _meta("worried about the car loan repayments")
    ref = MemoryRef(id="m1", text="worried about the car loan repayments", metadata=md)
    assert restraint.row_classes(ref.metadata, ref.text) == ("money", "affect")
    assert restraint.row_classes(ref.metadata, "worried about the car loan repayments, and the mortgage") == (
        "money", "affect")  # edited text: the label is invalid and the class is recomputed, not trusted


def test_off_stamps_nothing(monkeypatch):
    monkeypatch.setenv("ZOE_RESTRAINT", "off")
    assert "sensitivity" not in _meta("I've got the dentist on Friday")


def test_the_label_is_a_chroma_safe_scalar():
    md = _meta("my sister's divorce is hard on her")
    assert all(isinstance(v, (str, int, float, bool)) for v in md.values())
    assert isinstance(md["sensitivity"], str) and isinstance(md["sensitivity_v"], int)


def test_delete_user_erases_the_restraint_rows(monkeypatch):
    """The audited right-to-be-forgotten path calls restraint.erase_user (best effort, before the rows go)."""
    called = []

    async def erase(uid):
        called.append(uid)
        return 3

    monkeypatch.setattr(restraint, "erase_user", erase)
    svc = MemoryService.__new__(MemoryService)
    svc._user_locks = {}
    svc._seen_keys_by_user = {}
    import exact_words

    async def noop(uid):
        return 0

    monkeypatch.setattr(exact_words, "delete_user", noop)
    monkeypatch.setattr(MemoryService, "_require", lambda self, v, m: None, raising=False)
    monkeypatch.setattr(MemoryService, "_list_ids_for_user", lambda self, u: [], raising=False)
    monkeypatch.setattr(MemoryService, "_delete_audit_for_user_sync", lambda self, u: 0, raising=False)

    async def run_sync(self, fn, *a):
        return fn(self, *a) if getattr(fn, "__self__", None) is None else fn(*a)

    monkeypatch.setattr(MemoryService, "_run_sync", run_sync, raising=False)
    import memory_service

    monkeypatch.setattr(memory_service, "assert_write_allowed", lambda *a, **k: None)
    monkeypatch.setattr(memory_service, "_invalidate_agent_user_facts_cache", lambda u: None)
    asyncio.run(MemoryService.delete_user(svc, "demo-a", actor="test"))
    assert called == ["demo-a"]


def test_a_forget_cascade_clears_the_mutes_that_name_the_forgotten_entity(monkeypatch):
    import memory_forget_cascade as cascade

    erased = []

    async def erase(uid, name):
        erased.append((uid, name))
        return 1

    monkeypatch.setattr(restraint, "erase_entity", erase)

    async def no_db(*a, **k):
        return None

    class _NoPool:
        async def __aenter__(self):
            raise RuntimeError("no pool")

        async def __aexit__(self, *e):
            return False

    import db_pool

    monkeypatch.setattr(db_pool, "get_db_ctx", lambda: _NoPool())
    asyncio.run(cascade.cascade_forget("demo-a", "Dana"))
    assert erased == [("demo-a", "Dana")]
