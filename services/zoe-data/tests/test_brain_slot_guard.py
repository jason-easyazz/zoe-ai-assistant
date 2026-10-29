"""``brain_slot_guard``: post-turn 4B callers must not evict a hot conversation from the brain's one llama slot.

Root cause, measured 2026-10-12 (docs/knowledge/brain-prefix-cache-2026-10-12.md): the slot is shared with the post-turn memory
passes; each one that does not extend the conversation prompt pushes it through llama-server's host prompt cache, which keeps
only the last 512 positions of a sliding-window model, so the next turn re-prefills the whole prompt (5-7 s to first token).

The clock is a fake one so no test sleeps. The negative control (guard off) must let the call straight through while a turn is
in flight: if it did not, the other assertions would pass for the wrong reason.
"""
from __future__ import annotations

import asyncio

import pytest

import brain_slot_guard as g

pytestmark = pytest.mark.ci_safe


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def clock(monkeypatch):
    c = FakeClock()
    g.reset()
    monkeypatch.setattr(g, "_clock", c)
    monkeypatch.setenv(g.ENV, "on")
    monkeypatch.setenv(g.ENV_QUIET_S, "45")
    monkeypatch.setenv(g.ENV_MAX_DEFER_S, "240")

    real_sleep = asyncio.sleep

    async def fake_sleep(s):  # advance the fake clock instead of waiting; still yield to the loop
        c.t += s
        await real_sleep(0)

    monkeypatch.setattr(g.asyncio, "sleep", fake_sleep)
    yield c
    g.reset()


async def _call(label, ran):
    async with g.background_slot(label):
        ran.append(label)


async def test_no_turn_yet_runs_immediately(clock):
    ran = []
    await _call("a", ran)
    assert ran == ["a"] and g.STATS["immediate"] == 1 and g.STATS["deferred"] == 0


async def test_call_waits_while_a_turn_is_in_flight_and_runs_after_the_quiet_window(clock):
    ran = []
    g.turn_started()
    task = asyncio.ensure_future(_call("digest", ran))
    await asyncio.sleep(0)
    for _ in range(5):
        await asyncio.sleep(0)
    assert ran == [], "a turn is in flight: the background call must not touch the slot"
    g.turn_finished()
    assert g.is_hot(), "the conversation stays hot for the quiet window after the turn ends"
    await asyncio.wait_for(task, 5)
    assert ran == ["digest"]
    assert g.STATS["released_quiet"] == 1
    assert clock.t - 1000.0 >= 45.0, "released only after quiet_s had passed since the turn ended"


async def test_a_second_turn_inside_the_window_keeps_it_hot(clock):
    ran = []
    g.turn_started()
    g.turn_finished()
    clock.t += 30
    assert g.is_hot()
    g.turn_started()
    g.turn_finished()
    clock.t += 30                      # 30 s after the SECOND turn, 60 s after the first
    assert g.is_hot(), "the window restarts at the last turn, not the first"
    clock.t += 20
    assert not g.is_hot()


async def test_max_defer_releases_the_whole_backlog_back_to_back(clock):
    """A conversation that never goes quiet must still get its memory passes: every queued call runs, not one per max_defer_s."""
    ran = []
    g.turn_started()                    # never finishes inside the test
    tasks = [asyncio.ensure_future(_call(f"c{i}", ran)) for i in range(4)]
    await asyncio.wait_for(asyncio.gather(*tasks), 5)
    assert sorted(ran) == ["c0", "c1", "c2", "c3"]
    assert g.STATS["released_max"] == 1 and g.STATS["released_quiet"] + g.STATS["immediate"] == 3
    assert clock.t - 1000.0 < 2 * 240.0 + 1, "the backlog flushed together; it did not wait max_defer_s per call"
    assert not g._flush and g._waiters == 0, "the flush flag resets once the backlog is empty"


async def test_calls_are_serialised(clock):
    live = {"n": 0, "max": 0}

    async def one():
        async with g.background_slot("x"):
            live["n"] += 1
            live["max"] = max(live["max"], live["n"])
            await asyncio.sleep(0)
            live["n"] -= 1

    await asyncio.gather(*[one() for _ in range(5)])
    assert live["max"] == 1


async def test_guard_off_is_a_no_op_even_mid_turn(clock, monkeypatch):
    """Negative control for the tests above."""
    monkeypatch.setenv(g.ENV, "off")
    g.turn_started()
    ran = []
    await asyncio.wait_for(_call("a", ran), 1)
    assert ran == ["a"] and g.STATS["deferred"] == 0


async def test_an_exception_inside_the_call_releases_the_gate(clock):
    with pytest.raises(RuntimeError):
        async with g.background_slot("boom"):
            raise RuntimeError("llama down")
    assert g._waiters == 0
    ran = []
    await asyncio.wait_for(_call("after", ran), 1)
    assert ran == ["after"]


def test_turn_counter_never_goes_negative(clock):
    g.turn_finished()
    g.turn_finished()
    assert g._active == 0


def test_every_post_turn_llama_caller_goes_through_the_gate():
    """The set of background callers that share the brain slot. A new post-turn caller of the 4B that is not listed
    here evicts the conversation again; add it to the gate AND to this list."""
    import inspect

    import latent_intent_detector
    import memory_digest
    import memory_retire
    import person_extractor_llm
    import structural_reader
    import structural_verifier

    for mod, fn in (
        (person_extractor_llm, person_extractor_llm.process_text_llm),
        (latent_intent_detector, latent_intent_detector._complete),
        (memory_digest, memory_digest.run_turn_digest),
        (structural_reader, structural_reader._call),
        (structural_verifier, structural_verifier.judge),
        (memory_retire, memory_retire.ask_judge),
    ):
        assert "background_slot(" in inspect.getsource(fn), f"{mod.__name__}.{fn.__name__} must hold brain_slot_guard.background_slot"


def test_the_streaming_turn_brackets_the_slot():
    import inspect

    import zoe_flue_client

    src = inspect.getsource(zoe_flue_client.run_flue_brain_streaming)
    assert "brain_slot_guard.turn_started()" in src and "brain_slot_guard.turn_finished()" in src
    assert src.index("turn_started()") < src.index("try:\n        async for delta in turn:")


def test_the_default_quiet_window_is_180s_and_stays_under_the_max_defer(monkeypatch):
    """Owner-approved 2026-10-29: 45 s let a helper evict the slot in any longer pause (next turn re-prefilled ~1300 tokens).

    The quiet window must stay below the max defer, or a hot conversation could never release a waiting helper before
    the backlog flush."""
    monkeypatch.delenv(g.ENV_QUIET_S, raising=False)
    monkeypatch.delenv(g.ENV_MAX_DEFER_S, raising=False)
    assert g.DEFAULT_QUIET_S == 180.0 and g.quiet_s() == 180.0
    assert g.quiet_s() < g.max_defer_s() and g.DEFAULT_QUIET_S < g.DEFAULT_MAX_DEFER_S
