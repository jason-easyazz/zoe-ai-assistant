"""Samantha bar S21 - a question that NAMES a known person always reaches memory.

The bar's S21 ("a correction reaches the record") still failed with ZOE_CORRECTION_APPLY=1
because the follow-up ask "How many children does Dana Whitfield have?" matched no recall-floor
shape (no my/I, no event verb, no own-fact noun): the brain answered "I don't have any
information about Dana Whitfield's children" and recall_memory was called in 0 of 3 recorded
runs (docs/research/samantha-flags-ab-2026-10-06.md). ZOE_PERSON_RECALL_FLOOR forces the
for-prompt packet (and the people-graph block) when a question-shaped sentence names a person
THIS user knows.

This file: the pure matcher, the flag modes, the seam (chat AND the voice-mode turn), the
size caps. The SQL half (user isolation, the focus reads, the endpoint) is
test_person_recall_floor_db.py. Every name is synthetic.
"""
from __future__ import annotations

import json
import logging

import pytest

import memory_gate as mg
import person_recall_floor as prf
import zoe_flue_client as zc

pytestmark = pytest.mark.ci_safe  # fakes only: no DB, no model, no live service

S21_ASK = "How many children does Dana Whitfield have?"
KNOWN = ["Dana Whitfield", "Mika", "Priya Nair", "Sam", "Samantha Cole", "Will Turner", "Dana Reyes"]


# ── the pure matcher ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("q,want", [
    (S21_ASK, ["Dana Whitfield"]),
    ("how many kids does dana whitfield have", ["Dana Whitfield"]),            # transcript, no caps
    ("what's Mika's favourite colour", ["Mika"]),                               # possessive
    ("When is Priya Nair's birthday?", ["Priya Nair"]),
    ("When is Priya's birthday?", ["Priya Nair"]),                              # unique first name
    ("what did samantha say", ["Samantha Cole"]),                               # caseless first name
    ("How many children does Dana Reyes have?", ["Dana Reyes"]),
    ("Is Mika coming and does Priya Nair know?", ["Mika", "Priya Nair"]),       # in order of mention
])
def test_a_known_person_is_named(q, want):
    assert mg.person_names_in_question(q, KNOWN) == want


@pytest.mark.parametrize("q", [
    "How many children does my friend have?",        # no name at all
    "Does Dana have kids?",                          # two Danas: an ambiguous first name names nobody
    "does dana have kids",
    "Will it rain tomorrow?",                        # a function word is not Will Turner
])
def test_nobody_is_named(q):
    assert mg.person_names_in_question(q, KNOWN) == []


def test_sam_is_sam_and_not_samantha():
    """Exact whole-name matching: the LIKE %Sam% resolver this replaces would link both."""
    assert mg.person_names_in_question("How old is Sam?", KNOWN) == ["Sam"]
    assert mg.person_names_in_question("How old is Samantha?", KNOWN) == ["Samantha Cole"]
    assert mg.person_names_in_question("How old is Samuel?", KNOWN) == []


def test_a_lowercase_one_word_name_in_typed_text_is_not_a_name():
    # mixed case: "dana" lower-case among capitals is a word, not a person
    assert mg.person_names_in_question("Is the dana pizza place on Main Street open?", ["Dana Whitfield"]) == []
    assert mg.person_names_in_question("is the dana pizza place open", ["Dana Whitfield"]) == ["Dana Whitfield"]


def test_at_most_three_people_are_named():
    names = [f"Person{c} Test" for c in "ABCDE"]
    q = "Do " + ", ".join(names) + " all know each other?"
    assert len(mg.person_names_in_question(q, names)) == mg.PERSON_FLOOR_MAX_NAMED


@pytest.mark.parametrize("q,want", [
    (S21_ASK, True), ("how many kids does dana have", True), ("Tell me about Dana", True),
    ("Dana Whitfield has two kids, Mika and Biscuit.", False),       # a statement is stored, not asked
    ("Text Dana hello", False),
    ("How do I add Dana to my contacts?", False),                     # a how-to
])
def test_only_question_shaped_sentences_count(q, want):
    assert bool(mg.person_question_sentences(q)) is want


def test_candidate_names_are_capitalised_runs():
    assert mg.person_candidate_names("Hey Zoe, when is Priya Nair's birthday?") == ["Priya Nair"]
    assert mg.person_candidate_names("how many kids does dana have") == []


@pytest.mark.parametrize("raw,want", [
    (None, "enforce"), ("", "off"), ("0", "off"), ("false", "off"), ("OFF", "off"),
    ("1", "enforce"), ("true", "enforce"), ("enforce", "enforce"), ("shadow", "shadow"), (" Shadow ", "shadow"),
])
def test_floor_mode(monkeypatch, raw, want):
    monkeypatch.delenv(mg.PERSON_FLOOR_ENV, raising=False)
    if raw is not None:
        monkeypatch.setenv(mg.PERSON_FLOOR_ENV, raw)
    assert mg.person_floor_mode() == want


