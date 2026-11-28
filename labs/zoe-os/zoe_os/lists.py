"""The lists area of the door: the permission table for a household list change. The mechanism (what is recorded, ids only,
the order against a forget, ask and report) is ``door.py`` and is shared with every other area; nothing here holds a list.

The rules are household rules by ROLE (``capability.Person.role``). Not on the roster = unknown = refused (least access).

    list                 who                        item.add/remove/tick/edit   list.rename/delete/share   list.create
    private              its owner                  allow                       allow                      allow (their own)
    private              anyone else, the panel     refuse not_owner            refuse not_owner           refuse not_owner
    household            its owner                  allow                       allow                      allow
    household            an adult                   allow                       allow                      allow
    household            a child, a guest           allow                       refuse adults_only         allow
    household            the shared panel           allow                       refuse adults_only         allow
    any                  not on the roster          refuse unknown_person (all verbs)
    of an owner who is not on the roster: refuse unknown_person (all verbs, whoever asks; the owner's id is never written)
"""
from __future__ import annotations

from .capability import ADULT
from .door import (ADULTS_ONLY, ERASED, HOUSEHOLD, NOT_IN_MANIFEST, NOT_OWNER, PANEL, PRIVATE,  # noqa: F401 - the codes and the id
                   UNKNOWN_PERSON, Area, Door, _id)                                            # rule, re-exported
from .spine import Decision

ITEM_VERBS = ("item.add", "item.remove", "item.tick", "item.edit")
LIST_VERBS = ("list.rename", "list.delete", "list.share")
CREATE = "list.create"
VERBS = (CREATE, *LIST_VERBS, *ITEM_VERBS)


def decide(people: dict, verb: str, actor: str, *, via: str | None = None, owner: str | None = None,
           visibility: str = PRIVATE) -> Decision:
    """The whole permission table (module docstring), one rule per line. ``people`` is the kernel's roster (``GrantStore.people``)."""
    panel = via == PANEL
    who = None if panel else people.get(actor)
    if not panel and who is None:
        return Decision(False, UNKNOWN_PERSON)
    if visibility != HOUSEHOLD:                                   # a private list: its owner only, for everything
        return Decision(True) if who is not None and owner == actor else Decision(False, NOT_OWNER)
    if verb in ITEM_VERBS or verb == CREATE:                      # a household list: any member, and the panel for the house
        return Decision(True)
    if who is not None and (owner == actor or who.role == ADULT):  # rename / delete / share: its owner or an adult
        return Decision(True)
    return Decision(False, ADULTS_ONLY)


AREA = Area("lists", VERBS, decide, target="list", parts="items")


class ListDoor:
    """The lists face of the one door, by its own argument names (``list_ask`` / ``list_report`` on the wire)."""

    def __init__(self, door: Door) -> None:
        self.door, self.spine, self.enforcer = door, door.spine, door.enforcer

    def ask(self, skill: str, verb: str, person: str, *, via: str | None = None, list_id: str | None = None, items=(),
            owner: str | None = None, visibility: str | None = None, turn: str | None = None, stamps=None) -> dict:
        return self.door.ask(skill, AREA.name, verb, person, via=via, target=list_id, parts=items, owner=owner,
                             visibility=visibility, turn=turn, stamps=stamps)

    def report(self, skill: str, ask: int, outcome: str, *, stamps=None) -> dict:
        return self.door.report(skill, ask, outcome, stamps=stamps)
