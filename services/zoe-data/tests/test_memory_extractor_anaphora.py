"""Anaphoric capture — corrections and pronoun-subject facts anchor to the PRIOR user turn.

Live hard-gate 2026-07-07 (flue brain, real store): two whole classes of turn
produced NO stored fact because ``extract_candidates`` saw only the current
message:

  * entity-less correction — "my dentist appointment got moved to friday" then
    "wait no sorry I meant saturday not friday" stored nothing (the packet kept
    only the friday sentence);
  * pronoun-subject fact — "my wife's name is Emma" then "she's a doctor"
    never stored Emma-is-a-doctor (person_extractor is also current-turn-only
    and every one of its patterns requires a capitalized NAME, so "she's a
    doctor" matches nothing there either).

These tests pin the fix: ``prev_user_message`` anchors both shapes, a missing
or non-matching prior turn stores NOTHING (no hallucinated anchor), and the
``extract_and_ingest`` per-(user, session) LRU threads the prior turn with
zero call-site plumbing. Prior-turn text is USER-authored only — the purity
contract (tests/test_memory_extractor_purity.py) is untouched.
"""

from __future__ import annotations

import sys
import types

import pytest

pytestmark = pytest.mark.ci_safe  # pure regex + stdlib, slim-dep

import memory_extractor
from memory_extractor import extract_candidates

DENTIST_PREV = "my dentist appointment got moved to friday"
DENTIST_FIX = "wait no sorry I meant saturday not friday"

EMMA_PREV = "my wife's name is Emma"
EMMA_FIX = "she's a doctor"


# ── corrections ──────────────────────────────────────────────────────────────

def test_dentist_correction_stores_saturday_fact():
    out = extract_candidates(DENTIST_FIX, prev_user_message=DENTIST_PREV)
    assert len(out) == 1
    text = out[0].text.lower()
    assert "dentist" in text
    assert "saturday" in text
    assert "friday" not in text


@pytest.mark.parametrize("correction", [
    DENTIST_FIX,
    "no sorry, I meant saturday not friday",
    "actually it's saturday, not friday",
    "actually saturday not friday",
])
def test_correction_shape_variants_resolve(correction):
    out = extract_candidates(correction, prev_user_message=DENTIST_PREV)
    assert len(out) == 1
    assert "saturday" in out[0].text.lower()
    assert "friday" not in out[0].text.lower()


def test_correction_without_prior_message_stores_nothing():
    # No anchor → no fact. Never guess.
    assert extract_candidates(DENTIST_FIX) == []
    assert extract_candidates(DENTIST_FIX, prev_user_message="") == []


def test_correction_when_old_value_absent_from_prior_stores_nothing():
    # Prior turn never mentioned "thursday" — nothing to correct, store nothing.
    out = extract_candidates(
        "wait no sorry I meant saturday not thursday",
        prev_user_message="my dentist appointment is confirmed",
    )
    assert out == []


def test_correction_over_templated_fact_lands_in_canonical_shape():
    # Correcting a fact the templates already understand re-mines the corrected
    # sentence, so it stores in the same canonical shape as a direct statement.
    out = extract_candidates(
        "sorry I meant Anna not Emma", prev_user_message=EMMA_PREV
    )
    assert len(out) == 1
    assert out[0].text == "User's wife is named Anna"


def test_correction_value_with_backslash_is_literal():
    # re.sub template escapes (\1, \g<...>) in the user's replacement value
    # must be treated literally, never as group references.
    out = extract_candidates(
        r"I meant sa\1turday not friday", prev_user_message=DENTIST_PREV
    )
    assert len(out) == 1
    assert r"sa\1turday" in out[0].text


def test_identical_new_and_old_values_store_nothing():
    out = extract_candidates(
        "I meant friday not friday", prev_user_message=DENTIST_PREV
    )
    assert out == []


# ── pronoun-subject facts ────────────────────────────────────────────────────

def test_emma_doctor_stores_anchored_person_fact():
    out = extract_candidates(EMMA_FIX, prev_user_message=EMMA_PREV)
    assert len(out) == 1
    c = out[0]
    assert c.text == "Emma (user's wife) is a doctor"
    assert c.memory_type == "person"
    assert c.entity_type == "person"
    assert c.entity_id == "emma"


def test_pronoun_anchor_survives_sentence_initial_capital():
    # "My wife is Emma" (capital M) must anchor like "my wife is Emma" — the
    # bare intro pattern carries re.IGNORECASE like the template set it mirrors.
    out = extract_candidates("she's a nurse", prev_user_message="My wife is Emma")
    assert len(out) == 1
    assert out[0].text == "Emma (user's wife) is a nurse"


def test_pronoun_fact_without_person_intro_stores_nothing():
    # Prior turn introduced no person → nothing to anchor to.
    assert extract_candidates(EMMA_FIX) == []
    assert extract_candidates(
        EMMA_FIX, prev_user_message="my dentist appointment got moved to friday"
    ) == []


