"""Child safety floor: a child asking about a hazard gets one fixed "ask a grown-up" line, never the steps (QA round 10a, 2026-11-14).

THE MISS. A speaker who said they were ten asked how to use the gas stove and which knife was sharpest, and the brain gave the
steps. No floor covered those classes (``distress_handoff`` / ``medical_safety`` match none of them), and the account had an adult
role, so nothing knew the speaker was a child: the asks that were refused were refused by the brain's own judgement.

WHAT IT DOES. ``check`` (sync, pure, run on every brain turn by ``brain_dispatch``) reads the turn's own words. It records an explicit
OWN-age statement for this ``(user, session)`` in process memory (never a memory fact; STICKY: a later adult-age claim never clears it; a quoted or reported age is no one's declaration), then asks whether the turn is a REQUEST or QUESTION that names a hazard (``hazard_class``). Every other turn returns ``None``
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

GUEST VOICES (``ZOE_CHILD_SAFETY_GUESTS`` off | shadow | on, DEFAULT off). On a VOICE-panel turn (``brain_dispatch`` passes
``voice_mode``) whose speaker is a guest id or empty (an unrecognised voice may be a child), ``on`` answers a hazard request with
``guest_reply`` (why; "if you live here, sign in with your PIN" helps only a member whose voice was not recognised - a real guest has no PIN); ``shadow`` only logs ``source=guest ... would_refuse=1``; ``off``
is byte-identical to before. Typed chat, a recognised or signed-in member and an authenticated service caller are never "guests" here.

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
GUESTS_ENV = "ZOE_CHILD_SAFETY_GUESTS"
#: an unrecognised voice at the panel, guests flag on: why, and the one honest way through (a household member's PIN sign-in)
GUEST_REPLY = "I don't know your voice, so I'm playing it safe with that one. Ask a grown-up, or if you live here, sign in with your PIN and ask me again."
LEXICON_KEY = "child_safety"
FALLBACK_LANG = "en"
FALLBACK_REPLY = "That one needs a grown-up. Can you ask one to help you?"
#: a hazard IN PROGRESS (a gas smell, smoke, fire, sparks, something left on) in a refused request: the one instruction that matters
URGENT_REPLY = "Move away from it and tell a grown-up right now."
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
    voice: bool = False   # a voice-panel turn: an unrecognised voice there may be a child (``guests_mode``)
    guest_reply: str = ""


def mode() -> str:
    from typed_env import flag

    raw = str(flag(ENV) or "").strip().lower()
    return raw if raw in ("off", "shadow") else "enforce"   # anything else (unknown, empty) is a floor that enforces


def guests_mode() -> str:
    """``ZOE_CHILD_SAFETY_GUESTS`` off (DEFAULT: a guest voice is answered as before) | shadow (log ``would_refuse`` only) | on."""
    try:
        from typed_env import flag

        raw = str(flag(GUESTS_ENV) or "").strip().lower()
        return raw if raw in ("shadow", "on") else "off"
    except Exception:  # noqa: BLE001
        return "off"


def _safe_mode() -> str:
    try:
        return mode()
    except Exception:  # noqa: BLE001
        return "enforce"


def _fold(text: str) -> str:
    import distress_handoff

    s = unicodedata.normalize("NFKC", text or "").casefold()
    # a QUOTING single quote ("dad said 'i am 40'") becomes a double quote before the apostrophes are dropped; "i'm" is untouched
    s = re.sub(r"(?:^|(?<=\s))['\u2018\u2019`](?=\w)|(?<=[\w.!?,])['\u2018\u2019`](?=\s|$|[.,!?;:])", '"', s)
    return distress_handoff._strip(s)[:MAX_CHARS]


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
            # a question word at the start of ANY clause ("im 10, which knife is sharpest"; "im 9 and where do you keep the matches")
            "clause_q": re.compile(rf"(?:[,;:.!?]\s*|(?<!\w)(?:{e['clause_break']})\s+)(?:{e['clause_opener']})(?!\w)"),
            "clause_split": re.compile(rf"[,;:.!?]+|(?<!\w)(?:{e['clause_break']})(?!\w)"),
            "back_ref": w("|".join(e["back_refs"].split())),
            # later-clause openers skip auxiliaries and the question words (clause_q owns those, with its "what a ..." exclusion)
            "aux": frozenset(e["aux_openers"].split()) | {re.match(r"[a-z]+", q).group(0) for q in e["clause_opener"].split("|")},
            "aux_words": frozenset(e["aux_openers"].split()), "aux_subjects": frozenset(e["aux_subjects"].split()),
            "factual": re.compile(e["factual"]), "factual_not": w(e["factual_not"]), "benign": re.compile(e["benign"]),
            "body": w("|".join(e["body_parts"].split())), "live_ask": w(e["live_ask"]), "report": w(e["report"]), "in_progress": w(e["in_progress"]),
            "in_progress_not": frozenset(e["in_progress_not"].split()), "urgent": str(e.get("urgent_reply") or URGENT_REPLY),
            "guest_reply": str(e.get("guest_reply") or ""),
            "classes": [(c["id"], w(c["terms"]), c["reply"]) for c in e["classes"]]}


def _age(token: str, nums: dict) -> int:
    if token.isdigit():
        return int(token)
    return sum(nums.get(p, 0) for p in re.split(r"[ -]", token))


def _not_own(s: str, pos: int, rx: dict) -> bool:
    """Someone ELSE's words: inside quotes (single quotes are turned into double ones by ``_fold``). The own-age patterns are first
    person only, so a report whose embedded subject is the speaker ("mum told me im 10") counts, and "dad says shes 40" never matches."""
    return bool(sum(s[:pos].count(q) for q in '"\u201c\u201d') % 2)


def own_age_says_child(s: str, rx: dict) -> Optional[bool]:
    """True = the speaker states their own age as a child's ANYWHERE in the turn ("im 40 no wait im 10", "im 10, im 40"), False = only
    as an adult's, None = no own-age statement. A quoted or reported-about-someone-else age counts as neither."""
    found: list[tuple[int, bool]] = []
    for r in rx["own"]:
        found += [(m.start(), _age(m.group("age"), rx["nums"]) < rx["adult"]) for m in r.finditer(s) if not _not_own(s, m.start(), rx)]
    for r in rx["school"]:
        for m in r.finditer(s):
            if _not_own(s, m.start(), rx):
                continue
            year = m.groupdict().get("age")
            if year is None or _age(year, rx["nums"]) <= rx["max_year"]:
                found.append((m.start(), True))
    return any(child for _pos, child in found) if found else None   # any child age declares a child, whatever follows (sticky)


