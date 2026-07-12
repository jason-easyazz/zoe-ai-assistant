"""Bare-role backstop for the per-turn LLM person extraction.

An UNANCHORED relationship value is poison: "Emily: wife" stored from a message
describing a FRIEND's family ranked #1 for "Who is my wife?" (live 2026-07-12).
These tests lock in: the prompt carries the qualification rule, the
_is_unanchored_role predicate, and the drop path (bare role never reaches
apply_person_fact; qualified values do).
"""
import json

import pytest

pytestmark = pytest.mark.ci_safe  # pure regex + monkeypatched HTTP/apply

import person_extractor_llm as pel


# ── predicate ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("value", [
    "wife", "the wife", "girl", "girls", "daughter", "male friend",
    "best friend", "a colleague", "kids",
])
def test_bare_roles_are_unanchored(value):
    assert pel._is_unanchored_role(value) is True


@pytest.mark.parametrize("value", [
    "wife of Lindsay Cannon", "user's friend", "Lindsay's wife", "his wife",
    "their daughter", "my friend",           # anchored relationships pass
    "software engineer", "born 26/10/1982", "likes hiking", "from Brazil",
    "allergic to nuts",                       # non-relationship facts pass
])
def test_anchored_or_non_role_values_pass(value):
    assert pel._is_unanchored_role(value) is False


# ── prompt carries the rule ──────────────────────────────────────────────────

def test_both_prompts_require_qualified_relationships():
    for prompt in (pel._EXTRACTION_PROMPT, pel._EXTRACTION_PROMPT_CONF):
        assert "whose relative" in prompt
        assert "wife of Lindsay Cannon" in prompt


# ── drop path: bare role never reaches apply_person_fact ────────────────────

class _Resp:
    status_code = 200
    def __init__(self, payload): self._p = payload
    def raise_for_status(self): pass
    def json(self):
        return {"choices": [{"message": {"content": json.dumps(self._p)}}]}


class _Client:
    def __init__(self, payload): self._p = payload
    async def __aenter__(self): return self
    async def __aexit__(self, *exc): return False
    async def post(self, *a, **k): return _Resp(self._p)


@pytest.mark.asyncio
async def test_bare_role_dropped_qualified_applied(monkeypatch):
    llm_items = [
        {"name": "Emily Cannon", "fact_type": "preference", "value": "wife"},                      # bare → drop
        {"name": "Emily Cannon", "fact_type": "preference", "value": "wife of Lindsay Cannon"},    # anchored → apply
        {"name": "Aria Cannon", "fact_type": "preference", "value": "girl"},                       # bare → drop
        {"name": "Lindsay Cannon", "fact_type": "birthday", "value": "26/10/1982"},                # non-role → apply
    ]
    monkeypatch.setattr(pel.httpx, "AsyncClient", lambda **k: _Client(llm_items))
    applied = []

    async def fake_apply(name, fact_type, value, **kw):
        applied.append((name, value))
        return True
    import person_extractor
    monkeypatch.setattr(person_extractor, "apply_person_fact", fake_apply)

    written = await pel.process_text_llm(
        "Here are the details of my friends family and their names",
        user_id="demo-roles",
    )
    assert written == 2
    assert ("Emily Cannon", "wife of Lindsay Cannon") in applied
    assert ("Lindsay Cannon", "26/10/1982") in applied
    assert ("Emily Cannon", "wife") not in applied
    assert ("Aria Cannon", "girl") not in applied


# ── user-anchor validation (memory_quality.user_relationship_claim_unsupported) ──

from memory_quality import user_relationship_claim_unsupported as _unsupported

_SRC = "No Lindsay is my male friend, Emily is the wife and Aria and Olivia are the girls"


@pytest.mark.parametrize("fact", [
    "Emily is the user's wife.", "Emily: wife of user",
    "Lindsay Cannon: husband of the speaker", "Aria is a girl in the user's life.",
])
def test_guessed_user_anchor_is_unsupported(fact):
    assert _unsupported(fact, _SRC) is True


