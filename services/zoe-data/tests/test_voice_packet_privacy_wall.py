"""The voice recall packet's privacy wall (person-centred memory D6; open-problems 2026-11-04).

The panel is a shared room: a member's PRIVATE classes (health, money, grief, family trouble, feelings; a curated
health / medication / money note) are counted, never read, on a voice turn - unless the speaker gate VERIFIED this voice
and the words name the class. ``routers/voice_tts._voice_recall_packet`` routes every row (searched facts, relational lines,
the fallback dump) through the SAME ``restraint.apply_to_packet`` the chat packet uses, on ``restraint.VOICE_SURFACE``.

Each wall test has a twin that bypasses the wall and shows the same fixture DOES reach the packet without it (the
negative control: the assertions are not vacuous).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest

import restraint
import routers.voice_tts as v

pytestmark = pytest.mark.ci_safe

MEMBER = "member-a"
HEALTH = "Was prescribed medication for asthma"
MONEY = "Has a car loan with repayments every month"
FEELING = "Felt worried before the exam"
PLAIN = "Favourite colour is green"
PLAIN_2 = "Lives near the beach"
CURATED = "Takes the blue tablet at eight"          # names no illness: only the curated category makes it private


@dataclass
class _Ref:
    text: str
    id: str = ""
    metadata: dict = field(default_factory=dict)
    score: float = 0.0


def _rows() -> list:
    return [
        _Ref(HEALTH, "h1"),
        _Ref(MONEY, "m1"),
        _Ref(FEELING, "f1", {"memory_type": "emotional_moment"}),
        _Ref(CURATED, "c1", {"origin": "curator", "curated_category": "medication", "curated_sensitive": True}),
        _Ref(PLAIN, "p1"),
        _Ref(PLAIN_2, "p2"),
    ]


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    import memory_service

    async def _no_mutes(user_id):
        return []

    async def _no_relational(query, user_id):
        return []

    monkeypatch.setattr(restraint, "list_mutes", _no_mutes)
    monkeypatch.setattr(memory_service, "is_guest_memory_user", lambda u: u == "guest")
    monkeypatch.setattr(v, "_voice_relational_lines", _no_relational)
    monkeypatch.delenv("ZOE_RESTRAINT", raising=False)       # the live default: shadow
    restraint._reset_state()
    yield
    restraint._reset_state()


def _search(monkeypatch, refs):
    import memory_service

    class _Svc:
        async def search(self, query, *, user_id, limit=10, timeout_s=2.0):
            return list(refs)

    monkeypatch.setattr(memory_service, "get_memory_service", lambda: _Svc())


def _packet(text: str, verdict=None) -> str:
    async def _turn():
        restraint.bind_verdict(verdict)
        return await v._voice_recall_packet(text, MEMBER)

    return asyncio.run(_turn()) or ""


def _bypass_wall(monkeypatch):
    async def _passthrough(user_id, message, facts, hits, recent=None, **kw):
        return facts, hits, recent

    monkeypatch.setattr(restraint, "apply_to_packet", _passthrough)


# ── the wall ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("verdict", [None, False, True])
def test_private_rows_never_reach_an_unpulling_voice_turn(monkeypatch, verdict):
    _search(monkeypatch, _rows())
    block = _packet("tell me something", verdict)
    for private in (HEALTH, MONEY, FEELING, CURATED):
        assert private not in block
    assert PLAIN in block and PLAIN_2 in block, "ordinary rows must still ride the voice packet"


def test_negative_control_without_the_wall_the_private_rows_reach_the_packet(monkeypatch):
    _bypass_wall(monkeypatch)
    _search(monkeypatch, _rows())
    block = _packet("tell me something", None)
    for private in (HEALTH, MONEY, FEELING, CURATED):
        assert private in block


@pytest.mark.parametrize("verdict", [None, False])
def test_named_but_unverified_voice_hears_nothing_private(monkeypatch, verdict):
    # D6: pulled by name AND only by a verified owner. A PIN-bound panel with no voice verdict is not a verified VOICE.
    _search(monkeypatch, _rows())
    block = _packet("what medication am I on", verdict)
    assert HEALTH not in block and CURATED not in block
    assert PLAIN in block


def test_verified_voice_naming_the_class_hears_it(monkeypatch):
    _search(monkeypatch, _rows())
    block = _packet("what medication am I on", True)
    assert HEALTH in block and CURATED in block, "the member's own pull by name, by a verified voice, is answered"
    assert MONEY not in block and FEELING not in block, "a pull names ONE class; the others stay counted"


@pytest.mark.parametrize("text", ["what's up?", "I'm feeling really down"])
def test_open_question_or_mood_is_not_a_pull_on_voice(monkeypatch, text):
    # stricter than the chat packet: there an open question pulls everything and a mood statement pulls affect rows
    _search(monkeypatch, _rows())
    block = _packet(text, True)
    for private in (HEALTH, MONEY, FEELING, CURATED):
        assert private not in block


def test_mood_turn_sharing_topic_words_does_not_pull_a_feelings_row(monkeypatch):
    # the boundary the cross-review raised: the mood turn SHARES words with the feelings row ("worried", "exam")
    _search(monkeypatch, _rows())
    assert "affect" in restraint.make_turn("I'm worried about the exam").classes
    assert restraint.make_turn("I'm worried about the exam").stems & restraint.stems(FEELING), "fixture must overlap"
    block = _packet("I'm worried about the exam", True)
    assert FEELING not in block


def test_a_plain_topic_question_by_a_verified_voice_pulls_the_feelings_row(monkeypatch):
    # not a mood statement: the member asks about the exam itself, so the topic overlap is a pull by name
    _search(monkeypatch, _rows())
    assert "affect" not in restraint.make_turn("what happened before the exam").classes
    block = _packet("what happened before the exam", True)
    assert FEELING in block
    assert HEALTH not in block and MONEY not in block


@pytest.mark.parametrize("text", [
    "What did I tell you about feeling worried before the exam?",
    "what did i tell you about feeling worried before the exam",     # STT with no question mark
    "do you remember how worried I was before the exam",
])
def test_a_question_about_a_past_feeling_by_a_verified_voice_pulls_it(monkeypatch, text):
    # PR review: an explicit recall classes as affect too, but it is a question about the past, not a mood statement
    _search(monkeypatch, _rows())
    turn = restraint.make_turn(text)
    assert "affect" in turn.classes and turn.recall
    block = _packet(text, True)
    assert FEELING in block
    assert HEALTH not in block and MONEY not in block
    assert FEELING not in _packet(text, None), "still only for a verified voice"


@pytest.mark.parametrize("text,want", [
    ("I'm worried about the exam", False), ("I am so worried about the exam", False), ("feeling low today", False),
    ("what did I say about the exam", True), ("remind me what happened", True), ("is it raining?", False),
    ("why am I feeling worried about the exam?", False), ("am I worried about the exam?", False),
])
def test_is_recall_ask(text, want):
    assert restraint.is_recall_ask(text) is want


@pytest.mark.parametrize("mode", ["off", "shadow", "enforce"])
def test_the_wall_bites_in_every_restraint_mode(monkeypatch, mode):
    monkeypatch.setenv("ZOE_RESTRAINT", mode)
    _search(monkeypatch, _rows())
    block = _packet("tell me something", None)
    assert HEALTH not in block and CURATED not in block
    assert PLAIN in block


def test_private_relational_line_is_withheld_and_an_ordinary_one_kept(monkeypatch):
    async def _lines(query, user_id):
        return ["Sam is your brother", "Sam is in hospital after the surgery"]

    monkeypatch.setattr(v, "_voice_relational_lines", _lines)
    _search(monkeypatch, [])
    block = _packet("tell me about Sam", None)
    assert "Sam is your brother" in block
    assert "surgery" not in block


def test_fallback_dump_passes_the_wall(monkeypatch):
    import zoe_agent

    async def _refs(user_id, limit=20):
        return _rows()

    monkeypatch.setattr(zoe_agent, "_mempalace_user_fact_refs", _refs)
    _search(monkeypatch, [])                     # nothing relevant: the for-prompt dump is used
    block = _packet("how's it going", None)
    assert PLAIN in block
    for private in (HEALTH, MONEY, FEELING, CURATED):
        assert private not in block
    # the prewarm path (no words) pulls nothing either
    assert HEALTH not in _packet("", None)


def test_negative_control_fallback_without_the_wall(monkeypatch):
    import zoe_agent

    async def _refs(user_id, limit=20):
        return _rows()

    _bypass_wall(monkeypatch)
    monkeypatch.setattr(zoe_agent, "_mempalace_user_fact_refs", _refs)
    _search(monkeypatch, [])
    block = _packet("how's it going", None)
    assert HEALTH in block and CURATED in block


def test_wall_fails_closed(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("classifier down")

    monkeypatch.setattr(restraint, "row_classes", _boom)
    _search(monkeypatch, _rows())
    block = _packet("tell me something", True)
    for private in (HEALTH, MONEY, FEELING, CURATED):
        assert private not in block


# ── curated rows (#2158): the filing is private whatever the words say ─────────────────────────────────────────────────────────
@pytest.mark.parametrize("meta,want", [
    ({"curated_category": "medication"}, "health"),
    ({"curated_category": "Health"}, "health"),
    ({"curated_category": "money"}, "money"),
    ({"curated_sensitive": True}, "health"),          # sensitive with no known category: still private
    ({"curated_sensitive": "true"}, "health"),
    ({"memory_type": "health"}, "health"),
])
def test_row_classes_honour_the_curated_filing(meta, want):
    assert want in restraint.row_classes(meta, CURATED)


@pytest.mark.parametrize("meta", [{}, {"curated_category": "school"}, {"curated_category": None},
                                  {"curated_sensitive": False}, {"curated_category": 7}])
def test_row_classes_absent_or_ordinary_filing_adds_nothing(meta):
    assert restraint.row_classes(meta, PLAIN) == ()


def test_a_stored_label_cannot_clear_the_curated_filing():
    meta = {"curated_category": "medication", "sensitivity": "", "sensitivity_v": restraint.VERSION,
            "sensitivity_h": restraint.text_hash(CURATED)}
    assert "health" in restraint.row_classes(meta, CURATED)


# ── the chat packet is unchanged ──────────────────────────────────────────────────────────────────────────────────────────────
def _chat(rows, message, verdict=None):
    async def _go():
        restraint.bind_verdict(verdict)
        return await restraint.apply_to_packet(MEMBER, message, list(rows), [], None)

    return asyncio.run(_go())


def test_chat_shadow_keeps_every_row():
    facts, hits, recent = _chat(_rows(), "tell me something")
    assert [r.text for r in facts] == [r.text for r in _rows()] and hits == [] and recent is None


def test_chat_enforce_keeps_its_own_rules(monkeypatch):
    monkeypatch.setenv("ZOE_RESTRAINT", "enforce")
    # an open question pulls everything on chat (not on voice)
    facts, _, _ = _chat(_rows(), "what's up?")
    assert len(facts) == len(_rows())
    # a topic pull with no voice verdict is answered on chat
    facts, _, _ = _chat(_rows(), "what medication am I on")
    assert HEALTH in [r.text for r in facts]
    # unpulled: the sensitive rows (now including the curated one) wait
    facts, _, _ = _chat(_rows(), "tell me something")
    kept = [r.text for r in facts]
    assert PLAIN in kept and HEALTH not in kept and CURATED not in kept


def test_chat_off_is_a_no_op(monkeypatch):
    monkeypatch.setenv("ZOE_RESTRAINT", "off")
    rows = _rows()
    facts, hits, recent = _chat(rows, "tell me something")
    assert [r.text for r in facts] == [r.text for r in rows]


@pytest.mark.parametrize("text,want", [
    ("¿Qué te dije sobre estar preocupado por el examen?", True), ("Te acuerdas de lo que dije del examen", True),
    ("Estoy preocupado por el examen", False), ("¿Por qué estoy preocupado por el examen?", False),
    ("Qu'est-ce que je t'ai dit sur l'examen ?", True), ("Rappelle-moi ce que j'ai dit", True),
    ("Je suis inquiet pour l'examen", False),
    ("Was habe ich dir über die Prüfung gesagt?", True), ("Weißt du noch, was ich gesagt habe?", True),
    ("Ich bin besorgt wegen der Prüfung", False),
    ("我之前跟你说过什么关于考试", True), ("你还记得我说过的话吗", True), ("我很担心考试", False),
    ("私が前に試験のことを何と言ったか覚えてる", True), ("何て言ったっけ、試験のこと", True), ("試験が心配です", False),
])
def test_is_recall_ask_in_the_other_language_packs(text, want):
    """Codex #2289: recall_leads is populated for every shipped pack, so a verified speaker recalling a past feeling in es / fr / de /
    zh / ja is not treated as a mood disclosure (negative controls: present-tense statements and why-questions stay False)."""
    assert restraint.is_recall_ask(text) is want
