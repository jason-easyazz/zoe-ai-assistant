"""Digest dedup + weekly merge keep the RICHER fact (brain-extraction research L3 / L4).

The turn and nightly digests skipped a fact as a "duplicate" when more than 70 percent of its words were
SUBSTRINGS of the stored-facts blob. Against a stored "User's friend Dana has two kids":

    "Dana has two kids Mika and Biscuit"                scored 0.71   -> dropped
    "User's friend Dana has two kids Mika and Biscuit"  scored 0.78   -> dropped

and the new names (Mika, Biscuit) were lost. The rule now: word-boundary tokens, compared per stored
fact; a fact holding a NEW name / number / date is never a duplicate; one that strictly extends a stored
fact supersedes it (history kept). The weekly merge picks the survivor by richness (distinct entity /
number / date tokens), then authority class, then newest.

These tests drive the public entry points (run_turn_digest, run_memory_digest, _merge_near_duplicates)
with fakes only. Synthetic names; no model, DB or live store.
"""
from __future__ import annotations

import asyncio
import json
import sys
import types

import pytest

import memory_digest
import memory_overlap
import memory_reject_ledger as led

pytestmark = pytest.mark.ci_safe

STORED = "User's friend Dana has two kids."
BLOB = (f"## What I know about you:\n- {STORED}\n- User likes quiet mornings.\n"
        "- User's grandma lives in Perth.")   # "and" is a SUBSTRING of grandma: the old rule counted it shared
SHORT = "Dana has two kids Mika and Biscuit"                # scored 0.71 under the substring rule
LONG = "User's friend Dana has two kids Mika and Biscuit"   # scored 0.78
# the user's own words must carry the user-anchored roles, or the (separate) anchor guard drops the fact
TURN = "My friend Dana has two kids, Mika and Biscuit, and my kids play with them every weekend."


class _Row:
    def __init__(self, mem_id, text, metadata=None):
        self.id = mem_id
        self.text = text
        self.metadata = metadata if metadata is not None else {}


class _Svc:
    """MemoryService stand-in: records ingest / review; search returns the stored rows."""

    def __init__(self, rows=None):
        self.rows = rows or []
        self.ingested: list[tuple[str, dict]] = []
        self.reviewed: list[tuple[str, dict]] = []

    async def ingest(self, text, **kw):
        self.ingested.append((text, kw))
        return _Row(f"new-{len(self.ingested)}", text, {"status": "approved"})

    async def search(self, *a, **k):
        return self.rows

    async def get(self, mem_id):
        return next((r for r in self.rows if r.id == mem_id), None)

    async def review(self, mem_id, **kw):
        self.reviewed.append((mem_id, kw))
        return _Row(f"edited-{mem_id}", kw.get("edits", ""), {"status": "approved"})

    async def list_by_status(self, **kw):
        return self.rows


@pytest.fixture(autouse=True)
def ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("ZOE_MEMORY_REJECT_LEDGER", str(tmp_path / "ledger.json"))
    led.reset_for_tests()
    yield led
    led.reset_for_tests()


def _stub_world(monkeypatch, svc, facts, blob):
    """Fake the extractor LLM, the memory service and the stored-facts blob."""
    import memory_service

    monkeypatch.setattr(memory_service, "get_memory_service", lambda: svc)
    payload = json.dumps(facts)

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": payload}}]}

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            return _Resp()

    monkeypatch.setattr(memory_digest.httpx, "AsyncClient", _Client)
    stub = types.ModuleType("zoe_agent")

    async def _blob(*a, **k):
        return blob

    stub._mempalace_load_user_facts = _blob
    stub._invalidate_user_facts_cache = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "zoe_agent", stub)


def _turn(monkeypatch, svc, fact, blob=BLOB):
    _stub_world(monkeypatch, svc, [{"fact": fact, "type": "fact"}], blob)
    return asyncio.run(memory_digest.run_turn_digest("demo-user", TURN, session_id="s1"))


