"""Keep the voice conversation RESIDENT in the brain's one llama-server slot (``ZOE_BRAIN_SLOT_GUARD``, default ON).

The problem this exists for (measured 2026-10-12, ``docs/knowledge/brain-prefix-cache-2026-10-12.md``): the 4B brain has ONE
slot (``--parallel 1``, forced by upstream ggml-org/llama.cpp#28286 while ``--spec-type draft-mtp`` is set). After every
spoken turn the post-turn memory passes (person extractor, turn digest, latent-intent detector, structural reader, ...)
each POST a short prompt to that same slot. llama-server answers a request that does not extend the slot's prompt by
saving the slot's prompt into the ``--cache-ram`` host cache and loading something else. For a sliding-window-attention
model (Gemma) a saved state holds only the LAST 512 positions of the SWA layers (``llama_kv_cache::state_write`` skips
SWA-masked cells; ``--swa-full`` does not change that), so a conversation prompt that has been through the host cache can
be resumed only when the next prompt diverges from it within its last ~510 tokens. Anything deeper (the previous turn's
recall block being elided, the tool block changing, the history window moving) re-prefills the WHOLE prompt:
``first_cache_n=0``, 3.4-4.4k tokens at ~670 tok/s = 5.1-6.7 s before the first token, on about half of the live
voice turns. A conversation prompt that STAYS in the slot is reused at ANY divergence depth.

So the rule here is simple: while a conversation is hot, background 4B callers wait for it to go quiet and then run as a
batch, serially. Nothing is dropped and nothing is reordered: only WHEN the call is made changes. A call never waits
longer than ``max_defer_s`` (default 240 s), so a long conversation still gets its memory passes.

The household always wins the slot (2026-11-03: a ``released_max`` person-extractor call held the slot while the owner was
talking, and the next voice turn sat ~18 s queued behind it):

  * a background call never STARTS while a household brain turn is in flight - not even past ``max_defer_s`` (bounded by
    ``2 * max_defer_s`` so a leaked turn counter cannot park the helpers forever);
  * a call made through ``run_background`` is PREEMPTED when a household turn starts while it holds the slot: its request
    is cancelled (closing the HTTP connection; llama-server cancels a non-streamed task within its 1 s
    ``HTTP_POLLING_SECONDS``) and the call goes back to waiting and is retried later - nothing is dropped (up to
    ``MAX_PREEMPTS`` retries). After a preemption the backlog waits a fresh ``max_defer_s`` (or for quiet), so a
    conversation pays for at most one preemption per window, not one per queued call.

Single process by construction: zoe-data is one uvicorn worker (see ``pending_suggestions``), and every turn that fills
the slot with a conversation goes through ``zoe_flue_client.run_flue_brain_streaming``, which brackets the turn here.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator, Awaitable, Callable, Optional, TypeVar

import brain_scheduler

logger = logging.getLogger(__name__)
T = TypeVar("T")

ENV = "ZOE_BRAIN_SLOT_GUARD"            # on (default) | off
ENV_QUIET_S = "ZOE_BRAIN_SLOT_QUIET_S"  # a conversation is "hot" this long after its last brain turn started/ended
ENV_MAX_DEFER_S = "ZOE_BRAIN_SLOT_MAX_DEFER_S"
DEFAULT_QUIET_S = 180.0   # owner-approved 2026-10-29: a pause under 3 min keeps the conversation resident (was 45 s)
DEFAULT_MAX_DEFER_S = 240.0
_POLL_S = 0.5

_active = 0                 # brain turns in flight
_last_activity = 0.0        # monotonic stamp of the last turn start / end (0 = never)
_lock: Optional[asyncio.Lock] = None
_keeper_activity = 0.0      # monotonic stamp of the last idle-time keeper warm (holds helpers off, NOT the conversation's timer)
_waiters = 0                # background calls waiting on (or holding) the gate
_flush = False              # a waiter hit max_defer: let the whole backlog through, back to back
_household = 0              # household (non-keeper) brain turns in flight: a background call never starts under one
_preempted_at = 0.0         # monotonic stamp of the last preemption: the backlog's max_defer restarts from here
_holders: set = set()       # one future per preemptible background call holding the slot; a household turn start fires them
MAX_PREEMPTS = 10           # a call preempted this many times gives up (BrainSlotPreempted) rather than retry forever
STATS = {"deferred": 0, "released_quiet": 0, "released_max": 0, "immediate": 0, "preempted": 0}


class BrainSlotPreempted(Exception):
    """A background call was preempted by household turns ``MAX_PREEMPTS`` times and gave up."""


def _clock() -> float:
    return time.monotonic()


def enabled() -> bool:
    """Per-call read. Only an explicit falsy word turns the guard off."""
    return (os.environ.get("ZOE_BRAIN_SLOT_GUARD") or "on").strip().lower() not in ("0", "off", "false", "no", "disabled")


def _seconds(raw: str, default: float) -> float:
    try:
        v = float((raw or "").strip())
        return v if v >= 0 else default
    except ValueError:
        return default


def quiet_s() -> float:
    return _seconds(os.environ.get("ZOE_BRAIN_SLOT_QUIET_S", ""), DEFAULT_QUIET_S)


def max_defer_s() -> float:
    return _seconds(os.environ.get("ZOE_BRAIN_SLOT_MAX_DEFER_S", ""), DEFAULT_MAX_DEFER_S)


def reset() -> None:
    """Test hook."""
    global _active, _last_activity, _lock, _waiters, _flush, _keeper_activity, _household, _preempted_at
    _active = 0
    _household = 0
    _preempted_at = 0.0
    _holders.clear()
    _last_activity = 0.0
    _keeper_activity = 0.0
    _lock = None
    _waiters = 0
    _flush = False
    for k in STATS:
        STATS[k] = 0


def turn_started(keeper: bool = False) -> None:
    """A brain turn is about to fill the slot. Always paired with ``turn_finished`` (try/finally).

    ``keeper=True`` (the idle-time prefix keeper's throwaway warm, no person speaking) is in flight like any turn but does
    not stamp the conversation's quiet timer, so another member's wake is not told the slot is 'hot'.

    A household turn (``keeper=False``) also preempts every background call holding the slot (``run_background``)."""
    global _active, _last_activity, _starts, _household
    _active += 1
    _starts += 1
    if not keeper:
        _household += 1
        _last_activity = _clock()
        _preempt_holders()


def turn_finished(keeper: bool = False) -> None:
    global _active, _last_activity, _keeper_activity, _household
    _active = max(0, _active - 1)
    if keeper:
        _keeper_activity = _clock()   # keep the post-turn helpers off the prefix just loaded, without 'hot' for wakes
    else:
        _household = max(0, _household - 1)
        _last_activity = _clock()


def _fire(fut: "asyncio.Future") -> None:
    if not fut.done():
        fut.set_result(True)


def _preempt_holders() -> None:
    """Tell every preemptible background call holding the slot that a household turn has started. Thread-safe: the
    callback runs on the holder's own loop."""
    for fut in list(_holders):
        try:
            fut.get_loop().call_soon_threadsafe(_fire, fut)
        except RuntimeError:   # that loop is closed: nothing left to preempt
            _holders.discard(fut)


_starts = 0                 # monotonic count of turn_started() calls (never reset): "did a turn begin since X"


def starts() -> int:
    """How many brain turns have started in this process (monotonic). Compare two readings to learn whether any turn
    began in between, even one that has already finished."""
    return _starts


def last_turn_age_s() -> Optional[float]:
    """Seconds since a household turn last started or ended (None = none yet in this process). ``brain_scheduler`` uses it
    to tell a post-turn pass (derived from a turn that just finished) from an idle call."""
    if _last_activity <= 0.0:
        return None
    return max(0.0, _clock() - _last_activity)


def waiters() -> int:
    """Background calls waiting on (or holding) the gate right now. A batch is still draining while this is > 0."""
    return _waiters


def active_turns() -> int:
    """Brain turns currently in flight (a caller that holds one itself counts as one)."""
    return _active


def is_hot(now: Optional[float] = None) -> bool:
    """True while a brain turn is in flight or the last one ended less than ``quiet_s`` ago."""
    if _active > 0:
        return True
    if _last_activity <= 0.0:
        return False
    return ((now if now is not None else _clock()) - _last_activity) < quiet_s()


def _helpers_wait(now: Optional[float] = None) -> bool:
    """Background helpers hold off while the conversation is hot OR a keeper warm finished < ``quiet_s`` ago."""
    if is_hot(now):
        return True
    if _keeper_activity <= 0.0:
        return False
    return ((now if now is not None else _clock()) - _keeper_activity) < quiet_s()


@asynccontextmanager
async def background_slot(label: str = "") -> AsyncIterator[None]:
    """Hold this around ONE background call to the brain's llama-server.

    Waits while the conversation is hot (bounded by ``max_defer_s``), then runs serially with the other background
    callers, re-checking before it starts so a turn that began while it waited is not queued behind a batch. Never
    raises and never drops the call: the worst case is that it runs ``max_defer_s`` late. Off -> a no-op.
    """
    global _lock, _waiters, _flush
    if not enabled():
        yield
        return
    if _lock is None:
        _lock = asyncio.Lock()
    waited_since = _clock()
    deferred = False
    released = "immediate"
    _waiters += 1
    try:
        async with _lock:
            while True:
                now = _clock()
                if _household > 0 and now - waited_since < 2 * max_defer_s():
                    pass   # never start under an in-flight household turn, not even past max_defer: its requests would queue behind us
                elif _flush or not _helpers_wait(now):
                    released = "released_quiet" if deferred else "immediate"
                    break
                elif now - max(waited_since, _preempted_at) >= max_defer_s():
                    released = "released_max"
                    _flush = True   # the backlog behind this call goes through too, not one call per max_defer_s
                    break
                deferred = True
                await asyncio.sleep(_POLL_S)
            STATS[released] = STATS.get(released, 0) + 1
            if deferred:
                STATS["deferred"] += 1
                logger.info("BRAIN_SLOT_GUARD label=%s released=%s waited_ms=%d", label or "-", released,
                            int((_clock() - waited_since) * 1000))
            yield
    finally:
        _waiters -= 1
        if _waiters <= 0:
            _waiters = 0
            _flush = False


async def run_background(label: str, call: "Callable[[], Awaitable[T]]") -> "T":
    """Run ONE background call to the brain's llama-server through the gate, preemptible by a household turn.

    ``call`` is a zero-argument factory returning a fresh awaitable (the HTTP request); it is invoked again on every retry.
    The call waits exactly as ``background_slot`` does; while it holds the slot, a household turn that starts
    (``turn_started()`` with ``keeper=False``) cancels it at once, so that turn queues behind nothing. The call then waits
    again (for quiet, or a fresh ``max_defer_s``) and is retried: nothing is dropped unless it is preempted ``MAX_PREEMPTS``
    times, which raises ``BrainSlotPreempted`` (an ``Exception``: every caller already treats a failed call as "no result").
    Guard off -> one plain call, no gate.
    """
    global _flush, _preempted_at
    if not enabled():
        return await call()
    preempts = 0
    while True:
        async with background_slot(label):
            # No await between the gate's release check and this registration: a turn cannot slip in between.
            preempt = asyncio.get_running_loop().create_future()
            _holders.add(preempt)
            with brain_scheduler.declare(label):   # the scheduler's choke point names this call (the task copies the context)
                task = asyncio.ensure_future(call())
            try:
                await asyncio.wait({task, preempt}, return_when=asyncio.FIRST_COMPLETED)
            except BaseException:
                task.cancel()   # our own caller was cancelled: never leave the request running in the slot
                raise
            finally:
                _holders.discard(preempt)
            if task.done():
                return task.result()
            task.cancel()
            # asyncio.wait, not `await task`: it never raises the task's own CancelledError / error, so a cancellation of
            # OUR caller arriving here still propagates instead of being swallowed as the one we asked for.
            await asyncio.wait({task})
            if not task.cancelled():
                task.exception()   # an error that raced the cancel: retrieved (no "never retrieved" warning), then retried
        preempts += 1
        STATS["preempted"] = STATS.get("preempted", 0) + 1
        _preempted_at = _clock()
        _flush = False   # the conversation is demonstrably live: the backlog waits again instead of flushing into it
        logger.info("BRAIN_SLOT_GUARD label=%s preempted=%d (household turn started)", label or "-", preempts)
        if preempts >= MAX_PREEMPTS:
            logger.warning("BRAIN_SLOT_GUARD label=%s gave up after %d preemptions", label or "-", preempts)
            raise BrainSlotPreempted(label)
