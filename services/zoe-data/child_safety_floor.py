"""Child safety floor: a child asking HOW to use a hazard gets one fixed "ask a grown-up" line, never the steps (QA round 10a, 2026-11-14).

THE MISS. A speaker who said they were ten asked how to use the gas stove and which knife was sharpest, and the brain gave the
steps. No floor covered those classes (``distress_handoff`` / ``medical_safety`` match none of them), and the account had an adult
role, so nothing knew the speaker was a child: the asks that were refused were refused by the brain's own judgement.

WHAT IT DOES. ``check`` (sync, pure, run on every brain turn by ``brain_dispatch``) reads the turn's own words. It records an explicit
OWN-age statement for this ``(user, session)`` in process memory (never a memory fact; a later own age at or over the adult age clears
it), then asks whether the turn is a how-to ask about one hazard class. Every other turn returns ``None`` there and nothing changes.
On a hazard how-to, ``resolve`` decides whether the speaker is a child - the session declaration, else the member record
(``member_register.audience`` = child: a child role or a minor mode) - and a child gets the class's fixed line; the brain is not asked.
Deferred untouched: a turn the distress hand-off or the medical floor matches (they own it, and run first on the brain lane), a danger
or injury report ("the stove is on", "I cut my finger"), a factual question with no how-to cue ("why is fire hot"), an adult, and an
adult talking ABOUT a child ("my daughter is 10" is not the speaker's own age). Age is never inferred from a voice or a topic.

WORDS ARE DATA: ``lexicons_data/<lang>.json`` "child_safety" (English today; a language with no entry falls back to the English one).
FLAG ``ZOE_CHILD_SAFETY_FLOOR`` off | shadow | enforce (DEFAULT enforce like the other floors; an unknown value enforces). The log is
one line, ``CHILD_SAFETY_FLOOR class=<id> source=<role|declared> mode=<m> n=<count this session>``: never the words or the age.
No alert, no notification, no stored row. NEVER raises.
"""
from __future__ import annotations

import logging
import re
import time
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

logger = logging.getLogger(__name__)

ENV = "ZOE_CHILD_SAFETY_FLOOR"
LEXICON_KEY = "child_safety"
FALLBACK_LANG = "en"
MAX_CHARS = 2000
SESSION_TTL_S = 12 * 3600
MAX_SESSIONS = 2048
#: (user, session) -> {"child": True|False|None, "t": monotonic, "n": refusals}; process memory only, bounded, expires
_SESSIONS: "OrderedDict[tuple[str, str], dict]" = OrderedDict()


@dataclass(frozen=True)
class Pending:
    cls: str
    reply: str
    declared: bool
    key: tuple


def mode() -> str:
    from typed_env import flag

    return str(flag(ENV) or "enforce").strip().lower()


def _fold(text: str) -> str:
    import distress_handoff

    return distress_handoff._strip(unicodedata.normalize("NFKC", text or "").casefold())[:MAX_CHARS]


@lru_cache(maxsize=8)
def _rx(lang: str) -> Optional[dict]:
    import lexicons

    e = (lexicons.load(lang) or {}).get(LEXICON_KEY)
    if not isinstance(e, dict) or not e.get("classes"):
        return None
    small, tens = e["number_words"].split(), e["tens_words"].split()
    nums = {w: i + 1 for i, w in enumerate(small)} | {w: (i + 2) * 10 for i, w in enumerate(tens)}
    age = rf"(?P<age>\d{{1,3}}(?!\d)|(?:{'|'.join(tens)})(?:[ -](?:{'|'.join(small[:9])}))?(?!\w)|(?:{'|'.join(sorted(small, key=len, reverse=True))}))"
    lead = "".join(rf"(?<!{re.escape(w)})" for w in e["lead_not"].split("|"))
    comp = lambda p: re.compile(rf"{lead}(?<!\w)" + p.replace("{AGE}", age).replace("{END}", e["end"]))  # noqa: E731
    w = lambda body: re.compile(rf"(?<!\w)(?:{body})(?!\w)")  # noqa: E731
    return {"adult": int(e["adult_age"]), "max_year": int(e["max_school_year"]), "nums": nums,
            "own": [comp(p) for p in e["own_age"]], "school": [comp(p) for p in e["school"]],
            "howto": w(e["howto"]), "benign": w(e["benign"]), "report": w(e["report"]),
            "classes": [(c["id"], w(c["terms"]), c["reply"]) for c in e["classes"]]}