def _nightly(monkeypatch, svc, fact, blob=BLOB):
    _stub_world(monkeypatch, svc, [], blob)

    async def extracted(_chat):
        return [{"fact": fact, "type": "fact"}]

    async def messages(*a, **k):
        # the day's user turns carry the fact (the observation gate holds a fact no user sentence carries: that is its own tests);
        # this file is about the token-level dedup
        return fact + ". " + " ".join(["words"] * 30)

    async def no_contradiction(*a, **k):
        return False

    async def no_emotion(*a, **k):
        return 0

    monkeypatch.setattr(memory_digest, "_extract_facts_with_gemma", extracted)
    monkeypatch.setattr(memory_digest, "_load_todays_messages", messages)
    monkeypatch.setattr(memory_digest, "_is_contradiction", no_contradiction)
    monkeypatch.setattr(memory_digest, "_emotional_memory_pass", no_emotion)
    return asyncio.run(memory_digest.run_memory_digest("demo-user"))


# ── turn digest ──────────────────────────────────────────────────────────────

def test_turn_digest_keeps_the_new_names_when_the_fact_is_not_a_duplicate(monkeypatch):
    """0.71 overlap under the old substring rule: Mika and Biscuit were dropped."""
    svc = _Svc()
    result = _turn(monkeypatch, svc, SHORT)
    assert [t for t, _ in svc.ingested] == [SHORT]
    assert result["skipped_duplicates"] == 0 and result["new"] == 1


def test_turn_digest_supersedes_a_stored_fact_that_the_new_fact_extends(monkeypatch):
    """0.78 overlap under the old rule: the richer fact was dropped and the sparse row stayed."""
    svc = _Svc(rows=[_Row("stored-1", STORED)])
    result = _turn(monkeypatch, svc, LONG)
    assert svc.ingested == []                                   # not a second row beside the sparse one
    assert [(i, kw["edits"]) for i, kw in svc.reviewed] == [("stored-1", LONG)]   # supersede = review(edit), history kept
    assert result["skipped_duplicates"] == 0 and result["new"] == 1


def test_turn_digest_control_a_genuine_duplicate_is_still_skipped_and_counted(monkeypatch):
    svc = _Svc(rows=[_Row("stored-1", STORED)])
    result = _turn(monkeypatch, svc, STORED)
    assert svc.ingested == [] and svc.reviewed == []
    assert result["skipped_duplicates"] == 1
    assert led.summary(24)["reasons"] == {"guard_dedup_overlap": 1}   # the skip is on the ledger


# ── nightly digest ───────────────────────────────────────────────────────────

def test_nightly_digest_keeps_the_new_names_when_the_fact_is_not_a_duplicate(monkeypatch):
    svc = _Svc()
    result = _nightly(monkeypatch, svc, SHORT)
    assert [t for t, _ in svc.ingested] == [SHORT]
    assert result["skipped_duplicates"] == 0 and result["new"] == 1


def test_nightly_digest_control_a_genuine_duplicate_is_still_skipped_and_counted(monkeypatch):
    svc = _Svc(rows=[_Row("stored-1", "Dana has two kids, Mika and Biscuit.")])
    blob = "- Dana has two kids, Mika and Biscuit."
    result = _nightly(monkeypatch, svc, "Dana has two kids Mika and Biscuit", blob=blob)
    assert svc.ingested == [] and svc.reviewed == []
    assert result["skipped_duplicates"] == 1
    assert led.summary(24)["reasons"] == {"guard_dedup_overlap": 1}


# ── the comparison itself ────────────────────────────────────────────────────

def test_the_two_synthetic_sentences_and_a_control_get_the_right_verdict():
    assert memory_overlap.dedup_verdict(SHORT, BLOB)[0] == "novel"
    assert memory_overlap.dedup_verdict(LONG, BLOB) == ("extends", STORED)
    assert memory_overlap.dedup_verdict(STORED, BLOB) == ("duplicate", STORED)


