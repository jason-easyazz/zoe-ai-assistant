"""A reply that is tool-call SYNTAX is never shown or spoken (QA round 3b, 2026-11-07; ``tool_text_guard``).

The live miss: the brain's whole reply was ``add_to_list(item="swim goggles", list_type="tasks/to-do")`` with no tool call. The
guard drops call lines from a reply that OPENS with call syntax and replaces an empty remainder with one honest line; anything
that opens with prose is untouched. Streaming must equal one-shot at every delta cut.
"""
import asyncio

import pytest

import brain_dispatch
import tool_text_guard as g

pytestmark = pytest.mark.ci_safe

CALLS = [
    'add_to_list(item="swim goggles", list_type="tasks/to-do")',
    "add_to_list(item='flashcards', list_type='pharmacy study list')",
    '`add_to_list(item="x")`',
    '```python\nadd_to_list(item="x", list_type="shopping")\n```',
    'default_api.show_list(list_type="personal")',
    'home(action="on", room="kitchen");',
    'add_to_list(item="a")\nadd_to_list(item="b")',
    'add_to_list(item="a",\n  list_type="tasks")',          # a call broken over lines is still code
]

PROSE = [
    "Sure, I've put swim goggles on your to-do list.",
    "I'm not sure which list you mean - your to-do list or the shopping list?",
    "Okay",
    "Rome (the capital) is lovely in spring.",
    "Preheat the oven to 190°C (fan 170°C).",
    "Hello there!",
    "",
]


@pytest.mark.parametrize("text", CALLS)
def test_a_bare_call_becomes_the_honest_line(text):
    assert g.guard_text(text) == g.HONEST_LINE


@pytest.mark.parametrize("text", PROSE)
def test_negative_control_prose_is_untouched(text):
    assert g.guard_text(text) == text


def test_a_call_followed_by_prose_keeps_only_the_prose():
    text = 'add_to_list(item="swim goggles")\nWant me to add anything else?'
    assert g.guard_text(text) == "Want me to add anything else?"


def test_prose_that_mentions_a_call_later_is_untouched():
    text = 'Sure. I tried add_to_list(item="x") but it failed.'
    assert g.guard_text(text) == text


def test_kill_switch(monkeypatch):
    monkeypatch.setenv("ZOE_TOOL_TEXT_GUARD", "off")
    assert g.guard_text(CALLS[0]) == CALLS[0]


async def _collect(chunks):
    async def gen():
        for c in chunks:
            yield c

    return [d async for d in g.guard_stream(gen())]


@pytest.mark.parametrize("text", CALLS + PROSE[:-1] + ['add_to_list(item="x")\nWant anything else?'])
def test_stream_equals_one_shot_at_every_cut(text):
    want = g.guard_text(text)
    for cut in range(1, len(text)):
        got = "".join(asyncio.run(_collect([text[:cut], text[cut:]])))
        assert got == want, (text, cut)
    # one char per delta too
    assert "".join(asyncio.run(_collect(list(text)))) == want


def test_stream_passes_tool_sentinels_through_unheld():
    out = asyncio.run(_collect(["add_to_", '__TOOL__:{"phase": "start"}', 'list(item="x")']))
    assert out[0].startswith("__TOOL__:") and "".join(out[1:]) == g.HONEST_LINE


def test_prose_is_released_before_the_turn_ends():
    seen = []

    async def gen():
        yield "Sure"
        yield ", done."
        seen.append("released-before-end")
        yield " Anything else?"

    async def run():
        out = []
        async for d in g.guard_stream(gen()):
            out.append((d, list(seen)))
        return out

    out = asyncio.run(run())
    # released at the first non-identifier character, not at the end - and as the deltas that came, not re-chunked
    assert out[:2] == [("Sure", []), (", done.", [])]


def _dispatch_stream(text, kwargs):
    async def gen():
        yield text

    async def run():
        return [d async for d in brain_dispatch._claim_guarded(gen(), "add swim goggles", "u", kwargs)]

    return "".join(asyncio.run(run()))


def test_brain_dispatch_guards_the_one_shot_and_the_stream(monkeypatch):
    monkeypatch.setenv("ZOE_WRITE_CLAIM_GUARD", "off")      # this guard alone (the write-claim guard is tested on its own)
    assert brain_dispatch._claim_guarded_text(CALLS[0], "add swim goggles", "u", 0.0, {}) == g.HONEST_LINE
    assert _dispatch_stream(CALLS[0], {}) == g.HONEST_LINE
    monkeypatch.setenv("ZOE_WRITE_CLAIM_GUARD", "enforce")  # and in front of the write-claim guard
    assert _dispatch_stream(CALLS[0], {}) == g.HONEST_LINE


def test_negative_control_brain_dispatch_leaves_prose_and_the_replay_harness_alone(monkeypatch):
    monkeypatch.setenv("ZOE_WRITE_CLAIM_GUARD", "off")
    assert _dispatch_stream("Hello there!", {}) == "Hello there!"
    # the replay harness is never guarded (same exemption as the write-claim guard)
    assert _dispatch_stream(CALLS[0], {"replay_isolation": True}) == CALLS[0]
    assert brain_dispatch._claim_guarded_text(CALLS[0], "x", "u", 0.0, {"replay_isolation": True}) == CALLS[0]


def test_a_mid_stream_error_releases_the_held_lead_before_it_propagates():
    """The guard holds an identifier-only lead ('partial') until it knows it is not a call; if the brain dies there, the held text is
    delivered first and the error goes on - the failover contract (text already produced is never swallowed)."""
    import asyncio

    import tool_text_guard as g

    async def dies():
        yield "partial"
        raise RuntimeError("model exploded")

    async def run():
        seen = []
        try:
            async for d in g.guard_stream(dies()):
                seen.append(d)
        except RuntimeError as exc:
            return seen, str(exc)
        return seen, ""

    assert asyncio.run(run()) == (["partial"], "model exploded")
