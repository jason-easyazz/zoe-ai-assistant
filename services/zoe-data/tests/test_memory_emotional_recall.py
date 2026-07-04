"""Emotional-thread recall wiring (Samantha criterion #2), default OFF behind
ZOE_EMOTIONAL_RECALL_ENABLED.

Two pieces, both no-ops until the flag is on:
  1. `message_needs_emotional_recall` — an emotional-state cue ("how have I been",
     "feeling overwhelmed", "what made me happy") the base recall gate misses.
     Kept SEPARATE from `message_needs_memory` so the default gate is unchanged.
  2. `_build_memory_prompt_packet(boost_emotional=True)` — floats stored
     `emotional_moment` rows (by intensity) ahead of plain facts in the generic
     section, so a heavy user's emotional continuity isn't crowded out of the
     small packet.
"""
from types import SimpleNamespace

import pytest

from memory_gate import message_needs_emotional_recall, message_needs_memory
from routers.memories import _build_memory_prompt_packet, _emotional_intensity


# ── 1. emotional-cue detection ────────────────────────────────────────────────

# Emotional cues the base gate misses (statements, third-person) MUST be caught
# by the dedicated emotional detector — both valences (the 4B brain under-captures
# joy, so we must not also under-recall it).
@pytest.mark.parametrize("msg", [
    "how have I been feeling",
    "how am I doing lately",
    "I've been so stressed lately",          # statement, not a question
    "feeling really overwhelmed today",
    "what has Jason been worried about",      # third-person
    "I'm still grieving",
    "what made me happy last month",
    "I was so proud of the kids",
])
def test_emotional_cues_fire(msg):
    assert message_needs_emotional_recall(msg) is True, f"{msg!r} should be an emotional cue"


# Non-emotional messages must NOT be treated as emotional cues.
@pytest.mark.parametrize("msg", [
    "what's the weather",
    "where do I live",
    "add milk to my list",
    "what should I cook for dinner tonight",
    "how do I make pasta",
    "what time is it",
])
def test_non_emotional_messages_do_not_fire(msg):
    assert message_needs_emotional_recall(msg) is False, f"{msg!r} is not an emotional cue"


def test_emotional_detector_is_separate_from_base_gate():
    # An emotional STATEMENT with no possessive/question the base gate misses is
    # exactly what the emotional detector adds — proving the two are independent
    # and the base gate is unchanged by this wiring.
    msg = "I've been so stressed lately"
    assert message_needs_memory(msg) is False
    assert message_needs_emotional_recall(msg) is True


# ── 2. packet float ──────────────────────────────────────────────────────────

def _fact(rid, text):
    return SimpleNamespace(id=rid, text=text, metadata={"status": "approved", "memory_type": "fact"})


def _emo(rid, text, intensity):
    return SimpleNamespace(
        id=rid, text=text,
        metadata={"status": "approved", "memory_type": "emotional_moment",
                  "candidate_intensity": intensity},
    )


def test_intensity_sort_key():
    assert _emotional_intensity(_emo("a", "x", 0.9)) == 0.9
    assert _emotional_intensity(_fact("b", "y")) == -1.0          # plain facts sort last
    # garbled/missing intensity on an emotional row still floats ahead of plain facts
    bad = SimpleNamespace(id="c", text="z", metadata={"memory_type": "emotional_moment"})
    assert _emotional_intensity(bad) == 0.0


def test_boost_off_preserves_order():
    facts = [_fact("f1", "ordinary fact one"), _emo("e1", "was anxious about the move", 0.9)]
    packet = _build_memory_prompt_packet(facts, [], max_facts=12, boost_emotional=False)
    lines = packet["packet"].split("\n")
    # default order: plain fact appears before the emotional row (no reordering)
    assert lines.index(next(l for l in lines if "ordinary fact one" in l)) < \
           lines.index(next(l for l in lines if "anxious" in l))


def test_boost_floats_emotional_ahead_of_facts_by_intensity():
    facts = [
        _fact("f1", "ordinary fact one"),
        _fact("f2", "ordinary fact two"),
        _emo("e_low", "mildly pleased about lunch", 0.3),
        _emo("e_high", "was devastated about the loss", 0.95),
    ]
    packet = _build_memory_prompt_packet(facts, [], max_facts=12, boost_emotional=True)
    lines = [l for l in packet["packet"].split("\n") if l.startswith("- ")]
    # both emotional rows lead, highest intensity first, plain facts after
    assert "devastated" in lines[0]
    assert "mildly pleased" in lines[1]
    assert "ordinary fact" in lines[2] and "ordinary fact" in lines[3]


def test_boost_does_not_drop_or_duplicate():
    facts = [_fact("f1", "fact a"), _emo("e1", "felt joy at the news", 0.7)]
    off = _build_memory_prompt_packet(facts, [], max_facts=12, boost_emotional=False)
    on = _build_memory_prompt_packet(facts, [], max_facts=12, boost_emotional=True)
    assert off["count"] == on["count"] == 2       # same rows, only order differs
