"""The contacts area of the door: the permission table for a change to a contact (a row of a member's address book) or to the
relationships recorded for it. The mechanism (what is recorded, ids only, the order against a forget, ask and report) is
``door.py`` and is shared with every other area; nothing here holds a contact, and no name, number, birthday, note or
relationship ever reaches this process.

A contact belongs to the member whose address book it is in (``owner``: production's ``people.user_id``). ``visibility``
``household`` means the house shares it (production's ``family``, the default on the screen); anything else is private to its
owner. A change to a contact's relationships is a ``contact.edit`` of that contact. A merge is a ``contact.edit`` of the contact
kept and a ``contact.delete`` of the one folded into it: production asks for both and makes neither if either is refused.

The rules are household rules by ROLE (``capability.Person.role``). WHO ACTS is decided from the CURRENT roster only
(``door.Door.roster``: the shadow daemon's people.json as last read): ``decide`` is handed the people on it
(``door.Door._acting``; this area is not ``former``). A member taken off the roster whom the kernel still holds a record for
(kept in its people map so that record can be forgotten) is NOBODY here, exactly as a stranger is: not in their own book, not
in anyone else's. Not on the roster = unknown = refused (least access).

    contact              who                                  create                 edit                   delete
    any                  the member whose book it is in       allow                  allow                  allow
    private              anyone else (an adult too), the panel   refuse not_owner    refuse not_owner       refuse not_owner
    household            anyone on the roster                 allow                  allow                  allow
    household            the shared panel                     allow                  refuse not_owner       refuse not_owner
    any                  not on the roster (a former member too)  refuse unknown_person (all verbs)
    in the book of a FORMER member (off the roster, still kept for forgetting): the rows above, as for any member's book
    in the book of a person the kernel does not know at all: refuse unknown_person (all verbs, whoever asks; ``door.py``;
    never written)

Reading the table. This is TODAY'S rule, kept as it is: a household contact is the whole house's, so anyone in the house may add
one, change it or delete it; a private contact is its owner's alone, from adults too; a child or a guest never changes another
member's private contact. The shared panel, with nobody identified, may add a household contact and nothing else. READING is
not asked and is recorded nowhere. A contact whose owner or visibility was not told is treated as someone else's private one.

A FORMER MEMBER'S BOOK (decided here, review of #2699): the door accepts an OWNER who is in the kernel's people map but off the
roster, so what a member left behind can still be dealt with, and it can still be forgotten. That gives nobody anything the
table forbids: the former member themself acts nowhere (above), so no owner row ever matches them; their PRIVATE contacts stay
theirs alone, refused ``not_owner`` to every current member and the panel; their HOUSEHOLD contacts are the house's, which a
current member may already add, change or delete in ANY member's book, so dealing with them is that same row (and the owner's
switch applies to them as to any). Treating the book as unknown instead would leave those household contacts frozen until a
forget, with nothing protected by it. Once forgotten, the owner is someone the kernel does not know: refused, never written.

ONE LINE TO SWITCH, a household choice for the owner: ``HOUSEHOLD_CHANGE_ADULTS_ONLY``. Set, a household contact can still be
added by anyone but is changed or deleted only by its owner or an adult (a child's or a guest's change is ``adults_only``).
"""
from __future__ import annotations

from .capability import ADULT
from .door import ADULTS_ONLY, HOUSEHOLD, NOT_OWNER, PANEL, PRIVATE, UNKNOWN_PERSON, Area
from .spine import Decision

VERBS = ("contact.create", "contact.edit", "contact.delete")
CREATE = "contact.create"
#: False = today's rule (anyone in the house changes a household contact). True = only its owner or an adult does.
HOUSEHOLD_CHANGE_ADULTS_ONLY = False


def decide(people: dict, verb: str, actor: str, *, via: str | None = None, owner: str | None = None,
           visibility: str = PRIVATE) -> Decision:
    """The whole permission table (module docstring), one rule per line. ``people``: the CURRENT roster's people, handed in by
    ``door.Door._acting`` (not the kernel's whole people map, which also keeps former members for forgetting)."""
    panel = via == PANEL
    who = None if panel else people.get(actor)
    if not panel and who is None:
        return Decision(False, UNKNOWN_PERSON)
    if who is not None and owner == actor:                        # in their own book: everything
        return Decision(True)
    if visibility != HOUSEHOLD:                                   # someone else's private contact: nobody, an adult included
        return Decision(False, NOT_OWNER)
    if verb == CREATE:                                            # a household contact: anyone in the house, or the panel, may add one
        return Decision(True)
    if panel:                                                     # ... the panel adds, and nothing else
        return Decision(False, NOT_OWNER)
    if HOUSEHOLD_CHANGE_ADULTS_ONLY and who.role != ADULT:        # (the owner's switch; off = today's rule)
        return Decision(False, ADULTS_ONLY)
    return Decision(True)                                         # ... and anyone in the house may change or delete it


AREA = Area("people", VERBS, decide, target="contact")
