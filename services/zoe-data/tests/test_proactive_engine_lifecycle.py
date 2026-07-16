import asyncio

import pytest

import proactive.engine as engine

pytestmark = pytest.mark.ci_safe


@pytest.fixture
def engine_task_globals():
    previous_slow = engine._slow_loop_task
    previous_cleanup = engine._cleanup_loop_task
    engine._slow_loop_task = None
    engine._cleanup_loop_task = None
    try:
        yield
    finally:
        for task in (engine._slow_loop_task, engine._cleanup_loop_task):
            if task is not None and not task.done():
                task.cancel()
        engine._slow_loop_task = previous_slow
        engine._cleanup_loop_task = previous_cleanup


@pytest.mark.asyncio
async def test_stop_proactive_engine_cancels_cleanup_task(monkeypatch, engine_task_globals):
    monkeypatch.setattr(engine, "start_scheduler", lambda: None)
    monkeypatch.setattr(engine, "stop_scheduler", lambda: None)

    async def neverending():
        await asyncio.Event().wait()

    monkeypatch.setattr(engine, "_slow_loop", neverending)
    monkeypatch.setattr(engine, "_cleanup_expired_pending", neverending)

    engine.start_proactive_engine()
    slow_task = engine._slow_loop_task
    cleanup_task = engine._cleanup_loop_task

    assert slow_task is not None
    assert cleanup_task is not None
    assert not cleanup_task.done()

    engine.stop_proactive_engine()

    assert engine._slow_loop_task is None
    assert engine._cleanup_loop_task is None

    # Task.cancelling() is Python 3.11+; the box runs 3.10. Awaiting the tasks
    # and checking they ended cancelled proves the same thing portably.
    await asyncio.gather(slow_task, cleanup_task, return_exceptions=True)
    assert slow_task.cancelled()
    assert cleanup_task.cancelled()