# ── resolve_named_people: modes, scope, log line ──────────────────────────────

@pytest.fixture
def people(monkeypatch):
    """The resolver's two reads, faked per user - the SQL is exercised in the _db file."""
    owned = {"demo-owner": [("pid-dana", "Dana Whitfield"), ("pid-mika", "Mika")]}
    calls = []

    async def known(uid):
        calls.append(uid)
        return list(owned.get(uid, []))

    async def pending(uid, cands):
        return []

    monkeypatch.setattr(prf, "_known_people", known)
    monkeypatch.setattr(prf, "_pending_entity_names", pending)
    monkeypatch.delenv(mg.PERSON_FLOOR_ENV, raising=False)
    return calls


async def test_the_s21_ask_resolves_the_named_person_and_logs(people, caplog):
    caplog.set_level(logging.INFO, logger=prf.__name__)
    got = await prf.resolve_named_people(S21_ASK, "demo-owner")
    assert got == [prf.NamedPerson("Dana Whitfield", "pid-dana", "people")]
    line = next(r.getMessage() for r in caplog.records if "RECALL_FLOOR" in r.getMessage())
    assert "reason=named_person" in line and "mode=enforce" in line and "forced=1" in line
    assert "Dana" not in line, "the log carries counts, never the name"


async def test_no_name_no_floor_and_no_log(people, caplog):
    caplog.set_level(logging.INFO, logger=prf.__name__)
    assert await prf.resolve_named_people("How many children does my friend have?", "demo-owner") == []
    assert not [r for r in caplog.records if "RECALL_FLOOR" in r.getMessage()]


async def test_a_stranger_naming_the_owners_contact_gets_nothing(people):
    assert await prf.resolve_named_people(S21_ASK, "demo-stranger") == []
    assert people == ["demo-stranger"], "only the asker's own people are ever read"


@pytest.mark.parametrize("uid", ["", "guest", "voice-guest", "anonymous", "  "])
async def test_guests_are_never_resolved(people, uid):
    assert await prf.resolve_named_people(S21_ASK, uid) == []
    assert people == []


async def test_off_reads_nothing(people, monkeypatch, caplog):
    monkeypatch.setenv(mg.PERSON_FLOOR_ENV, "off")
    caplog.set_level(logging.INFO, logger=prf.__name__)
    assert await prf.resolve_named_people(S21_ASK, "demo-owner") == []
    assert people == [] and not [r for r in caplog.records if "RECALL_FLOOR" in r.getMessage()]


async def test_shadow_logs_what_it_would_force_and_forces_nothing(people, monkeypatch, caplog):
    monkeypatch.setenv(mg.PERSON_FLOOR_ENV, "shadow")
    caplog.set_level(logging.INFO, logger=prf.__name__)
    assert await prf.resolve_named_people(S21_ASK, "demo-owner") == []
    line = next(r.getMessage() for r in caplog.records if "RECALL_FLOOR" in r.getMessage())
    assert "reason=named_person" in line and "mode=shadow" in line and "forced=0" in line


async def test_a_failing_store_is_no_floor_not_a_failed_turn(monkeypatch):
    async def boom(*args):
        raise RuntimeError("store down")

    monkeypatch.setattr(prf, "_known_people", boom)
    monkeypatch.setattr(prf, "_pending_entity_names", boom)
    assert await prf.resolve_named_people(S21_ASK, "demo-owner") == []


async def test_a_person_fact_entity_with_no_contact_row_is_named(people, monkeypatch):
    seen = []

    async def pending(uid, cands):
        seen.append((uid, cands))
        return ["Priya Nair"] if uid == "demo-owner" else []

    monkeypatch.setattr(prf, "_pending_entity_names", pending)
    got = await prf.resolve_named_people("When is Priya Nair's job?", "demo-owner")
    assert got == [prf.NamedPerson("Priya Nair", "", "memory")]
    assert prf.focus_ids(got) == []
    assert seen == [("demo-owner", ["Priya Nair"])]


async def test_a_mood_statement_sentence_is_continuitys_not_the_floors(people):
    q = "I'm so anxious about Dana Whitfield?"
    own = zc._continuity_owns_sentence
    assert own(q)
    assert await prf.resolve_named_people(q, "demo-owner", exclude=own) == []
    assert await prf.resolve_named_people(q, "demo-owner") != []


# ── the seam: chat, and the voice-mode turn ──────────────────────────────────

PACKET = (
    "## What I know about you\n"
    "- Dana Whitfield has one kid, Mika. [mem:aaaa1111]\n"
    "\n"
    "## People & important dates\n"
    "- Dana Whitfield (friend) [people]\n"
    "- Mika's parent is Dana Whitfield [relationship]\n"
)


class _Resp:
    def raise_for_status(self):
        return None

    def json(self):
        return {"result": {"text": "ok"}}


