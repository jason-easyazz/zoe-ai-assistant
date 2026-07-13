"""QA review F9 — cross-writer supersession.

Every conversational memory writer (person_extractor, memory_digest turn +
nightly digests, expert_dispatch, memory_extractor) must route through the
shared ``memory_quality.reconcile_for_ingest`` ADD/UPDATE/SKIP decision instead
of blind-ADDing near-duplicate or contradicting rows. Proves:

1. a person_extractor update to an existing attribute UPDATEs (supersedes)
   rather than duplicating;
2. a digest re-statement of an already-stored fact SKIPs (writes nothing);
3. reconciliation errors fall back to ADD — a fact is never lost because the
   dedup step errored;
4. the entity guards (namesake / third-person-name protection) survive the
   extraction into the shared helper.
"""
import asyncio

import pytest

memory_quality = pytest.importorskip("memory_quality")
person_extractor = pytest.importorskip("person_extractor")
memory_digest = pytest.importorskip("memory_digest")

pytestmark = pytest.mark.ci_safe  # fakes only — no DB, no model, no live service


def _run(coro):
    return asyncio.run(coro)


class _Row:
    def __init__(self, mem_id, text, metadata=None):
        self.id = mem_id
        self.text = text
        self.metadata = metadata if metadata is not None else {}


class _FakeSvc:
    """Minimal MemoryService stand-in recording ingest/review calls."""

    def __init__(self, search_rows=None, search_exc=None):
        self.ingested: list[tuple[str, dict]] = []
        self.reviewed: list[tuple[str, dict]] = []
        self.relinked: list[tuple[str, str, str]] = []
        self._search_rows = search_rows or []
        self._search_exc = search_exc

    async def ingest(self, text, **kw):
        self.ingested.append((text, kw))
        return _Row(f"new-{len(self.ingested)}", text)

    async def search(self, *a, **k):
        if self._search_exc:
            raise self._search_exc
        return self._search_rows

    async def get(self, mem_id):
        for r in self._search_rows:
            if r.id == mem_id:
                return r
        return None

    async def review(self, mem_id, **kw):
        self.reviewed.append((mem_id, kw))
        return _Row(f"edited-{mem_id}", kw.get("edits", ""))

    async def relink_entity(self, user_id, mem_id, entity_type, entity_id):
        self.relinked.append((mem_id, entity_type, entity_id))
        return True


# ---------------------------------------------------------------------------
# reconcile_for_ingest — the shared seam itself
# ---------------------------------------------------------------------------

def test_reconcile_update_on_same_attribute_new_value():
    svc = _FakeSvc(search_rows=[_Row("old", "Jessica's birthday is March 15")])
    op, target = _run(memory_quality.reconcile_for_ingest(
        svc, "Jessica's birthday is March 25", "jason", title="Jessica"))
    assert (op, target) == ("update", "old")


def test_reconcile_skip_on_sparser_restatement():
    svc = _FakeSvc(search_rows=[_Row("rich", "My dad's name is Neil, spelled N-E-I-L.")])
    op, target = _run(memory_quality.reconcile_for_ingest(
        svc, "User's father's name is Neil.", "jason"))
    assert (op, target) == ("skip", "rich")


def test_reconcile_error_falls_back_to_add():
    svc = _FakeSvc(search_exc=RuntimeError("chroma down"))
    op, target = _run(memory_quality.reconcile_for_ingest(
        svc, "Jessica's birthday is March 25", "jason", title="Jessica"))
    assert (op, target) == ("add", None)


def test_reconcile_entity_guard_protects_namesake():
    # A Jessica correction must never supersede Karen's same-attribute row.
    svc = _FakeSvc(search_rows=[_Row("karen", "Karen's birthday is March 15")])
    op, target = _run(memory_quality.reconcile_for_ingest(
        svc, "Jessica's birthday is March 25", "jason", title="Jessica"))
    assert (op, target) == ("add", None)


def test_reconcile_entity_guard_bare_name_vs_full_name():
    # Bare "Jessica" candidate must not touch a "Jessica Smith" row.
    svc = _FakeSvc(search_rows=[_Row("smith", "Jessica Smith's birthday is March 15")])
    op, target = _run(memory_quality.reconcile_for_ingest(
        svc, "Jessica's birthday is March 25", "jason", title="Jessica"))
    assert (op, target) == ("add", None)


def test_reconcile_titleless_guard_third_person_name():
    # A titleless candidate never mentioning Karen must not supersede her row.
    svc = _FakeSvc(search_rows=[_Row("karen", "Karen's birthday is March 15")])
    op, target = _run(memory_quality.reconcile_for_ingest(
        svc, "The birthday is actually March 25", "jason"))
    assert (op, target) == ("add", None)


# ---------------------------------------------------------------------------
# 1. person_extractor — same-attribute update supersedes, no duplicate
# ---------------------------------------------------------------------------

def test_person_extractor_update_supersedes_not_duplicates(monkeypatch):
    svc = _FakeSvc(search_rows=[_Row("old", "Jessica's birthday is March 15",
                                    {"entity_type": "person_pending", "entity_id": "slug:jessica"})])
    import memory_service
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: svc)
    mem_id = _run(person_extractor._ingest_to_mempalace(
        "Jessica's birthday is March 25", "jason", "Jessica", "slug:jessica"))
    assert svc.ingested == [], "correction must supersede, not blind-ADD a duplicate"
    assert [mid for mid, _ in svc.reviewed] == ["old"]
    assert svc.reviewed[0][1].get("edits") == "Jessica's birthday is March 25"
    assert mem_id == "edited-old"


