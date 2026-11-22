"""The music area of the door: the kernel decides and records a member's music action. Nothing is stored anywhere for music:
the action is a command to the music engine, sent by production. No track, artist, search or playlist name reaches this
process: the verb, who asked, and the id of the speaker.

Music is low-stakes and belongs to the whole house:

    who                                  any music verb
    anyone on the roster                 allow
    the shared panel (nobody identified) allow
    not on the roster                    refuse unknown_person (a member TAKEN OFF it too: see ``roster``)

The point is the record and the single door. STOPPING IS NEVER HELD UP: production makes ``music.stop`` (pause, stop, mute) and
``music.quieter`` (volume down, or a volume it cannot tell the direction of) FIRST and tells the kernel afterwards; they are in
the table so that what was done is recorded under the same rule. Music that nobody asked for (an alarm's music, the discovery
jobs) is not an action of a person and is never told.
"""
from __future__ import annotations

from typing import Callable

from .door import PANEL, PRIVATE, UNKNOWN_PERSON, Area
from .spine import Decision

VERBS = ("music.play", "music.stop", "music.louder", "music.quieter", "music.queue", "music.library")
QUIET_VERBS = ("music.stop", "music.quieter")
#: ``() -> the ids on the CURRENT roster``, or None: everyone in ``people`` is. The shadow daemon installs its own (``build``): it
#: keeps a member taken OFF the roster in the kernel's people while it holds a record for them (so the record can still be
#: forgotten), and such a member controls nothing. ``home_door`` reads the same one.
roster: Callable[[], frozenset] | None = None


def on_roster(people: dict, actor: str):
    """The roster's ``Person`` for ``actor``, or None: not a person here, or a member taken off the roster (``roster``)."""
    who = people.get(actor)
    return who if who is not None and (roster is None or actor in roster()) else None


def decide(people: dict, verb: str, actor: str, *, via: str | None = None, owner: str | None = None,
           visibility: str = PRIVATE) -> Decision:
    """The whole table: the house's music is anyone's in the house, and the panel's."""
    if via == PANEL or on_roster(people, actor) is not None:
        return Decision(True)
    return Decision(False, UNKNOWN_PERSON)


AREA = Area("music", VERBS, decide, target="player")
