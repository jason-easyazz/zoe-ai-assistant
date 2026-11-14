"""Child safety floor: a child asking about a hazard gets one fixed "ask a grown-up" line, never the steps (QA round 10a, 2026-11-14).

THE MISS. A speaker who said they were ten asked how to use the gas stove and which knife was sharpest, and the brain gave the
steps. No floor covered those classes (``distress_handoff`` / ``medical_safety`` match none of them), and the account had an adult
role, so nothing knew the speaker was a child: the asks that were refused were refused by the brain's own judgement.

WHAT IT DOES. ``check`` (sync, pure, run on every brain turn by ``brain_dispatch``) reads the turn's own words. It records an explicit
OWN-age statement for this ``(user, session)`` in process memory (never a memory fact; a later own age at or over the adult age clears
it), then asks whether the turn is a REQUEST or QUESTION that names a hazard (``hazard_class``). Every other turn returns ``None``
there and nothing changes. There is deliberately no "how-to" phrase list: for a child, a hazard word in any request or question is
enough (over-refusing a child is the safe side). A STATEMENT that only mentions a hazard ("dad used the drill today") is not a request.
``resolve`` then decides who is speaking. FAIL SAFE: the session declaration or a child record (minor mode, or a role in
``manner_block.CHILD_ROLES``) refuses; a role in ``manner_block.ADULT_ROLES`` passes; a guest / empty id passes (unchanged; an owner
decision); and a member who COULD be a child - no ``auth_users`` row, a role in neither set, a lookup that raises or exceeds
``LOOKUP_BUDGET_S`` - refuses too (``source=unverified``). Any error inside ``resolve`` refuses (``source=error``). An adult hit by a
lookup failure on a hazard ask therefore gets the cautious line: the accepted cost.
Deferred untouched: a turn the distress hand-off or the medical floor matches (they own it, and run first on the brain lane), a danger
or injury report ("the stove is on", "I cut my finger"), a factual question ("why is fire hot", "what is a knife made of", "how does a
drill work"), an adult, and an adult talking ABOUT a child ("my daughter is 10" is not the speaker's own age). Age is never inferred
from a voice or a topic.

WORDS ARE DATA: ``lexicons_data/<lang>.json`` "child_safety" (English today; a language with no entry falls back to the English one).
FLAG ``ZOE_CHILD_SAFETY_FLOOR`` off | shadow | enforce (DEFAULT enforce like the other floors; an unknown value enforces). The log is
one line, ``CHILD_SAFETY_FLOOR class=<id> source=<role|declared|unverified|error> mode=<m> n=<count>``: never the words or the age.
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
FALLBACK_REPLY = "That one needs a grown-up. Can you ask one to help you?"
MAX_CHARS = 2000
SESSION_TTL_S = 12 * 3600
LOOKUP_BUDGET_S = 0.5
MAX_SESSIONS = 2048
#: (user, session) -> {"child": True|False|None, "t": monotonic, "n": refusals}; process memory only, bounded, expires
_SESSIONS: "OrderedDict[tuple[str, str], dict]" = OrderedDict()


@dataclass(frozen=True)
class Pending:
    cls: str
    reply: str
    declared: bool
    key: tuple
    blind: bool = False   # the floor itself failed: refuse unless the speaker is POSITIVELY an adult


def mode() -> str:
    from typed_env import flag

    raw = str(flag(ENV) or "").strip().lower()
    return raw if raw in ("off", "shadow") else "enforce"   # anything else (unknown, empty) is a floor that enforces


def _safe_mode() -> str:
    try:
        return mode()
    except Exception:  # noqa: BLE001
        return "enforce"


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
            "ask": w(e["ask"]), "opener": re.compile(rf"(?:{e['opener']})(?!\w)"), "lead": re.compile(e["lead_strip"]),
            "factual": re.compile(e["factual"]), "factual_not": w(e["factual_not"]), "benign": w(e["benign"]), "report": w(e["report"]),
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


def is_request(s: str, rx: dict) -> bool:
    """A request or question: a ``?``, an ``ask`` cue anywhere, or an ``opener`` as the first word. A plain statement is not one."""
    head = rx["lead"].sub("", s, count=1)
    return "?" in s or bool(rx["ask"].search(s)) or bool(rx["opener"].match(head))


def _cue_before(s: str, pos: int, rx: dict) -> bool:
    """A request cue (the same ``ask`` / ``opener`` set ``is_request`` uses) BEFORE ``pos``: then the words after it are the ask."""
    head = rx["lead"].sub("", s[:pos], count=1)
    return bool(rx["ask"].search(s[:pos])) or bool(head and rx["opener"].match(head))


def hazard_class(s: str, rx: dict) -> Optional[tuple[str, str]]:
    """(class id, fixed reply) when ``s`` is a request or question naming one hazard and reports no danger, else None."""
    core = re.sub(r"[\s.,!?;:]+$", "", rx["lead"].sub("", s, count=1))
    if rx["factual"].match(core) and not rx["ask"].search(core) and not rx["factual_not"].search(core):
        return None   # "why is fire hot": a pure factual question; "how does it work" / "how do I" / "can I" never match
    s = rx["benign"].sub(" ", s)
    if not is_request(s, rx):
        return None
    rep = rx["report"].search(s)
    if rep and not _cue_before(s, rep.start(), rx):
        return None   # a report ("i cut my finger") - unless a request cue came FIRST ("how do i cut my bread with a knife")
    for cid, terms, reply in rx["classes"]:
        if terms.search(s):
            return cid, reply
    return None


def _owned_by_other_floor(text: str) -> bool:
    try:
        import distress_handoff
        import medical_safety

        return distress_handoff.detect(text).tier != "none" or bool(medical_safety.detect(text).cls)
    except Exception:  # noqa: BLE001 - a broken detector must not wave a child's hazard ask through: this floor still refuses
        return False


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
    """The sync pre-check. None = not a hazard request (or the floor is off): the turn goes on exactly as before. NEVER raises.
    FAIL SAFE: an error AFTER a hazard was found keeps that class's refusal; an error BEFORE detection could run (lexicon, import,
    regex) refuses with the generic line - both ``blind``, so ``resolve`` lets only a positively adult speaker through."""
    key: tuple = ("", "")
    hit: Optional[tuple[str, str]] = None
    try:
        key = ((user_id or "").strip(), (session_id or "").strip())
        if mode() == "off":
            return None
        s = _fold(text)
        if not s:
            return None
        import lexicons

        rx = _rx(lexicons.detect(s) or FALLBACK_LANG) or _rx(FALLBACK_LANG)
        if rx is None:
            raise LookupError("no child_safety lexicon")
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
        logger.warning("CHILD_SAFETY_FLOOR check failed stage=%s (%s)", "after" if hit else "before", type(exc).__name__)
        return Pending(cls=hit[0] if hit else "error", reply=hit[1] if hit else FALLBACK_REPLY, declared=False, key=key, blind=True)


async def _who(user_id: str) -> str:
    """``child`` | ``adult`` | ``guest`` | ``unverified`` (could be a child: no account, a role in neither set, a failed or slow lookup)."""
    import asyncio

    import manner_block

    uid = (user_id or "").strip()
    if not uid or manner_block.is_guest(uid):
        return "guest"
    try:
        import enrollment_state

        minor, role = await asyncio.wait_for(enrollment_state._account_facts(uid), timeout=LOOKUP_BUDGET_S)
    except Exception as exc:  # noqa: BLE001 - not knowing is not "adult"
        logger.warning("CHILD_SAFETY_FLOOR lookup failed (%s)", type(exc).__name__)
        return "unverified"
    if minor or role in manner_block.CHILD_ROLES:
        return "child"
    return "adult" if role in manner_block.ADULT_ROLES else "unverified"


_SOURCE = {"child": "role", "unverified": "unverified"}


async def resolve(p: Pending, user_id: str = "") -> Optional[str]:
    """The fixed reply when the speaker of a hazard ask is (or could be) a child and the floor ENFORCES, else None (ask the brain).
    NEVER raises; an error refuses."""
    try:
        if p.blind:
            source = "" if await _who(user_id) == "adult" else "error"
        else:
            source = "declared" if p.declared else _SOURCE.get(await _who(user_id), "")
        if not source:
            return None
        n = 1
        if p.key[1]:
            e = _entry(p.key)
            e["n"] += 1
            n = e["n"]
        m = _safe_mode()
        logger.info("CHILD_SAFETY_FLOOR class=%s source=%s mode=%s n=%d", p.cls, source, m, n)
        return (p.reply or FALLBACK_REPLY) if m == "enforce" else None
    except Exception as exc:  # noqa: BLE001 - fail SAFE: a hazard ask we could not clear gets the cautious line
        logger.warning("CHILD_SAFETY_FLOOR class=%s source=error (%s)", getattr(p, "cls", "?"), type(exc).__name__)
        return getattr(p, "reply", "") or FALLBACK_REPLY