def test_comparison_is_word_boundary_not_substring():
    # "Max likes sea food" shares every word as a SUBSTRING of "Maxwell likes seafood" - not as a word
    assert memory_overlap.overlap("Max likes sea food", "Maxwell likes seafood") == pytest.approx(0.25)
    assert memory_overlap.dedup_verdict("Max likes sea food", "- Maxwell likes seafood.")[0] == "novel"


def test_a_new_number_or_date_is_never_a_duplicate():
    stored = "- User's dentist appointment is on Tuesday."
    assert memory_overlap.dedup_verdict("User's dentist appointment is on Tuesday at 3pm.", stored)[0] == "extends"
    assert memory_overlap.dedup_verdict("User's dentist appointment is on Thursday.", stored)[0] == "novel"
    assert memory_overlap.dedup_verdict("User's dentist appointment is on Tuesday.", stored)[0] == "duplicate"


def test_each_stored_fact_is_compared_on_its_own():
    """Words scattered across unrelated stored facts must not make a new fact look covered."""
    blob = "- Mika likes swimming.\n- Biscuit is a dog.\n- Dana has two kids."
    assert memory_overlap.dedup_verdict("Dana has two kids Mika and Biscuit", blob)[0] != "duplicate"


# ── weekly merge ─────────────────────────────────────────────────────────────

class _MergeSvc:
    """Records archive_duplicate calls (the weekly merge's only write)."""

    def __init__(self, rows):
        self.rows = rows
        self.archived: list[tuple[str, str]] = []

    async def list_by_status(self, **kw):
        return list(self.rows)

    async def archive_duplicate(self, mem_id, keeper_id, **kw):
        self.archived.append((mem_id, keeper_id))
        return True


def _meta(cls="model_from_transcript", added="2026-09-01T00:00:00Z", conf=0.8):
    return {"authority_class": cls, "added_at": added, "confidence": conf, "status": "approved"}


def test_weekly_merge_never_retires_the_richer_row_for_a_sparser_one():
    """The sparse row is user-class and ranks FIRST under the old key: it became the keeper and the
    richer row was the one sent to archive_duplicate."""
    sparse = _Row("sparse", "User's friend Dana has two kids", _meta("user_stated"))
    rich = _Row("rich", "User's friend Dana has two kids Mika and Biscuit", _meta("model_from_transcript"))
    svc = _MergeSvc([sparse, rich])
    asyncio.run(memory_digest._merge_near_duplicates(svc, "demo-user"))
    assert ("rich", "sparse") not in svc.archived
    assert svc.archived in ([], [("sparse", "rich")])           # at most the sparse echo goes, into the rich row


def test_weekly_merge_tie_goes_to_the_higher_authority_class_then_the_newest():
    text = "User likes quiet mornings."
    voice = _Row("voice", text, _meta("user_stated", added="2026-08-01T00:00:00Z"))
    model_new = _Row("model-new", text, _meta("model_from_transcript", added="2026-09-20T00:00:00Z"))
    svc = _MergeSvc([model_new, voice])
    asyncio.run(memory_digest._merge_near_duplicates(svc, "demo-user"))
    assert svc.archived == [("model-new", "voice")]              # class beats age

    old, new = (_Row("old", text, _meta(added="2026-08-01T00:00:00Z")),
                _Row("new", text, _meta(added="2026-09-20T00:00:00Z")))
    svc = _MergeSvc([old, new])
    asyncio.run(memory_digest._merge_near_duplicates(svc, "demo-user"))
    assert svc.archived == [("old", "new")]                      # same class + richness: newest survives


def test_weekly_merge_text_overlap_ignores_punctuation():
    # "kids." vs "kids" used to be different words and pushed a true containment below 0.85
    assert memory_digest._text_overlap("User's friend Dana has two kids.",
                                       "User's friend Dana has two kids Mika and Biscuit.") == 1.0
