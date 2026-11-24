"""The messages area of the door: a MEMBER asking Zoe to send something. The verb is the CHANNEL CLASS; the recipient is a
member's id (``owner``) when the recipient is a known person, and is otherwise not named at all. NEVER the words of the
message, a phone number, a chat id or an address: the wire has no field for them (this area has no target and no turn
id; an ask that supplies either is rejected before anything is read or written, ``door.Area``).

Zoe's OWN notifications (a reminder firing, an alarm, a proactive nudge, a system alert) are automatic: never asked, never
recorded.

    verb             what it is in production today
    send.panel       the member's words on the house's own panels (the intercom's announce and ring; the brain's panel toast)
    send.telegram    a message to a LINKED member's Telegram chat (today only "send the sign-in link to my own phone")
    send.push        a push notification to a member's own browsers (no member-driven sender today: reserved)
    send.external    to an address that is not a known contact or channel of the household (NO sender today: reserved)

    who                                   send.panel   send.telegram / send.push                 send.external
    an adult                              allow        allow                                     allow
    a child, a guest on the roster        allow        to themself: allow; to anyone else:       refuse adults_only
                                                       refuse adults_only
    the shared panel, nobody identified   allow (1)    refuse not_owner                          refuse not_owner
    not on the roster                     refuse unknown_person (all verbs)

(1) Today's rule: the panel toast is open to the bare panel; the spoken intercom refuses a guest in production, before it asks.
Production keeps its own rules (quiet hours, "only an adult may drop in", "only a linked member is ever messaged") and makes
them before it asks. ``send.external`` is the one verb production never sends unchecked: a kernel it cannot ask means NO.
"""
from __future__ import annotations

from .capability import ADULT
from .door import ADULTS_ONLY, NOT_OWNER, PANEL, PRIVATE, UNKNOWN_PERSON, Area
from .spine import Decision

PANEL_SEND, TELEGRAM, PUSH, EXTERNAL = "send.panel", "send.telegram", "send.push", "send.external"
VERBS = (PANEL_SEND, TELEGRAM, PUSH, EXTERNAL)


def decide(people: dict, verb: str, actor: str, *, via: str | None = None, owner: str | None = None,
           visibility: str = PRIVATE) -> Decision:
    """The whole permission table (module docstring), one rule per line. ``owner`` is the recipient when it is a known person."""
    panel = via == PANEL
    who = None if panel else people.get(actor)
    if not panel and who is None:
        return Decision(False, UNKNOWN_PERSON)
    if verb == PANEL_SEND:                                        # the house's own panels: anyone in the house, and the panel
        return Decision(True)
    if panel:                                                     # nobody identified sends nothing off the panels
        return Decision(False, NOT_OWNER)
    if who.role == ADULT:                                         # an adult: anyone they may message today
        return Decision(True)
    if verb != EXTERNAL and owner == actor:                       # a child or a guest: their own phone, their own browsers
        return Decision(True)
    return Decision(False, ADULTS_ONLY)


AREA = Area("messages", VERBS, decide, target=None, turn=False)
