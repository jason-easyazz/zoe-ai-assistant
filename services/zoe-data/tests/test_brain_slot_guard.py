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


async def test_max_defer_releases_the_whole_backlog_back_to_back(clock, monkeypatch):
    """A conversation that never goes quiet must still get its memory passes: every queued call runs, not one per max_defer_s."""
    monkeypatch.setenv(g.ENV_QUIET_S, "100000")   # hot for the whole test, but no turn in flight between turns
    ran = []
    g.turn_started()
    g.turn_finished()
    tasks = [asyncio.ensure_future(_call(f"c{i}", ran)) for i in range(4)]
    await asyncio.wait_for(asyncio.gather(*tasks), 5)
    assert sorted(ran) == ["c0", "c1", "c2", "c3"]
    assert g.STATS["released_max"] == 1 and g.STATS["released_quiet"] + g.STATS["immediate"] == 3
    assert clock.t - 1000.0 < 240.0 + 1, "the backlog flushed together; it did not wait max_defer_s per call"
    assert not g._flush and g._waiters == 0, "the flush flag resets once the backlog is empty"


async def test_max_defer_never_starts_a_call_under_an_in_flight_household_turn(clock):
    """2026-11-03: a released_max helper started mid-conversation and the next voice turn queued ~18 s behind it. Past
    max_defer the call still waits for the in-flight household turn to end (bounded by 2 x max_defer for a leaked counter)."""
    ran = []
    g.turn_started()                    # a household turn in flight
    task = asyncio.ensure_future(_call("late", ran))
    for _ in range(5000):               # the waiter's own polls advance the fake clock; bounded so a regression cannot hang
        if clock.t - 1000.0 >= 300.0 or ran:   # well past max_defer (240 s)
            break
        await asyncio.sleep(0)
    assert ran == [],"past max_defer, but a household turn is in flight: the call must not start"
    g.turn_finished()
    await asyncio.wait_for(task, 5)
    assert ran == ["late"] and g.STATS["released_max"] == 1


async def test_a_leaked_household_counter_cannot_park_the_helpers_forever(clock):
    ran = []
    g.turn_started()                    # never finishes inside the test
    await asyncio.wait_for(_call("c", ran), 5)
    assert ran == ["c"] and 2 * 240.0 <= clock.t - 1000.0 < 2 * 240.0 + 1


async def test_a_keeper_warm_in_flight_does_not_count_as_a_household_turn(clock, monkeypatch):
    monkeypatch.setenv(g.ENV_QUIET_S, "0")
    g.turn_started(keeper=True)
    assert g._household == 0
    g.turn_finished(keeper=True)
    g.turn_started()
    assert g._household == 1
    g.turn_finished()
    g.turn_finished()                   # never negative
    assert g._household == 0


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
        src = inspect.getsource(fn)
        # run_background, not the bare background_slot: only it yields the slot to a household turn that starts mid-call.
        assert "brain_slot_guard.run_background(" in src, f"{mod.__name__}.{fn.__name__} must go through brain_slot_guard.run_background"
        assert "background_slot(" not in src, f"{mod.__name__}.{fn.__name__} holds the slot without preemption"


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


# ── Preemption: a household turn that arrives while a background call holds the slot gets it within a small bound ──
# Real time (no fake clock): the "slot" is an asyncio.Lock standing in for llama-server's one slot, and the background call
# holds it for a simulated 240 s generation, exactly the 2026-11-03 shape (person_extractor_llm released_max, ~18 s turn).

_TURN_BOUND_S = 0.25   # the household turn must get the slot within this (it was ~18 s live; unbounded in the control)


@pytest.fixture
def live_guard(monkeypatch):
    g.reset()
    monkeypatch.setenv(g.ENV, "on")
    monkeypatch.setenv(g.ENV_QUIET_S, "0")      # retry as soon as the household turn ends
    monkeypatch.setenv(g.ENV_MAX_DEFER_S, "240")
    monkeypatch.setattr(g, "_POLL_S", 0.01)
    yield
    g.reset()


def _hog(slot: asyncio.Lock, calls: list, hold_s: float = 5.0):
    """A background LLM request: takes the slot and generates for ``hold_s`` on its first attempt, instantly after.
    5 s stands in for the live 240 s hold: 20x the bound, and short enough that a regression fails instead of hanging."""
    async def _call():
        calls.append(1)
        async with slot:
            if len(calls) == 1:
                await asyncio.sleep(hold_s)
            return "facts"
    return _call


