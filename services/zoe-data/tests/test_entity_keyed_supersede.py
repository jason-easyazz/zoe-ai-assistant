"""Entity-keyed supersession for compact person rows (QA follow-up 2026-07-13).

Compact "Name: value" rows written by person_extractor._ingest_to_mempalace
have no parseable attribute for memory_quality's text reconciliation, so a
corrected value ("Delia: March 27" after "Delia: March 15") stacked next to
the stale row forever (live repro, demo-tg-supersede users). Supersession for
these rows is decided by entity linkage + fact kind (pattern_type) instead.

Fakes only — no DB, no model, no live service.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import person_extractor  # noqa: E402
from memory_service import MemoryRef  # noqa: E402

pytestmark = pytest.mark.ci_safe

USER = "u1"
PID = "person-uuid-delia"
SLUG = "slug:delia"


class _FakeSvc:
    def __init__(self, entity_rows=None):
        self._entity_rows = entity_rows or []
        self.ingested: list[tuple[str, dict]] = []
        self.reviewed: list[tuple[str, dict]] = []
        self.relinked: list[tuple] = []
        self.list_calls: list[tuple] = []

    async def list_by_entity(self, user_id, entity_ids, *, status="approved"):
        self.list_calls.append((user_id, tuple(entity_ids), status))
        return list(self._entity_rows)

    async def review(self, mem_id, **kw):
        self.reviewed.append((mem_id, kw))
        if kw.get("decision") == "edit":
            return MemoryRef(id="new-" + mem_id, text=kw.get("edits") or "")
        return None

    async def relink_entity(self, user_id, mem_id, entity_type, entity_id):
        self.relinked.append((user_id, mem_id, entity_type, entity_id))

    async def ingest(self, text, **kw):
        self.ingested.append((text, kw))
        return MemoryRef(id="ingested", text=text)

    async def search(self, *a, **k):
        return []

    async def get(self, mem_id):
        return None


def _row(mem_id, text, pattern_type=None, status="approved"):
    meta = {"status": status, "entity_id": PID}
    if pattern_type:
        meta["pattern_type"] = pattern_type
    return MemoryRef(id=mem_id, text=text, metadata=meta)


async def _ingest(svc, text, pattern_type, monkeypatch, entity_id=PID):
    monkeypatch.setattr(
        person_extractor, "get_memory_service", lambda: svc, raising=False
    )
    import memory_service as ms
    monkeypatch.setattr(ms, "get_memory_service", lambda: svc)
    return await person_extractor._ingest_to_mempalace(
        text, USER, "Delia", entity_id, pattern_type=pattern_type
    )


@pytest.mark.asyncio
async def test_same_kind_different_value_supersedes(monkeypatch):
    svc = _FakeSvc([_row("old", "Delia: March 15", pattern_type="birthday")])
    out = await _ingest(svc, "Delia: March 27", "birthday", monkeypatch)
    assert out == "new-old"
    assert svc.reviewed and svc.reviewed[0][0] == "old"
    assert svc.reviewed[0][1]["decision"] == "edit"
    assert svc.reviewed[0][1]["edits"] == "Delia: March 27"
    assert svc.ingested == [], "supersede must not ALSO plain-ingest"
    # resolved person id is re-linked onto the superseding row
    assert svc.relinked == [(USER, "new-old", "person", PID)]


@pytest.mark.asyncio
async def test_legacy_birthday_row_without_pattern_type_supersedes(monkeypatch):
    """Rows written before pattern_type existed still supersede for birthdays
    when they are the compact date shape."""
    svc = _FakeSvc([_row("legacy", "Delia: March 15")])
    out = await _ingest(svc, "Delia: March 27", "birthday", monkeypatch)
    assert out == "new-legacy"
    assert svc.ingested == []


@pytest.mark.asyncio
async def test_same_value_skips(monkeypatch):
    svc = _FakeSvc([_row("old", "Delia: March 15", pattern_type="birthday")])
    out = await _ingest(svc, "Delia: March 15", "birthday", monkeypatch)
    assert out == "old"
    assert svc.reviewed == [] and svc.ingested == []


@pytest.mark.asyncio
async def test_different_kind_untouched(monkeypatch):
    """A meeting fact must never supersede the birthday row."""
    svc = _FakeSvc([_row("bday", "Delia: March 15", pattern_type="birthday")])
    out = await _ingest(svc, "Delia: met at the gym", "meeting", monkeypatch)
    # falls through to a plain, correctly-linked ADD
    assert svc.reviewed == []
    assert svc.ingested and svc.ingested[0][0] == "Delia: met at the gym"
    assert out == "ingested"


@pytest.mark.asyncio
async def test_legacy_non_date_row_not_matched_for_other_kinds(monkeypatch):
    """The legacy (no pattern_type) fallback applies ONLY to birthday +
    date-shaped values; anything looser risks merging distinct fact kinds."""
    svc = _FakeSvc([_row("legacy", "Delia: works at the hospital")])
    await _ingest(svc, "Delia: works at the clinic", "work", monkeypatch)
    assert svc.reviewed == []
    assert svc.ingested  # plain add


@pytest.mark.asyncio
async def test_extra_stale_duplicates_archived(monkeypatch):
    svc = _FakeSvc([
        _row("a", "Delia: March 15", pattern_type="birthday"),
        _row("b", "Delia: March 12", pattern_type="birthday"),
    ])
    out = await _ingest(svc, "Delia: March 27", "birthday", monkeypatch)
    assert out == "new-a"
    decisions = {(mid, kw.get("decision")) for mid, kw in svc.reviewed}
    assert ("a", "edit") in decisions
    assert ("b", "archive") in decisions


@pytest.mark.asyncio
async def test_entity_lookup_failure_falls_through_to_add(monkeypatch):
    class _Broken(_FakeSvc):
        async def list_by_entity(self, *a, **k):
            raise RuntimeError("store down")

    svc = _Broken()
    out = await _ingest(svc, "Delia: March 27", "birthday", monkeypatch)
    assert svc.ingested and out == "ingested", "a fact must never be lost"


@pytest.mark.asyncio
async def test_pattern_type_stored_on_new_rows(monkeypatch):
    svc = _FakeSvc([])
    await _ingest(svc, "Delia: March 27", "birthday", monkeypatch)
    _, kw = svc.ingested[0]
    assert (kw.get("metadata") or {}).get("pattern_type") == "birthday"


def test_discourse_openers_are_not_names():
    """"actually Delia's birthday…" must not mint a person called
    "Actually Delia" (live repro 2026-07-13: slug:actually_delia)."""
    from person_extractor import _looks_like_person_name

    for junk in ("actually Delia", "wait Jessica", "Okay Karen", "sorry Emily"):
        assert not _looks_like_person_name(junk), junk
    for real in ("Delia", "caitlin farrell", "Mary-Jane Smith"):
        assert _looks_like_person_name(real), real