def _age(token: str, nums: dict) -> int:
    if token.isdigit():
        return int(token)
    return sum(nums.get(p, 0) for p in re.split(r"[ -]", token))


def own_age_says_child(s: str, rx: dict) -> Optional[bool]:
    """True = the speaker states their own age as a child's, False = as an adult's, None = no own-age statement. The LAST one wins."""
    found: list[tuple[int, bool]] = []
    for r in rx["own"]:
        found += [(m.start(), _age(m.group("age"), rx["nums"]) < rx["adult"]) for m in r.finditer(s)]
    for r in rx["school"]:
        for m in r.finditer(s):
            year = m.groupdict().get("age")
            if year is None or _age(year, rx["nums"]) <= rx["max_year"]:
                found.append((m.start(), True))
    return max(found)[1] if found else None


def hazard_class(s: str, rx: dict) -> Optional[tuple[str, str]]:
    """(class id, fixed reply) when ``s`` asks HOW to use / do one hazard and reports no danger, else None."""
    s = rx["benign"].sub(" ", s)
    if not rx["howto"].search(s) or rx["report"].search(s):
        return None
    for cid, terms, reply in rx["classes"]:
        if terms.search(s):
            return cid, reply
    return None


def _owned_by_other_floor(text: str) -> bool:
    try:
        import distress_handoff
        import medical_safety

        return distress_handoff.detect(text).tier != "none" or bool(medical_safety.detect(text).cls)
    except Exception:  # noqa: BLE001 - cannot rule an emergency out: leave the turn to the path that owns emergencies
        return True


def _entry(key: tuple) -> dict:
    now = time.monotonic()
    e = _SESSIONS.get(key)
    if e is None or now - e["t"] > SESSION_TTL_S:
        e = {"child": None, "t": now, "n": 0}
    e["t"] = now
    _SESSIONS[key] = e
    _SESSIONS.move_to_end(key)
    while len(_SESSIONS) > MAX_SESSIONS:
        _SESSIONS.popitem(last=False)
    return e


def check(text: str, session_id: str, user_id: str = "", *, dry: bool = False) -> Optional[Pending]:
    """The sync pre-check. None = not a hazard how-to (or the floor is off): the turn goes on exactly as before. NEVER raises."""
    try:
        if mode() == "off":
            return None
        s = _fold(text)
        if not s:
            return None
        import lexicons

        rx = _rx(lexicons.detect(s) or FALLBACK_LANG) or _rx(FALLBACK_LANG)
        if rx is None:
            return None
        key = ((user_id or "").strip(), (session_id or "").strip())
        said = own_age_says_child(s, rx)
        if said is not None and key[1] and not dry:
            _entry(key)["child"] = said
        hit = hazard_class(s, rx)
        if hit is None or _owned_by_other_floor(text):
            return None
        held = _SESSIONS.get(key) if key[1] else None
        declared = said if said is not None else bool(held and time.monotonic() - held["t"] <= SESSION_TTL_S and held["child"])
        return Pending(cls=hit[0], reply=hit[1], declared=bool(declared), key=key)
    except Exception as exc:  # noqa: BLE001
        logger.warning("child_safety_floor check failed (%s)", type(exc).__name__)
        return None


async def _role_child(user_id: str) -> bool:
    import member_register

    return await member_register.audience(user_id) == member_register.AUDIENCE_CHILD


async def resolve(p: Pending, user_id: str = "") -> Optional[str]:
    """The fixed reply when the speaker of a hazard how-to is a child and the floor ENFORCES, else None (ask the brain). NEVER raises."""
    try:
        source = "declared" if p.declared else ("role" if await _role_child((user_id or "").strip()) else "")
        if not source:
            return None
        n = 1
        if p.key[1]:
            e = _entry(p.key)
            e["n"] += 1
            n = e["n"]
        m = mode()
        logger.info("CHILD_SAFETY_FLOOR class=%s source=%s mode=%s n=%d", p.cls, source, m, n)
        return p.reply if m == "enforce" and p.reply else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("child_safety_floor resolve failed (%s)", type(exc).__name__)
        return None
