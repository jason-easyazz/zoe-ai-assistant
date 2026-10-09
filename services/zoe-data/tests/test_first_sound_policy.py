"""First-sound policy (voice_first_sound.py): clause-boundary first unit, the tool-turn acknowledgement at dispatch and
the prefetch that lets it cost no brain time. Every behaviour change is behind a ZOE_FIRST_SOUND_* flag, OFF by default."""
from __future__ import annotations

import asyncio
import inspect

import pytest

import routers.voice_tts as v
import voice_first_sound as fs

pytestmark = pytest.mark.ci_safe


@pytest.fixture
def clause_on(monkeypatch):
    monkeypatch.setenv("ZOE_FIRST_SOUND_CLAUSE", "1")
    for k in ("ZOE_FIRST_SOUND_CLAUSE_MIN_CHARS", "ZOE_FIRST_SOUND_CLAUSE_MIN_WORDS"):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def ack_on(monkeypatch):
    monkeypatch.setenv("ZOE_FIRST_SOUND_TOOL_ACK", "1")


@pytest.mark.parametrize("buf,unit", [   # OFF is today's _extract_first_unit
    ("It's twelve degrees and clear, with a light breeze.", None),
    ("Sure, it's sunny. ", "Sure, it's sunny."),
    ("Sure, I can help with that, and I really would like to", None),
    ("The weather this morning across the whole region is looking quite pleasant, and ",
     "The weather this morning across the whole region is looking quite pleasant,"),
])
def test_off_is_todays_extract_first_unit(monkeypatch, buf, unit):
    monkeypatch.delenv("ZOE_FIRST_SOUND_CLAUSE", raising=False)
    assert fs.clause_enabled() is False and fs.tool_ack_enabled() is False
    assert v._extract_first_unit(buf)[0] == unit
    assert fs.dispatch_ack({"routed": "calendar"}) is None


def test_cut_at_first_boundary_past_the_minimums(clause_on):
    assert v._extract_first_unit("Sure, I can help with that, and I really would like to") == (
        "Sure, I can help with that,", "and I really would like to")
    assert v._extract_first_unit("Sure, it is a lovely day and I would")[0] is None           # "Sure," is a stub
    assert v._extract_first_unit("Sure, I can help with that,")[0] is None                    # no following space yet
    assert v._extract_first_unit("Sure, I can help with that, and more. Next")[0] == "Sure, I can help with that, and more."


@pytest.mark.parametrize("text,unit", [
    ("The total was 3.5, which is quite a lot for one single day", None),                      # a number before the comma
    ("The meeting is on the 3rd, 4th and 5th of the month and runs late", None),
    ("In 2024, the company moved to a much bigger office downtown", None),
    ("It is about 12,000 people, mostly from the north side of town", "It is about 12,000 people,"),
])
def test_never_cut_right_after_a_number(clause_on, text, unit):
    assert v._extract_first_unit(text)[0] == unit


@pytest.mark.parametrize("text,unit", [   # an abbreviation or initial is part of its word
    ("I spoke with Dr. Patel about it and then went home for the evening", None),
    ("You can use fruit, e.g., apples and pears, or anything else you like", "You can use fruit, e.g., apples and pears,"),
    ("Bring snacks, drinks, etc., and a blanket for the whole afternoon trip", None),
    ("It was written by J. K. Rowling, the famous author of those books", "It was written by J. K. Rowling,"),
    ('He said "I am quite sure, absolutely certain it works', None),                             # an open quote
    ("It works (mostly, but not always, in practice) and that is fine", None),                    # an open bracket
    ("It works well enough (in practice), and that is quite fine", "It works well enough (in practice),"),
])
def test_never_cut_inside_an_abbreviation_quote_or_bracket(clause_on, text, unit):
    got, rest = fs.extract_first_clause(text)   # the clause rule; the sentence rule ("Dr." + space) is unchanged
    assert got == unit
    if got:
        assert (got + " " + rest).split() == text.split()   # nothing lost, nothing reordered


def test_token_by_token_the_unit_is_a_prefix_of_the_reply(clause_on):
    reply = "Honestly, I think the best thing you can do is rest, drink some water, and take a short walk later."
    buf = ""
    for i in range(0, len(reply), 3):
        buf += reply[i:i + 3]
        unit, rest = v._extract_first_unit(buf)
        if unit:
            assert reply.startswith(unit) and unit.endswith(",") and len(unit) >= 24
            assert (unit + " " + rest).split() == buf.split()
            return
    pytest.fail("no unit was cut")


@pytest.mark.parametrize("routed", ["calendar", "lists", "reminders", "weather", "memory", "timers", "people"])
def test_tool_domains_get_a_short_line_that_claims_no_result(ack_on, routed):
    line = fs.dispatch_ack({"routed": routed})
    assert line and len(line) < 40
    assert not any(w in line.lower() for w in ("you have", "you've got", "here are", "i found", "nothing"))


@pytest.mark.parametrize("decision", [{"routed": "chat"}, {"routed": ""}, {}, None, {"routed": "bogus"},
                                      {"domain": "calendar"}])   # only the ROUTED domain counts
def test_chat_and_unknown_turns_get_no_ack(ack_on, decision):
    assert fs.dispatch_ack(decision) is None and "chat" not in fs._TOOL_ACK_LINES


def test_no_ack_when_sound_already_started_and_the_loop_uses_the_shared_decision(ack_on):
    d = {"routed": "calendar"}
    assert fs.dispatch_ack(d) is not None
    for kw in ("audio_started", "filler_emitted", "processing_ack_sent"):
        assert fs.dispatch_ack(d, **{kw: True}) is None
    src = inspect.getsource(v.voice_command)
    assert "_ack_line = _first_sound.dispatch_ack(" in src and "_brain_stream = _prefetched = _first_sound.prefetch(" in src


def test_prefetch_starts_the_first_pull_at_construction_and_a_plain_generator_does_not():
    async def go():
        started: list[int] = []

        async def gen(items):
            started.append(1)
            for it in items:
                yield it

        stream, plain = fs.prefetch(gen(["a", "b"])), gen(["x"])
        await asyncio.sleep(0.05)               # the "acknowledgement synthesis"
        assert started == [1]                   # only the prefetched stream began (the plain one is still lazy)
        return [x async for x in stream], plain
    assert asyncio.run(go())[0] == ["a", "b"]


def test_prefetch_edges_empty_error_and_close():
    async def boom():
        raise RuntimeError("sidecar down")
        yield  # pragma: no cover

    async def slow(closed):
        try:
            await asyncio.sleep(10)
            yield "never"
        finally:
            closed.set()

    async def empty():
        return
        yield  # pragma: no cover

    async def go():
        assert [x async for x in fs.prefetch(empty())] == []
        with pytest.raises(RuntimeError):
            async for _ in fs.prefetch(boom()):
                pass
        closed = asyncio.Event()
        stream = fs.prefetch(slow(closed))
        await asyncio.sleep(0.01)
        stream.close_nowait()                   # cancels the pending pull and closes the stream
        await asyncio.wait_for(closed.wait(), 1.0)
        stream.close_nowait()                   # idempotent

    asyncio.run(go())
