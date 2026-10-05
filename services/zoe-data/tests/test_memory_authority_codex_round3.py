"""Codex round on #1868 (head 446c162d): three threads, one red-before-green test group each.

  1  P1  a model's compact entity-keyed fact ("Alice: 15 March" after the user's "Alice: 16 March")
         was refused by the entity reconciliation edit, then FELL THROUGH to ordinary ingest -
         ``conflict_kind`` could not read compact rows, so ingest returned an approved row and
         ``apply_person_fact`` wrote the structured birthday tables anyway. Now: a refusal is final
         (no fall-through, no structured write) and ``conflict_kind`` reads the compact shape.
  2  P1  the short-answer support check validated the attribute cue but not the SUBJECT of the
         question: "Where does your sister live?" -> "Perth" stamped "User lives in Perth." as
         ``user_stated_derived``. Now the question's subject must be the fact's subject.
  3  P2  conflict detection truncated the newest-first protected-row list at 2,000: an older
         user-stated row outside the prefix was invisible to the wall. Now every row is compared.

Synthetic people only; fake Chroma + in-memory SQLite; no network, no model (``ci_safe``).
"""
from __future__ import annotations

import asyncio
import logging
import types

import pytest

import memory_authority as ma
import memory_service
import person_extractor
from memory_service import MemoryService
from test_memory_authority import _Col
from test_memory_authority_people import _db_with_0037
from test_temporal_relationships import _seed_people

pytestmark = pytest.mark.ci_safe

USER = "jason"


def _match(meta: dict, where) -> bool:
    """``test_memory_authority._match`` plus ``{"$in": [...]}`` (the entity-keyed lookup)."""
    for key, want in (where or {}).items():
        if key == "$and":
            if not all(_match(meta, w) for w in want):
                return False
        elif key == "$or":
            if not any(_match(meta, w) for w in want):
                return False
        elif isinstance(want, dict) and "$in" in want:
            if meta.get(key) not in want["$in"]:
                return False
        elif meta.get(key) != want:
            return False
    return True


class _InCol(_Col):
    def get(self, *, ids=None, where=None, include=None, **_kw):
        keys = [i for i in ids if i in self.rows] if ids is not None else list(self.rows)
        keys = [k for k in keys if _match(self.rows[k][1], where)]
        return {"ids": keys, "documents": [self.rows[i][0] for i in keys],
                "metadatas": [dict(self.rows[i][1]) for i in keys]}


@pytest.fixture
def svc(monkeypatch):
    monkeypatch.delenv("ZOE_MEMORY_AUTHORITY", raising=False)
    s = MemoryService(data_dir="/nonexistent/zoe-test-memory-authority-round3")
    col = _InCol()
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


# ── 1. compact person facts cannot bypass the wall ───────────────────────────

@pytest.mark.parametrize("new,old,kind", [
    ("Alice: 15 March", "Alice: 16 March", "birthday"),
    ("Alice: March 15", "Alice: 16th of March 1990", "birthday"),
    ("Alice Smith: 15 Mar", "Alice Smith: 16 March", "birthday"),
])
def test_conflict_kind_reads_the_compact_name_value_shape(new, old, kind):
    assert ma.conflict_kind(new, old) == kind


@pytest.mark.parametrize("new,old", [
    ("Alice: 15 March", "Alice: March 15"),            # the same day, written differently
    ("Alice: 15 March", "Alice: 15th of March 1990"),  # the same day, with a year
    ("Bob: 15 March", "Alice: 16 March"),              # another person
    ("Alice: met at the gym", "Alice: 16 March"),      # not a date row: a different fact kind
    ("Alice: met on 15 March at the gym", "Alice: 16 March"),
])
def test_conflict_kind_leaves_compact_rows_that_do_not_disagree(new, old):
    assert ma.conflict_kind(new, old) is None


async def _birthday_world(svc):
    db = await _db_with_0037()
    await db.execute("CREATE TABLE person_activities (id TEXT, person_id TEXT, user_id TEXT, "
                     "activity_type TEXT, description TEXT, source TEXT, venue TEXT, "
                     "session_id TEXT, mem_id TEXT)")
    await db.execute("ALTER TABLE person_important_dates ADD COLUMN mem_id TEXT")
    await _seed_people(db)
    # the user said it (a compact, entity-keyed, birthday-typed row)
    await svc.ingest("Alice: 16 March", user_id=USER, source="voice_fact", entity_type="person",
                     entity_id="pa", status="approved", metadata={"pattern_type": "birthday"})
    return db


