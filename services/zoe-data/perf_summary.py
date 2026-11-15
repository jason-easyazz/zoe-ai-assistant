"""perf_summary - counters that log ONE summary line per ``SUMMARY_EVERY_S``, never a line per call.

The measurement-gap instruments (gap audit 2026-11-14.6) need a live number for code that runs many times a minute: the SQL
rewrite memo (``db_pool``), the search query embed (``memory_service``) and the palace MiniLM batches (``palace_embed``). A line per
call would flood the app log, so each keeps plain counters and a fixed-bucket histogram, and the call that finds the interval
elapsed writes one ``<NAME>_SUMMARY`` line and starts the next interval. Cost per call: one lock, one monotonic read, a few integer
adds. Codes, counts and milliseconds only: the line never carries text, ids or arguments.

An interval with no call writes nothing (and its counts wait for the next call): a quiet service is silent, not zero.
Buckets are fixed so the scorecard can merge lines across intervals and restarts (``labs/felt-scorecard``).
"""
from __future__ import annotations

import bisect
import logging
import threading
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)

SUMMARY_EVERY_S = 600.0                         # one line per 10 minutes of activity, at most
MS_BUCKETS = (5, 10, 20, 50, 100, 200, 500)     # upper edges, ms; the last bucket is "gt500"
LEN_BUCKETS = (1, 2, 4, 8, 16, 32)              # upper edges, texts per batch; the last bucket is "gt32"


def bucket_names(prefix: str, edges: tuple) -> list:
    return [f"{prefix}_le{e}" for e in edges] + [f"{prefix}_gt{edges[-1]}"]


class Summary:
    """Named integer counters plus optional histograms; ``tick`` writes the line when the interval has elapsed."""

    def __init__(self, name: str, counters: tuple, hists: Optional[dict] = None, *, every_s: float = SUMMARY_EVERY_S,
                 clock: Callable[[], float] = time.monotonic, log: Optional[logging.Logger] = None,
                 fill: Optional[Callable[[dict], None]] = None):
        """``fill(counters)``, when given, sets counters at write time (a delta of numbers the caller keeps anyway)."""
        self.name, self.counters, self.hists, self.fill = name, tuple(counters), dict(hists or {}), fill
        self.every_s, self.clock, self.log = every_s, clock, log or logger
        self.lock = threading.Lock()
        self._reset(clock())

    def _reset(self, now: float) -> None:
        self.started = now
        self.c = dict.fromkeys(self.counters, 0)
        self.h = {k: [0] * (len(edges) + 1) for k, edges in self.hists.items()}

    def add(self, **inc: int) -> None:
        """Add to counters (unknown names are ignored) and a tick. Never raises."""
        self.observe({}, **inc)

    def observe(self, values: dict, **inc: int) -> None:
        """One observation per histogram named in ``values`` (plus counter adds) and a tick. Never raises."""
        try:
            self._observe(values, inc)
        except Exception:  # noqa: BLE001
            return
        self.tick()

    def _observe(self, values: dict, inc: dict) -> None:
        with self.lock:
            for hist, value in values.items():
                edges = self.hists.get(hist)
                if edges is not None:
                    self.h[hist][bisect.bisect_left(edges, value)] += 1
            for k, v in inc.items():
                if k in self.c:
                    self.c[k] += int(v)

    def tick(self) -> bool:
        """Write the line and start the next interval when it has elapsed. True when a line was written. Never raises."""
        try:
            now = self.clock()
            if now - self.started < self.every_s:
                return False
            with self.lock:
                if now - self.started < self.every_s:
                    return False
                if self.fill is not None:
                    self.fill(self.c)
                span, c, h = now - self.started, self.c, self.h
                self._reset(now)
            parts = [f"s={int(span)}"] + [f"{k}={int(v)}" for k, v in c.items()]
            for k, edges in self.hists.items():
                parts += [f"{n}={v}" for n, v in zip(bucket_names(k, edges), h[k])]
            self.log.info("%s_SUMMARY %s", self.name, " ".join(parts))
            return True
        except Exception:  # noqa: BLE001 - an instrument never breaks the call it measures
            return False
