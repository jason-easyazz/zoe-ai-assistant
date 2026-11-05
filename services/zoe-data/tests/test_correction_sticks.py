"""A member's own explicit correction of a person fact sticks (QA round 2, 2026-11-05).

The bug: a member said a family member was 9, then corrected it three times ("actually <name>'s 10 now"). The person
extractor's supersede of the old row was "refused by authority", both rows stayed approved, and the brain kept
answering 9. Three causes, each pinned here with a break-the-fix control:

1. ``memory_authority.supports`` did not read "<name>'s 10 now" as an AGE ("10 years old" needs an age cue and the
   sentence had none), so the model's correctly-paraphrased correction was ``unanchored`` - no standing at all.
2. Even supported, a model paraphrase (``user_stated_derived``) cannot replace a value the member stated DIRECTLY;
   an explicit correction in their own verbatim sentence now carries their words' power (``explicit_correction``).
3. Age had no fact kind: the model filed it as "preference", a MULTI-valued kind, so the entity-keyed supersede could
   have retired an unrelated preference row instead. Ages are keyed ``age``; multi-valued kinds never supersede.

And the serve side: rows from other writers ("X is 9 years old") are not in the supersede chain, so the packet serves
ONE age per person (``person_extractor.stale_age_ids``).

Real MemoryService over a where-honouring Chroma stand-in, real in-memory SQLite people table; no network, no model.
Neutral synthetic names only.
"""
from __future__ import annotations

import asyncio

import pytest

import memory_authority as ma
import memory_service
import person_extractor
from memory_service import MemoryRef, MemoryService
from routers.memories import _build_memory_prompt_packet
from test_memory_authority import _Col
from test_temporal_relationships import _open_db, _seed_people

pytestmark = pytest.mark.ci_safe

A = "member_a"
B = "member_b"
LLM = "person_extractor_llm"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("ZOE_MEMORY_AUTHORITY", raising=False)
    monkeypatch.delenv("ZOE_EXPLICIT_CORRECTION", raising=False)
    monkeypatch.delenv("ZOE_STRUCTURAL_CLAIMS", raising=False)


def _match(meta: dict, where) -> bool:
    """``test_memory_authority._match`` plus ``$in`` (the entity-keyed read asks for the person id OR its slug)."""
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
    s = MemoryService(data_dir="/nonexistent/zoe-test-correction-sticks")
    col = _InCol()
    s._collection = lambda: col

    async def no_audit(**_kw):
        return None

    async def not_opted_out(_uid):
        return False

    s._append_audit = no_audit
    monkeypatch.setattr(memory_service, "_user_opted_out", not_opted_out)
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: s)
    s._col = col
    return s


def _rows(svc, user, status="approved"):
    return [r.text for r in asyncio.run(svc.list_by_status(user_id=user, status=status))]


def _apply(value, excerpt, *, user=A, fact_type="preference"):
    """The LLM half of the person extractor writing one fact, exactly as process_text_llm calls it."""
    async def run():
        db = await _open_db()
        await _seed_people(db, user_id=user)
        try:
            return await person_extractor.apply_person_fact(
                "Alice", fact_type, value, user_id=user, source="conversation", db=db,
                source_excerpt=excerpt, origin=LLM)
        finally:
            await db.close()

    return asyncio.run(run())


def _seed_model_preference(svc):
    """A preference the extractor model wrote from the member's turn (``user_stated_derived``, rank 3): a later
    model write of equal rank MAY replace it, so the multi-valued guard - not the wall - is what keeps it."""
    ref = asyncio.run(svc.ingest("Alice: hates mushrooms", user_id=A, source="conversation", origin=LLM,
                                 source_excerpt="Alice hates mushrooms", status="approved",
                                 entity_type="person", entity_id="pa", memory_type="person",
                                 metadata={"pattern_type": "preference"}))
    assert ma.row_class(ref.metadata, ref.text) == ma.USER_STATED_DERIVED
    return ref


def _seed_stated_age(svc, user=A, text="Alice is 9 years old"):
    """The age the member stated directly (a teach-lane row: user_stated), linked to Alice."""
    ref = asyncio.run(svc.ingest(text, user_id=user, source="voice_fact", status="approved", confidence=0.9,
                                 entity_type="person", entity_id="pa", memory_type="person"))
    assert ma.row_class(ref.metadata, ref.text) == ma.USER_STATED
    return ref


# ── 1 + 2: the resolver ──────────────────────────────────────────────────────


@pytest.mark.parametrize("turn", [
    "oh actually Alice's 10 now, she had her birthday last month",
    "alice's 10 though, I told you earlier",
    "no, Alice is 10",
])
def test_an_explicit_correction_in_the_members_words_carries_their_power(turn):
    r = ma.resolve_write(LLM, "Alice: 10 years old", anchor_text=turn, user_id=A)
    assert (r.basis, r.power) == (ma.CORRECTION_BASIS, ma.USER_RANK)