def test_pronoun_gender_mismatch_refuses_anchor():
    # "she" after a husband introduction (and vice versa) → refuse, store nothing.
    assert extract_candidates(
        "she's a doctor", prev_user_message="my husband is named Mark"
    ) == []
    assert extract_candidates(
        "he's a teacher", prev_user_message="my wife's name is Emma"
    ) == []


def test_pronoun_fact_after_met_person_anchors_without_relation():
    out = extract_candidates(
        "he's a carpenter", prev_user_message="I met Steve at the market today"
    )
    assert len(out) == 1
    assert out[0].text == "Steve is a carpenter"


def test_pronoun_fact_never_anchors_to_a_pet():
    # Pet introductions are deliberately not person anchors.
    assert extract_candidates(
        "she's a good girl", prev_user_message="my dog is named Bella"
    ) == []


# ── current-turn extraction is unchanged when no prev is supplied ────────────

def test_plain_fact_extraction_unchanged():
    out = extract_candidates("my favourite recipe is lasagna")
    assert any("lasagna" in c.text.lower() for c in out)


def test_prev_message_alone_is_never_mined():
    # The prior turn only ANCHORS the current turn — a plain current message
    # must not resurrect facts from the previous one.
    out = extract_candidates("okay sounds good", prev_user_message=EMMA_PREV)
    assert out == []


# ── extract_and_ingest LRU plumbing (zero call-site changes) ─────────────────

class _FakeSvc:
    def __init__(self):
        self.ingested: list[dict] = []

    async def ingest(self, text, **kw):
        self.ingested.append({"text": text, **kw})
        return f"ref-{len(self.ingested)}"


@pytest.fixture
def fake_memory_service(monkeypatch):
    svc = _FakeSvc()
    mod = types.ModuleType("memory_service")
    mod.get_memory_service = lambda: svc
    monkeypatch.setitem(sys.modules, "memory_service", mod)
    # Isolate the LRU per test.
    monkeypatch.setattr(memory_extractor, "_prev_user_turns", type(
        memory_extractor._prev_user_turns
    )())
    return svc


@pytest.mark.asyncio
async def test_lru_threads_prior_turn_across_ingest_calls(fake_memory_service):
    svc = fake_memory_service
    await memory_extractor.extract_and_ingest(
        DENTIST_PREV, user_id="u1", session_id="s1", source="chat_regex"
    )
    await memory_extractor.extract_and_ingest(
        DENTIST_FIX, user_id="u1", session_id="s1", source="chat_regex"
    )
    texts = [row["text"].lower() for row in svc.ingested]
    assert any("saturday" in t and "dentist" in t for t in texts)
    assert not any("friday" in t for t in texts)


@pytest.mark.asyncio
async def test_lru_is_scoped_per_session(fake_memory_service):
    svc = fake_memory_service
    await memory_extractor.extract_and_ingest(
        DENTIST_PREV, user_id="u1", session_id="s1", source="chat_regex"
    )
    # Same user, DIFFERENT session — the correction must not cross sessions.
    await memory_extractor.extract_and_ingest(
        DENTIST_FIX, user_id="u1", session_id="s2", source="chat_regex"
    )
    assert not any("saturday" in row["text"].lower() for row in svc.ingested)


@pytest.mark.asyncio
async def test_lru_is_scoped_per_user(fake_memory_service):
    svc = fake_memory_service
    await memory_extractor.extract_and_ingest(
        EMMA_PREV, user_id="u1", session_id="s1", source="chat_regex"
    )
    await memory_extractor.extract_and_ingest(
        EMMA_FIX, user_id="u2", session_id="s1", source="chat_regex"
    )
    assert not any("doctor" in row["text"].lower() for row in svc.ingested)


@pytest.mark.asyncio
async def test_explicit_empty_prev_disables_anchoring(fake_memory_service):
    svc = fake_memory_service
    await memory_extractor.extract_and_ingest(
        DENTIST_PREV, user_id="u1", session_id="s1", source="chat_regex"
    )
    await memory_extractor.extract_and_ingest(
        DENTIST_FIX, user_id="u1", session_id="s1", source="chat_regex",
        prev_user_message="",
    )
    assert not any("saturday" in row["text"].lower() for row in svc.ingested)


def test_lru_is_bounded(monkeypatch):
    monkeypatch.setattr(memory_extractor, "_prev_user_turns", type(
        memory_extractor._prev_user_turns
    )())
    for i in range(memory_extractor._PREV_TURN_MAX + 50):
        memory_extractor.note_user_turn(f"user-{i}", "s", "hello there")
    assert len(memory_extractor._prev_user_turns) <= memory_extractor._PREV_TURN_MAX
