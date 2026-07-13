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


# Real junk observed in jason's live store (source=voice person-extractor echoes
# and source=synthesis meta-commentary) that the gate must now reject.
@pytest.mark.parametrize("text", [
    "Zoe: is being addressed in a conversation",
    "Zoe: is being addressed in a message",
    "Jason: is being addressed in a conversation",
    "Zoe: Has something scheduled on her calendar",
    "Jason: Has nothing scheduled for this week",
    "Zoe: Add work to my calendar at 9am",
    "The provided facts primarily relate to the characteristics and associations of individuals within a social context.",
    'The provided facts illustrate that the concept of a "gift" is being used as a prompt or placeholder.',
])
def test_observed_live_junk_is_rejected(text):
    storable, reason = is_storable_fact(text)
    assert storable is False, f"{text!r} should be rejected (reason={reason})"


# Real GOOD facts from jason's store must still be accepted (no over-rejection).
@pytest.mark.parametrize("text", [
    "My dad's name is Neil, spelled N-E-I-L.",
    "My mum's name is Janice.",
    "My mum's birthday is the 17th of the 11th, 1947.",
    "I have two sisters, Karen and Julie.",
    "User lives in Geraldton",
    "My mum likes NCIS.",
])
def test_observed_live_good_facts_are_accepted(text):
    storable, reason = is_storable_fact(text)
    assert storable is True, f"{text!r} should be accepted (reason={reason})"


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


# --- correction must never be silently dropped as a "skip" --------------------
# A value change to a same-attribute fact is a correction → supersede the stale
# row, never skip it. Regression guards for the synonym-leak / near-dup paths.

def test_first_person_value_correction_supersedes_not_skips():
    # "dad" canonicalises to "father" in both the key AND the value tokens, so it
    # must NOT leak in as a fake shared value and mask the Neil→Tom correction.
    action, mid = classify_against_existing(
        "My dad's name is Tom",
        [("old", "My dad's name is Neil")],
    )
    assert action == "update" and mid == "old"


def test_near_identical_value_correction_supersedes_not_skips():
    # High text similarity (Jo vs Joe ≈ 0.97) must still be read as a value
    # correction, not phrasing-only noise → supersede.
    action, mid = classify_against_existing(
        "my dad's name is Joe",
        [("old", "my dad's name is Jo")],
    )
    assert action == "update" and mid == "old"


def test_phrasing_only_duplicate_skips():
    # Same value, only punctuation differs → keep the existing row, write nothing.
    action, mid = classify_against_existing(
        "My dad's name is Neil",
        [("old", "My dad's name is Neil.")],
    )
    assert action == "skip" and mid == "old"


# --- shared incidental modifier must not mask a correction --------------------
# A correction that happens to share an adjective/descriptor with the stale row
# ("red", "iced", "small brown") asserts a DIFFERENT distinctive value → it must
# supersede, never collapse-by-richness into skip.

@pytest.mark.parametrize("candidate,existing", [
    ("my car is a red Toyota", "my car is a red Honda"),
    ("my dog is a small brown poodle", "my dog is a small brown beagle"),
    ("my employer is the big tech company Google",
     "my employer is the big tech company Microsoft"),
    ("my favorite drink is iced black coffee", "my favorite drink is iced green tea"),
])
def test_shared_modifier_correction_supersedes_not_skips(candidate, existing):
    action, mid = classify_against_existing(candidate, [("stale", existing)])
    assert (action, mid) == ("update", "stale"), (
        f"{candidate!r} corrects {existing!r} — a shared incidental modifier "
        "must not read as the same value"
    )


@pytest.mark.parametrize("candidate,existing", [
    ("my car is a red Toyota", "my car is Toyota red"),
    ("my dog is a small brown poodle", "my dog is a brown small poodle"),
    ("my favorite drink is iced black coffee", "my favorite drink is black iced coffee"),
])
def test_reordered_rephrasing_never_adds_duplicate(candidate, existing):
    # Same value, tokens merely reordered → merge (skip/update), never a 2nd row.
    action, mid = classify_against_existing(candidate, [("old", existing)])
    assert action in ("skip", "update") and mid == "old", (
        f"{candidate!r} is a rephrasing of {existing!r} — must not add a duplicate"
    )