async def _household_turn(slot: asyncio.Lock) -> float:
    """A voice turn: brackets the brain call like run_flue_brain_streaming, returns how long it waited for the slot."""
    loop = asyncio.get_running_loop()
    g.turn_started()
    try:
        t0 = loop.time()
        await asyncio.wait_for(slot.acquire(), 2.0)
        waited = loop.time() - t0
        slot.release()
        return waited
    finally:
        g.turn_finished()


async def _until_held(slot: asyncio.Lock):
    for _ in range(500):
        if slot.locked():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("the background call never took the slot")


async def test_household_turn_preempts_a_background_call_holding_the_slot(live_guard):
    slot, calls = asyncio.Lock(), []
    task = asyncio.ensure_future(g.run_background("person_extractor_llm", _hog(slot, calls)))
    await _until_held(slot)
    waited = await _household_turn(slot)
    assert waited < _TURN_BOUND_S, f"household turn waited {waited:.3f}s behind a background call"
    assert await asyncio.wait_for(task, 2.0) == "facts", "the preempted call is retried, not dropped"
    assert len(calls) == 2 and g.STATS["preempted"] == 1


async def test_control_a_bare_background_slot_makes_the_household_turn_wait(live_guard):
    """Negative control (the 'before'): the same call held under background_slot alone is NOT preempted - the turn would
    wait out the whole generation. Without this, the test above could pass for the wrong reason."""
    slot, calls = asyncio.Lock(), []

    async def _old_style():
        async with g.background_slot("person_extractor_llm"):
            return await _hog(slot, calls)()

    task = asyncio.ensure_future(_old_style())
    await _until_held(slot)
    with pytest.raises(asyncio.TimeoutError):
        await _household_turn(slot)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_a_keeper_warm_does_not_preempt(live_guard):
    slot, calls = asyncio.Lock(), []
    task = asyncio.ensure_future(g.run_background("x", _hog(slot, calls, hold_s=0.05)))
    await _until_held(slot)
    g.turn_started(keeper=True)
    try:
        assert await asyncio.wait_for(task, 2.0) == "facts"
    finally:
        g.turn_finished(keeper=True)
    assert len(calls) == 1 and g.STATS["preempted"] == 0


async def test_cancelling_the_caller_cancels_the_request(live_guard):
    slot, calls = asyncio.Lock(), []
    task = asyncio.ensure_future(g.run_background("x", _hog(slot, calls)))
    await _until_held(slot)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.01)
    assert not slot.locked(), "the request must not keep running in the slot after its caller is gone"
    assert not g._holders and g._waiters == 0


async def test_gives_up_after_max_preempts(live_guard, monkeypatch):
    monkeypatch.setattr(g, "MAX_PREEMPTS", 1)
    slot, calls = asyncio.Lock(), []
    task = asyncio.ensure_future(g.run_background("x", _hog(slot, calls)))
    await _until_held(slot)
    await _household_turn(slot)
    with pytest.raises(g.BrainSlotPreempted):
        await asyncio.wait_for(task, 2.0)
    assert len(calls) == 1


async def test_after_a_preemption_the_backlog_does_not_flush_into_the_conversation(live_guard, monkeypatch):
    """A preemption restarts the backlog's max_defer: a call queued long ago waits for quiet (or a FRESH max_defer)
    instead of starting into the live conversation at once."""
    now = [5000.0]
    monkeypatch.setattr(g, "_clock", lambda: now[0])
    monkeypatch.setenv(g.ENV_QUIET_S, "100000")
    g.turn_started()
    g.turn_finished()                   # hot, nothing in flight
    ran = []
    task = asyncio.ensure_future(g.run_background("next", _hog(asyncio.Lock(), ran, hold_s=0)))
    await asyncio.sleep(0.05)
    now[0] += 300.0                     # the call has now waited past max_defer...
    g._preempted_at = now[0]            # ...but a household turn just preempted the call ahead of it
    await asyncio.sleep(0.1)
    assert ran == [], "inside a fresh max_defer window after a preemption: the call must wait"
    now[0] += 241.0                     # the fresh window ends: the backlog runs
    assert await asyncio.wait_for(task, 2.0) == "facts" and g.STATS["released_max"] == 1


async def test_guard_off_run_background_is_one_plain_call(live_guard, monkeypatch):
    monkeypatch.setenv(g.ENV, "off")
    g.turn_started()
    calls = []
    assert await asyncio.wait_for(g.run_background("x", _hog(asyncio.Lock(), calls, hold_s=0)), 1) == "facts"
    assert len(calls) == 1
