"""The routines area of the door: the permission table for a change to a routine (a member's morning, a child's routine an
adult set up, a household routine) and for ticking its steps. The mechanism (what is recorded, ids only, the order against a
forget, ask and report) is ``door.py``. Nothing here holds a routine: no name, no step text, no how-to reaches this process.

A routine belongs to the member it is FOR (``owner``: production's ``routines.user_id``, the subject). ``visibility``
``household`` means the house shares it (production's ``family``). Production also says, in the VERB, whether someone else set
the routine up for its subject: ``routine.edit_given`` / ``routine.delete_given`` are an edit / a delete of a routine whose
creator is not its subject (the kernel never learns who the creator is).

ASKED FIRST: ``routine.create``, ``routine.edit`` (a template step or a one-off line is an edit), ``routine.delete`` and the
two ``_given`` forms. TOLD AFTERWARDS: ``routine.tick``, ``routine.skip``, ``routine.snooze``, ``routine.undo``: the person
has already done the thing and a spoken walk has nowhere to wait, so production makes the change (after its own rules: a
medicine line needs its verified subject; a closed day is settled) and tells the kernel; "refuse" below then only means the
record says OBJECTED.

    routine     who                                    create / edit / delete                  tick / skip / snooze / undo
    any         its subject                            allow (1)                               recorded
    private     an adult, for a CHILD's routine        allow                                   objected not_owner (2)
    private     anyone else                            refuse not_owner                        objected not_owner
    household   anyone on the roster                   allow (3)                               recorded
    household   the shared panel                       refuse not_owner                        recorded (the fridge rule)
    private     the shared panel                       refuse not_owner                        objected not_owner
    any         not on the roster                      refuse unknown_person                   objected unknown_person

(1) EXCEPT a CHILD editing or deleting a routine someone else set up for them (the ``_given`` verbs): ``adults_only``. This
    follows the owner's acceptance words (docs/knowledge/routines.md line 10: the child ticks their routine; only a parent
    changes it), not production's looser code. ONE LINE restores the looser rule: ``CHILD_MAY_CHANGE_GIVEN = True``.
(2) An adult who set a child's routine up does not tick it for them: nothing is ticked that the person did not tick.
(3) Today's rule, kept. ONE LINE makes it stricter: ``HOUSEHOLD_CHANGE_STRICT = True`` (only its subject or an adult edits or
    deletes a household routine; anyone still creates one).
"""
from __future__ import annotations

from .capability import ADULT, CHILD
from .door import ADULTS_ONLY, HOUSEHOLD, NOT_OWNER, PANEL, PRIVATE, UNKNOWN_PERSON, Area
from .spine import Decision

CREATE, EDIT, DELETE, EDIT_GIVEN, DELETE_GIVEN = ("routine.create", "routine.edit", "routine.delete", "routine.edit_given",
                                                  "routine.delete_given")
TOLD_VERBS = ("routine.tick", "routine.skip", "routine.snooze", "routine.undo")
GIVEN = (EDIT_GIVEN, DELETE_GIVEN)
VERBS = (CREATE, EDIT, DELETE, EDIT_GIVEN, DELETE_GIVEN, *TOLD_VERBS)
#: False = the owner's acceptance words (a child does not change a routine set up for them). True = production's looser rule.
CHILD_MAY_CHANGE_GIVEN = False
#: False = today's rule (anyone in the house changes a household routine). True = only its subject or an adult does.
HOUSEHOLD_CHANGE_STRICT = False


def decide(people: dict, verb: str, actor: str, *, via: str | None = None, owner: str | None = None,
           visibility: str = PRIVATE) -> Decision:
    """The whole permission table (module docstring), one rule per line. ``people`` is the kernel's roster."""
    panel = via == PANEL
    who = None if panel else people.get(actor)
    told = verb in TOLD_VERBS
    if not panel and who is None:
        return Decision(False, UNKNOWN_PERSON)
    if panel:                                                     # nobody identified: ticks a household routine, nothing else
        return Decision(True) if told and visibility == HOUSEHOLD else Decision(False, NOT_OWNER)
    if owner == actor:                                            # their own routine
        if verb in GIVEN and who.role == CHILD and not CHILD_MAY_CHANGE_GIVEN:
            return Decision(False, ADULTS_ONLY)                   # ... set up for a child by someone else: the child ticks, a parent changes
        return Decision(True)
    if visibility == HOUSEHOLD:                                   # the house's routine: anyone in the house
        if HOUSEHOLD_CHANGE_STRICT and not told and verb != CREATE and who.role != ADULT:
            return Decision(False, ADULTS_ONLY)
        return Decision(True)
    subject = people.get(owner) if owner else None
    if not told and who.role == ADULT and subject is not None and subject.role == CHILD:
        return Decision(True)                                     # an adult sets up and changes a child's routine (never ticks it)
    return Decision(False, NOT_OWNER)


AREA = Area("routines", VERBS, decide, target="routine")
