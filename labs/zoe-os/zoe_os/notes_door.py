"""The notes and journal areas of the door: the permission tables for a change to a note or a journal entry. The mechanism (what
is recorded, ids only, the order against a forget, ask and report) is ``door.py`` and is shared with every other area; nothing
here holds a note or an entry, and no title, text, mood or tag ever reaches this process.

A note belongs to its AUTHOR and a journal entry to its WRITER (``owner``: production's ``user_id`` on the row; there is no other
owner). ``visibility`` ``household`` means every member sees the note (production's ``family``). A journal entry is never
household: production writes every one ``personal``.

The rules are household rules by ROLE (``capability.Person.role``). Not on the roster = unknown = refused (least access).

    NOTES                who                                  create                 edit / delete
    any                  its author                           allow                  allow
    private              anyone else (an adult too), the panel   refuse not_owner    refuse not_owner
    household            an adult (not its author)            refuse not_owner       allow
    household            a child, a guest (not its author)    refuse not_owner       refuse adults_only
    household            the shared panel                     refuse not_owner       refuse not_owner
    any                  not on the roster                    refuse unknown_person (all verbs)

    JOURNAL              who                                  create / edit / delete
    any                  its writer                           allow
    any                  anyone else (an adult too), the panel   refuse not_owner
    any                  not on the roster                    refuse unknown_person

    for a note or an entry of a person who is not on the roster: refuse unknown_person (``door.py``; their id is never written)

Reading the tables. A PRIVATE NOTE is its author's alone: an adult may not change or delete a child's (production does not let
them today, so the table does not either). A HOUSEHOLD note may be added by anyone in the house, as its own author (nobody
makes a note in another person's name), and changed or deleted by its author or an adult. This table is a CEILING: production
still lets only the author change any note. The shared panel, with nobody identified,
writes no notes. A JOURNAL is private to its writer: nobody else, adult or not, may add to it, change it or delete from it,
whatever the entry's visibility says. READING is not asked and is recorded nowhere. A note or an entry whose owner was not told
is treated as someone else's.
"""
from __future__ import annotations

from .capability import ADULT
from .door import ADULTS_ONLY, HOUSEHOLD, NOT_OWNER, PANEL, PRIVATE, UNKNOWN_PERSON, Area
from .spine import Decision

NOTE_VERBS = ("note.create", "note.edit", "note.delete")
JOURNAL_VERBS = ("journal.create", "journal.edit", "journal.delete")


def decide_note(people: dict, verb: str, actor: str, *, via: str | None = None, owner: str | None = None,
                visibility: str = PRIVATE) -> Decision:
    """The notes table (module docstring), one rule per line. ``people`` is the kernel's roster (``GrantStore.people``)."""
    if via == PANEL:                                              # nobody identified: the panel writes no notes
        return Decision(False, NOT_OWNER)
    who = people.get(actor)
    if who is None:
        return Decision(False, UNKNOWN_PERSON)
    if owner == actor:                                            # its author: everything
        return Decision(True)
    if visibility != HOUSEHOLD:                                   # someone else's private note: nobody, an adult included
        return Decision(False, NOT_OWNER)
    if verb == "note.create":                                     # nobody makes a note in another person's name
        return Decision(False, NOT_OWNER)
    if who.role == ADULT:                                         # a household note: an adult may change or delete it
        return Decision(True)
    return Decision(False, ADULTS_ONLY)


def decide_journal(people: dict, verb: str, actor: str, *, via: str | None = None, owner: str | None = None,
                   visibility: str = PRIVATE) -> Decision:
    """The journal table: the writer, and nobody else. ``visibility`` is not read: a journal entry is never the house's."""
    if via == PANEL:
        return Decision(False, NOT_OWNER)
    if people.get(actor) is None:
        return Decision(False, UNKNOWN_PERSON)
    return Decision(True) if owner == actor else Decision(False, NOT_OWNER)


NOTES = Area("notes", NOTE_VERBS, decide_note, target="note")
JOURNAL = Area("journal", JOURNAL_VERBS, decide_journal, target="entry")
