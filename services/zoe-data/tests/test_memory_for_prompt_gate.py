"""The /for-prompt semantic-search keyword gate.

The Pi memory extension hits /api/memories/for-prompt on every brain turn. Facts
(a cheap metadata read) always load, but the ONNX-embed + Chroma semantic search
is gated to recall-ish messages so it doesn't run on every turn. These pin the
gate predicate.
"""
import pytest

from memory_gate import message_needs_memory as _message_needs_memory

pytestmark = pytest.mark.ci_safe


def test_single_source_of_truth():
    """Both the /for-prompt endpoint and the legacy agent must use the ONE gate in
    memory_gate, so a keyword change can never make the two paths diverge."""
    import memory_gate
    from routers import memories
    assert memories._message_needs_memory is memory_gate.message_needs_memory
    import zoe_agent
    assert zoe_agent._message_needs_memory is memory_gate.message_needs_memory
    assert zoe_agent._MEMORY_TRIGGER_WORDS is memory_gate.MEMORY_TRIGGER_WORDS


@pytest.mark.parametrize("msg", [
    "what do you remember about me",
    "do you recall my birthday",
    "what's my favourite colour",
    "what is my dog's name",
    "remind me what I told you",
    "how old am I",
    "am I allergic to anything",
    "who is my brother",
    "I prefer tea",
])
def test_recall_messages_trigger_search(msg):
    assert _message_needs_memory(msg) is True


@pytest.mark.parametrize("msg", [
    "turn on the lights",
    "what's the temperature outside",
    "set a timer for 5 minutes",
    "tell me a joke",
    "add bread to my shopping list",
    "what time is it",
    "",
])
def test_non_recall_messages_skip_search(msg):
    assert _message_needs_memory(msg) is False


def test_case_insensitive():
    assert _message_needs_memory("REMEMBER my name") is True
    assert _message_needs_memory("Tell Me A Joke") is False
