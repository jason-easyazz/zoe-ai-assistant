"""reply_polish.py (ZOE_REPLY_POLISH) + the QA-2026-11-08 routing gaps. Synthetic phrasings only (ci_safe).

Pinned:
  * plumbing words ("activate the home ability", "use the home tool") never reach the person; a raw tool identifier is humanised; the
    name list matches the tool names in labs/flue-zoe-brain-2x/src/tools/zoe-tools.ts (drift pin);
  * the question cap: a generic closing question is dropped ONLY when the previous reply ended on a question and something of substance
    comes before it; a specific question, a lone closer and a first reply are never touched (NEGATIVE CONTROLS);
  * the stream form equals the batch form for every way of cutting a reply into deltas; sentinels pass at once; ``off`` / ``shadow``
    change nothing; the state follows the reply the person actually received;
  * "what time do I usually get up?" and "when is <name>'s learner test?" are the person's own life, not the clock (memory_gate +
    expert_dispatch), while "what time is it" still is the clock (NEGATIVE CONTROL).
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

import memory_gate as mg
import reply_polish as rp

pytestmark = pytest.mark.ci_safe


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("ZOE_REPLY_POLISH", raising=False)
    rp.reset_state()
    yield
    rp.reset_state()


def _run(coro):
    return asyncio.run(coro)


# -- plumbing words ------------------------------------------------------------------------------------------
@pytest.mark.parametrize("reply,want", [
    ("I'd activate the home ability and then use the home tool to turn on the kitchen light.", "I'd turn on the kitchen light."),
    ("I'll use the add_calendar_event tool to do that. Done.", "I'll do that. Done."),
    ("I'd unlock the calendar ability and then show your week.", "I'd show your week."),
    ("I can check show_calendar for Thursday.", "I can check show calendar for Thursday."),
    # a reply that is ONLY plumbing is dropped, never passed through (cross-review #2347)
    ("I'll use the home tool.", ""),
    ("I'll use the home tool. Done.", "Done."),
    ("Activate the calendar ability. Your event is saved.", "Your event is saved."),
    # negative controls: ordinary words are untouched
    ("The home stretch is the hard part and the tool kit is in the shed.", "The home stretch is the hard part and the tool kit is in the shed."),
    ("I'd turn on the kitchen light.", "I'd turn on the kitchen light."),
])
def test_plumbing_words_are_scrubbed(reply, want):
    assert rp.polish(reply) == want


def test_tool_names_match_the_brain_source():
    src = (Path(__file__).resolve().parents[3] / "labs/flue-zoe-brain-2x/src/tools/zoe-tools.ts").read_text()
    names = set(re.findall(r"^\s+name: '([a-z_]+)',", src, re.MULTILINE))
    assert names, "no tool names found in zoe-tools.ts"
    assert names - set(rp.TOOL_NAMES) == set(), f"scrub list is missing {sorted(names - set(rp.TOOL_NAMES))}"


# -- the question cap ----------------------------------------------------------------------------------------
CLOSER = "How are you feeling overall?"


@pytest.mark.parametrize("reply,prev,want", [
    ("That sounds heavy about your mum. " + CLOSER, True, "That sounds heavy about your mum."),
    ("That sounds heavy about your mum. " + CLOSER, False, "That sounds heavy about your mum. " + CLOSER),     # first question is allowed
    (CLOSER, True, CLOSER),                                                                                    # only a closer: kept whole
    ("I can add that. Which list should I put bolts on?", True, "I can add that. Which list should I put bolts on?"),   # specific question
    ("Hello. " + CLOSER + " It is cold.", True, "Hello. " + CLOSER + " It is cold."),                          # not the last sentence
    ("Fine. Does that make sense?", True, "Fine."),
])
def test_closing_question_cap(reply, prev, want):
    assert rp.polish(reply, "hi", prev) == want


def test_stream_equals_batch_for_every_cut():
    reply = "That sounds heavy about your mum, and I'd ring her tonight. Is there anything else? How are you feeling overall?"
    want = rp.polish(reply, "hi", True)
    assert want != reply
    for size in (1, 2, 3, 5, 8, 13, len(reply)):
        p = rp.PolishStream(rp.closer_lexicon_for("hi"), True)
        out = "".join(p.feed(reply[i:i + size]) for i in range(0, len(reply), size)) + p.finish()
        assert out == want, size


async def _deltas(parts):
    for x in parts:
        yield x


def _collect(parts, **kw):
    async def go():
        return [c async for c in rp.filter_stream(_deltas(parts), "hi", "u1", "s1")]
    return _run(go())


def test_state_follows_the_reply_the_person_got(monkeypatch):
    first = _collect(["I think so. ", "What about you?"])
    assert "".join(first) == "I think so. What about you?" and rp.ended_on_question("u1", "s1")
    second = _collect(["Sure thing, it is Tuesday. ", "Is there anything else?"])
    assert "".join(second) == "Sure thing, it is Tuesday." and not rp.ended_on_question("u1", "s1")   # capped, so the next may ask
    third = _collect(["Fine. ", "Is there anything else?"])
    assert "".join(third) == "Fine. Is there anything else?"


def test_sentinels_pass_at_once_and_off_and_shadow_change_nothing(monkeypatch):
    parts = ['__TOOL__:{"n":1}', "Ok. ", "Does that make sense?"]
    rp.note_reply("u1", "s1", "Really?")
    got = _collect(parts)
    assert got[0] == parts[0] and "".join(got[1:]) == "Ok."
    rp.note_reply("u1", "s1", "Really?")
    monkeypatch.setenv("ZOE_REPLY_POLISH", "shadow")
    assert _collect(parts) == parts
    rp.note_reply("u1", "s1", "Really?")
    monkeypatch.setenv("ZOE_REPLY_POLISH", "off")
    assert _collect(parts) == parts


def test_seam_wraps_the_stream_and_off_does_not(monkeypatch):
    import inspect

    import zoe_flue_client as zc

    src = inspect.getsource(zc.run_flue_brain_streaming)
    assert "reply_polish.filter_stream(turn, _spoken, user_id, session_id)" in src and 'reply_polish.mode() != "off"' in src


# -- the clock is not the person's life ---------------------------------------------------------------------
@pytest.mark.parametrize("text,kind", [
    ("what time do I usually get up?", "my_habit"),
    ("what time do i normally go to bed", "my_habit"),
    ("when is jonahs learner test?", "their_event"),
    ("When is Jonah's learner test", "their_event"),
    ("when is mums appointment", "their_event"),
    ("what time is my dentist appointment", "my_event"),
    # negative controls: the clock, a world event, a plain activity
    ("when is chris' exam", "their_event"),
    ("when is christmas dinner", ""), ("when is physics exam", ""),
    ("what time is it", ""), ("what time does the game start", ""), ("what time does tennis practice start", ""), ("when is the next flight", ""),
])
def test_event_time_shapes(text, kind):
    assert mg.event_time_question_kind(text) == kind


def test_time_expert_hands_a_personal_question_to_the_brain_but_answers_the_clock():
    import expert_dispatch as ed

    assert ed._plan("time", "what time do I usually get up?") is None
    assert ed._plan("time", "when is jonahs learner test?") is None
    plan = ed._plan("time", "what time is it")
    assert plan is not None and plan[0] == "time_query" and plan[2] == "read"


def test_a_failing_sentence_is_passed_through_not_duplicated(monkeypatch):
    real = rp.scrub

    def flaky(sentence):
        if "boom" in sentence:
            raise RuntimeError("injected")
        return real(sentence)

    monkeypatch.setattr(rp, "scrub", flaky)
    got = "".join(_collect(["First one. The boom sentence. ", "Last one."]))
    assert got == "First one. The boom sentence. Last one."


def test_stream_equals_batch_for_arbitrary_cuts():
    import random

    reply = "I'd activate the home ability and then use the home tool to turn it on. Fine. Is there anything else? Does that make sense?"
    want = rp.polish(reply, "hi", True)
    rnd = random.Random(7)
    for _ in range(60):
        cuts = sorted(rnd.sample(range(1, len(reply)), rnd.randint(1, 12)))
        parts = [reply[a:b] for a, b in zip([0] + cuts, cuts + [len(reply)])]
        p = rp.PolishStream(rp.closer_lexicon_for("hi"), True)
        assert "".join(p.feed(x) for x in parts) + p.finish() == want


@pytest.mark.asyncio
async def test_filter_stream_cancel_closes_the_inner_stream_promptly(monkeypatch):
    """A barge-in cancels the consumer mid-stream: the wrapper must not swallow the cancel or leave the inner turn running."""
    import asyncio

    monkeypatch.setenv("ZOE_REPLY_POLISH", "on")
    closed: list = []

    async def inner():
        try:
            yield "Morning! "
            await asyncio.sleep(30)
        finally:
            closed.append(True)

    got = asyncio.Event()

    async def consume():
        async for _ in rp.filter_stream(inner(), "morning", "u", "s"):
            got.set()

    task = asyncio.ensure_future(consume())
    await asyncio.wait_for(got.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 5)
    assert closed == [True]