class _Client:
    captured: dict = {}

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, content=None, headers=None):
        type(self).captured["content"] = content
        return _Resp()


async def _outbound(monkeypatch, message, uid="demo-owner", **kw):
    monkeypatch.setenv("ZOE_FLUE_WIRE", "1")
    monkeypatch.setattr(_Client, "captured", {})
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    fetched = []

    async def fake_fetch(user_id, msg, focus=None):
        fetched.append((user_id, msg, [p.name for p in focus or ()], prf.focus_ids(focus or ())))
        return PACKET

    monkeypatch.setattr(zc, "_fetch_for_prompt_packet", fake_fetch)

    async def no_continuity(*a, **k):
        return ""

    monkeypatch.setattr(zc, "_continuity_context_block", no_continuity)
    out = [c async for c in zc.run_flue_brain_streaming(message, "s21", uid, **kw)]
    assert out == ["ok"]
    return json.loads(_Client.captured["content"])["message"], fetched


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("ZOE_SEAM_RECALL_INJECT", "1")
    monkeypatch.delenv(mg.PERSON_FLOOR_ENV, raising=False)
    monkeypatch.delenv("ZOE_SEAM_CONTINUITY_INJECT", raising=False)


@pytest.mark.parametrize("voice", [False, True])
async def test_the_s21_ask_gets_the_memory_block_and_the_relational_block(people, monkeypatch, voice):
    """The exact S21 ask. Before this floor: shape "" -> no packet fetch at all."""
    assert zc._recall_floor_shape(S21_ASK) == "", "no grammatical shape claims it - that was the gap"
    msg, fetched = await _outbound(monkeypatch, S21_ASK, voice_mode=voice)
    assert fetched == [("demo-owner", S21_ASK, ["Dana Whitfield"], ["pid-dana"])]
    first, rest = msg.split("\n", 1)
    assert first == " zoe-uid:demo-owner"
    assert rest.startswith(zc._RECALL_BLOCK_OPEN)
    block = rest[: rest.index(zc._RECALL_BLOCK_CLOSE)]
    assert "Dana Whitfield has one kid, Mika." in block
    assert "Mika's parent is Dana Whitfield [relationship]" in block
    assert rest.endswith(S21_ASK)


async def test_a_question_with_no_name_gets_no_floor(people, monkeypatch):
    q = "How many children does my friend have?"
    msg, fetched = await _outbound(monkeypatch, q)
    assert msg == f" zoe-uid:demo-owner\n{q}"
    assert fetched == []


async def test_a_stranger_gets_no_block_for_the_owners_contact(people, monkeypatch):
    msg, fetched = await _outbound(monkeypatch, S21_ASK, uid="demo-stranger")
    assert msg == f" zoe-uid:demo-stranger\n{S21_ASK}"
    assert fetched == []


@pytest.mark.parametrize("env", [("ZOE_PERSON_RECALL_FLOOR", "off"), ("ZOE_PERSON_RECALL_FLOOR", "shadow"),
                                 ("ZOE_SEAM_RECALL_INJECT", "0")])
async def test_off_shadow_and_the_master_switch_leave_the_outbound_message_byte_identical(people, monkeypatch, env):
    monkeypatch.setenv(*env)
    msg, fetched = await _outbound(monkeypatch, S21_ASK)
    assert msg == f" zoe-uid:demo-owner\n{S21_ASK}"
    assert fetched == []


async def test_a_grammatical_shape_still_wins_and_never_looks_a_person_up(people, monkeypatch):
    q = "what's my locker code?"
    msg, fetched = await _outbound(monkeypatch, q)
    assert fetched == [("demo-owner", q, [], [])]
    assert people == [], "a claimed turn costs no extra read"


# ── the size caps ─────────────────────────────────────────────────────────────

def test_the_relational_block_keeps_a_reserved_slice_under_the_same_caps():
    vector = "## What I know about you\n(header)\n" + "\n".join(f"- vector fact {i} " + "x" * 80 for i in range(14))
    rel = "## People & important dates\n" + "\n".join(f"- relational line {i} [relationship]" for i in range(9))
    packet = vector + "\n\n" + rel
    plain = zc._truncate_packet(packet)
    assert "relational line 0" not in plain, "the old cap cuts the people-graph block off whole"
    kept = zc._truncate_packet_keeping_relational(packet)
    assert "relational line 0" in kept and "relational line 5" in kept and "relational line 6" not in kept
    bullets = sum(1 for ln in kept.splitlines() if ln.startswith("- "))
    assert bullets <= zc._RECALL_MAX_BULLETS
    assert len(kept) <= zc._RECALL_MAX_CHARS
    assert kept.index("vector fact 0") < kept.index("relational line 0")


def test_a_packet_without_a_relational_section_truncates_as_before():
    packet = "## What I know about you\n" + "\n".join(f"- fact {i}" for i in range(20))
    assert zc._truncate_packet_keeping_relational(packet) == zc._truncate_packet(packet)
