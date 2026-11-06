"""memory_views - the read-only report over the pointer views (episodes, slot conflicts + ageing, dormant threads) for ONE member.

Why (Samantha initiative item 15, lane C E4 / E7 / episodes): each view ships flag-dark, and the lane's rule is "shadow first,
measure, then serve". This is the measurement: one call per member that runs every view over the member's own rows, turns and
threads and returns COUNTS AND IDS ONLY - never a row's text, a quote, a value or a turn. The owner reads it before any view is
switched on (``scripts/maintenance/memory_views_report.py``).

The views it runs (each documented in its own module):

* ``memory_episodes``  - the member's conversations over the last ``days`` as turn-id clusters;
* ``memory_slot_check`` - person-keyed single-value conflicts (held / hidden ids, the one question) and the "still true?" rows;
* ``dormant_threads``  - today's own turns against the member's quiet night-mind threads (at most one candidate).

Nothing here writes anything, and every input is injectable so the report runs on a synthetic household in the tests.
"""
from __future__ import annotations

import datetime as _dt
import logging
from typing import Any, Awaitable, Callable, Iterable, Optional, Sequence

import dormant_threads
import memory_episodes
import memory_slot_check

logger = logging.getLogger(__name__)

DEFAULT_DAYS = 7


async def _today_signals(user_id: str, start: _dt.datetime, end: _dt.datetime) -> "list[dormant_threads.Signal]":
    """The member's own-words turns of the day as signals (``memory_digest.load_day_messages``: ownership, own words, forgotten turns
    skipped, off-the-record excluded). The text lives only inside this report call."""
    import memory_digest

    tr = await memory_digest.load_day_messages(user_id, start.isoformat(), end.isoformat())
    day = start.date().isoformat()
    return [dormant_threads.Signal("turn", str(mid), str(text), day) for mid, text in getattr(tr, "turns", ()) or () if mid and text]


async def report(user_id: str, rows: Iterable[Any], *, now: Optional[float] = None, days: int = DEFAULT_DAYS,
                 tz: Optional[_dt.tzinfo] = None,
                 pointers: Optional[Callable[[str, _dt.datetime, _dt.datetime], Awaitable[Sequence[Any]]]] = None,
                 signals: Optional[Callable[[str, _dt.datetime, _dt.datetime], Awaitable[Sequence[Any]]]] = None,
                 threads_backend: Any = None) -> "dict[str, Any]":
    """Every view for one member. ``rows`` = the member's memory rows (``{id, text, metadata}`` or ``MemoryRef``); the loaders
    default to the live reads. A view whose read fails reports ``{"error": <class>}`` and the others still run."""
    import time

    if tz is None:
        from time_utils import zoe_timezone

        tz = zoe_timezone()
    t = time.time() if now is None else now
    local_now = _dt.datetime.fromtimestamp(t, tz)
    day_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    out: "dict[str, Any]" = {"user_id": user_id}

    mine = [r for r in rows]
    out["slots"] = memory_slot_check.report(mine, t)

    try:
        load = pointers or memory_episodes.load_turn_pointers
        eps = memory_episodes.build(user_id, await load(user_id, day_start - _dt.timedelta(days=max(0, days - 1)), local_now))
        out["episodes"] = {"days": days, "count": len(eps), "turns": sum(e.n_turns for e in eps),
                           "longest_turns": max((e.n_turns for e in eps), default=0), "ids": [e.id for e in eps]}
    except Exception as exc:  # noqa: BLE001 - one view's read failing is reported, never fatal
        out["episodes"] = {"error": type(exc).__name__}

    try:
        import night_store

        threads = await (threads_backend or night_store.get_backend()).threads(user_id)
        sig = await (signals or _today_signals)(user_id, day_start, local_now)
        today = local_now.date()
        found = dormant_threads.candidates(threads, sig, today)
        out["dormant"] = {"threads": len(threads),
                          "dormant": sum(1 for th in threads if dormant_threads.is_dormant(th, today) and not dormant_threads.is_leave(th)),
                          "signals": len(sig), "candidates": [c.as_dict() for c in found]}
    except Exception as exc:  # noqa: BLE001
        out["dormant"] = {"error": type(exc).__name__}
    return out
