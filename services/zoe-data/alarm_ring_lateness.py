"""alarm_ring_lateness - how late a wall panel learns that the RINGING set changed (gap audit 2026-11-14.6, for #2472).

Counts and milliseconds only. ``changed`` is called from ``alarms._emit`` when a transition that changes the ringing set has been
committed (an alarm starts ringing, is snoozed, dismissed, auto-stopped, deleted, or edited while ringing); it notes one monotonic
time. ``seen_by_panel`` is called by ``GET /api/alarms/ringing`` when a PANEL principal reads it: the first such read after a
change writes ``ALARM_RING_SEEN event=<enum> ms=<n> changes=<n>`` (ms from the FIRST change not yet read; changes = how many changes
that read caught up with) and clears the note.

That is "change committed -> a panel re-read the ring state", on ONE clock (the server's), with or without the push of #2472: before
the push it is the 2 s poll's lateness, after it the push's (the poll stays the safety net). It is NOT ring-to-screen: the response's
trip back and the panel's paint are not in it (the panel has no way to report its render time today without a new endpoint or
a panel deploy). Only the FIRST panel to read is measured. No read within ``STALE_MS`` logs ``ms=-1`` (no panel read the change).
"""
from __future__ import annotations

import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

#: The events that change what is ringing (alarm_push.RING_SET_EVENTS on #2472's branch is the same set); plus `updated` of a
#: row that was ringing (an edit stops it). `missed` never does.
RING_SET_EVENTS = frozenset({"rang", "snoozed", "dismissed", "auto_stopped", "deleted"})
EVENTS = RING_SET_EVENTS | {"updated"}
STALE_MS = 600_000

_pending: Optional[list] = None      # [monotonic s of the first unread change, its event, changes folded in]


def changes_ring_set(event: str, alarm) -> bool:
    return event in RING_SET_EVENTS or (event == "updated" and isinstance(alarm, dict) and alarm.get("state") == "ringing")


def changed(event: str, alarm, *, clock=time.monotonic) -> None:
    """Note a committed ring-set change (in-process, never raises)."""
    global _pending
    try:
        if not changes_ring_set(event, alarm):
            return
        if _pending is not None and (clock() - _pending[0]) * 1000 > STALE_MS:
            seen_by_panel(clock=clock)  # nobody read the old one in time: it is logged ms=-1, and this change starts afresh
        if _pending is None:
            _pending = [clock(), event, 1]
        else:
            _pending[2] += 1
    except Exception:  # noqa: BLE001
        pass


def seen_by_panel(*, clock=time.monotonic) -> None:
    """A panel just read the ring state: log the first unread change's lateness once. Never raises."""
    global _pending
    try:
        p, _pending = _pending, None
        if p is None:
            return
        ms = int((clock() - p[0]) * 1000)
        ev = p[1] if p[1] in EVENTS else "other"
        logger.info("ALARM_RING_SEEN event=%s ms=%d changes=%d", ev, ms if 0 <= ms <= STALE_MS else -1, p[2])
    except Exception:  # noqa: BLE001
        pass


def reset() -> None:
    global _pending
    _pending = None