async def _counts(db):
    out = []
    for table in ("person_activities", "person_important_dates"):
        async with db.execute(f"SELECT count(*) FROM {table}") as cur:
            out.append((await cur.fetchone())[0])
    return out


def test_a_models_compact_birthday_is_held_back_not_applied_to_the_people_tables(svc, caplog):
    caplog.set_level(logging.INFO, logger=ma.logger.name)

    async def run():
        db = await _birthday_world(svc)
        try:
            ok = await person_extractor.apply_person_fact(
                "Alice", "birthday", "15 March", user_id=USER, source="synthesis", db=db)
            return ok, await _counts(db)
        finally:
            await db.close()

    ok, counts = asyncio.run(run())
    assert ok is False and counts == [0, 0]
    approved = asyncio.run(svc.list_by_status(user_id=USER, status="approved"))
    assert [r.text for r in approved] == ["Alice: 16 March"]  # the user's row survives, unedited
    disputed = asyncio.run(svc.list_by_status(user_id=USER, status="disputed"))
    assert [r.text for r in disputed] == ["Alice: 15 March"]  # ...and the proposal is a candidate
    assert disputed[0].metadata["authority_blocked"] and disputed[0].metadata["contradicts_id"]
    assert "AUTHORITY_BLOCKED writer=synthesis kind=birthday" in caplog.text


def test_the_refusal_does_not_fall_through_to_ordinary_ingest(svc, monkeypatch):
    """Reconciliation's refusal is final: ``svc.ingest`` is not called a second time."""
    seen: list[str] = []
    real = svc.ingest

    async def spy(text, **kw):
        seen.append(text)
        return await real(text, **kw)

    async def run():
        db = await _birthday_world(svc)
        await db.close()
        svc.ingest = spy
        return await person_extractor._ingest_to_mempalace(
            "Alice: 15 March", USER, "Alice", "pa", memory_type="person", source="synthesis",
            pattern_type="birthday")

    assert asyncio.run(run()) == person_extractor.AUTHORITY_BLOCKED
    assert seen == []


def test_the_walls_own_ingest_also_sees_a_compact_row(svc):
    """conflict_kind alone closes the second route: a model's compact row ingested directly is
    a disputed candidate (no reconciliation in front of it)."""
    asyncio.run(svc.ingest("Alice: 16 March", user_id=USER, source="voice_fact", entity_type="person",
                           entity_id="pa", status="approved"))
    ref = asyncio.run(svc.ingest("Alice: 15 March", user_id=USER, source="synthesis", entity_type="person",
                                 entity_id="pa", status="approved"))
    assert ref.metadata["authority_blocked"] and ref.metadata["status"] == "disputed"


def test_break_the_fix_control_without_the_wall_the_compact_fact_is_applied(svc, monkeypatch):
    monkeypatch.setenv("ZOE_MEMORY_AUTHORITY", "0")

    async def run():
        db = await _birthday_world(svc)
        try:
            ok = await person_extractor.apply_person_fact(
                "Alice", "birthday", "15 March", user_id=USER, source="synthesis", db=db)
            return ok, await _counts(db)
        finally:
            await db.close()

    ok, counts = asyncio.run(run())
    assert ok is True and counts[1] == 1


def test_a_plain_review_failure_still_falls_through(svc):
    """Only a REFUSAL is final: an ordinary None (no predicate / nothing outranks) falls through."""
    calls: list[str] = []

    class Fake:
        async def list_by_entity(self, *_a, **_k):
            return [types.SimpleNamespace(id="old", text="Alice: 16 March",
                                          metadata={"status": "approved", "pattern_type": "birthday"})]

        async def review(self, *_a, **_k):
            return None

        async def get(self, _id):
            return None

        async def search(self, *_a, **_k):
            return []

        async def ingest(self, text, **_kw):
            calls.append(text)
            return types.SimpleNamespace(id="fresh", metadata={})

    async def run():
        return await person_extractor._ingest_to_mempalace(
            "Alice: 15 March", USER, "Alice", "pa", source="synthesis", pattern_type="birthday")

    import memory_service as ms
    orig = ms.get_memory_service
    ms.get_memory_service = lambda: Fake()
    try:
        assert asyncio.run(run()) == "fresh"
    finally:
        ms.get_memory_service = orig
    assert calls == ["Alice: 15 March"]


