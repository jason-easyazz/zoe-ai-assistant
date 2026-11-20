"""The Zoe OS door for MEMORY (``ZOE_OS_MEMORY=kernel``; default off; docs/knowledge/zoe-os-memory-door.md).

The memory rows have one writer, ``memory_service.MemoryService``, and every mutating call says who is acting
(``memory_actor``). The door sits INSIDE those methods: with the flag on and a MEMBER acting,

  * remember / correct / approve  ASK the kernel first ("may this person change this row?"). A "no" is final: nothing is
    written and ``MemoryRefused`` is raised. A kernel that is down, old or slow is ``not_checked``: the change proceeds.
  * forget (an erase, a rejected or archived row, a whole-member erase)  is MADE FIRST and the kernel is TOLD AFTERWARDS. It is
    never asked, never delayed, and nothing here stands in front of an erase. The existing fail-closed forget step
    (``os_shadow.forget_for_erase`` / ``forget_user`` / the parked plan) is a different thing and is untouched.

AUTOMATIC writers are neither asked nor told. With the flag off nothing here runs past one flag read.

ON THE WIRE, and in the one log line: the area, the verb (a closed code), the acting member's id, the row's id, its owner's
id, household or private, and the erase stamps. NEVER memory text, a name, an excerpt, a tag or a reason string.
"""
from __future__ import annotations

import contextvars
import functools
import re
from typing import Any, Optional

import kernel_door
import memory_actor
import os_shadow

AREA = "memory"
REMEMBER, CORRECT, APPROVE, FORGET = "memory.remember", "memory.correct", "memory.approve", "memory.forget"
_ID = re.compile(r"[A-Za-z0-9._:@_-]{1,80}\Z")          # what the kernel accepts as an id; anything else is not sent
#: per call of a mutating method: what was asked (to report) and what is to be told once the call has ended well
_CALL: contextvars.ContextVar = contextvars.ContextVar("zoe_memory_door_call", default=None)


class MemoryRefused(kernel_door.Refused):
    """The kernel refused this change to a memory. FINAL: nothing was written."""

    area = AREA
    lines = {
        "not_owner": "That memory isn't yours to change, so I've left it as it is.",
        "adults_only": "That one needs a grown-up, so I've left it as it is.",
        "unknown_person": "I don't know who is asking, so I haven't changed what I remember.",
    }
    fallback = "I'm not allowed to change that memory."


def on(acting: Any) -> bool:
    """Is this call a member's, with the door on? (An automatic writer: never. Flag off: one flag read.)"""
    return isinstance(acting, memory_actor.Member) and os_shadow.area_on(AREA)


def _ids(acting: memory_actor.Member, row: Any, owner: Any, household: bool) -> tuple:
    actor = acting.user_id
    panel = not actor or kernel_door.is_panel(actor)        # nobody identified: an unverified panel, a guest
    whose = str(owner or "")
    return ("" if panel else actor), {
        "panel": panel, "target": row if isinstance(row, str) and _ID.match(row) else None,
        "owner": None if (not whose or kernel_door.is_panel(whose)) else whose, "household": bool(household),
        "stamps_at": acting.stamps if acting.stamps is not None else os_shadow.stamp_snapshot()}


async def ask(acting: Any, verb: str, *, owner: Any, household: bool, row: Any = None) -> Optional[kernel_door.Decision]:
    """Before a member's remember / correct / approve is written. None: not asked. Raises ``MemoryRefused`` on a kernel "no"."""
    if not on(acting):
        return None
    person, ids = _ids(acting, row, owner, household)
    decision, reason, ask_no, stamps = await os_shadow.ask_door(AREA, verb, person, **ids)
    if decision == "refuse":
        raise MemoryRefused(reason)
    asked = kernel_door.Decision(decision, reason, ask_no, stamps=stamps, area=AREA)
    call = _CALL.get()
    if call is not None:
        call.append(asked)
    return asked


def tell_after(acting: Any, *, owner: Any, household: bool = False, row: Any = None) -> None:
    """A member's forget is about to be made: once the call has ended well the kernel is TOLD (never asked, never waited for)."""
    if not on(acting):
        return
    call = _CALL.get()
    if call is not None:
        call.append((FORGET, *_ids(acting, row, owner, household)))


def _close(call: list, ok: bool) -> None:
    for item in call:
        if isinstance(item, kernel_door.Decision):
            kernel_door.wrote(item, "done" if ok else "failed")
        elif ok:
            verb, person, ids = item
            os_shadow.record_door(AREA, verb, person, **ids)


def reporting(method):
    """Around a mutating ``MemoryService`` method: what its body asked is reported ``done`` or ``failed`` when it returns
    (None / False / an exception = not made), and what it said it would tell is told only if it ended well. With the door off
    or an automatic caller the method runs exactly as it is."""
    @functools.wraps(method)
    async def run(self, *args, **kw):
        if not on(kw.get("acting")):
            return await method(self, *args, **kw)
        call: list = []
        token = _CALL.set(call)
        try:
            try:
                out = await method(self, *args, **kw)
            except BaseException:
                _close(call, False)
                raise
            _close(call, out is not None and out is not False)
            return out
        finally:
            _CALL.reset(token)

    return run
