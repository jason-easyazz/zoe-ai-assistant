"""user_erase_gate - per-user coordination between background writers and the right-to-be-forgotten path.

Why. ``commitments.record`` / ``commitments.sweep`` / ``reply_ledger.write`` run as background tasks. One of them can be waiting for a
database connection when ``MemoryService.delete_user`` (or the forget cascade) runs and finishes; its INSERT then lands AFTER the
erase and the person is remembered again. A sweep that already fetched a promise can likewise recreate the candidate the cascade just
removed. Forgetting must win every such race.

How. Two things per user, in this process (zoe-data is one process, like ``memory_tombstones``):
  * a lock the erasers and the writers both take - a writer holds it from before it asks for its connection to after its commit, so an
    erase queues behind a write in flight and removes what it wrote, and a write queued behind an erase sees the generation moved;
  * a generation counter the eraser bumps when it ENTERS and again when it leaves. A writer snapshots the generation when it is
    scheduled; if it has moved by the time the writer holds the lock, the write belonged to the world before the erase and is dropped.

  * for the MemoryService writers (post-turn passes, nightly digest, idle consolidation) the snapshot travels with the task
    (``scheduled`` / ``scheduled_at_call``) and the store checks it under its OWN per-user lock (``erased_since_scheduled``).

The lock is taken BEFORE any database connection, never after, so a writer holding a pooled connection can never wait on an eraser
that is waiting for a connection (no pool deadlock). Everything here is in-memory and cannot raise into a caller.
"""
from __future__ import annotations

import asyncio
import contextlib
import contextvars
import functools
import inspect
import weakref
from typing import AsyncIterator, Iterator

_generation: dict = {}
#: one lock table per running event loop (an ``asyncio.Lock`` binds to a loop; tests start many)
_locks: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def generation(user_id: str) -> int:
    """The user's erase generation now. Snapshot it when a background write is SCHEDULED."""
    return _generation.get(user_id, 0)


def snapshot() -> dict:
    """Every user's erase generation now (a copy). A member absent from it was at generation 0. For a batch that fetches rows of many
    members and writes for each later."""
    return dict(_generation)


def _lock(user_id: str) -> asyncio.Lock:
    table = _locks.setdefault(asyncio.get_running_loop(), {})
    lock = table.get(user_id)
    if lock is None:
        lock = table[user_id] = asyncio.Lock()
    return lock


@contextlib.asynccontextmanager
async def erasing(user_id: str, *, bump: bool = True) -> AsyncIterator[None]:
    """An erase of ``user_id``'s data. ``bump`` (a whole-user erase) also stales every write scheduled before it; an entity erase
    passes ``bump=False``: writes in flight just queue behind it and are checked against the forgotten ledger instead."""
    if bump:
        _generation[user_id] = generation(user_id) + 1       # before waiting: writes queued behind us are already stale
    async with _lock(user_id):
        try:
            yield
        finally:
            if bump:
                _generation[user_id] = generation(user_id) + 1


@contextlib.asynccontextmanager
async def writing(user_id: str, scheduled_at: int) -> AsyncIterator[bool]:
    """A background write for ``user_id`` that was scheduled at generation ``scheduled_at``. Yields True when it may proceed (hold
    the block through the commit), False when an erase happened since it was scheduled (drop it)."""
    async with _lock(user_id):
        yield generation(user_id) == scheduled_at


#: the post-turn / background work the current task belongs to: ``(user_id, generation it was scheduled at)``, or None
_scheduled: "contextvars.ContextVar" = contextvars.ContextVar("user_erase_gate_scheduled", default=None)


@contextlib.contextmanager
def scheduled(user_id: str, scheduled_at: int) -> Iterator[None]:
    """Run a block as background work for ``user_id`` SCHEDULED at generation ``scheduled_at``; tasks it starts inherit it.

    For the MemoryService writers (the turn digest and every post-turn pass, the nightly digest, idle consolidation). Their rows are
    guarded by the store's own per-user lock, which ``delete_user`` holds while it enumerates, so ``MemoryService.ingest`` /
    ``review(edit)`` check ``erased_since_scheduled`` UNDER that lock rather than taking ``writing``: ``delete_user`` takes this
    module's lock inside its memory lock, so a writer holding ``writing`` while it waits for the memory lock would deadlock it. The
    generation has already moved before ``delete_user`` takes its memory lock (its first step, the day-review erase, bumps it)."""
    token = _scheduled.set((user_id, scheduled_at))
    try:
        yield
    finally:
        _scheduled.reset(token)


def scheduled_here(user_id: str) -> "contextlib.AbstractContextManager":
    """``scheduled`` at the generation NOW, unless this task already carries an earlier snapshot for ``user_id`` (that one wins)."""
    held = _scheduled.get()
    if held is not None and held[0] == user_id:
        return contextlib.nullcontext()
    return scheduled(user_id, generation(user_id))


def erased_since_scheduled(user_id: str) -> bool:
    """True when the current task is background work for ``user_id`` and the member was erased since it was scheduled."""
    held = _scheduled.get()
    return held is not None and held[0] == user_id and generation(user_id) != held[1]


def scheduled_at_call(user_arg: str):
    """Decorate an async post-turn writer: the generation of the member named by argument ``user_arg`` is read when the function is
    CALLED - the moment its work is queued (``ensure_future(fn(...))``) - not when its body starts; the body runs ``scheduled``. A
    caller that already carries a snapshot for the same member (a batch that took one when IT was scheduled) keeps the earlier one."""
    def wrap(fn):
        sig = inspect.signature(fn)

        @functools.wraps(fn)
        def call(*args, **kwargs):
            user_id = sig.bind_partial(*args, **kwargs).arguments.get(user_arg) or ""
            held = _scheduled.get()
            gen = held[1] if held is not None and held[0] == user_id else generation(user_id)

            async def body():
                with scheduled(user_id, gen):
                    return await fn(*args, **kwargs)
            return body()
        if hasattr(inspect, "markcoroutinefunction"):     # 3.12: still reads as a coroutine function
            inspect.markcoroutinefunction(call)
        return call
    return wrap


def reset() -> None:
    """Forget all generations (tests)."""
    _generation.clear()
