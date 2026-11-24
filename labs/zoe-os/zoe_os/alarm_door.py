"""The alarms and timers areas of the door: the permission table for a change to a wake-up alarm or a countdown timer. The
mechanism (what is recorded, ids only, the order against a forget, ask and report) is ``door.py`` and is shared with every other
area; nothing here holds an alarm or a timer, and no label, time or sound ever reaches this process.

An alarm or a timer belongs to the person it is FOR (``owner``). One that belongs to the HOUSE has no owner (production keeps
those under a household marker or a guest principal, neither of which is a person) and is told ``visibility = household``.

The rules are household rules by ROLE (``capability.Person.role``). Not on the roster = unknown = refused (least access).

    alarm / timer        who                               create / edit / delete (cancel)     snooze / stop
    any                  the person it is for              allow                               allow
    private              an adult, and it is for a child   allow                               allow
    private              anyone else on the roster         refuse not_owner                    allow
    private              the shared panel                  refuse not_owner                    allow
    household            anyone on the roster, the panel   allow                               allow
    any                  not on the roster                 refuse unknown_person (all verbs)
    for a person who is not on the roster: refuse unknown_person (all verbs, whoever asks; ``door.py``; their id is never written)

Reading the table. A person may do everything to their own. An adult may set, change and delete one FOR a child (another adult's
private one is nobody else's). A HOUSEHOLD alarm or timer is the house's own clock: anyone in the house may set, change or cancel
it, and so may the shared panel with nobody identified (the wall panel is where the house's alarms are set). SILENCING is for
whoever is standing there: ``alarm.snooze``, ``alarm.stop`` and ``timer.stop`` are allowed to every person on the roster and
to the shared panel, whoever the alarm or timer is for. One whose owner or visibility was not told is someone else's private one.

SILENCING IS NEVER HELD UP BY THIS TABLE. Production does not ask before it silences a ringing alarm or timer: it silences it and
tells the kernel afterwards. The silence verbs are in the table so that what was done is recorded under the same rules.
"""
from __future__ import annotations

from .capability import ADULT, CHILD
from .door import HOUSEHOLD, NOT_OWNER, PANEL, PRIVATE, UNKNOWN_PERSON, Area
from .spine import Decision

ALARM_VERBS = ("alarm.create", "alarm.edit", "alarm.delete", "alarm.snooze", "alarm.stop")
TIMER_VERBS = ("timer.create", "timer.edit", "timer.cancel", "timer.stop")
SILENCE_VERBS = ("alarm.snooze", "alarm.stop", "timer.stop")


def decide(people: dict, verb: str, actor: str, *, via: str | None = None, owner: str | None = None,
           visibility: str = PRIVATE) -> Decision:
    """The whole permission table (module docstring), one rule per line. ``people`` is the kernel's roster (``GrantStore.people``)."""
    panel = via == PANEL
    who = None if panel else people.get(actor)
    if not panel and who is None:
        return Decision(False, UNKNOWN_PERSON)
    if verb in SILENCE_VERBS:                                     # whoever is standing there may silence what is ringing
        return Decision(True)
    if visibility == HOUSEHOLD:                                   # the house's own clock: anyone in the house, and the panel
        return Decision(True)
    if who is not None and owner == actor:                        # their own: everything
        return Decision(True)
    whom = people.get(owner) if owner else None
    if who is not None and who.role == ADULT and whom is not None and whom.role == CHILD:   # an adult, FOR a child
        return Decision(True)
    return Decision(False, NOT_OWNER)


ALARMS = Area("alarms", ALARM_VERBS, decide, target="alarm")
TIMERS = Area("timers", TIMER_VERBS, decide, target="timer")
