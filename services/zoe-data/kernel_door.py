"""The service half of a Zoe OS door, shared by every area (lists: ``list_service``; reminders: ``reminder_service``).

The design (docs/architecture/zoe-os-cutover-plan.md, "Changed 2026-11-18"): an area's ONE door asks the kernel "may this person
do this?" before its write, the kernel answers and records ids only, the door writes in its own store, then reports how the write
ended. A kernel "no" is final (``Refused``); a kernel that is down or slow is ``not_checked`` and the change proceeds.

What is here is what every area's door does the same way: what an ask returns (``Decision``), the refusal a caller turns into
its own words (``Refused``; ONE app-level handler makes it a 403), and WHEN a report goes out: at once for a write that stands
on its own, or once the caller's own transaction has ended (``reports_after``: ``done`` after its commit, ``failed`` if it rolled
back). The wire half (queue, thread, budget, stamps, log line) is ``os_shadow``'s door section. An area's own door keeps only
what differs: its verbs, how it reads who a thing belongs to, and its refusal sentences.
"""
from __future__ import annotations

import contextvars
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Optional

import os_shadow
import panel_principal


class Refused(Exception):
    """The kernel refused this change. FINAL: nothing was written. ``reason`` is a closed code (``os_shadow.DOOR_REFUSALS``);
    ``line()`` is the plain sentence every caller shares (no names, no text of the thing). An area subclasses this."""

    area = ""
    lines: dict = {}
    fallback = "I'm not allowed to change that."

    def __init__(self, reason: str = "other") -> None:
        super().__init__(reason)
        self.reason = reason

    def line(self) -> str:
        return self.lines.get(self.reason, self.fallback)


async def refused_response(_request, exc: Refused):
    """The ONE app-level handler (``main.py``, registered for ``Refused`` so it covers every area's subclass): a change the kernel
    refused is a 403 with the area's plain sentence; nothing was written."""
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=403, content={"detail": exc.line()})


@dataclass
class Decision:
    """What the kernel said about one change: ``allow`` | ``refuse`` | ``not_checked`` (the change proceeds) | ``off``."""
    decision: str
    reason: str = ""
    ask: Optional[int] = None
    reported: bool = False
    stamps: Optional[dict] = None       # what the ask carried (``os_shadow.erase_stamp`` per member named); its report carries the same
    area: str = "lists"


OFF = Decision("off")
#: allowed asks whose write sits inside a caller's transaction: reported once that transaction has committed (or failed)
_HELD: contextvars.ContextVar = contextvars.ContextVar("zoe_kernel_door_held", default=None)


def is_panel(user_id: Optional[str]) -> bool:
    """Nobody identified: the shared panel acting for the house (a guest id, or the household's own owner / viewer id)."""
    return panel_principal.is_guest_id(user_id) or user_id in (panel_principal.HOUSEHOLD_OWNER_ID, panel_principal.HOUSEHOLD_VIEWER)


def stamps_now() -> Optional[dict]:
    """The erase stamps as they are NOW (``os_shadow.stamp_snapshot``: a small copy from memory, no I/O), or None with every
    door off (a flag read per area, nothing copied). A caller that TELLS a door who a thing belongs to (starting a list, making
    a reminder: no row to read the owner from yet) takes this as the first thing it does, before anything it awaits, and hands
    it to the door as ``stamps_at``: a member whose forget is confirmed while the caller is still on its way to the door keeps
    the stamp from before it. The snapshot is the same for every area, so one serves a caller that reaches two doors."""
    return os_shadow.stamp_snapshot() if os_shadow.doors_on() else None


def report(decision: Optional[Decision], outcome: str) -> None:
    """Close an allowed ask with ``done`` | ``failed`` (once). A no-op for anything that was not an allowed ask."""
    if decision is not None and decision.decision == "allow" and not decision.reported:
        decision.reported = True
        os_shadow.report_door(decision.area, decision.ask, outcome, decision.stamps)


def wrote(decision: Optional[Decision], outcome: str = "done") -> None:
    """A door's write returned. Inside a held transaction the report waits for the commit; otherwise it goes now (a statement on
    its own has committed)."""
    if decision is None:
        return
    held = _HELD.get()
    if outcome == "done" and held is not None:
        held.append(decision)
    else:
        report(decision, outcome)


async def write(decision: Optional[Decision], pending):
    """Run one door's write and tell the kernel how it ended."""
    try:
        result = await pending
    except BaseException:
        wrote(decision, "failed")
        raise
    wrote(decision)
    return result


@asynccontextmanager
async def reports_after():
    """Around a transaction that holds door writes (of any area): their reports go out when the block exits (``done``), or
    ``failed`` if it raised (rolled back). Nested use joins the outer block. With every door off: a flag read per area."""
    if _HELD.get() is not None or not os_shadow.doors_on():
        yield
        return
    held: list = []
    token = _HELD.set(held)
    try:
        yield
    except BaseException:
        _HELD.reset(token)
        for decision in held:
            report(decision, "failed")
        raise
    _HELD.reset(token)
    for decision in held:
        report(decision, "done")
