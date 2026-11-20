"""The memory area of the door: the permission table for a change a MEMBER asks for to a memory row (a fact Zoe keeps about
them). The mechanism (what is recorded, ids only, the order against a forget, ask and report) is ``door.py`` and is shared with
every other area. NOTHING here holds a memory: no text, no name, no excerpt, no tag and no reason ever reaches this process.
The target is the row's id (production's ids are already hashes) and the rest is who asked, whose row it is, and whether the
house shares it.

A row belongs to the member it is about (``owner``: production's ``user_id`` on the row). ``visibility`` ``household`` means
the whole house sees it (production's ``family``); anything else is private to its owner.

Verbs. ``memory.remember`` (store a fact), ``memory.correct`` (replace one), ``memory.approve`` (accept a held one) are ASKED
FIRST. ``memory.forget`` is MADE FIRST AND TOLD AFTERWARDS by production: the kernel can neither delay nor stop an erase, so for
that verb "refuse" below only means the record says OBJECTED; the erase has happened and stands. Production alone keeps "a child
may not forget their own" and decides a guardian's forget; the kernel records them. Automatic writers (extractors, night
passes) are never asked and never recorded. READING is not asked and is recorded nowhere.

    row         who                                   remember / correct / approve       forget (told afterwards)
    any         the member whose memory it is         allow                              recorded
    private     another ADULT                         allow (1)                          recorded (a guardian's forget)
    private     anyone else (a child, a guest)        refuse not_owner                   objected not_owner
    household   anyone on the roster                  allow                              recorded
    any         the shared panel, nobody identified   refuse not_owner                   objected not_owner
    any         not on the roster                     refuse unknown_person              objected unknown_person
    a row of a person who is not on the roster: refuse unknown_person (whoever asks; ``door.py``; never written)

(1) TODAY'S rule, kept as it is: production lets a curator (a carer the member opted in to) and an admin reviewing held rows
write to another member's private memory, and it still makes those checks itself before it asks. The kernel only knows the
asker is an adult. The panel is "nobody identified": a member whose voice the panel has verified asks AS THAT MEMBER.

ONE LINE TO SWITCH, a household choice for the owner: ``STRICT``. Set, remembering / correcting / approving is:
a private row: its owner only (an adult too is ``not_owner``); a household row: anyone on the roster may REMEMBER one, and only
its author (``owner``) or an adult may correct or approve it (anyone else ``adults_only``). Forget is unchanged: it is never
decided here.
"""
from __future__ import annotations

from .capability import ADULT
from .door import ADULTS_ONLY, HOUSEHOLD, NOT_OWNER, PANEL, PRIVATE, UNKNOWN_PERSON, Area
from .spine import Decision

REMEMBER, CORRECT, APPROVE, FORGET = "memory.remember", "memory.correct", "memory.approve", "memory.forget"
VERBS = (REMEMBER, CORRECT, APPROVE, FORGET)
TOLD_VERBS = (FORGET,)            # production makes these first and tells the kernel afterwards
#: False = today's rule. True = the stricter rule in the module docstring (private: owner only; household: author or an adult).
STRICT = False


def decide(people: dict, verb: str, actor: str, *, via: str | None = None, owner: str | None = None,
           visibility: str = PRIVATE) -> Decision:
    """The whole permission table (module docstring), one rule per line. ``people`` is the kernel's roster."""
    panel = via == PANEL
    who = None if panel else people.get(actor)
    if not panel and who is None:
        return Decision(False, UNKNOWN_PERSON)
    if who is not None and owner == actor:                        # their own memory: everything
        return Decision(True)
    if panel:                                                     # nobody identified: nothing
        return Decision(False, NOT_OWNER)
    strict = STRICT and verb != FORGET                            # (a forget is recorded, never decided: the switch leaves it)
    if visibility != HOUSEHOLD:                                   # someone else's private memory: an adult (production's own checks apply)
        return Decision(True) if who.role == ADULT and not strict else Decision(False, NOT_OWNER)
    if strict and verb != REMEMBER and who.role != ADULT:         # (the switch) a household row: its author or an adult corrects it
        return Decision(False, ADULTS_ONLY)
    return Decision(True)                                         # a household row: anyone in the house


AREA = Area("memory", VERBS, decide, target="memory")
