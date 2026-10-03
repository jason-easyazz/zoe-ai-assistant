"""The person extractors keep the user's words beside the fact (``source_excerpt``).

Live 2026-09-30 (recall_evidence_probe, demo_bar_4b9a62ce): "Just so you know, my sister
Marisol is flying in from Lisbon on Thursday." produced two rows. The turn digest stored
"User's sister is named Marisol" WITH the utterance; the LLM person extractor stored
"Marisol: flying in from Lisbon on Thursday" (reconcile ``text_len=42`` = that string)
through ``apply_person_fact`` → ``_ingest_to_mempalace`` WITHOUT it, under source
``conversation`` — which was not a quotable writer either. So the Lisbon bullet could be
dated but never quoted. Both halves are pinned here; MemoryService scrubs and caps the
excerpt at its write boundary (#1782, test_memory_service_metadata).

Fakes only. Negative controls: drop the forward in any of the three writers (LLM pass,
regex pass, the edit paths) or drop "conversation"/"voice" from QUOTABLE_WRITERS — the
matching test goes red.
"""
from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.ci_safe  # fakes only: no DB, no model

import memory_service as ms
import person_extractor
import person_extractor_llm as pel
import recall_evidence as rev
from memory_service import MemoryRef

SAID = "Just so you know, my sister Marisol is flying in from Lisbon on Thursday."
FACT = "Marisol: flying in from Lisbon on Thursday"


class _Resp:
    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        pass

    def json(self):
        return {"choices": [{"message": {"content": json.dumps(self._p)}}]}


class _Client:
    def __init__(self, payload):
        self._p = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, *a, **k):
        return _Resp(self._p)


async def test_llm_pass_forwards_the_utterance(monkeypatch):
    items = [{"name": "Marisol", "fact_type": "plan", "value": "flying in from Lisbon on Thursday"}]
    monkeypatch.setattr(pel.httpx, "AsyncClient", lambda **k: _Client(items))
    seen = []

    async def fake_apply(name, fact_type, value, **kw):
        seen.append(kw.get("source_excerpt"))
        return True

    monkeypatch.setattr(person_extractor, "apply_person_fact", fake_apply)
    assert await pel.process_text_llm(f"  {SAID}\n", user_id="u1", source="conversation") == 1
    assert seen == [SAID]  # whitespace-collapsed, as the turn digest stores it


async def test_regex_pass_forwards_the_utterance(monkeypatch):
    seen = []

    async def fake_ingest(text, user_id, person_name, entity_id, **kw):
        seen.append((text, kw.get("source_excerpt")))
        return "mem-1"

    async def no_db(db):
        return object(), False

    async def no_person(*a):
        return None

    monkeypatch.setattr(person_extractor, "_ensure_db", no_db)
    monkeypatch.setattr(person_extractor, "_resolve_person_uuid", no_person)
    monkeypatch.setattr(person_extractor, "_ingest_to_mempalace", fake_ingest)
    said = "Marisol works at   the aquarium in Hobart."
    assert await person_extractor.process_text(said, user_id="u1", source="voice") == 1
    assert seen and seen[0][1] == "Marisol works at the aquarium in Hobart."


class _Svc:
    def __init__(self, entity_rows=(), match=None):
        self.rows, self.match = list(entity_rows), match
        self.ingested, self.reviewed = [], []

    async def list_by_entity(self, user_id, entity_ids, *, status="approved"):
        return list(self.rows)

    async def review(self, mem_id, **kw):
        self.reviewed.append(kw)
        return MemoryRef(id="new-" + mem_id, text=kw.get("edits") or "")

    async def relink_entity(self, *a):
        pass

    async def ingest(self, text, **kw):
        self.ingested.append(kw)
        return MemoryRef(id="ingested", text=text)

    async def get(self, mem_id):
        return self.match


async def _write(svc, monkeypatch, pattern_type="plan"):
    monkeypatch.setattr(ms, "get_memory_service", lambda: svc)
    return await person_extractor._ingest_to_mempalace(
        FACT, "u1", "Marisol", "pid-1", source="conversation", pattern_type=pattern_type,
        source_excerpt=SAID)


async def test_plain_ingest_carries_the_excerpt(monkeypatch):
    import memory_quality

    async def add(*a, **k):
        return "add", None

    monkeypatch.setattr(memory_quality, "reconcile_for_ingest", add)
    svc = _Svc()
    assert await _write(svc, monkeypatch) == "ingested"
    assert svc.ingested[0]["source_excerpt"] == SAID


async def test_edits_carry_the_new_evidence_not_the_old(monkeypatch):
    """review(edit) carries the OLD row's excerpt forward unless given one."""
    old = MemoryRef(id="old", text="Marisol: flying in on Friday",
                    metadata={"entity_id": "pid-1", "pattern_type": "plan"})
    svc = _Svc([old])
    assert await _write(svc, monkeypatch) == "new-old"  # entity-keyed supersede
    assert svc.reviewed[0]["source_excerpt"] == SAID

    import memory_quality

    async def update(*a, **k):
        return "update", "old"

    monkeypatch.setattr(memory_quality, "reconcile_for_ingest", update)
    svc = _Svc(match=MemoryRef(id="old", text="x", metadata={
        "entity_type": "person", "entity_id": "pid-1"}))
    assert await _write(svc, monkeypatch, pattern_type=None) == "new-old"  # text reconcile
    assert svc.reviewed[0]["source_excerpt"] == SAID


@pytest.mark.parametrize("source, quoted", [
    ("conversation", True), ("voice", True),   # chat / voice person passes: the utterance
    ("notes", False), ("journal", False),      # a note body is not something they SAID
])
def test_person_rows_are_quotable_on_the_utterance_lanes(source, quoted):
    meta = {"source": source, "source_excerpt": SAID}
    assert (rev.quote_for(meta, FACT) == SAID) is quoted
