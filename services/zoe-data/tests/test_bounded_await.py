"""``bounded_await.bounded`` never swallows a cancellation (the PR #2141 ``validate`` hang).

``asyncio.wait_for`` on Python 3.10 returns the inner result when the outer task is cancelled just as the inner awaitable
finished; a long-lived worker that did so looped on, cancellation consumed, and the loop's shutdown waited forever.
Every wait here is capped by ``asyncio.wait_for`` on the TEST side, so a regression fails instead of hanging the lane.
"""
from __future__ import annotations

import asyncio

import pytest

from bounded_await import bounded

pytestmark = pytest.mark.ci_safe


async def _quick():
    await asyncio.sleep(0)
    return "done"


async def test_returns_the_result_and_the_exception():
    assert await bounded(_quick(), 5) == "done"

    async def boom():
        raise ValueError("x")

    with pytest.raises(ValueError):
        await bounded(boom(), 5)


async def test_timeout_raises_and_cancels_the_inner():
    state = {"cancelled": False}

    async def slow():
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            state["cancelled"] = True
            raise

    with pytest.raises(asyncio.TimeoutError):
        await bounded(slow(), 0.05)
    assert state["cancelled"]


@pytest.mark.parametrize("ticks", range(0, 6))
async def test_a_cancel_landing_as_the_inner_finishes_is_never_swallowed(ticks):
    task = asyncio.ensure_future(bounded(_quick(), 5))
    for _ in range(ticks):
        await asyncio.sleep(0)
    if not task.cancel():                      # already finished before the cancel: nothing to swallow
        assert task.result() == "done"
        return
    with pytest.raises(asyncio.CancelledError):
        await _await_cancelled(task)


async def _await_cancelled(task):
    done, _ = await asyncio.wait({task}, timeout=5)
    assert done, "the cancelled task never finished: the cancellation was swallowed"
    task.result()                               # raises CancelledError when it was honoured


async def test_a_worker_loop_built_on_bounded_dies_on_cancel_every_time():
    """The shape of struggle_detector._worker: wait on a queue, do a bounded store write, loop. Cancelling it at every
    tick of the write must end the task."""
    for ticks in range(0, 8):
        q: asyncio.Queue = asyncio.Queue()
        q.put_nowait(1)

        async def worker():
            while True:
                await q.get()
                try:
                    await bounded(_quick(), 5)
                finally:
                    q.task_done()

        w = asyncio.ensure_future(worker())
        for _ in range(ticks):
            await asyncio.sleep(0)
        w.cancel()
        done, _ = await asyncio.wait({w}, timeout=5)
        assert done, f"worker survived a cancel at tick {ticks}: the cancellation was swallowed"
