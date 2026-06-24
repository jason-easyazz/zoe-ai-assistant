"""Write-quality gate: non-facts must never become stored memories.

Covers the pure `is_storable_fact` shape check, the near-dedup / supersession
decision (`classify_against_existing`), and that the conversational write paths
(expert_dispatch.store_fact, memory_extractor.extract_and_ingest,
zoe_agent._mempalace_add) actually drop rejected candidates instead of calling
MemoryService.ingest.
"""
import asyncio

import pytest

memory_quality = pytest.importorskip("memory_quality")
expert_dispatch = pytest.importorskip("expert_dispatch")
memory_service = pytest.importorskip("memory_service")
memory_extractor = pytest.importorskip("memory_extractor")

from memory_quality import is_storable_fact, classify_against_existing


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# is_storable_fact — pure shape check
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    # interrogatives / recall questions
    "Do you remember what my mum's name is?",
    "do you recall my dad's name",
    "what is my dad's name?",
    "what's my mum's name",
    "who is my sister",
    "can you remember where I work",
    "When is my birthday?",
    "How old am I",
    # LLM meta-rambling
    "The provided statements illustrate a pattern of family relationships.",
    "The context suggests the user is asking about names.",
    "The concept of memory is central here.",
    "Based on the conversation, the user likes coffee.",
    "In summary, several facts were mentioned.",
    "It seems the user has a dog.",
    "As an AI, I don't have personal memories.",
    # empty / too short
    "",
    "   ",
    "ok",
    "yes",
])
def test_non_facts_are_rejected(text):
    storable, reason = is_storable_fact(text)
    assert storable is False, f"{text!r} should be rejected (reason={reason})"
    assert reason


@pytest.mark.parametrize("text", [
    "My dad's name is Neil",
    "my dad's name is spelt N-E-I-L",
    "I prefer morning coffee",
    "I like morning coffee",
    "my lucky number is 47",
    "I live in Geraldton",
    "remember that my mum likes NCIS",
    "note that my lucky number is 47",
    "don't forget my sister's birthday is in May",
    "keep in mind my dentist is on Tuesday",
    # third-person extraction summaries are legitimate stored shapes
    "User's name is Jason",
    "Preference: user likes morning coffee",
    "Person the user met: Sarah (a teacher)",
])
def test_real_facts_are_accepted(text):
    storable, reason = is_storable_fact(text)
    assert storable is True, f"{text!r} should be accepted (reason={reason})"
    assert reason == ""


def test_memory_command_with_question_word_still_stored():
    # A teach command that merely contains a question word is still a fact.
    storable, _ = is_storable_fact("remember that I asked who is coming on Friday")
    assert storable is True


# ---------------------------------------------------------------------------
# classify_against_existing — ADD vs UPDATE/supersede
# ---------------------------------------------------------------------------

def test_new_attribute_is_added():
    action, mid = classify_against_existing(
        "I live in Geraldton", [("m1", "My dad's name is Neil")])
    assert action == "add" and mid is None


def test_same_attribute_different_value_supersedes():
    action, mid = classify_against_existing(
        "my dad's name is spelt N-E-I-L",
        [("old", "my dad's name is Neil")],
    )
    assert action == "update" and mid == "old"


def test_near_exact_duplicate_of_equal_fact_skips():
    # Same fact, no new information → keep the existing row, write nothing
    # (don't churn the store by archiving + re-adding an identical value).
    action, mid = classify_against_existing(
        "my dad's name is Neil",
        [("old", "My dad's name is Neil.")],
    )
    assert action == "skip" and mid == "old"


def test_empty_existing_adds():
    action, mid = classify_against_existing("my dad's name is Neil", [])
    assert action == "add" and mid is None


# --- consolidation near-duplicate dedup (the Neil repro) ----------------------
# The idle-consolidation engine distils a THIRD-person restatement of a fact the
# user already taught in FIRST person. Same attribute, same value, but the
# existing row is richer (it spells the name) → keep existing, store NOTHING.

def test_distilled_third_person_dup_of_richer_existing_is_skipped():
    action, mid = classify_against_existing(
        "User's father's name is Neil.",
        [("approved", "My dad's name is Neil, spelled N-E-I-L.")],
    )
    assert action == "skip" and mid == "approved", (
        "distilled near-dup of a richer fact must NOT create a 2nd row"
    )


def test_distilled_richer_fact_supersedes_sparser_existing():
    # The reverse direction: the new distilled fact carries MORE detail than the
    # stored one → supersede the sparse row instead of keeping both.
    action, mid = classify_against_existing(
        "My dad's name is Neil, spelled N-E-I-L.",
        [("sparse", "User's father's name is Neil.")],
    )
    assert action == "update" and mid == "sparse"


def test_same_attribute_corrected_value_supersedes():
    # Same attribute (father's name) but a genuinely different value → this is a
    # correction, supersede the stale row rather than keeping a contradiction.
    action, mid = classify_against_existing(
        "User's father's name is Tom",
        [("stale", "My dad's name is Neil")],
    )
    assert action == "update" and mid == "stale"


def test_genuinely_new_fact_is_added():
    action, mid = classify_against_existing(
        "My favourite colour is blue",
        [("approved", "My dad's name is Neil, spelled N-E-I-L.")],
    )
    assert action == "add" and mid is None


def test_unrelated_facts_are_kept_separate():
    # Different attributes must never be merged, even when search returns them.
    action, mid = classify_against_existing(
        "User's mother's name is Sandra",
        [("dad", "My dad's name is Neil")],
    )
    assert action == "add" and mid is None


