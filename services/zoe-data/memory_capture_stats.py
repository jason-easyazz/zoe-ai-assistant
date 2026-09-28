"""Per-user counters for the post-turn memory capture.

``routers/chat.py`` schedules ``_persist_memory_candidates`` with
``asyncio.ensure_future``: the HTTP turn returns BEFORE the regex extractor and
the LLM turn digest have run, and a deduplicated candidate never becomes a
visible row — so nothing else lets a caller observe that a turn's capture has
COMPLETED. These counters do. ``GET /api/memories/capture-status?user_id=``
(internal token) exposes them; ``scripts/perf/samantha_bar.py`` polls them
before scoring S7 (the short-duplicate scenario) instead of sleeping.

In-process only (a restart resets them), bounded to ``_MAX_USERS`` ids.
"""
from __future__ import annotations

import threading
import time
from typing import Any

_MAX_USERS = 1024
_LOCK = threading.Lock()
_STATS: dict[str, dict[str, Any]] = {}


def _entry(user_id: str) -> dict[str, Any]:
    s = _STATS.get(user_id)
    if s is None:
        if len(_STATS) >= _MAX_USERS:
            _STATS.pop(next(iter(_STATS)))  # oldest-inserted id
        s = _STATS[user_id] = {"started": 0, "completed": 0, "failed": 0,
                               "last_completed_at": None}
    return s


def started(user_id: str) -> None:
    with _LOCK:
        _entry(user_id)["started"] += 1


def completed(user_id: str, *, ok: bool = True) -> None:
    with _LOCK:
        s = _entry(user_id)
        s["completed"] += 1
        if not ok:
            s["failed"] += 1
        s["last_completed_at"] = time.time()


def snapshot(user_id: str) -> dict[str, Any]:
    """Counters for ``user_id`` (zeros when never seen) plus ``in_flight``."""
    with _LOCK:
        s = dict(_STATS.get(user_id) or {"started": 0, "completed": 0, "failed": 0,
                                         "last_completed_at": None})
    s["in_flight"] = max(0, s["started"] - s["completed"])
    return s


def reset() -> None:  # tests
    with _LOCK:
        _STATS.clear()
