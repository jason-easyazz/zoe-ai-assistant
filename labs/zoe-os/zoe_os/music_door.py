"""The music area of the door: the kernel decides and records a member's music action. Nothing is stored anywhere for music:
the action is a command to the music engine, sent by production. No track, artist, search or playlist name reaches this
process: the verb, who asked, and the id of the speaker.

Music is low-stakes and belongs to the whole house:

    who                                  any music verb
    anyone on the roster                 allow
    the shared panel (nobody identified) allow
    not on the roster                    refuse unknown_person

The point is the record and the single door. STOPPING IS NEVER HELD UP: production makes ``music.stop`` (pause, stop, mute) and
``music.quieter`` (volume down, or a volume it cannot tell the direction of) FIRST and tells the kernel afterwards; they are in
the table so that what was done is recorded under the same rule. Music that nobody asked for (an alarm's music, the discovery
jobs) is not an action of a person and is never told.
"""
from __future__ import annotations

from .door import PANEL, PRIVATE, UNKNOWN_PERSON, Area
from .spine import Decision

VERBS = ("music.play", "music.stop", "music.louder", "music.quieter", "music.queue", "music.library")
QUIET_VERBS = ("music.stop", "music.quieter")


def decide(people: dict, verb: str, actor: str, *, via: str | None = None, owner: str | None = None,
           visibility: str = PRIVATE) -> Decision:
    """The whole table: the house's music is anyone's in the house, and the panel's."""
    if via == PANEL or people.get(actor) is not None:
        return Decision(True)
    return Decision(False, UNKNOWN_PERSON)


AREA = Area("music", VERBS, decide, target="player")