def is_request(s: str, rx: dict) -> bool:
    """A request or question: a ``?``, an ``ask`` cue anywhere, an ``opener`` as the first word, a question word opening any later
    clause (``clause_q``), or an imperative opener starting any later clause ("why is fire hot, get me the big knife"; an auxiliary
    such as "is" / "do" opens a later-clause QUESTION only when a subject word follows it and the clause names a hazard: "im 10, is
    the knife sharp" asks, "dad used the drill and is tired" / "the lawnmower is loud and does my head in" stay statements). A plain
    statement is not one."""
    if "?" in s or rx["ask"].search(s) or rx["clause_q"].search(s) or rx["opener"].match(rx["lead"].sub("", s, count=1)):
        return True
    for c in clauses(s, rx)[1:]:
        head = rx["lead"].sub("", c, count=1)
        m = rx["opener"].match(head)
        if not m:
            continue
        word = m.group(0)
        if word not in rx["aux"]:
            return True
        rest = head[m.end():].split()
        if (word in rx["aux_words"] and rest and rest[0] in rx["aux_subjects"]
                and any(terms.search(c) for _cid, terms, _r in rx["classes"])):
            return True
    return False


def _cue_before(s: str, pos: int, rx: dict) -> bool:
    """Is there an ask BEFORE ``pos``? Exactly ``is_request`` on the words before it - ONE cue test, no second copy - so a clause-start
    question word counts too ("im 10, which scissors will cut my hair")."""
    return is_request(s[:pos].strip(), rx)


def in_progress(s: str, rx: dict) -> bool:
    """A live hazard phrase that is not negated by the word or two just before it ("without smoke", "no gas smell")."""
    for m in rx["in_progress"].finditer(s):
        if not set(s[max(0, m.start() - 24):m.start()].split()[-2:]) & rx["in_progress_not"]:
            return True
    return False


def clauses(s: str, rx: dict) -> list[str]:
    """The turn's clauses, split the way the ask detection splits them (punctuation, and / so / but / then ...)."""
    return [c.strip() for c in rx["clause_split"].split(s) if c and c.strip()]


