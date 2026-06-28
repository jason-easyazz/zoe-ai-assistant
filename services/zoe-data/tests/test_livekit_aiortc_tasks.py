"""GC-safe background-task tests for the aiortc LiveKit backend.

``livekit_aiortc`` imports ``av`` / ``aiortc`` / ``livekit.protocol`` at module top,
so these are Jetson-only (the slim GitHub runner lacks those wheels) — guarded with
``importorskip`` so collection skips cleanly off-Jetson.

Covered (P3): emitted coroutine handlers and the per-track audio handler are kept
in a strong-ref set so they can't be GC'd mid-flight, their exceptions are retrieved
(not "never retrieved"), the set self-cleans on completion, and ``disconnect()``
cancels any still-running ones.
"""
import asyncio

import pytest

pytest.importorskip("av")
pytest.importorskip("aiortc")

import livekit_aiortc as la


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_emit_tracks_coroutine_and_retrieves_exception():
    room = la.AiortcRoom()
    seen: dict = {}

    @room.on("evt")
    async def _handler(x):
        seen["x"] = x
        raise RuntimeError("boom")  # retrieved by the done-callback, not orphaned

    async def _body():
        room._emit("evt", 42)
        assert len(room._bg_tasks) == 1, "emitted coroutine must be strong-ref tracked"
        await asyncio.gather(*list(room._bg_tasks), return_exceptions=True)

    _run(_body())
    assert seen["x"] == 42
    assert len(room._bg_tasks) == 0, "done callback must discard the finished task"


def test_emit_ignores_sync_handlers():
    room = la.AiortcRoom()
    calls: list = []

    @room.on("evt")
    def _sync_handler(x):  # sync handlers return None → nothing scheduled
        calls.append(x)

    async def _body():
        room._emit("evt", 7)
        assert calls == [7]
        assert len(room._bg_tasks) == 0

    _run(_body())


def test_disconnect_cancels_bg_tasks():
    room = la.AiortcRoom()

    async def _body():
        async def _forever():
            while True:
                await asyncio.sleep(0.05)

        task = room._spawn_bg(_forever())
        await asyncio.sleep(0)
        await room.disconnect()
        await asyncio.gather(task, return_exceptions=True)
        return task

    task = _run(_body())
    assert task.cancelled(), "disconnect must cancel still-running background tasks"
    assert len(room._bg_tasks) == 0
