"""QA review F6 + F8 — correction handling must never store garbled/verbatim junk.

F6 (live, session demo-a-family): the correction re-mining path substituted
old→new inside a compound prior sentence and STORED the garble — "Actually
their daughter's name is Ruby-Rose, not Ruby" over "their kids are Ruby and
Max" became "Their kids are their daughter's name is Ruby-Rose and Max".
Pinned here: the substitution is sanity-gated; a failed gate stores ONLY the
anchored clean pair, or nothing.

F8 (live prod log): "No Caitlin is allergic to shellfish, I don't believe
Jessica is allergic to anything" was verbatim-taught ("Got it — I'll remember
No Caitlin is allergic to shellfish, you don't believe Je…"). Pinned here:
correction/negation-shaped turns are detected (`memory_quality.
looks_like_correction`), routed through the correction path, and NEVER stored
raw when that path yields nothing.
"""

from __future__ import annotations

import asyncio

import pytest

pytestmark = pytest.mark.ci_safe  # pure regex + monkeypatched fakes, slim-dep

memory_quality = pytest.importorskip("memory_quality")
memory_extractor = pytest.importorskip("memory_extractor")
expert_dispatch = pytest.importorskip("expert_dispatch")
memory_service = pytest.importorskip("memory_service")

from memory_extractor import extract_candidates
from memory_quality import ambiguous_negation_subject, looks_like_correction


def _run(coro):
    return asyncio.run(coro)


# ── F6: compound-sentence correction must not store the garbled splice ───────

RUBY_FIX = "Actually their daughter's name is Ruby-Rose, not Ruby"


def test_compound_correction_never_stores_garble_unanchored():
    # No person intro in the prior turn → nothing storable, never the garble.
    out = extract_candidates(RUBY_FIX, prev_user_message="their kids are Ruby and Max")
    assert out == []


def test_compound_correction_falls_back_to_anchored_clean_pair():
    prev = "my friend Lindsay's kids are Ruby and Max"
    out = extract_candidates(RUBY_FIX, prev_user_message=prev)
    assert len(out) == 1
    text = out[0].text
    assert "Lindsay" in text
    assert "Ruby-Rose" in text
    # the garble signature — two copulas jammed into one clause — must be absent
    assert "are their daughter's name is" not in text.lower()
    assert out[0].memory_type == "person"
    assert out[0].title == "Lindsay"


@pytest.mark.parametrize("prev,fix", [
    # clausal new-value over a compound sentence (the live F6 shape)
    ("their kids are Ruby and Max", RUBY_FIX),
    ("Lindsay's kids are Ruby and Max, and they live in Perth", RUBY_FIX),
])
def test_no_candidate_ever_carries_jammed_copulas(prev, fix):
    for cand in extract_candidates(fix, prev_user_message=prev):
        assert not memory_extractor._JAMMED_COPULA_RE.search(cand.text), cand.text


def test_simple_correction_still_resolves():
    # The sanity gate must not break the plain single-clause correction path.
    out = extract_candidates(
        "wait no sorry I meant saturday not friday",
        prev_user_message="my dentist appointment got moved to friday",
    )
    assert len(out) == 1
    assert "saturday" in out[0].text.lower()
    assert "friday" not in out[0].text.lower()


def test_templated_correction_still_lands_canonical():
    out = extract_candidates(
        "sorry I meant Anna not Emma",
        prev_user_message="my wife's name is Emma",
    )
    assert len(out) == 1
    assert "Anna" in out[0].text and "Emma" not in out[0].text


# ── F8: looks_like_correction shape detector ─────────────────────────────────

@pytest.mark.parametrize("text", [
    "No Caitlin is allergic to shellfish, I don't believe Jessica is allergic to anything",
    "no, that's not right",
    "No! that's wrong",
    "that's wrong, my dad's name is Tom",
    "That's not true",
    "actually it's saturday not friday",
    "Actually their daughter's name is Ruby-Rose, not Ruby",
    "wait no I meant Anna",
    "sorry I meant saturday not friday",
])
def test_correction_shapes_detected(text):
    assert looks_like_correction(text) is True, text


@pytest.mark.parametrize("text", [
    "My dad's name is Neil",
    "I like morning coffee",
    "Nothing beats a good coffee",     # "No…" prefix inside a word
    "Noah is my friend",               # name starting with "No"
    "remember that my mum likes NCIS",
    "november is my favourite month",
])
def test_plain_teaches_not_flagged_as_corrections(text):
    assert looks_like_correction(text) is False, text


def test_ambiguous_negation_subject_extracted():
    assert ambiguous_negation_subject(
        "No Caitlin is allergic to shellfish, I don't believe Jessica is allergic to anything"
    ) == "Caitlin"
    assert ambiguous_negation_subject("no, that's wrong") is None
    assert ambiguous_negation_subject("my dad's name is Neil") is None


# ── F8: store_fact must never verbatim-teach a correction shape ──────────────

class _FakeSvc:
    def __init__(self):
        self.ingested: list[tuple[str, dict]] = []

    async def ingest(self, text, **kw):
        self.ingested.append((text, kw))
        return _Row("new", text)

    async def search(self, *a, **k):
        return []

    async def review(self, mem_id, **kw):
        return None


class _Row:
    def __init__(self, mem_id, text):
        self.id = mem_id
        self.text = text


@pytest.fixture
def patched(monkeypatch):
    svc = _FakeSvc()
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: svc)

    async def _fake_run_expert(domain, text, user_id, session_id):
        return "RECALLED"

    monkeypatch.setattr(expert_dispatch, "_run_expert", _fake_run_expert)
    return svc


def test_store_fact_never_stores_ambiguous_negation_verbatim(patched):
    svc = patched
    text = ("No Caitlin is allergic to shellfish, I don't believe Jessica "
            "is allergic to anything")
    out = _run(expert_dispatch.store_fact("memory", text, "demo-user"))
    assert svc.ingested == [], "ambiguous negation must store NOTHING"
    # honest clarification, never "I'll remember No Caitlin…"
    assert out is not None and "Caitlin" in out
    assert "remember no" not in (out or "").lower()


def test_store_fact_correction_resolved_by_extractor_stores_clean_fact(patched):
    svc = patched
    out = _run(expert_dispatch.store_fact(
        "memory", "that's wrong, my dad's name is Tom", "demo-user"))
    # the extractor mined the canonical fact — the raw sentence is never stored
    stored = [t for t, _ in svc.ingested]
    assert stored and all("that's wrong" not in t.lower() for t in stored)
    assert any("Tom" in t for t in stored)
    assert out == "Got it — I've updated that."


def test_store_fact_unresolved_correction_defers_to_brain(patched):
    svc = patched
    out = _run(expert_dispatch.store_fact(
        "memory", "actually my meeting got moved around a bit", "demo-user"))
    assert svc.ingested == [], "unresolved correction must not be stored raw"
    assert out is None  # defer to the brain, reply-only


def test_store_fact_plain_teach_still_stores(patched):
    svc = patched
    out = _run(expert_dispatch.store_fact("people", "my lucky number is 47", "demo-user"))
    assert svc.ingested, "plain teaches must still store"
    assert out and out.startswith("Got it")
