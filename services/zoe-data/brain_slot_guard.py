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

Single process by construction: zoe-data is one uvicorn worker (see ``pending_suggestions``), and every turn that fills
the slot with a conversation goes through ``zoe_flue_client.run_flue_brain_streaming``, which brackets the turn here.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

logger = logging.getLogger(__name__)

ENV = "ZOE_BRAIN_SLOT_GUARD"            # on (default) | off
ENV_QUIET_S = "ZOE_BRAIN_SLOT_QUIET_S"  # a conversation is "hot" this long after its last brain turn started/ended
ENV_MAX_DEFER_S = "ZOE_BRAIN_SLOT_MAX_DEFER_S"
DEFAULT_QUIET_S = 45.0
DEFAULT_MAX_DEFER_S = 240.0
_POLL_S = 0.5

_active = 0                 # brain turns in flight
_last_activity = 0.0        # monotonic stamp of the last turn start / end (0 = never)
_lock: Optional[asyncio.Lock] = None
_waiters = 0                # background calls waiting on (or holding) the gate
_flush = False              # a waiter hit max_defer: let the whole backlog through, back to back
STATS = {"deferred": 0, "released_quiet": 0, "released_max": 0, "immediate": 0}


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
    global _active, _last_activity, _lock, _waiters, _flush
    _active = 0
    _last_activity = 0.0
    _lock = None
    _waiters = 0
    _flush = False
    for k in STATS:
        STATS[k] = 0


def turn_started() -> None:
    """A brain turn is about to fill the slot. Always paired with ``turn_finished`` (try/finally)."""
    global _active, _last_activity, _starts
    _active += 1
    _starts += 1
    _last_activity = _clock()


def turn_finished() -> None:
    global _active, _last_activity
    _active = max(0, _active - 1)
    _last_activity = _clock()


_starts = 0                 # monotonic count of turn_started() calls (never reset): "did a turn begin since X"


def starts() -> int:
    """How many brain turns have started in this process (monotonic). Compare two readings to learn whether any turn
    began in between, even one that has already finished."""
    return _starts


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
                if _flush or not is_hot():
                    released = "released_quiet" if deferred else "immediate"
                    break
                if _clock() - waited_since >= max_defer_s():
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
