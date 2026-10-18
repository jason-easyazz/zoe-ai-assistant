"""Event-loop lag gauge (A1, architecture deep dive 2026-10-18).

zoe-data is ONE uvicorn worker on ONE asyncio loop: every voice turn, push socket, poll and health check shares it. A
synchronous call made inside an ``async def`` (the two-stage router's ``urlopen`` to the FunctionGemma sidecar was the
worst, ~400 ms per turn) freezes all of them for its whole duration, and nothing used to show it: per-request timings
only measure the request that blocked, not the ones that waited behind it.

``monitor()`` sleeps ``INTERVAL_S`` in a loop and records how LATE each wake-up is. On an idle, healthy loop the lag is
about a millisecond; a blocking call shows up as lag of the same size as the block, whoever made it. Exported on
``/metrics`` (same ``REGISTRY`` as ``voice_metrics``):

* ``zoe_event_loop_lag_seconds`` - histogram of every sample (the p99 is the number to watch);
* ``zoe_event_loop_lag_max_seconds`` - the worst sample in the current ``WINDOW_S`` window (and the previous one until
  the current one overtakes it), so a scrape every 15-60 s never misses a spike.

A sample at or above ``WARN_S`` also logs ONE ``EVENT_LOOP_LAG`` warning per ``WARN_EVERY_S`` (grep
``~/.zoe-logs/zoe-data.app.log``). Started from ``main.lifespan``; no flag, it costs four wake-ups a second.
Pinned by ``tests/test_router_off_loop.py``.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable, Optional

from prometheus_client import Gauge, Histogram

from memory_metrics import REGISTRY

logger = logging.getLogger(__name__)

INTERVAL_S = 0.25
WINDOW_S = 60.0
WARN_S = 0.25
WARN_EVERY_S = 10.0

event_loop_lag_seconds = Histogram(
    "zoe_event_loop_lag_seconds",
    "How late the event loop woke a 250 ms sleeper (seconds): time the single worker spent blocked.",
    buckets=(0.002, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0),
    registry=REGISTRY,
)
event_loop_lag_max_seconds = Gauge(
    "zoe_event_loop_lag_max_seconds",
    "Worst event-loop lag (seconds) in the current 60 s window (or the previous one, whichever is larger).",
    registry=REGISTRY,
)


class _State:
    def __init__(self) -> None:
        self.window_start = 0.0
        self.window_max = 0.0
        self.prev_window_max = 0.0
        self.last = 0.0
        self.samples = 0
        self.over_warn = 0
        self.last_warn = float("-inf")


_state = _State()


def record(lag_s: float, *, now: Optional[float] = None) -> None:
    """Record one lag sample (seconds). Never raises."""
    try:
        lag = max(0.0, float(lag_s))
        t = time.monotonic() if now is None else now
        st = _state
        if t - st.window_start >= WINDOW_S:
            st.prev_window_max, st.window_max, st.window_start = st.window_max, 0.0, t
        st.window_max = max(st.window_max, lag)
        st.last = lag
        st.samples += 1
        event_loop_lag_seconds.observe(lag)
        event_loop_lag_max_seconds.set(max(st.window_max, st.prev_window_max))
        if lag >= WARN_S:
            st.over_warn += 1
            if t - st.last_warn >= WARN_EVERY_S:
                st.last_warn = t
                logger.warning(
                    "EVENT_LOOP_LAG %.3fs: something blocked the single zoe-data worker (every request, push socket and "
                    "voice stream waited); %d sample(s) >= %.2fs since start", lag, st.over_warn, WARN_S)
    except Exception:  # noqa: BLE001 - instrumentation must never hurt the loop it measures
        pass


def snapshot() -> dict:
    """The current numbers (for tests and ad-hoc inspection)."""
    st = _state
    return {"last_s": st.last, "window_max_s": max(st.window_max, st.prev_window_max), "samples": st.samples,
            "over_warn": st.over_warn}


def reset_for_test() -> None:
    global _state
    _state = _State()


async def monitor(interval_s: float = INTERVAL_S, *, clock: Callable[[], float] = time.monotonic) -> None:
    """Run forever (until cancelled), recording how late each ``interval_s`` sleep wakes."""
    while True:
        t0 = clock()
        await asyncio.sleep(interval_s)
        record(clock() - t0 - interval_s)


def start() -> Optional["asyncio.Task"]:
    """Schedule ``monitor()`` on the running loop. Never raises (None when it could not start)."""
    try:
        return asyncio.get_running_loop().create_task(monitor(), name="event_loop_lag_monitor")
    except Exception as exc:  # noqa: BLE001
        logger.warning("event-loop lag monitor not started (non-fatal): %s", exc)
        return None