# ---------------------------------------------------------------------------
# Write paths must not ingest rejected candidates
# ---------------------------------------------------------------------------

class _FakeSvc:
    def __init__(self, search_rows=None):
        self.ingested: list[tuple[str, dict]] = []
        self.archived: list[str] = []
        self._search_rows = search_rows or []

    async def ingest(self, text, **kw):
        self.ingested.append((text, kw))
        # Mirror MemoryService.ingest: return a ref with a distinct id so the
        # supersede path can tell the new row apart from the archived old one.
        return _Row("new", text)

    async def search(self, *a, **k):
        return self._search_rows

    async def review(self, mem_id, **kw):
        self.archived.append(mem_id)


class _Row:
    def __init__(self, mem_id, text):
        self.id = mem_id
        self.text = text


@pytest.fixture
def patched(monkeypatch):
    svc = _FakeSvc()
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: svc)
    recalled: list[str] = []

    async def _fake_run_expert(domain, text, user_id, session_id):
        recalled.append(text)
        return "RECALLED"

    monkeypatch.setattr(expert_dispatch, "_run_expert", _fake_run_expert)
    return svc, recalled


def test_store_fact_rejects_meta_rambling(patched):
    svc, recalled = patched
    junk = "The provided statements illustrate a pattern of family names."
    out = _run(expert_dispatch.store_fact("memory", junk, "jason"))
    assert svc.ingested == [], "meta rambling must not be stored"
    assert recalled == [junk]
    assert out == "RECALLED"


def test_store_fact_stores_real_fact(patched):
    svc, recalled = patched
    out = _run(expert_dispatch.store_fact("people", "my dad's name is Neil", "jason"))
    assert [t for t, _ in svc.ingested] == ["my dad's name is Neil"]
    assert out and out.startswith("Got it")


def test_store_fact_supersedes_existing_same_attribute(monkeypatch):
    svc = _FakeSvc(search_rows=[_Row("old", "my dad's name is Neil")])
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: svc)
    out = _run(expert_dispatch.store_fact("people", "my dad's name is spelt N-E-I-L", "jason"))
    # New value stored with a supersedes link, old row archived (not duplicated).
    assert len(svc.ingested) == 1
    text, kw = svc.ingested[0]
    assert text == "my dad's name is spelt N-E-I-L"
    assert (kw.get("metadata") or {}).get("supersedes") == "old"
    assert svc.archived == ["old"]
    assert out and out.startswith("Got it")


def test_ingest_or_supersede_skips_dup_of_richer_existing(monkeypatch):
    # The consolidation path: a sparser distilled restatement of a richer stored
    # fact must write NOTHING and must NOT archive the existing row.
    svc = _FakeSvc(search_rows=[_Row("approved", "My dad's name is Neil, spelled N-E-I-L.")])
    _run(expert_dispatch._ingest_or_supersede(
        svc, "User's father's name is Neil.",
        user_id="jason", source="idle_consolidation",
        session_id="s1", user_turn_id="t1",
        memory_type="fact", confidence=0.8, tags=["idle"],
    ))
    assert svc.ingested == [], "near-dup of a richer fact must not be stored"
    assert svc.archived == [], "the richer existing row must be kept"


def test_extract_and_ingest_drops_rejected(monkeypatch):
    svc = _FakeSvc()
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: svc)
    # Force a junk candidate through the extractor.
    junk = memory_extractor.MemoryCandidate(text="The provided context is about names.")

    def _fake_extract(*a, **k):
        return [junk]

    monkeypatch.setattr(memory_extractor, "extract_candidates", _fake_extract)
    saved = _run(memory_extractor.extract_and_ingest("whatever", user_id="jason"))
    assert saved == 0
    assert svc.ingested == []


def test_extract_and_ingest_keeps_real_fact(monkeypatch):
    svc = _FakeSvc()
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: svc)
    real = memory_extractor.MemoryCandidate(text="User's name is Jason")

    def _fake_extract(*a, **k):
        return [real]

    monkeypatch.setattr(memory_extractor, "extract_candidates", _fake_extract)
    monkeypatch.setattr(memory_extractor, "_invalidate_user_facts_cache", lambda *a, **k: None, raising=False)
    saved = _run(memory_extractor.extract_and_ingest("my name is Jason", user_id="jason"))
    assert saved == 1
    assert [t for t, _ in svc.ingested] == ["User's name is Jason"]


def test_mempalace_add_rejects_question(monkeypatch):
    zoe_agent = pytest.importorskip("zoe_agent")
    svc = _FakeSvc()
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: svc)
    monkeypatch.setattr(zoe_agent, "_invalidate_user_facts_cache", lambda *a, **k: None, raising=False)
    ok = _run(zoe_agent._mempalace_add("Do you remember what my mum's name is?", "jason"))
    assert ok is False
    assert svc.ingested == []


def test_mempalace_add_stores_real_fact(monkeypatch):
    zoe_agent = pytest.importorskip("zoe_agent")
    svc = _FakeSvc()
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: svc)
    monkeypatch.setattr(zoe_agent, "_invalidate_user_facts_cache", lambda *a, **k: None, raising=False)
    ok = _run(zoe_agent._mempalace_add("My mum's name is Janice", "jason"))
    assert ok is True
    assert [t for t, _ in svc.ingested] == ["My mum's name is Janice"]
