"""The lookups area of the door: a RECORD that someone in the house looked something up off the box (the weather, a web
search, a page fetch). Lookups are read-only for the household, so there is nothing to permit: production MAKES the lookup and
TELLS the kernel afterwards. The kernel is never asked first and can neither delay nor stop one.

What is recorded: who, and the CLASS of lookup (a closed verb). NEVER the query, the place, the URL or the result: the wire
has no field for them and this area has no target.

    who                                  lookup.weather / lookup.web_search / lookup.web_fetch
    anyone on the roster (a child too)   recorded
    the shared panel, nobody identified  recorded
    not on the roster                    objected unknown_person  (production itself refuses to send a lookup in the name of
                                         someone it knows is not on the roster: it would leave the box in their name)

There is NO child rule for the web in production today (no safe-search setting, no per-child block), so there is none here:
a child's lookup is recorded like anyone's. If one is added it belongs in production first and is mirrored here.
"""
from __future__ import annotations

from .door import PANEL, PRIVATE, UNKNOWN_PERSON, Area
from .spine import Decision

VERBS = ("lookup.weather", "lookup.web_search", "lookup.web_fetch")
TOLD_VERBS = VERBS


def decide(people: dict, verb: str, actor: str, *, via: str | None = None, owner: str | None = None,
           visibility: str = PRIVATE) -> Decision:
    """The whole table: anyone in the house, and the panel."""
    if via == PANEL or people.get(actor) is not None:
        return Decision(True)
    return Decision(False, UNKNOWN_PERSON)


AREA = Area("lookups", VERBS, decide, target="lookup")