@pytest.mark.parametrize("fact,src", [
    ("Lindsay is the user's male friend.", _SRC),          # source says "my male friend"
    ("Lindsay: male friend of user", _SRC),
    ("Emily Cannon is Lindsay Cannon's wife", _SRC),       # third-party anchor
    ("The user is allergic to nuts.", "I am allergic to nuts"),  # not a relationship
    ("User's wife is Emma", "I love my wife Emma dearly"), # supported user anchor
])
def test_supported_or_non_relationship_pass(fact, src):
    assert _unsupported(fact, src) is False


@pytest.mark.asyncio
async def test_guessed_user_anchor_dropped_in_llm_loop(monkeypatch):
    llm_items = [
        {"name": "Emily Cannon", "fact_type": "preference", "value": "wife of user"},   # guessed → drop
        {"name": "Lindsay Cannon", "fact_type": "preference", "value": "user's male friend"},  # supported → apply
    ]
    monkeypatch.setattr(pel.httpx, "AsyncClient", lambda **k: _Client(llm_items))
    applied = []

    async def fake_apply(name, fact_type, value, **kw):
        applied.append((name, value))
        return True
    import person_extractor
    monkeypatch.setattr(person_extractor, "apply_person_fact", fake_apply)

    written = await pel.process_text_llm(_SRC, user_id="demo-roles")
    assert written == 1
    assert applied == [("Lindsay Cannon", "user's male friend")]


# ── synonym support + bare-role rescue (Greptile r2) ─────────────────────────

def test_role_synonyms_support_user_anchor():
    # source "my mum" supports a fact phrased "mother"; "my girls" supports "daughter"
    assert _unsupported("User's mother is Janice", "my mum is Janice, born 1947") is False
    assert _unsupported("User's daughter is Aria", "I took my girls to school") is False
    # and a guessed anchor still drops
    assert _unsupported("Emily is the user's wife.", "Emily is the wife") is True


@pytest.mark.asyncio
async def test_bare_role_rescued_when_turn_supports_it(monkeypatch):
    """'No Lindsay is my male friend...' + LLM value 'male friend' (bare) must be
    RESCUED as \"user's male friend\", not dropped (Greptile P2)."""
    llm_items = [
        {"name": "Lindsay Cannon", "fact_type": "preference", "value": "male friend"},  # bare, supported → rescue
        {"name": "Emily Cannon", "fact_type": "preference", "value": "wife"},           # bare, unsupported → drop
    ]
    monkeypatch.setattr(pel.httpx, "AsyncClient", lambda **k: _Client(llm_items))
    applied = []

    async def fake_apply(name, fact_type, value, **kw):
        applied.append((name, value))
        return True
    import person_extractor
    monkeypatch.setattr(person_extractor, "apply_person_fact", fake_apply)

    written = await pel.process_text_llm(
        "No Lindsay is my male friend, Emily is the wife and the kids are girls",
        user_id="demo-roles",
    )
    assert written == 1
    assert applied == [("Lindsay Cannon", "user's male friend")]


# ── role-list sync + irregular plurals (Greptile r3) ─────────────────────────

def test_children_plural_supports_child_facts():
    assert _unsupported("User's child is Emily", "my children are Emily and Aria") is False
    assert _unsupported("User's children are Emily and Aria", "my children are Emily and Aria") is False


def test_colleague_class_roles_are_validated():
    # validator now knows colleague/coworker/boss/neighbour — a guessed anchor drops,
    # a supported one passes, and the pel rescue path can't sneak one through.
    assert _unsupported("user's colleague", "he introduced his colleague Bob") is True
    assert _unsupported("user's colleague", "I had lunch with my colleague Bob") is False
    assert _unsupported("user's neighbor", "my neighbour plays loud music") is False  # synonym pair


