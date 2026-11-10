"""Pure "may this person be a child" predicate, shared by the voice store and the person profile.

No I/O and no import of any voice-store module, so a reader of people never has to touch learned voices (the learned-voice
reader allow-list test pins which modules may). Fail-safe direction: any child word in the relationship, or a
birthday under 18, means child.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Optional

CHILD_WORDS = frozenset({"child", "kid", "son", "daughter", "baby", "toddler", "grandson", "granddaughter", "grandchild",
                         "stepson", "stepdaughter", "boy", "girl", "infant", "teen", "teenager", "minor",
                         "niece", "nephew", "grandkid", "grandchildren", "grandkids", "nieces", "nephews"})


def person_may_be_child(person: dict, today: Optional[date] = None) -> bool:
    words = set(re.findall(r"[a-z]+", str(person.get("relationship") or "").lower()))
    if words & CHILD_WORDS:
        return True
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", str(person.get("birthday") or ""))
    if m:
        t = today or date.today()
        y, mo, d = (int(g) for g in m.groups())
        if y > 1900 and (t.year - y - ((t.month, t.day) < (mo, d))) < 18:
            return True
    return False
