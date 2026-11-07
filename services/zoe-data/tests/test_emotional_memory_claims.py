"""An emotional turn never gets a promise-then-failure (QA round 3b, 2026-11-07).

Live, a synthetic demo user told Zoe about a relative's fall and "I just keep worrying". The brain's emotional-capture doctrine
said it MUST call remember_emotional_moment, but the tool was only disclosed by a sidecar regex that missed the phrasing - so
she SAID "I'm going to remember...", and the write-claim guard replaced it, mid-disclosure, with the LIST line "I didn't
manage to add that; want me to try again?". Other memory promises ("I'm going to make sure I remember", "I've updated my
notes", "I've cleared that thought") with no tool call passed the guard untouched.

  * one emotional-turn test (memory_gate.is_emotional_turn) over ONE cue file the sidecar reads too
  * on an emotional turn a false memory / note claim is DROPPED (never the list line); a whole-claim reply gets a quiet line
  * a blocked memory / note claim on any other turn gets MEMORY wording, never "add that"
  * the memory promise forms are claims (data), and a real write still backs them

Neutral synthetic phrasing; pure logic (``ci_safe``).
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

import memory_gate
import write_claim_guard as g
import write_receipts as wr

pytestmark = pytest.mark.ci_safe

UID = "demo_bar_0000em01"
FALL = "yeah. and my uncle back home had a fall last week. he says he's fine but he lives alone and i just keep worrying"
LIST_LINE = "I didn't manage to add that; want me to try again?"
SAVE_LINE = "I didn't manage to save that; want me to try again?"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("ZOE_WRITE_CLAIM_GUARD", raising=False)
    wr.reset()
    yield
    wr.reset()


def guard(reply, user_text):
    return g.guard_text(reply, UID, wr.begin(), user_text=user_text)


def stream(reply, user_text, *, cut=7):
    async def inner():
        for i in range(0, len(reply), cut):
            yield reply[i:i + cut]

    async def collect():
        return "".join([p async for p in g.guard_stream(inner(), UID, wr.begin(), user_text=user_text)])
    return asyncio.run(collect())


# ── one test for "this turn carries feeling" ─────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    FALL,
    "just the embarrassment i guess. everyone looked at me",
    "i feel guilty being so far away",
    "my boss embarrassing me at work",
    "I've been really stressed about the move",
    "I’m so proud of her",                       # a phone keyboard's curly apostrophe
])
def test_everyday_feeling_phrasing_is_an_emotional_turn(text):
    assert memory_gate.is_emotional_turn(text)


@pytest.mark.parametrize("text", [
    "what's the weather tomorrow?", "add milk to the shopping list", "i'm feeling like pizza tonight",
    "I'm going through my email", "",
])
def test_small_talk_is_not(text):
    assert not memory_gate.is_emotional_turn(text)


def test_the_cues_are_one_data_file_the_sidecar_reads_too():
    path = Path(memory_gate.__file__).parent / "lexicons_data" / "emotional_cues.json"
    data = json.loads(path.read_text("utf-8"))["en"]
    assert memory_gate.EMOTIONAL_TRIGGER_WORDS == frozenset(data["recall_cues"])
    assert memory_gate.EMOTIONAL_WEIGHT_WORDS == frozenset(data["weight_cues"])
    # the deliberate exclusions of the recall set survive the move to data
    assert "feeling" not in memory_gate.EMOTIONAL_TRIGGER_WORDS and "going through" not in memory_gate.EMOTIONAL_TRIGGER_WORDS
    assert "how have i been" in memory_gate.EMOTIONAL_TRIGGER_WORDS
    sidecar = (Path(memory_gate.__file__).parents[2] / "labs" / "flue-zoe-brain-2x" / "src" / "tools" / "emotional-cues.ts")
    assert "services', 'zoe-data', 'lexicons_data', 'emotional_cues.json'" in sidecar.read_text("utf-8")


# ── on an emotional turn: drop the false promise, never the list line ────────────────────────────────────────────────

PROMISE = "I'm going to remember that your uncle had a fall."


def test_emotional_turn_drops_the_false_memory_claim():
    out = guard("I'm so sorry about your uncle; that worry makes sense. " + PROMISE, FALL)
    assert out == "I'm so sorry about your uncle; that worry makes sense."
    assert "add that" not in out and "manage" not in out


def test_negative_control_the_same_claim_on_a_plain_turn_gets_memory_wording():
    out = guard("Sure. I'm going to remember that your uncle is called Sam.", "my uncle is called Sam")
    assert SAVE_LINE in out and LIST_LINE not in out


def test_a_whole_claim_reply_on_an_emotional_turn_gets_the_quiet_line():
    assert guard(PROMISE, FALL) == "I'm here, and I'm listening."


def test_stream_matches_text_on_an_emotional_turn():
    reply = "I'm so sorry about your uncle; that worry makes sense. " + PROMISE
    assert stream(reply, FALL).strip() == guard(reply, FALL)
    assert stream(PROMISE, FALL) == "I'm here, and I'm listening."


def test_a_real_write_still_backs_the_promise():
    started = wr.begin()
    wr.record(UID, "memory", "row-1", "memory_store")
    reply = "I'm so sorry. " + PROMISE
    assert g.guard_text(reply, UID, started, user_text=FALL) == reply


def test_a_list_claim_on_an_emotional_turn_keeps_its_honest_line():
    out = guard("I've added tissues to your shopping list.", FALL)
    assert out == LIST_LINE


# ── the memory claim forms (data) ────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("sentence, kinds", [
    ("I'm going to remember how much that stung for you.", ("memory",)),
    ("I'm going to make sure I remember those things for you.", ("memory",)),
    ("I'll make sure I remember that.", ("memory",)),
    ("I've updated my notes.", ("note",)),
    ("I've updated my memory with that.", ("memory",)),
    ("I've also cleared that thought.", ("forget",)),
])
def test_memory_promise_forms_are_claims(sentence, kinds):
    claim = g.find_claim(sentence)
    assert claim is not None and claim.kinds == kinds, (sentence, claim)


@pytest.mark.parametrize("sentence", [
    "I'm going to be honest with you.",
    "I'm going to the shops later.",
    "Are you going to remember to call him?",
    "I'm not going to remember that.",
    "Do you want me to remember that?",
    "Going to the beach sounds lovely.",
])
def test_non_claims_stay_non_claims(sentence):
    assert g.find_claim(sentence) is None, sentence


def test_a_blocked_note_claim_gets_save_wording_not_list_wording():
    claim = g.find_claim("I've updated my notes.")
    assert claim.honest == SAVE_LINE
    assert g.find_claim("I've made a note of that.").honest == SAVE_LINE
