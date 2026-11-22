"""The Zoe OS door for ROUTINES (``ZOE_OS_ROUTINES=kernel``; default off; docs/knowledge/zoe-os-routines-door.md).

``routines_store.py`` is the one writer of the four routines tables; this is what its entry points call.

  * create / edit (a template step, a one-off line) / delete  ASK the kernel before writing, and only AFTER every check of the
    store's own (who may edit, ``MAX_STEPS``, a closed day, the write gate's erase and forgotten-words checks): the kernel never
    hears, or refuses, a change the store itself would refuse. A "no" is final: nothing is written and ``RoutineRefused`` is raised. A kernel that is down, old or slow is ``not_checked``: the change proceeds.
  * tick / skip / snooze / undo  are MADE FIRST and the kernel is TOLD afterwards: the person has already done the thing and a
    spoken walk has nowhere to wait. The store's own rules (a medicine line needs its verified subject; a closed day is
    settled; the said tick acts only on the step Zoe just named) run BEFORE the change and are not the kernel's.

The verb says whether someone else set the routine up for its subject (``_given``): the kernel decides "a child does not
change a routine set up for them" without learning who the creator is.

ON THE WIRE: the verb, the acting member's id (or the panel), the routine's id, its subject's id, household or private, the
erase stamps. NEVER a routine's name, a step's words or a how-to.
"""
from __future__ import annotations

from typing import Any, Optional

import kernel_door
import os_shadow

AREA = "routines"
CREATE, EDIT, DELETE = "routine.create", "routine.edit", "routine.delete"
TOLD = {"done": "routine.tick", "skipped": "routine.skip", "snoozed": "routine.snooze", "pending": "routine.undo"}


class RoutineRefused(kernel_door.Refused):
    """The kernel refused this change to a routine. FINAL: nothing was written."""

    area = AREA
    lines = {
        "not_owner": "That routine isn't yours to change, so I've left it as it is.",
        "adults_only": "That one needs a grown-up, so I've left it as it is.",
        "unknown_person": "I don't know who is asking, so I haven't changed the routine.",
    }
    fallback = "I'm not allowed to change that routine."


def stamps_now() -> Optional[dict]:
    """First statement of a routines entry point, before anything is awaited (None while the door is off)."""
    return kernel_door.stamps_now(AREA)


def change(verb: str, routine: dict) -> str:
    """``verb``, or its ``_given`` form when someone other than its subject set this routine up."""
    return verb + "_given" if routine.get("created_by") not in (None, "", routine.get("user_id")) else verb


async def ask(verb: str, actor: Any, routine_id: str, *, owner: Any, household: bool,
              stamps_at: Optional[dict]) -> Optional[kernel_door.Decision]:
    """Before a routine is created or changed. None: not asked (door off). Raises ``RoutineRefused`` on a kernel "no"."""
    return await kernel_door.gate(AREA, verb, actor=str(actor or "") or "guest", target=routine_id, owner=owner,
                                  household=household, stamps_at=stamps_at, refused=RoutineRefused)


def told(state: str, actor: Any, routine: dict, stamps_at: Optional[dict]) -> None:
    """A step was ticked, skipped, snoozed or put back: tell the kernel. Never waits, never raises, never refuses."""
    if stamps_at is None or not os_shadow.area_on(AREA):
        return
    who = str(actor or "")
    panel = not who or kernel_door.is_panel(who)
    os_shadow.record_door(AREA, TOLD[state], "" if panel else who, panel=panel, target=str(routine["id"]),
                          owner=str(routine.get("user_id") or "") or None, household=routine.get("visibility") == "family",
                          stamps_at=stamps_at)