def _clause_clears(c: str, rx: dict) -> bool:
    """May this ONE clause stand without a refusal? It is exempt (a pure factual question, an art / spelling ask whose hazard is only
    the object, a report with no request cue before it) or it names no hazard and is not a request pointing back at one ("how do i
    use one"). Every exemption is judged per clause, so an exempt clause never shields another clause's ask."""
    core = re.sub(r"[\s.,!?;:\"]+$", "", rx["lead"].sub("", c, count=1))
    if not core:
        return True
    if rx["factual"].match(core) and not rx["ask"].search(core) and not rx["factual_not"].search(core):
        return True   # "why is fire hot": a pure factual question; "how does it work" / "how do I" / "can I" never match
    if rx["benign"].fullmatch(core) and not rx["body"].search(core):
        return True   # "how do i draw a knife": the hazard is only the OBJECT; "draw a knife across my hand" / "paint with bleach" never match
    rep = rx["report"].search(c)
    if rep and not _cue_before(c, rep.start(), rx):
        return True   # a report ("i cut my finger") - unless a request cue came FIRST ("how do i cut my bread with a knife")
    if any(terms.search(c) for _cid, terms, _r in rx["classes"]):
        return False
    return not (is_request(c, rx) and rx["back_ref"].search(c))


def hazard_class(s: str, rx: dict) -> Optional[tuple[str, str]]:
    """(class id, fixed reply) when ``s`` is a request or question naming a hazard that no exemption covers in EVERY clause, else None."""
    if in_progress(s, rx) and (is_request(s, rx) or rx["live_ask"].search(s)):
        # a hazard IN PROGRESS plus ANY request cue, anywhere ("the stove is on fire what do i do"): the urgent line, noun or not
        return next(((cid, rx["urgent"]) for cid, terms, _r in rx["classes"] if terms.search(s)), ("live", rx["urgent"]))
    if not is_request(s, rx) or not any(terms.search(s) for _cid, terms, _r in rx["classes"]):
        return None
    blocking = [c for c in clauses(s, rx) if not _clause_clears(c, rx)]
    if not blocking:
        return None   # every clause is exempt or harmless: "why is fire hot, how do i spell cat"
    for cid, terms, reply in rx["classes"]:
        if any(terms.search(c) for c in blocking):
            return cid, reply
    # the blocking clause points back ("how do i spell knife and how do i use one"): the hazard named elsewhere in the turn
    return next(((cid, reply) for cid, terms, reply in rx["classes"] if terms.search(s)), None)


def _owned_by_other_floor(text: str, floors_live: bool = True) -> bool:
    """Defer ONLY to a floor that will actually ANSWER this turn: it runs on this lane (``floors_live``: the Flue seam runs them) and its
    own mode read for what it detected is enforce. A shadow / off floor, another lane, or any error: this floor answers."""
    if not floors_live:
        return False
    try:
        import distress_handoff
        import medical_safety

        v = distress_handoff.detect(text)
        if v.tier != "none" and distress_handoff.mode_for(v.tier) == "enforce":
            return True
        return bool(medical_safety.detect(text).cls) and medical_safety.mode() == "enforce"
    except Exception:  # noqa: BLE001 - a broken detector or mode read must not wave a child's hazard ask through: this floor answers
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


def check(text: str, session_id: str, user_id: str = "", *, dry: bool = False, floors_live: bool = True,
          voice: bool = False) -> Optional[Pending]:
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
        held = _SESSIONS.get(key) if key[1] else None
        held_child = bool(held and time.monotonic() - held["t"] <= SESSION_TTL_S and held["child"])
        if said and key[1] and not dry:
            _entry(key)["child"] = True   # STICKY for the session: a later adult-age claim never clears it
        hit = hazard_class(s, rx)
        if hit is None or _owned_by_other_floor(text, floors_live):
            return None
        declared = bool(said) or held_child
        return Pending(cls=hit[0], reply=hit[1], declared=bool(declared), key=key, voice=bool(voice), guest_reply=str(rx.get("guest_reply") or ""))
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


def _guest(p: Pending) -> Optional[str]:
    """An unrecognised voice on a voice-panel turn (``guests_mode``). off: None, exactly as before (no log line)."""
    g = guests_mode()
    if g == "off":
        return None
    m = _safe_mode()
    refuse = g == "on" and m == "enforce"
    logger.info("CHILD_SAFETY_FLOOR class=%s source=guest mode=%s guests=%s would_refuse=1", p.cls, m, g)
    if not refuse:
        return None
    if p.cls == "live" or p.reply == URGENT_REPLY:
        return p.reply or URGENT_REPLY   # a live hazard: "move away" first, never a sign-in hint
    return p.guest_reply or GUEST_REPLY


async def resolve(p: Pending, user_id: str = "") -> Optional[str]:
    """The fixed reply when the speaker of a hazard ask is (or could be) a child and the floor ENFORCES, else None (ask the brain).
    NEVER raises; an error refuses."""
    try:
        if p.blind:
            source = "" if await _who(user_id) == "adult" else "error"
        else:
            who = "" if p.declared else await _who(user_id)
            if who == "guest" and p.voice:
                return _guest(p)
            source = "declared" if p.declared else _SOURCE.get(who, "")
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
