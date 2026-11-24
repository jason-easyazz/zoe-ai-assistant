"""The calendar area of the door: the permission table for a change to a calendar event. The mechanism (what is recorded, ids
only, the order against a forget, ask and report) is ``door.py`` and is shared with every other area; nothing here holds an
event, and no title, day, time, place or guest ever reaches this process.

An event belongs to the person whose calendar it is on (``owner``: production's ``events.user_id``). Production holds no separate
"who made it": the person whose calendar it is on is the one who made it. ``visibility`` ``household`` means the whole house sees
it (production's ``family``); anything else (``personal``, ``busy``) is private to that person. An event the HOUSE made at the
shared panel is on nobody's calendar (production keeps it under a household marker, which is not a person): it is told with no
owner, and the shared panel is its maker.

The rules are household rules by ROLE (``capability.Person.role``). Not on the roster = unknown = refused (least access).

    event                who                                  create                 edit / delete
    any                  the person whose calendar it is on   allow                  allow
    private              an adult, and it is a child's        allow                  allow
    private              anyone else, the shared panel        refuse not_owner       refuse not_owner
    household            an adult                             allow                  allow
    household            a child, a guest (not theirs)        allow                  refuse adults_only
    household, a person's   the shared panel                  allow                  refuse adults_only
    household, the house's  the shared panel (its maker)      allow                  allow
    any                  not on the roster                    refuse unknown_person (all verbs)
    on the calendar of a person who is not on the roster: refuse unknown_person (all verbs, whoever asks; ``door.py``; never written)

Reading the table. A person may do everything to their own events. An adult may add, change and delete an event FOR a child
(another adult's private event is nobody else's). Anyone in the house, and the shared panel with nobody identified, may ADD a
household event. A household event is changed or deleted only by the person whose calendar it is on (its maker) or by an adult;
the shared panel may change or delete only the house's own (the ones made at the panel, which name no person). A child or a guest
never changes another person's event. READING is not asked and is recorded nowhere. An event whose owner or visibility was not
told is treated as someone else's private one.
"""
from __future__ import annotations

from .capability import ADULT, CHILD
from .door import ADULTS_ONLY, HOUSEHOLD, NOT_OWNER, PANEL, PRIVATE, UNKNOWN_PERSON, Area
from .spine import Decision

VERBS = ("event.create", "event.edit", "event.delete")
CREATE = "event.create"


def decide(people: dict, verb: str, actor: str, *, via: str | None = None, owner: str | None = None,
           visibility: str = PRIVATE) -> Decision:
    """The whole permission table (module docstring), one rule per line. ``people`` is the kernel's roster (``GrantStore.people``)."""
    panel = via == PANEL
    who = None if panel else people.get(actor)
    if not panel and who is None:
        return Decision(False, UNKNOWN_PERSON)
    if who is not None and owner == actor:                        # on their own calendar: everything
        return Decision(True)
    if visibility != HOUSEHOLD:                                   # someone else's private one: only an adult, FOR a child
        whom = people.get(owner) if owner else None
        if who is not None and who.role == ADULT and whom is not None and whom.role == CHILD:
            return Decision(True)
        return Decision(False, NOT_OWNER)
    if verb == CREATE:                                            # a household event: anyone in the house, or the panel, may add one
        return Decision(True)
    if who is not None and who.role == ADULT:                     # ... an adult may change or delete it
        return Decision(True)
    if panel and owner is None:                                   # ... and the panel its own (the house's, on nobody's calendar)
        return Decision(True)
    return Decision(False, ADULTS_ONLY)


AREA = Area("calendar", VERBS, decide, target="event")