def test_nightly_batch_source_empty_drops_all_user_anchored_roles():
    """The nightly digest has no turn provenance (whole-day transcript), so it
    passes source_text=\"\" — EVERY user-anchored relationship fact must drop
    there, even ones a day-level grep would falsely support (Greptile r3)."""
    assert _unsupported("Emily is the user's wife.", "") is True
    assert _unsupported("User's mother is Janice", "") is True
    # non-relationship facts still pass with empty source
    assert _unsupported("The user is allergic to nuts.", "") is False


def test_boss_role_normalization():
    """rstrip('s') stripped ALL trailing s's ('boss' → 'bo'), breaking supported
    boss claims (Greptile r4). group(1) is already canonical — no strip needed."""
    assert _unsupported("user's boss", "my boss is Sarah and she is great") is False
    assert _unsupported("user's boss", "his boss is Sarah, a director") is True
    # plural-with-s roles still normalize via the regex itself
    assert _unsupported("User's kids are Aria and Olivia", "my kids are Aria and Olivia") is False


# ── head-noun bare gate + coworker synonym (Greptile r5) ─────────────────────

@pytest.mark.parametrize("value", ["male friend from work", "friend from school", "a colleague from the office"])
def test_role_head_with_tail_is_still_bare(value):
    assert pel._is_unanchored_role(value) is True


@pytest.mark.parametrize("value", ["great with kids", "loves her kids", "works with kids"])
def test_trait_phrases_are_not_relationship_claims(value):
    assert pel._is_unanchored_role(value) is False


def test_coworker_supports_colleague_fact():
    assert _unsupported("user's colleague", "my coworker Bob is fun") is False


def test_of_mine_is_a_user_anchor():
    """'friend of mine' is a USER anchor and must be validated like \"user's
    friend\" — not treated as third-party-qualified (Greptile r6)."""
    assert _unsupported("Bob is a friend of mine", "he introduced his friend Bob") is True
    assert _unsupported("Bob is a friend of mine", "my friend Bob came over") is False
    assert _unsupported("Emily Cannon is the wife of Lindsay", "Emily is Lindsay's wife") is False


def test_my_in_fact_and_of_mine_in_source(  # Greptile r7 mirror cases
):
    # fact-side: "my friend" in a STORED fact is a user anchor → validated
    assert _unsupported("Bob: my friend", "he introduced his friend Bob") is True
    assert _unsupported("Bob: my friend", "my friend Bob came over") is False
    # source-side: "a friend of mine" phrasing supports a user's-friend fact
    assert _unsupported("user's friend", "a friend of mine, Bob, came over") is False
    # genuine self-stated facts keep passing
    assert _unsupported("My dad's name is Neil", "my dad is Neil, born 1945") is False


def test_compound_roles_require_full_support():
    """'user's wife and daughter' from a turn saying only 'my daughter Emily'
    must drop — one supported role can't carry an unsupported one (Greptile r8)."""
    assert _unsupported("Emily: user's wife and daughter", "my daughter Emily is here") is True
    assert _unsupported("Emily: user's wife and daughter", "my wife and my daughter Emily") is False


def test_parent_sibling_grandparent_roles_validated():
    """parent/sibling/grandparent were missing from the role vocab (Greptile r11)."""
    assert _unsupported("user's parent", "her parents visited today") is True
    assert _unsupported("User's parents live nearby", "my parents live nearby") is False
    assert pel._is_unanchored_role("sibling") is True


def test_generic_roles_supported_by_specific_forms():
    """Directional hyponyms (Greptile r12): 'my mum' supports \"user's parent\",
    'my sister' supports sibling, 'my son' supports kid — but specifics never
    cross-support each other ('my dad' must not support \"user's mother\")."""
    assert _unsupported("User's parent is Janice", "my mum is Janice") is False
    assert _unsupported("User's sibling is Karen", "my sister Karen visited") is False
    assert _unsupported("User's kid loves soccer", "my son loves soccer") is False
    assert _unsupported("User's mother is Janice", "my dad is Neil") is True
    assert _unsupported("user's parent", "her parents visited") is True