# ── 2. the question's subject must be the fact's subject ─────────────────────

@pytest.mark.parametrize("fact,answer,question", [
    ("User lives in Perth.", "Perth", "Where does your sister live?"),
    ("User lives in Perth.", "Perth", "Where does Alice live?"),
    ("User is 12 years old.", "12", "How old is your son?"),
    ("User's name is Casey.", "Casey", "What is your brother's name?"),
    ("User's sister lives in Perth.", "Perth", "Where do you live?"),
    ("Alice works at Acme.", "Acme", "Where does Bob work?"),
    ("User's brother is 12 years old.", "12", "How old is your son?"),
])
def test_a_short_answer_about_someone_else_does_not_support_a_fact_about_another_subject(
        fact, answer, question):
    assert not ma.supports(fact, answer, question)


@pytest.mark.parametrize("fact,answer,question", [
    ("User lives in Perth.", "Perth", "Where do you live?"),
    ("User lives in Perth.", "no, Perth now", "Where do you live now?"),
    ("User is 41 years old.", "41", "How old are you?"),
    ("User's name is Alex.", "it's Alex", "What's your name?"),
    ("User's sister lives in Perth.", "Perth", "Where does your sister live?"),
    ("User's son is 12 years old.", "12", "How old is your son?"),
    ("Alice works at Acme.", "Acme", "Where does Alice work?"),
])
def test_a_short_answer_about_the_right_subject_still_supports(fact, answer, question):
    assert ma.supports(fact, answer, question)


def test_a_misattributed_answer_is_not_stamped_user_stated_derived():
    res = ma.resolve_write("turn_digest", "User lives in Perth.", anchor_text="Perth",
                           prompt_text="Where does your sister live?")
    assert res.cls != ma.USER_STATED_DERIVED
    ok = ma.resolve_write("turn_digest", "User lives in Perth.", anchor_text="Perth",
                          prompt_text="Where do you live?")
    assert ok.cls == ma.USER_STATED_DERIVED


# ── 3. no recency prefix in conflict detection ───────────────────────────────

def _row(i, text, cls, status="approved"):
    return types.SimpleNamespace(
        id=f"r{i}", text=text,
        metadata={"status": status, "authority_class": cls, "authority": ma.authority_of(cls)})


def test_find_conflict_sees_a_protected_row_beyond_the_old_2000_prefix():
    rows = [_row(i, f"User collects item number {i}.", ma.MODEL_FROM_TURN) for i in range(2500)]
    rows.append(_row(9999, "User lives in Sydney.", ma.USER_STATED))  # the OLDEST: last, newest-first
    hit = ma.find_conflict("User lives in Perth.", rows, ma.RANK[ma.MODEL_FROM_TURN])
    assert hit is not None and hit[0].id == "r9999" and hit[1] == "home"


def test_a_user_stated_row_ranked_beyond_the_prefix_still_blocks_an_inferred_contradiction(svc):
    asyncio.run(svc.ingest("User lives in Sydney.", user_id=USER, source="voice_fact", status="approved"))
    template = next(iter(svc._col.rows.values()))[1]
    for i in range(2100):  # newer, inferred, unrelated: they push the user's row past 2,000
        meta = dict(template, added_at=f"2999-01-01T{i // 3600:02d}:{i // 60 % 60:02d}:{i % 60:02d}Z",
                    source="digest", authority="inferred", authority_class=ma.MODEL_FROM_TURN)
        svc._col.rows[f"filler{i}"] = (f"User collects item number {i}.", meta)
    ref = asyncio.run(svc.ingest("User lives in Perth.", user_id=USER, source="turn_digest",
                                 status="approved"))
    assert ref.metadata["status"] == "disputed" and ref.metadata["authority_blocked"]
    stored = {text: meta["status"] for text, meta in svc._col.rows.values()}
    assert stored["User lives in Sydney."] == "approved"  # the user's row was not touched
    assert stored["User lives in Perth."] == "disputed"   # the proposal is a candidate, not a fact