@pytest.mark.parametrize("turn,basis", [
    ("Alice is 10", "anchored_user_turn"),             # supported, but nothing says it corrects anything
    ("I think Alice's 10 now", "anchored_user_turn"),  # a hedge is not a plain correction
    ("Alice's 10 minutes away", "unanchored"),          # a number that is not an age
    ("actually Alice's mum is 10 now", "unanchored"),   # a relative's number is not Alice's age
    ("is Alice 10 now?", "unanchored"),                 # a question states nothing
    ("actually we were talking about 10 things", "unanchored"),  # the turn never says it (inference)
    ("Carol told me that actually Alice is 10 now", "anchored_user_turn"),  # someone else's claim, relayed
    ("apparently Alice is 10 now", "unanchored"),                         # hearsay is not a correction
])
def test_controls_hedges_non_ages_and_inference_get_no_correction_power(turn, basis):
    r = ma.resolve_write(LLM, "Alice: 10 years old", anchor_text=turn, user_id=A)
    assert r.basis == basis and r.power < ma.USER_RANK


def test_break_the_fix_the_flag_off_returns_the_old_class(monkeypatch):
    """Flag off = the pre-fix result exactly: the bare-number age shape is off too, so it is unanchored again."""
    monkeypatch.setenv("ZOE_EXPLICIT_CORRECTION", "0")
    r = ma.resolve_write(LLM, "Alice: 10 years old", anchor_text="actually Alice's 10 now", user_id=A)
    assert r.basis == "unanchored" and r.power == ma.RANK[ma.MODEL_FROM_TURN]


def test_the_members_own_i_said_is_not_a_report():
    r = ma.resolve_write(LLM, "Alice: 10 years old", anchor_text="no, I told you, Alice is 10 now", user_id=A)
    assert r.basis == ma.CORRECTION_BASIS


def test_a_stored_correction_row_keeps_the_members_standing():
    meta = {"authority_class": ma.USER_STATED_DERIVED, "authority_basis": ma.CORRECTION_BASIS}
    assert ma.row_class(meta) == ma.USER_STATED and ma.label_of_meta(meta) == "promote"


# ── the whole write path ─────────────────────────────────────────────────────


def test_the_members_correction_supersedes_the_age_they_stated(svc):
    _seed_stated_age(svc)
    assert _apply("10 years old", "oh actually Alice's 10 now, she had her birthday last month") is True
    assert _rows(svc, A) == ["Alice: 10 years old"]
    assert _rows(svc, A, "superseded") == ["Alice is 9 years old"]


def test_inference_does_not_supersede_what_the_member_stated(svc):
    """The turn never says Alice is 10: the model's guess is refused and the stated age stays current."""
    _seed_stated_age(svc)
    assert _apply("10 years old", "we spent ages on the 10 times table with Alice tonight") is False
    assert _rows(svc, A) == ["Alice is 9 years old"]


def test_break_the_fix_without_the_correction_power_the_stated_age_wins(svc, monkeypatch):
    monkeypatch.setenv("ZOE_EXPLICIT_CORRECTION", "0")
    _seed_stated_age(svc)
    _apply("10 years old", "oh actually Alice's 10 now, she had her birthday last month")
    assert "Alice is 9 years old" in _rows(svc, A)
    assert _rows(svc, A, "superseded") == []


def test_another_member_cannot_override_this_members_row(svc):
    """Member B's correction lands in B's own memory; A's stated age is untouched."""
    _seed_stated_age(svc)
    assert _apply("10 years old", "actually Alice's 10 now", user=B) is True
    assert _rows(svc, A) == ["Alice is 9 years old"]
    assert _rows(svc, A, "superseded") == []
    assert _rows(svc, B) == ["Alice: 10 years old"]


def test_an_age_filed_as_a_preference_never_retires_a_real_preference(svc):
    """Cause 3: the model's "preference" label must not make an age correction retire "Alice hates mushrooms"."""
    asyncio.run(svc.ingest("Alice hates mushrooms", user_id=A, source="voice_fact", status="approved",
                           entity_type="person", entity_id="pa", memory_type="person",
                           metadata={"pattern_type": "preference"}))
    _seed_stated_age(svc)
    _apply("10 years old", "actually Alice's 10 now")
    assert sorted(_rows(svc, A)) == ["Alice hates mushrooms", "Alice: 10 years old"]