def test_person_extractor_restatement_skips(monkeypatch):
    svc = _FakeSvc(search_rows=[_Row("rich", "Jessica's birthday is March 15, she loves cake",
                                    {"entity_type": "person_pending", "entity_id": "slug:jessica"})])
    import memory_service
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: svc)
    mem_id = _run(person_extractor._ingest_to_mempalace(
        "Jessica's birthday is March 15", "jason", "Jessica", "slug:jessica"))
    assert svc.ingested == [], "sparser restatement must not be stored"
    assert svc.reviewed == []
    assert mem_id == "rich", "skip returns the kept row so linkage still works"


def test_person_extractor_supersede_promotes_pending_slug(monkeypatch):
    # A resolved call editing a same-name pending-slug row must promote the
    # row's linkage to the real people.id (review(edit) keeps the old link).
    svc = _FakeSvc(search_rows=[_Row("old", "Jessica's birthday is March 15",
                                     {"entity_type": "person_pending", "entity_id": "slug:jessica"})])
    import memory_service
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: svc)
    uuid_id = "3f0a4a1e-0000-4000-8000-000000000001"
    mem_id = _run(person_extractor._ingest_to_mempalace(
        "Jessica's birthday is March 25", "jason", "Jessica", uuid_id))
    assert [mid for mid, _ in svc.reviewed] == ["old"]
    assert svc.relinked == [("edited-old", "person", uuid_id)]
    assert mem_id == "edited-old"


def test_person_extractor_unlinked_row_falls_back_to_add(monkeypatch):
    # Linkage guard: a matched row from another writer that is NOT keyed to this
    # person (raw voice_fact / generic digest row) must not be edited or
    # returned as this person's mem_id — plain, correctly-linked ADD instead.
    svc = _FakeSvc(search_rows=[_Row("raw", "Jessica's birthday is March 15",
                                     {"entity_type": "conversation", "entity_id": ""})])
    import memory_service
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: svc)
    mem_id = _run(person_extractor._ingest_to_mempalace(
        "Jessica's birthday is March 25", "jason", "Jessica", "slug:jessica"))
    assert svc.reviewed == [], "must not edit a row not keyed to this person"
    assert [t for t, _ in svc.ingested] == ["Jessica's birthday is March 25"]
    assert mem_id == "new-1"


def test_person_extractor_reconcile_error_falls_back_to_add(monkeypatch):
    svc = _FakeSvc(search_exc=RuntimeError("store down"))
    import memory_service
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: svc)
    mem_id = _run(person_extractor._ingest_to_mempalace(
        "Jessica's birthday is March 25", "jason", "Jessica", "slug:jessica"))
    assert [t for t, _ in svc.ingested] == ["Jessica's birthday is March 25"], \
        "a reconciliation error must never lose the fact"
    assert mem_id is not None


# ---------------------------------------------------------------------------
# 2. memory_digest.run_turn_digest — re-statement SKIPs
# ---------------------------------------------------------------------------

def _patch_turn_digest(monkeypatch, svc, facts):
    """Fake the LLM (httpx) + memory service + zoe_agent facts blob."""
    import json as _json
    import sys
    import types

    import memory_service
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: svc)

    payload = _json.dumps(facts)

    class _FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": payload}}]}

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            return _FakeResp()

    monkeypatch.setattr(memory_digest.httpx, "AsyncClient", _FakeClient)

    # Stub zoe_agent so the light word-overlap predup sees no existing blob and
    # the cache invalidation is a no-op (keeps the test slim — no zoe_agent import).
    stub = types.ModuleType("zoe_agent")

    async def _fake_load_facts(*a, **k):
        return ""

    stub._mempalace_load_user_facts = _fake_load_facts
    stub._invalidate_user_facts_cache = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "zoe_agent", stub)


def test_turn_digest_restatement_skips(monkeypatch):
    svc = _FakeSvc(search_rows=[_Row("rich", "My dad's name is Neil, spelled N-E-I-L.")])
    _patch_turn_digest(monkeypatch, svc, [{"fact": "User's father's name is Neil.", "type": "fact"}])
    result = _run(memory_digest.run_turn_digest(
        "jason", "my dad's name is Neil", session_id="s1"))
    assert svc.ingested == [], "digest re-statement of a stored fact must SKIP"
    assert svc.reviewed == []
    assert result.get("new", 0) == 0
    assert result.get("skipped_duplicates", 0) >= 1


def test_turn_digest_correction_supersedes(monkeypatch):
    svc = _FakeSvc(search_rows=[_Row("old", "User's birthday is March 15.")])
    _patch_turn_digest(monkeypatch, svc, [{"fact": "User's birthday is March 25.", "type": "fact"}])
    result = _run(memory_digest.run_turn_digest(
        "jason", "actually my birthday is March 25", session_id="s1"))
    assert svc.ingested == [], "value correction must supersede, not duplicate"
    assert [mid for mid, _ in svc.reviewed] == ["old"]
    assert result.get("new", 0) == 1


def test_turn_digest_reconcile_error_falls_back_to_add(monkeypatch):
    svc = _FakeSvc(search_exc=RuntimeError("store down"))
    _patch_turn_digest(monkeypatch, svc, [{"fact": "User's father's name is Neil.", "type": "fact"}])
    result = _run(memory_digest.run_turn_digest(
        "jason", "my dad's name is Neil", session_id="s1"))
    assert [t for t, _ in svc.ingested] == ["User's father's name is Neil."], \
        "a reconciliation error must never lose the fact"
    assert result.get("new", 0) == 1
