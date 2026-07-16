"""Recall keyword-gate coverage — the structural rule added so natural recall
questions ("where do I live", "tell me about my mum") actually trigger the
MemPalace search that feeds the /for-prompt packet the brain reads each turn.

Before: message_needs_memory was a naive word-allowlist that missed those, so
search never fired and the relevant fact never reached the brain. The structural
rule fires on a personal self-reference in a question/recall shape, while still
NOT firing on non-personal questions/commands (which would waste the embed).
"""
import pytest

from memory_gate import message_needs_memory

pytestmark = pytest.mark.ci_safe


# Recall questions that MUST now trigger memory (previously missed by the list).
@pytest.mark.parametrize("msg", [
    "where do I live",
    "where do we live",
    "tell me about my mum",
    "what team do we support",
    "who are my kids",
    "how old is my dog",
    "what's my dog's name",
    "do I have any siblings",
    "when is my mum's birthday",
    "what is my name",
    "remind me what my sister does",
])
def test_recall_questions_fire(msg):
    assert message_needs_memory(msg) is True, f"{msg!r} should trigger memory recall"


# Non-personal questions and commands must NOT fire (keep the embed off the hot
# path for turns that don't need it).
@pytest.mark.parametrize("msg", [
    "what's the weather",
    "add milk to my list",
    "turn on the lights",
    "set a timer for 5 minutes",
    "play some music",
    "what time is it",
    "how do I make pasta",
    "tell me a joke",
    "how do I get to the shops",
    "what films were popular last year?",  # "were" must NOT match \bwe\b (structural rule)
    "are you ill?",                          # "ill" must NOT match a first-person subject
])
def test_non_recall_messages_do_not_fire(msg):
    assert message_needs_memory(msg) is False, f"{msg!r} should NOT trigger memory recall"


def test_existing_trigger_words_still_fire():
    # The original keyword list is preserved.
    assert message_needs_memory("remember my dad is Neil") is True
    assert message_needs_memory("what is my favourite colour") is True
    assert message_needs_memory("I like coffee") is True