def test_a_second_preference_is_another_fact_not_a_replacement(svc):
    _seed_model_preference(svc)
    _apply("loves chess", "Alice loves chess")
    assert sorted(_rows(svc, A)) == ["Alice: hates mushrooms", "Alice: loves chess"]
    assert _rows(svc, A, "superseded") == []


def test_control_a_single_valued_kind_still_supersedes(svc):
    asyncio.run(svc.ingest("Alice works at the bakery", user_id=A, source="voice_fact", status="approved",
                           entity_type="person", entity_id="pa", memory_type="person",
                           metadata={"pattern_type": "work"}))
    _apply("Alice works at the library", "actually Alice works at the library now", fact_type="work")
    assert _rows(svc, A) == ["Alice works at the library"]


# ── the served packet: one current age per person ───────────────────────────


def _ref(mid, text, cls, added_at, status="approved"):
    return MemoryRef(id=mid, text=text, metadata={
        "status": status, "authority_class": cls, "authority_basis": "x", "added_at": added_at,
        "user_id": A, "memory_type": "person"})


def _packet(rows):
    return _build_memory_prompt_packet(rows, [])["packet"]


def test_the_packet_serves_only_the_current_age():
    rows = [
        _ref("old1", "Alice is 9 years old", ma.USER_STATED, "2026-11-01T08:00:00+00:00"),
        _ref("old2", "The user has a daughter named Alice who is 9 years old", ma.MODEL_FROM_TURN,
             "2026-11-01T08:01:00+00:00"),
        _ref("new", "Alice: 10 years old", ma.USER_STATED_DERIVED, "2026-11-03T08:00:00+00:00"),
        _ref("other", "Alice hates mushrooms", ma.USER_STATED, "2026-11-01T08:00:00+00:00"),
    ]
    p = _packet(rows)
    assert "Alice: 10 years old" in p and "Alice hates mushrooms" in p
    assert "9 years old" not in p


def test_a_newer_inference_never_hides_the_age_the_member_said():
    rows = [
        _ref("said", "Alice is 10 years old", ma.USER_STATED, "2026-11-01T08:00:00+00:00"),
        _ref("guess", "Alice is 11 years old", ma.MODEL_FROM_TURN, "2026-11-04T08:00:00+00:00"),
    ]
    p = _packet(rows)
    assert "Alice is 10 years old" in p and "11 years old" not in p


def test_different_people_keep_their_own_ages():
    rows = [
        _ref("a", "Alice is 9 years old", ma.USER_STATED, "2026-11-01T08:00:00+00:00"),
        _ref("b", "Bob is 41 years old", ma.USER_STATED, "2026-11-03T08:00:00+00:00"),
    ]
    p = _packet(rows)
    assert "Alice is 9 years old" in p and "Bob is 41 years old" in p


def test_an_operator_age_is_never_hidden_by_a_newer_member_statement():
    """The write wall refuses a member statement over an operator row; the packet must not do it by the back door."""
    rows = [
        _ref("op", "Alice is 10 years old", ma.OPERATOR, "2026-11-01T08:00:00+00:00"),
        _ref("said", "Alice is 9 years old", ma.USER_STATED, "2026-11-04T08:00:00+00:00"),
    ]
    p = _packet(rows)
    assert "Alice is 10 years old" in p and "9 years old" not in p


def test_break_the_fix_flag_off_an_age_stays_a_preference_and_preferences_supersede(svc, monkeypatch):
    """Flag off restores the old write path too: no ``age`` kind, and the old preference supersede."""
    monkeypatch.setenv("ZOE_EXPLICIT_CORRECTION", "0")
    _seed_model_preference(svc)
    _apply("loves chess", "Alice loves chess")
    assert _rows(svc, A, "superseded") == ["Alice: hates mushrooms"]


def test_break_the_fix_the_flag_off_serves_every_age(monkeypatch):
    monkeypatch.setenv("ZOE_EXPLICIT_CORRECTION", "0")
    rows = [
        _ref("old", "Alice is 9 years old", ma.USER_STATED, "2026-11-01T08:00:00+00:00"),
        _ref("new", "Alice: 10 years old", ma.USER_STATED_DERIVED, "2026-11-03T08:00:00+00:00"),
    ]
    p = _packet(rows)
    assert "Alice is 9 years old" in p and "Alice: 10 years old" in p


@pytest.mark.parametrize("text,claim", [
    ("Alice: 10 years old", ("alice", 10)),
    ("The user has a daughter named Alice who is 9 years old", ("alice", 9)),
    ("Alice turned 10 last month", ("alice", 10)),
    ("Alice's mum is 40 years old", None),
    ("User is 41 years old", None),
    ("Alice: 10", None),
    ("Alice has 10 cards", None),
])
def test_person_age_claim(text, claim):
    assert person_extractor.person_age_claim(text) == claim
