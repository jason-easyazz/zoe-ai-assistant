"""``bounded`` - ``asyncio.wait_for`` that can never swallow a cancellation.

On Python 3.10 (the CI ``validate`` interpreter and the Jetson's system python) ``wait_for`` returns the inner
result instead of raising when the OUTER task is cancelled at the instant the inner awaitable has already finished
(bpo-42130, fixed in 3.12). A process-lifetime worker (``struggle_detector._worker``) that hits that race loops back
to ``q.get()`` with its cancellation consumed, and the event loop's shutdown (``_cancel_all_tasks``) waits on it
forever - the 6-hour ``validate`` hang on PR #2141. Same contract as ``wait_for`` otherwise: the awaitable's result or
exception, ``asyncio.TimeoutError`` on timeout (the awaitable is cancelled and awaited first).
"""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Optional


async def bounded(aw: Awaitable[Any], timeout: Optional[float]) -> Any:
    fut = asyncio.ensure_future(aw)
    try:
        await asyncio.wait({fut}, timeout=timeout)    # asyncio.wait re-raises the OUTER task's CancelledError
    except BaseException:
        fut.cancel()
        raise
    if fut.done():
        return fut.result()
    fut.cancel()
    try:
        await fut
    except asyncio.CancelledError:
        pass
    raise asyncio.TimeoutError()
