"""Keep the household's brain PREFIX resident in llama-server's one slot between conversations (``ZOE_BRAIN_PREFIX_KEEPER``).

Measured 2026-11-02 over 26 real wake warm-ups (``docs/knowledge/cold-wake-2026-11-02.md``): 12 were a COLD ~3.3k-token
prefill (``first_cache_n=0``, 4.2-5.8 s), and 10 of those 12 followed >= 4 background helper prompts since the last
conversation. Every helper prompt (a few hundred tokens, ~70 ms) makes llama-server swap the slot's prompt into its host
cache and load something else; after a handful the member's prefix no longer comes back. ``brain_slot_guard`` already
batches the guarded helpers into quiet periods, but nothing puts the prefix BACK once the batch has run, so the next wake
pays 5 s of prefill while the person is already speaking (and the real turn queues behind it).

This module is that missing step. A cheap poll of ``GET /slots`` (the slot's current prompt length, no inference) says
whether the slot still holds a conversation-sized prompt. When it does not (a helper batch ran, the brain restarted, an
unguarded caller displaced it), and nothing is going on, it runs the SAME throwaway KV warm the wake runs
(``zoe_flue_client.warm_kv_prefix``) for the panel's likely speaker. The next wake's own warm then sees the prefix resident
(``first_cache_n`` ~3.2k, ~0.3-1 s) or is skipped as hot. No llama-server flag, no new endpoint, works on b11194 and b11982
(``/slots`` reports ``n_prompt_tokens`` on both).

Never raises into the loop; never competes with a live conversation or a lab harness. It acts only when ALL hold:
  * enabled, the Flue lane, ``ZOE_BRAIN_KV_WARM_ON_WAKE`` on, and a panel speaker was noted in the last 12 h (``warm_speaker``);
  * ``brain_slot_guard`` is not hot (no turn in flight or within the quiet window) and no helper is queued or running;
  * the brain-window lock (``/tmp/zoe-brain-window.lock``: landings, the voice gate, bake-offs, the night window) is not held;
  * ``/slots`` is reachable, the slot is idle, and its prompt is shorter than ``ZOE_BRAIN_PREFIX_KEEPER_MIN_TOKENS``
    for ``SETTLE_S`` in a row (a back-to-back helper burst is over);
  * rate limits: >= ``MIN_GAP_S`` between warms, <= ``MAX_PER_HOUR`` an hour, ``FAIL_BACKOFF_S`` after a failed one.
"""
from __future__ import annotations

import asyncio
import collections
import fcntl
import logging
import os
import time
from typing import Optional

import brain_slot_guard
from typed_env import env_bool, env_int

logger = logging.getLogger(__name__)

ENV = "ZOE_BRAIN_PREFIX_KEEPER"
ENV_MIN_TOKENS = "ZOE_BRAIN_PREFIX_KEEPER_MIN_TOKENS"
DEFAULT_MIN_TOKENS = 2000   # the prefix is ~3.2k tokens; a helper prompt is < 500
POLL_S = 15.0
SETTLE_S = 8.0              # the slot must look displaced this long, so a helper burst is over before the warm starts
MIN_GAP_S = 120.0
MAX_PER_HOUR = 8
FAIL_BACKOFF_S = 300.0
SLOTS_TIMEOUT_S = 2.0
WINDOW_LOCK = "/tmp/zoe-brain-window.lock"

_clock = time.monotonic
_state: dict = {"displaced_since": None, "last_warm": None, "warms": collections.deque(), "backoff_until": 0.0}
STATS: "collections.Counter[str]" = collections.Counter()


def enabled() -> bool:
    return env_bool(ENV, default=True)


def min_tokens() -> int:
    return max(1, env_int(ENV_MIN_TOKENS, DEFAULT_MIN_TOKENS))


def reset() -> None:
    """Test hook."""
    _state.update(displaced_since=None, last_warm=None, backoff_until=0.0)
    _state["warms"].clear()
    STATS.clear()


def take_window_lock(path: Optional[str] = None) -> "Optional[int]":
    """Claim the brain-window lock EXCLUSIVELY for the length of one warm (non-blocking). It fails if ANYONE holds the file
    (the A/B harnesses hold it shared, the bake-off and the night window exclusive) and, while held, excludes shared
    holders too: a harness preflight probes with a shared lock, so a shared hold here would let it start under the warm.
    Returns the open fd (the caller closes it to release), None when someone holds the lock, -1 when there is no lock file
    / it cannot be used (nothing to hold; not a reason to skip)."""
    try:
        fd = os.open(path or WINDOW_LOCK, os.O_RDONLY)
    except OSError:
        return -1
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except BlockingIOError:
        os.close(fd)
        return None
    except OSError:
        os.close(fd)
        return -1


