"""The reminders area of the door: the permission table for a change to a household reminder. The mechanism (what is recorded,
ids only, the order against a forget, ask and report) is ``door.py`` and is shared with every other area; nothing here holds a
reminder, and no title, day or time ever reaches this process.

A reminder belongs to the person it is FOR (``owner``: production's ``reminders.user_id``). ``visibility`` ``household`` means
it is addressed to the house (production's ``family``); anything else is private to that person. Production's reminder rows hold
no separate "who made it": the person it is for is the only member a reminder names, besides whoever is acting now.

The rules are household rules by ROLE (``capability.Person.role``). Not on the roster = unknown = refused (least access).

    reminder             who                               create / edit / delete     done / snooze
    any                  the person it is for              allow                      allow
    private              an adult, and it is for a child   allow                      allow
    private              anyone else, the shared panel     refuse not_owner           refuse not_owner
    household            an adult                          allow                      allow
    household            a child, a guest (not theirs)     refuse adults_only         allow
    household            the shared panel                  refuse adults_only         allow
    any                  not on the roster                 refuse unknown_person (all verbs)
    for a person who is not on the roster: refuse unknown_person (all verbs, whoever asks; ``door.py``; their id is never written)

Reading the table: a person may do everything to their own reminders. An adult may make, change, complete and delete a reminder
FOR a child (an adult's private reminder is nobody else's, another adult's included). A reminder addressed to the house can be
ANSWERED by anyone in it and by the shared panel with nobody identified (``reminder.done``, ``reminder.snooze``: the alert rang
for everyone), but only an adult, or the person it is for, may change or delete it. A child or a guest never changes another
person's reminder. A reminder whose owner or visibility was not told is treated as someone else's private one.
"""
from __future__ import annotations

from .capability import ADULT, CHILD
from .door import ADULTS_ONLY, HOUSEHOLD, NOT_OWNER, PANEL, PRIVATE, UNKNOWN_PERSON, Area
from .spine import Decision

CHANGE_VERBS = ("reminder.create", "reminder.edit", "reminder.delete")
ANSWER_VERBS = ("reminder.done", "reminder.snooze")
VERBS = ("reminder.create", "reminder.edit", "reminder.done", "reminder.snooze", "reminder.delete")


def decide(people: dict, verb: str, actor: str, *, via: str | None = None, owner: str | None = None,
           visibility: str = PRIVATE) -> Decision:
    """The whole permission table (module docstring), one rule per line. ``people`` is the kernel's roster (``GrantStore.people``)."""
    panel = via == PANEL
    who = None if panel else people.get(actor)
    if not panel and who is None:
        return Decision(False, UNKNOWN_PERSON)
    if who is not None and owner == actor:                        # their own reminder: everything
        return Decision(True)
    if visibility != HOUSEHOLD:                                   # someone else's private one: only an adult, FOR a child
        whom = people.get(owner) if owner else None
        if who is not None and who.role == ADULT and whom is not None and whom.role == CHILD:
            return Decision(True)
        return Decision(False, NOT_OWNER)
    if who is not None and who.role == ADULT:                     # addressed to the house: an adult, everything
        return Decision(True)
    if verb in ANSWER_VERBS:                                      # ... and anyone in the house, or the panel, may answer it
        return Decision(True)
    return Decision(False, ADULTS_ONLY)


AREA = Area("reminders", VERBS, decide, target="reminder")