def test_generic_statement_is_not_an_attribute_assertion():
    # The optional-subject regex must NOT treat any "X is Y" clause as a personal
    # attribute → no spurious same-attribute match.
    assert classify_against_existing(
        "The weather is nice today",
        [("w", "The weather is cold today")],
    ) == ("add", None)


# ---------------------------------------------------------------------------
# Write paths must not ingest rejected candidates
# ---------------------------------------------------------------------------

class _FakeSvc:
    def __init__(self, search_rows=None):
        self.ingested: list[tuple[str, dict]] = []
        self.archived: list[str] = []
        self.reviewed: list[tuple[str, dict]] = []
        self._search_rows = search_rows or []

    async def ingest(self, text, **kw):
        self.ingested.append((text, kw))
        # Mirror MemoryService.ingest: return a ref with a distinct id so the
        # supersede path can tell the new row apart from the archived old one.
        return _Row("new", text)

    async def search(self, *a, **k):
        return self._search_rows

    async def review(self, mem_id, **kw):
        # Mirror MemoryService.review: record the call and return the new ref
        # (the supersede path treats a None return as failure and falls back
        # to a plain ingest).
        self.reviewed.append((mem_id, kw))
        self.archived.append(mem_id)
        return _Row("new", kw.get("edits") or "")


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
    # The extractor-first path (#1242/#1264) stores the canonical template form,
    # not the raw utterance; the payload must survive.
    stored = [t for t, _ in svc.ingested]
    assert stored, "fact was not stored"
    assert any("neil" in t.lower() and "dad" in t.lower() for t in stored), stored
    assert out and out.startswith("Got it")


def test_store_fact_supersedes_existing_same_attribute(monkeypatch):
    svc = _FakeSvc(search_rows=[_Row("old", "my dad's name is Neil")])
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: svc)
    out = _run(expert_dispatch.store_fact("people", "my dad's name is spelt N-E-I-L", "jason"))
    # Supersession since QA F2/F9 (#1260/#1280): the shared reconcile classifies
    # UPDATE and the stale row is superseded IN PLACE via review(decision="edit")
    # — no duplicate row is stacked and no metadata.supersedes link is written.
    assert svc.reviewed, "existing same-attribute row was not superseded"
    target, kw = svc.reviewed[0]
    assert target == "old"
    assert kw.get("decision") == "edit"
    assert "n-e-i-l" in (kw.get("edits") or "").lower()
    # The superseding edit replaces the row — a plain ingest of the same fact
    # must not ALSO run (that would be the pre-F2 duplicate-stacking bug).
    assert not any("n-e-i-l" in t.lower() for t, _ in svc.ingested), svc.ingested
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


def test_guard_allows_supersede_of_name_value_rows():
    """Residual QA F2: 'Neil' in "my dad's name is Neil" is the attribute VALUE,
    not a third person the row is about — a titleless correction to the same
    user-anchored attribute must reach classification (and supersede)."""
    from memory_quality import guard_existing_by_entity, classify_against_existing
    existing = [("old", "my dad's name is Neil")]
    for cand in (
        "my dad's name is Kevin",
        "my dad's name is spelt N-E-I-L",
        "User's dad is named spelt N-E-I-L",
    ):
        kept = guard_existing_by_entity(cand, existing, None)
        assert kept == existing, f"{cand!r} was wrongly guarded away"
        assert classify_against_existing(cand, kept) == ("update", "old")


def test_guard_still_protects_third_person_rows():
    """The name-value exclusion must not weaken namesake / third-person
    protection: rows ABOUT someone (possessive subject or repeated mention)
    stay guarded from unrelated titleless candidates."""
    from memory_quality import guard_existing_by_entity
    assert guard_existing_by_entity(
        "my friend Jessica's birthday is March 25",
        [("k", "Karen's birthday is January first")], None) == []
    assert guard_existing_by_entity(
        "allergic to shellfish",
        [("c", "Caitlin Farrell: allergic to nuts")], None) == []
    # Subject of a name fact is still protected ("Jessica's name is Neil" is
    # ABOUT Jessica even though Neil is a value).
    assert guard_existing_by_entity(
        "my dad's name is Kevin",
        [("j", "Jessica's name is Neil")], None) == []