def window_lock_held(path: Optional[str] = None) -> bool:
    """True while another process holds the brain-window lock in any mode. Holds nothing afterwards."""
    fd = take_window_lock(path)
    if fd is not None and fd >= 0:
        os.close(fd)
    return fd is None


async def read_slot() -> "Optional[dict]":
    """``{"tokens": int, "processing": bool}`` for slot 0 from llama-server's ``/slots``, or None when unreachable or
    unreadable. A slot that has never run a task has no ``n_prompt_tokens`` key: that is 0 tokens."""
    try:
        import httpx

        from gemma_endpoint import gemma_base

        async with httpx.AsyncClient(timeout=SLOTS_TIMEOUT_S) as client:
            resp = await client.get(f"{gemma_base()}/slots")
        if resp.status_code != 200:
            return None
        data = resp.json()
        if not isinstance(data, list) or not data or not all(isinstance(x, dict) for x in data):
            return None
        # Fail closed: every slot must say explicitly whether it is processing (a missing field is no answer, not 'idle').
        if not all(isinstance(x.get("is_processing"), bool) for x in data):
            return None
        # Any busy slot means busy; the prefix is resident if ANY slot holds a conversation-sized prompt (a future two-slot
        # layout keeps working). A slot that never ran a task has no ``n_prompt_tokens`` key: 0 tokens.
        return {"tokens": max(int(x.get("n_prompt_tokens") or 0) for x in data),
                "processing": any(x["is_processing"] for x in data)}
    except Exception:  # noqa: BLE001 - unreachable / mid-restart / not JSON: no decision
        return None


def _reset_debounce() -> None:
    _state["displaced_since"] = None


async def tick() -> str:
    """One decision. Returns a short label (tests and the loop's counters); warms at most once."""
    try:
        if not enabled():
            _reset_debounce()
            return "off"
        import zoe_flue_client
        from brain_dispatch import use_flue_brain

        if not use_flue_brain() or not zoe_flue_client.kv_warm_enabled():
            _reset_debounce()
            return "lane_off"
        import warm_speaker

        member = await asyncio.to_thread(warm_speaker.latest)
        if not member:
            _reset_debounce()
            return "no_speaker"
        if brain_slot_guard.is_hot() or brain_slot_guard.waiters() > 0 or brain_slot_guard.active_turns() > 0:
            _reset_debounce()
            return "busy"
        lock_fd = await asyncio.to_thread(take_window_lock)
        if lock_fd is None:
            _reset_debounce()
            return "window_lock"
        try:
            return await _decide_and_warm(member, zoe_flue_client)
        finally:
            if lock_fd >= 0:
                os.close(lock_fd)   # released only after the warm has finished
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - a keeper must never take the loop down
        logger.warning("PREFIX_KEEPER tick failed (non-fatal): %s", type(exc).__name__)
        return "error"


async def _decide_and_warm(member: str, zoe_flue_client) -> str:
    """The rest of one tick, run with the window lock held (exclusive) so no harness can start under the warm."""
    slot = await read_slot()
    if slot is None:
        _reset_debounce()
        return "unreachable"
    if slot["processing"]:
        _reset_debounce()
        return "processing"
    if slot["tokens"] >= min_tokens():
        _reset_debounce()
        return "resident"
    now = _clock()
    if _state["displaced_since"] is None:
        _state["displaced_since"] = now
        return "settling"
    if now - _state["displaced_since"] < SETTLE_S:
        return "settling"
    if now < _state["backoff_until"]:
        return "backoff"
    if _state["last_warm"] is not None and now - _state["last_warm"] < MIN_GAP_S:
        return "rate_gap"
    warms = _state["warms"]
    while warms and now - warms[0] > 3600.0:
        warms.popleft()
    if len(warms) >= MAX_PER_HOUR:
        return "rate_cap"
    settled_s = now - _state["displaced_since"]
    # yield_to_helpers: re-checks, inside the warm and just before it takes the slot, that no helper queued meanwhile.
    outcome = await zoe_flue_client.warm_kv_prefix(member, yield_to_helpers=True)
    if not outcome.startswith("skipped:"):   # a skip ran nothing, so it costs no budget and is retried next poll
        _state["last_warm"] = now
        warms.append(now)
        if outcome == "warmed":
            _reset_debounce()
        else:
            _state["backoff_until"] = _clock() + FAIL_BACKOFF_S
    logger.info("PREFIX_KEEPER outcome=%s slot_tokens=%d displaced_s=%.0f", outcome, slot["tokens"], settled_s)
    return f"warm:{outcome}"


async def run_loop() -> None:
    """Started once by ``main`` (lifespan). One poll every ``POLL_S``; each tick is cheap unless it warms."""
    await asyncio.sleep(30.0)   # let the brain, the sidecar and the startup warm settle first
    while True:
        label = await tick()
        STATS[label.split(":")[0]] += 1
        await asyncio.sleep(POLL_S)
