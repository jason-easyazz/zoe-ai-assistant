"""Hold the fact - keep what the owner told Zoe unless they show something new (Samantha person bench P5a/P12, ZOE_HOLD_THE_FACT).

The failure (person-likeness bench, 2026-10-09, live baseline): the owner states a fact ("my dentist is
Friday"), Zoe says it back, the owner pushes back with nothing new - "No, I'm sure it's Thursday." - and the
4B brain caved 26 of 30 times ("I've updated that to Thursday", sometimes after claiming to have changed a
calendar). Spelling the rule out in the prompt (the bench's oracle arm) still flipped 15 of 30, and the flip
rate grew with the length of the conversation (3 of 4 at turn 4, 4 of 4 at turn 20). It is a code property.

The rule (docs/research/person-likeness-2026-10-09.md WRM3 + WRM5): a FACT THE OWNER STATED is kept unless they
show something new; Zoe disagrees ONCE, kindly, and offers to update if they are sure; a second explicit
confirmation updates the row. A neutral "are you sure?" is not a contradiction and is untouched (the brain
already holds that, bench P5a.iii 0 of 17 caves).

What this module does, as a deterministic tier ahead of the router and the brain (``fast_tiers.resolve`` ->
``_conversation_quality_tier``; every channel that uses the core), so a held turn never reaches a model that
could cave or call a tool that writes:

* ``read`` - is the message a BARE pushback ("No, I'm sure it's Thursday."), a pushback that brings EVIDENCE
  ("I checked the calendar, it moved to Thursday." - the update half: the owner's later word wins, so the row is
  edited and the reply says so; "let me check my calendar for Thursday" is a plan, not evidence, and is left alone),
  a neutral challenge ("Are you sure?" - untouched) or something else (untouched, no I/O)?
* ``plan`` - from the session history (the last rows of ``chat_messages``, however many filler turns lie
  between: the length of the conversation is irrelevant, bench P12) find the answer Zoe gave that the pushed value
  contradicts, then check the OWNER-STATED row behind it: only a row the owner said (or confirmed), per
  ``memory_authority.row_authority``, makes the fact theirs to defend. A row Zoe inferred, a value from a tool,
  or an unverified voice is NOT held - the owner's word wins there, as before. The second turn is read against the
  hold reply itself (it is in the history): the owner standing by their value is the explicit confirmation.
* ``handle`` - bare pushback: the HOLD reply (the stored value, "that's what you told me", an offer to change it);
  nothing is written, and the turn's extractors are skipped (``is_own_reply``) so the contradicting claim is not
  stored behind the reply's back. Confirmation: the row is edited through ``MemoryService.review`` as the
  owner's account (``user_confirmed``, a superseding edit - nothing is deleted) and the reply says what changed
  ONLY after the edit went through.

``ZOE_HOLD_THE_FACT`` = ``shadow`` (default: detect and log ``HOLD_THE_FACT mode=shadow ...``, change nothing)
| ``enforce`` | ``off``. Labels are logged, never the user's words. Stdlib; never raises.

Known limits (stated so a reader can discount them): the contradiction is read from days, dates, clock times
and numbers - a name/place pushback ("No, it's Dr Patel") is not detected and goes to the brain as before; the
fact must have been answered in the SAME session's recent history; English only.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from typed_env import env_str

logger = logging.getLogger(__name__)

ENV = "ZOE_HOLD_THE_FACT"
HISTORY_ROWS = 400           # newest first (200 exchanges); the claim is found however many filler turns precede the pushback - a 16-row read lost it after 8 exchanges
MAX_WORDS = 16               # a pushback is a short sentence; a paragraph that mentions Thursday is not one
_GUEST_IDS = ("", "guest", "anonymous", "voice-guest", "voice-daemon")


def mode() -> str:
    """``shadow`` (default, unset/unknown) | ``enforce`` | ``off``. Per-call env read."""
    raw = env_str("ZOE_HOLD_THE_FACT").lower()
    if raw in ("0", "false", "no", "off", "disabled"):
        return "off"
    if raw in ("1", "true", "yes", "on", "enforce"):
        return "enforce"
    return "shadow"


# -- value atoms: the facts a pushback can contradict -------------------------------------------------

_DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_DAY_ALIAS = {"tue": "tuesday", "tues": "tuesday", "wed": "wednesday", "weds": "wednesday", "thu": "thursday",
              "thur": "thursday", "thurs": "thursday", "fri": "friday"}
_DAY_RX = re.compile(r"\b(" + "|".join(list(_DAYS) + list(_DAY_ALIAS)) + r")\b", re.IGNORECASE)
_MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
           "november", "december")
_MON_ALIAS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "sept": 9,
              "oct": 10, "nov": 11, "dec": 12}
_MON_ALT = "|".join(list(_MONTHS) + list(_MON_ALIAS))
_ORD = r"(\d{1,2})(?:st|nd|rd|th)?"
_DATE_DM = re.compile(rf"\b{_ORD}\s+(?:of\s+)?({_MON_ALT})\b", re.IGNORECASE)
_DATE_MD = re.compile(rf"\b({_MON_ALT})\s+{_ORD}\b", re.IGNORECASE)
_DATE_ORD = re.compile(r"\b(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)\b", re.IGNORECASE)
_TIME_RX = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)(?![a-z])|\b(\d{1,2}):(\d{2})\b|\b(noon|midnight)\b",
                      re.IGNORECASE)
_NUM_RX = re.compile(r"(?<![\w:/.-])(\d{1,4})(?![\w:/-]|\.\d)")


@dataclass(frozen=True)
class Atom:
    """One value in a sentence: ``kind`` ('day' | 'date' | 'time' | 'num'), ``key`` (normalised, comparable),
    ``shown`` (the words as written) and its span."""

    kind: str
    key: Any
    shown: str
    start: int
    end: int


def _month_no(word: str) -> int:
    w = word.lower()
    return _MON_ALIAS.get(w) or (_MONTHS.index(w) + 1 if w in _MONTHS else 0)


def atoms(text: str) -> list[Atom]:
    """The days, dates, clock times and numbers ``text`` names, in order. Pure."""
    t = text or ""
    out: list[Atom] = []
    taken: list[tuple[int, int]] = []

    def free(a: int, b: int) -> bool:
        return not any(a < y and x < b for x, y in taken)

    def add(kind: str, key: Any, m: "re.Match[str]") -> None:
        shown, end = m.group(0), m.end()
        if shown.endswith(".") and not re.search(r"[ap]\.m\.$", shown):     # "4:30 pm." - the dot ends the sentence
            shown, end = shown[:-1], end - 1
        taken.append((m.start(), end))
        out.append(Atom(kind, key, shown.strip(), m.start(), end))

    for m in _TIME_RX.finditer(t):
        if m.group(6):
            noon = m.group(6).lower() == "noon"
            add("time", (0, "pm" if noon else "am"), m)
            continue
        hh = int(m.group(1) or m.group(4))
        mm = int(m.group(2) or m.group(5) or 0)
        mer = (m.group(3) or "").lower().replace(".", "")[:1]
        if hh > 24 or mm > 59:
            continue
        add("time", ((hh % 12) * 60 + mm, ({"a": "am", "p": "pm"}.get(mer) or ("pm" if hh >= 12 else None))), m)
    for rx, order in ((_DATE_DM, "dm"), (_DATE_MD, "md")):
        for m in rx.finditer(t):
            if not free(m.start(), m.end()):
                continue
            day, mon = (int(m.group(1)), _month_no(m.group(2))) if order == "dm" else (int(m.group(2)), _month_no(m.group(1)))
            if mon == 5 and not re.search(r"\d", m.group(0)):
                continue
            if 1 <= day <= 31 and mon:
                add("date", (mon, day), m)
    for m in _DATE_ORD.finditer(t):
        if free(m.start(), m.end()) and 1 <= int(m.group(1)) <= 31:
            add("date", (0, int(m.group(1))), m)
    for m in _DAY_RX.finditer(t):
        if free(m.start(), m.end()):
            w = m.group(1).lower()
            add("day", _DAY_ALIAS.get(w, w), m)
    for m in _NUM_RX.finditer(t):
        if free(m.start(), m.end()):
            add("num", int(m.group(1)), m)
    return sorted(out, key=lambda a: a.start)


def _same(a: Atom, b: Atom) -> bool:
    """Do two atoms of one kind name the same value? A date with no month matches the same day-of-month;
    a time with no am/pm matches either meridiem."""
    if a.kind != b.kind:
        return False
    if a.kind == "date":
        return a.key[1] == b.key[1] and (not a.key[0] or not b.key[0] or a.key[0] == b.key[0])
    if a.kind == "time":
        return a.key[0] == b.key[0] and (a.key[1] is None or b.key[1] is None or a.key[1] == b.key[1])
    return a.key == b.key


def contradiction(held_text: str, pushed_text: str) -> Optional[tuple[Atom, Atom]]:
    """``(held atom, pushed atom)`` when ``pushed_text`` names a value of a kind ``held_text`` also names, with
    a DIFFERENT value and none in common; else None. A kind counts only when ``held_text`` holds exactly ONE
    value of it (which of two appointments is wrong is not a thing to guess)."""
    held, pushed = atoms(held_text), atoms(pushed_text)
    for p in pushed:
        same_kind = [h for h in held if h.kind == p.kind]
        if len(same_kind) != 1 or any(_same(h, p) for h in same_kind):
            continue
        return same_kind[0], p
    return None


# -- what the owner said: bare pushback / evidence / neutral / confirmation ---------------------------

_EVIDENCE_RX = re.compile(
    r"\bi\s+(?:just\s+|have\s+)?(?:checked|looked|saw|seen|read|got|received|spoke|spoken|talked|called|"
    r"rang|phoned|emailed|messaged|texted|confirmed|rebooked|found|heard)\b"
    r"|\bi['\u2019]ve\s+(?:checked|looked|seen|read|got|received|spoken|talked|called|rung|phoned|emailed|messaged|"
    r"texted|confirmed|found|heard)\b"
    r"|\b(?:they|he|she|the\s+\w+)\s+(?:just\s+)?(?:called|rang|phoned|emailed|texted|messaged|said|told|confirmed|"
    r"moved|changed|rescheduled|cancelled|canceled|postponed)\b"
    r"|\b(?:got|received)\s+(?:a|an|the|my)\s+(?:text|email|call|letter|message|notification|invite|reminder)\b"
    r"|\b(?:my|the|your)\s+(?:calendar|diary|planner|email|emails|inbox|invite|booking|confirmation|receipt|"
    r"letter|text|message|app|website|ticket|reminder|appointment\s+card)\b"
    r"|\b(?:says|shows|showed|reads)\b|\baccording\s+to\b"
    r"|\b(?:was|been|got|is|has\s+been|have\s+been)\s+(?:moved|changed|rescheduled|postponed|brought\s+forward|"
    r"pushed|updated|switched|delayed|cancelled|canceled)\b"
    r"|\b(?:moved|changed|rescheduled|postponed|switched)\s+(?:it\s+)?(?:to|forward|back)\b"
    r"|\b(?:new|updated|latest)\s+(?:date|time|day)\b|\bjust\s+(?:found\s+out|heard|learned|learnt)\b"
    r"|\bi\s+(?:re)?(?:booked|scheduled|rescheduled|moved|changed)\s+it\b"
    r"|\bi\s+(?:was\s+wrong|got\s+(?:it|that)\s+wrong|made\s+a\s+mistake|misspoke|mixed\s+(?:it|that)\s+up|misremembered)\b",
    re.IGNORECASE)

_LEAD = (r"^\W*(?:(?:no|nope|nah|nuh|um+|uh+|hmm+|but|well|actually|wait|hang\s+on|hold\s+on|oh|sorry|yeah\s+no)"
         r"[\s,.!\u2026-]+)*")
_PUSH_CUE_RX = re.compile(
    _LEAD + r"(?:i(?:['\u2019]m|\s+am)\s+(?:really\s+|totally\s+|absolutely\s+|completely\s+|pretty\s+|quite\s+|100%\s+)?"
    r"(?:sure|certain|positive|confident)|i\s+know|i\s+said|i\s+(?:thought|reckon|think)|it(?:['\u2019]s|\s+is|\s+was)|"
    r"its\b|that(?:['\u2019]s|\s+is|\s+was)|it\s+(?:should|has\s+to|must)\s+be|should\s+be|supposed\s+to\s+be|"
    r"(?:isn['\u2019]t|wasn['\u2019]t)\s+it|definitely|surely|certainly|absolutely|(?:no|nope|nah)\b)",
    re.IGNORECASE)
_NO_VALUE_WRONG_RX = re.compile(
    _LEAD + r"(?:(?:that|it|this)(?:['\u2019]s|\s+is)\s+(?:not\s+(?:right|true|correct)|wrong|incorrect)|"
    r"you(?:['\u2019]re|\s+are)\s+(?:wrong|mistaken)|you\s+got\s+(?:it|that)\s+wrong)\W*$", re.IGNORECASE)
_COMMANDISH_RX = re.compile(
    r"\b(?:remind|set|add|book|schedule|cancel|move|change|call|text|send|turn|play|put|make|create|delete|"
    r"works|fine|perfect|thanks|thank|problem|please)\b", re.IGNORECASE)
_DEIXIS_AFTER_RX = re.compile(r"\b(?:today|tomorrow|yesterday|now|already|again)\b", re.IGNORECASE)
_TENTATIVE_RX = re.compile(
    r"\b(?:let\s+me|i['\u2019]ll|i\s+will|i['\u2019]m\s+going\s+to|going\s+to|gonna|should\s+i|shall\s+i|maybe|might|could|"
    r"if|whether|wonder|not\s+sure|check(?:ing)?\s+(?:my|the|if|whether))\b", re.IGNORECASE)
_NEW_FACT_RX = re.compile(
    r"\b(?:moved|changed|rescheduled|postponed|switched|brought\s+forward|pushed|now|says|shows|showed|reads|"
    r"confirmed|it['\u2019]s|it\s+is|was\s+wrong|made\s+a\s+mistake|misspoke|new)\b", re.IGNORECASE)
_REQUEST_RX = re.compile(r"\b(?:can|could|would|will)\s+you\b|\bi['\u2019]ve\s+got\s+(?:a|an|the)\b", re.IGNORECASE)
_NEUTRAL_RX = re.compile(
    r"^\W*(?:(?:um+|uh+|hmm+|but|no|nah|wait|hang\s+on|oh|so|ok(?:ay)?)[\s,.!\u2026-]+)*"
    r"(?:(?:are|r)\s+(?:you|u)\s+(?:really\s+|absolutely\s+|completely\s+)?(?:sure|certain|positive)"
    r"(?:\s+about\s+that)?|you\s+sure(?:\s+about\s+that)?|really|seriously|is\s+that\s+right|"
    r"(?:can|could)\s+you\s+(?:double[-\s]?check|verify|confirm)(?:\s+that)?)\W*$", re.IGNORECASE)
_DECLINE_RX = re.compile(
    r"\b(?:leave\s+it|never\s*mind|forget\s+it|keep\s+it|don['\u2019]t\s+(?:change|update)|no\s+need|"
    r"it['\u2019]s\s+fine|you\s+(?:were|are)\s+right|my\s+mistake|my\s+bad)\b", re.IGNORECASE)


def _strip_hint(message: str) -> str:
    """Drop a leading balanced ``[Intent hint: ...]`` prefix (chat.py adds it on the streaming path)."""
    msg = (message or "").strip()
    if not msg.startswith("[Intent hint:"):
        return msg
    depth = 0
    for i, ch in enumerate(msg):
        depth += (ch == "[") - (ch == "]")
        if depth == 0:
            return msg[i + 1:].lstrip()
    return msg


@dataclass(frozen=True)
class Reading:
    """What the owner's message is: ``kind`` is ``bare`` | ``evidence`` | ``neutral`` | ``none``."""

    kind: str
    text: str = ""
    pushed: tuple = field(default_factory=tuple)   # the values the message asserts (empty: "that's wrong")


def read(message: str) -> Reading:
    """Classify one user message. Cheap and pure: a message that is not a short pushback costs one regex
    scan and no I/O, so every other turn goes on untouched."""
    msg = _strip_hint(message)
    if not msg or len(msg) > 200 or len(msg.split()) > MAX_WORDS:
        return Reading("none")
    if _EVIDENCE_RX.search(msg):
        return Reading("evidence", msg, tuple(atoms(msg)))
    if _NEUTRAL_RX.match(msg):
        return Reading("neutral", msg)
    pushed = tuple(atoms(msg))
    if pushed and _PUSH_CUE_RX.match(msg) and not _COMMANDISH_RX.search(msg):
        after = msg[pushed[-1].end:]
        if not _DEIXIS_AFTER_RX.search(after) and len(after.split()) <= 4:
            return Reading("bare", msg, pushed)
    if not pushed and _NO_VALUE_WRONG_RX.match(msg):
        return Reading("bare", msg, ())
    return Reading("none", msg, pushed)


_C_LEAD = r"(?:yes|yeah|yep|yup|yea|sure|ok|okay|correct|definitely|absolutely|please|right|no|nope|nah|well|actually)"
_C_ASSERT = (r"(?:(?:i'?m|i am)\s+)?(?:(?:really|totally|absolutely|completely|pretty|quite|100%)\s+)?"
             r"(?:sure|certain|positive|confident)|i\s+know|i\s+said|go\s+ahead|do\s+it|"
             r"(?:change|update|switch|fix|make)\s+it|that'?s\s+(?:right|correct|what\s+i\s+said)|definitely|absolutely")
_C_VALUE = r"(?:(?:it'?s|it\s+is|it\s+was)\s+)?(?:definitely\s+)?(?:the\s+)?x"
_C_TAIL = r"(?:please|thanks|thank\s+you|mate|zoe)"
_CONFIRM_FULL_RX = re.compile(
    rf"^(?:{_C_LEAD}\s+)*(?:(?:{_C_ASSERT})\s+)*(?:{_C_VALUE}\s+)?(?:{_C_TAIL}\s*)?$")
_TAIL_ONLY_RX = re.compile(rf"^{_C_TAIL}\s*$")


def _confirm_body(message: str) -> str:
    """The message with every value replaced by ``x`` and the punctuation folded to single spaces."""
    msg = _strip_hint(message)
    for a in sorted(atoms(msg), key=lambda a: -a.start):
        msg = msg[:a.start] + " x " + msg[a.end:]
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9'%]+", " ", msg.lower().replace("\u2019", "'"))).strip() + " "


def is_update_claim(rd: "Reading") -> bool:
    """Is this EVIDENCE the owner is reporting a new value from - "I checked the calendar, it moved to Thursday."
    - rather than a plan to look ("let me check my calendar for Thursday") or a question? The owner's later
    statement wins, and it is the update half of the pair: a Zoe that only ever held would be a stubborn one."""
    if rd.kind != "evidence" or not rd.pushed:
        return False
    msg = rd.text
    return ("?" not in msg and not _TENTATIVE_RX.search(msg) and not _COMMANDISH_RX.search(msg)
            and not _REQUEST_RX.search(msg) and bool(_NEW_FACT_RX.search(msg)))


def may_confirm(message: str) -> bool:
    """Could this short message be the owner standing by a value they pushed? ("Yes, I'm sure." / "Yes, change
    it." / "It's Thursday." / "I'm positive") - the WHOLE message must be a confirmation, so "OK what's the
    weather" is not one. A cheap gate; the real test is ``reads_as_confirmation`` against the hold reply."""
    msg = _strip_hint(message)
    if not msg or len(msg.split()) > MAX_WORDS:
        return False
    body = _confirm_body(msg)
    return bool(body.strip()) and not _TAIL_ONLY_RX.match(body) and bool(_CONFIRM_FULL_RX.match(body))


def reads_as_confirmation(message: str, pushed: tuple, held: Optional[Atom]) -> bool:
    """After Zoe held a fact once: is this the owner standing by their value? A repeated pushback with the same
    value, "Yes, I'm sure", "Yes, change it", "I'm positive" - and nothing that names a DIFFERENT value, goes back
    to the stored one, retreats ("leave it", "you were right") or brings evidence (the brain updates that)."""
    msg = _strip_hint(message)
    if not msg or len(msg.split()) > MAX_WORDS or _DECLINE_RX.search(msg) or _EVIDENCE_RX.search(msg):
        return False
    if re.match(r"^\W*(?:no|nope|nah)\b[\s,.!]*$", msg, re.IGNORECASE):
        return False
    now = atoms(msg)
    if held is not None and any(_same(held, a) for a in now):
        return False                               # they came back to the stored value: the owner agrees with it
    if now and pushed and not any(_same(p, a) for p in pushed for a in now):
        return False                               # a THIRD value: a new pushback, held afresh
    return may_confirm(msg)


# -- the reply forms ------------------------------------------------------------------------------

_HOLD_PREFIX = "I've got "
_UPDATED_PREFIX = "Done - I've changed "
_CANNOT_PREFIX = "I couldn't change that "
_OWN_REPLY_RX = re.compile(
    r"^(?:I've got .{0,90}? down as .{0,40}?, and that's what you told me\.|I've got .{0,40}? noted, and that's what"
    r" you told me\.|Done - I've changed |I couldn't change that )")


def is_own_reply(text: str) -> bool:
    """Is ``text`` a reply this module wrote (hold / update / could-not-update)? The memory extractors skip the
    owner's message on such a turn: the contradicting claim was not accepted, so it is not stored behind it."""
    return bool(_OWN_REPLY_RX.match((text or "").strip()))


_SUBJECT_MY_RX = re.compile(
    r"\bmy\s+([a-z][a-z' -]{1,36}?)(?=\s+(?:is|are|was|will|on|at|this|next|again|please|now)\b|[?.!,]|$)",
    re.IGNORECASE)
_SUBJECT_POSS_RX = re.compile(
    r"\b([A-Z][a-z]{1,20})['\u2019]s\s+([a-z][a-z -]{1,28}?)(?=\s+(?:is|are|was|on|at)\b|[?.!,]|$)")
_WH = {"day", "date", "time", "what", "when", "which", "where"}


def subject_of(question: str) -> str:
    """"your dentist appointment" from "Which day is my dentist appointment?" - the words to say the fact back
    with, or '' when the question gives none (the reply then names just the value)."""
    q = _strip_hint(question)
    m = _SUBJECT_MY_RX.search(q)
    if m:
        s = m.group(1).strip()
        if s and s.split()[0].lower() not in _WH:
            return "your " + s
    m = _SUBJECT_POSS_RX.search(q)
    if m:
        return f"{m.group(1)}'s {m.group(2).strip()}"
    return ""


def _shown(atom: Atom) -> str:
    return atom.shown.capitalize() if atom.kind == "day" else atom.shown


def hold_reply(subject: str, held: str, pushed: str) -> str:
    """The HOLD form: keep the record, say where it came from, disagree once, offer to change it."""
    if subject:
        lead = f"{_HOLD_PREFIX}{subject} down as {held}, and that's what you told me."
    else:
        lead = f"{_HOLD_PREFIX}{held} noted, and that's what you told me."
    tail = (f" If you're sure it's {pushed}, just say so and I'll change it." if pushed
            else " If that's not right, tell me what it should be and I'll change it.")
    return lead + tail


def updated_reply(subject: str, pushed: str) -> str:
    """Said only after the edit went through - and only about what was edited: Zoe's notes, not a calendar."""
    return f"{_UPDATED_PREFIX}{subject or 'it'} to {pushed} in my notes."


def cannot_update_reply() -> str:
    return f"{_CANNOT_PREFIX}just now. Tell me the right one again in a full sentence and I'll make a note of it."


def _row_replace(text: str, held: Atom, pushed: Atom) -> str:
    """``text`` with the held value swapped for the pushed one - when the value is named exactly once."""
    new = _shown(pushed)
    hits = [a for a in atoms(text) if _same(held, a)]
    if len(hits) != 1:                      # the value appears twice (two things on that day): which one is wrong is not guessed
        return text
    a = hits[0]
    rep = new.lower() if a.kind == "day" and a.shown[:1].islower() else new
    return text[:a.start] + rep + text[a.end:]


# -- an evidence update must be about THE SAME THING --------------------------------------------------

_ACTOR_VERBS = (r"(?:just\s+)?(?:called|rang|phoned|emailed|texted|messaged|said|says|told|confirmed|moved|changed|"
                r"rescheduled|cancelled|canceled|postponed|shows|showed|reads)")
_ACTOR_NOUN_RX = re.compile(r"\b(?:the|my|our|a|an)\s+([a-z][a-z'-]{2,})\s+" + _ACTOR_VERBS + r"\b", re.IGNORECASE)
_ACTOR_NAME_RX = re.compile(r"\b([A-Z][a-z]{2,})\s+" + _ACTOR_VERBS + r"\b")
# who/what can be the source of news about ANY appointment: the record, the venue, the office
_GENERIC_SOURCES = frozenset({
    "calendar", "diary", "planner", "email", "emails", "inbox", "invite", "booking", "confirmation", "receipt",
    "letter", "text", "message", "app", "website", "ticket", "reminder", "card", "appointment", "event", "meeting",
    "clinic", "office", "surgery", "practice", "reception", "receptionist", "hospital", "school", "team", "airline",
    "company", "venue", "restaurant", "system", "schedule", "one", "other", "new", "same", "notification", "page",
    "time", "date", "day", "doctor", "nurse", "secretary", "assistant", "admin",
})
_NOT_NAMES = frozenset({"the", "they", "she", "her", "his", "this", "that", "there", "what", "who", "yes", "no"})


def _words_of(text: str) -> set:
    return set(re.findall(r"[a-z]+", (text or "").lower()))


def foreign_subject(message: str, question: str, answer: str) -> bool:
    """Does an EVIDENCE message report news from a source that has nothing to do with the held exchange - "The
    plumber called, it is Thursday now." against "your dentist appointment is on Friday"? A different weekday alone
    is not a correction of THIS fact; with the source named and absent from the exchange, the owner's row is not
    edited (the turn goes on to the brain as before). A generic source (calendar, clinic, email...) or one the
    exchange itself names is about the same thing."""
    msg = _strip_hint(message)
    seen = _words_of(question) | _words_of(answer)
    stems = seen | {w[:-1] for w in seen if w.endswith("s")}
    cands = [m.group(1).lower() for m in _ACTOR_NOUN_RX.finditer(msg)]
    cands += [m.group(1).lower() for m in _ACTOR_NAME_RX.finditer(msg) if m.group(1).lower() not in _NOT_NAMES]
    for c in cands:
        base = c[:-2] if c.endswith("'s") else c
        if base in _GENERIC_SOURCES or base in stems or base.rstrip("s") in stems:
            continue
        return True
    return False


# -- history + the owner-stated row -----------------------------------------------------------------

@dataclass
class Plan:
    """One decided turn. ``action`` is ``hold`` | ``update`` | ``cannot_update`` | ``none``."""

    action: str
    reply: str = ""
    held: str = ""
    pushed: str = ""
    row_id: str = ""
    reason: str = ""


async def _history(session_id: str) -> list:
    """``[(role, content)]`` newest first for this session, or []. Never raises."""
    sid = (session_id or "").strip()
    if not sid:
        return []
    try:
        from database import get_db_ctx

        async with get_db_ctx() as db:
            cur = await db.execute(
                "SELECT role, content FROM chat_messages WHERE session_id = ? "
                "ORDER BY created_at DESC LIMIT ?", (sid, HISTORY_ROWS))
            rows = await cur.fetchall()
        return [(str(r[0]), str(r[1] or "")) for r in rows]
    except Exception as exc:  # noqa: BLE001
        logger.debug("hold_the_fact: history read failed (%s)", type(exc).__name__)
        return []


def _norm(s: str) -> str:
    return re.sub(r"\W+", " ", (s or "").lower()).strip()


def exchanges(history: list, current: str) -> list:
    """``[(question, answer)]`` newest first: each assistant row with the user row before it. The turn being
    answered now (the newest user row, when it is already stored) is dropped."""
    rows = list(history)
    if rows and rows[0][0] == "user" and _norm(rows[0][1]) == _norm(_strip_hint(current)):
        rows = rows[1:]
    out = []
    for i, (role, content) in enumerate(rows):
        if role == "assistant":
            out.append((next((c for r, c in rows[i + 1:] if r == "user"), ""), content))
    return out


async def _owner_row(user_id: str, question: str, held: Atom) -> Optional[Any]:
    """The approved row the OWNER stated (or confirmed) that carries the held value, or None. A row a model
    inferred, an unverified voice said, a candidate or a recorded change is not theirs to defend."""
    try:
        import memory_authority as ma
        from memory_service import get_memory_service

        refs = await get_memory_service().search(question, user_id=user_id, limit=8, history=False, timeout_s=1.5)
        for r in refs or []:
            meta = getattr(r, "metadata", None) or {}
            text = getattr(r, "text", "") or ""
            if str(meta.get("status") or "approved") != "approved" or str(meta.get("memory_type")) == "state_change":
                continue
            if ma.is_unverified(meta) or ma.is_candidate(r):
                continue
            if ma.row_authority(meta, text) not in ma.PROTECTED:
                continue
            if any(_same(held, a) for a in atoms(text)):
                return r
    except Exception as exc:  # noqa: BLE001 - no row is "not held", never a failed turn
        logger.debug("hold_the_fact: row lookup failed (%s)", type(exc).__name__)
    return None


async def _apply_update(user_id: str, session_id: str, row: Any, held: Atom, pushed: Atom, message: str) -> bool:
    """Edit the owner's row to the value they stood by, as the owner's account (``user_confirmed``; a
    superseding edit - nothing is deleted). True only when the edit went through."""
    try:
        from memory_service import get_memory_service

        old = getattr(row, "text", "") or ""
        new_text = _row_replace(old, held, pushed)
        if not new_text or new_text == old:
            return False
        ref = await get_memory_service().review(
            row.id, decision="edit", edits=new_text, actor=user_id,
            note="owner stood by a corrected value after one gentle check (hold_the_fact)",
            source_excerpt=" ".join((message or "").split()), session_id=session_id)
        return ref is not None
    except Exception as exc:  # noqa: BLE001
        logger.warning("hold_the_fact: update failed (%s)", type(exc).__name__)
        return False


async def plan(message: str, user_id: str, session_id: str, *, speaker_verified: Optional[bool] = None,
               apply: bool = False) -> Optional[Plan]:
    """The decision for this turn, or None (leave the turn alone). ``apply`` performs an update. NEVER raises."""
    try:
        if (user_id or "").strip().lower() in _GUEST_IDS or speaker_verified is False:
            return None
        rd = read(message)
        evidence = rd.kind == "evidence" and is_update_claim(rd)
        if rd.kind == "neutral" or (rd.kind == "evidence" and not evidence) or \
                (rd.kind == "none" and not may_confirm(message)):
            return None
        ex = exchanges(await _history(session_id), message)
        if not ex:
            return None
        # 1) a confirmation: the newest assistant row is OUR hold reply and the owner stands by their value
        if not evidence and is_own_reply(ex[0][1]) and ex[0][1].startswith(_HOLD_PREFIX):
            confirmed = await _confirm(ex, message, user_id, session_id, apply)
            if confirmed is not None:
                return confirmed
        if rd.kind not in ("bare", "evidence"):
            return None
        # 2) a pushback: the answer it contradicts, however far back in the session
        for question, answer in ex:
            if is_own_reply(answer):
                continue
            if rd.pushed:
                hit = contradiction(answer, rd.text)
                if hit is None:
                    continue
                if evidence and foreign_subject(rd.text, question, answer):
                    continue                        # news about something else is not a correction of this fact
                held, pushed = hit
            else:                                   # "That's wrong." - the newest answer that states one value
                vals = [a for a in atoms(answer) if a.kind in ("day", "date", "time")]
                if len(vals) != 1:
                    continue
                held, pushed = vals[0], None
            row = await _owner_row(user_id, question, held)
            if row is None:
                return Plan("none", held=held.shown, reason="no_owner_row")
            if evidence:                             # they brought something new: the owner's later word wins
                return await _update(row, held, pushed, subject_of(question), message, user_id, session_id, apply,
                                     "evidence")
            return Plan("hold", hold_reply(subject_of(question), _shown(held), _shown(pushed) if pushed else ""),
                        _shown(held), _shown(pushed) if pushed else "", getattr(row, "id", ""), "owner_stated_row")
        return None
    except Exception as exc:  # noqa: BLE001 - the tier must never break a turn
        logger.warning("hold_the_fact.plan failed (non-fatal): %s", type(exc).__name__)
        return None


async def _update(row: Any, held: Atom, pushed: Atom, subject: str, message: str, user_id: str, session_id: str,
                  apply: bool, reason: str) -> Plan:
    """The owner's row edited to the value they stood by / brought evidence for; the reply only after the edit."""
    done = Plan("update", updated_reply(subject, _shown(pushed)), _shown(held), _shown(pushed),
                getattr(row, "id", ""), reason)
    if not apply:
        return done
    if await _apply_update(user_id, session_id, row, held, pushed, message):
        return done
    return Plan("cannot_update", cannot_update_reply(), _shown(held), _shown(pushed), getattr(row, "id", ""),
                "edit_refused")


async def _confirm(ex: list, message: str, user_id: str, session_id: str, apply: bool) -> Optional[Plan]:
    """The owner answered Zoe's hold. ``ex[0]`` is (their pushback, the hold reply); the held answer is the
    nearest older answer (``ex[1:]``, however many filler exchanges lie between) that the pushed value
    contradicts - the same search ``plan`` used to make the hold."""
    if len(ex) < 2:
        return None
    prd = read(ex[0][0])
    if prd.kind != "bare" or not prd.pushed:
        return None
    for q2, a2 in ex[1:]:
        if is_own_reply(a2):
            continue
        hit = contradiction(a2, prd.text)
        if hit is None:
            continue
        held, pushed = hit
        if not reads_as_confirmation(message, prd.pushed, held):
            return None
        row = await _owner_row(user_id, q2, held)
        if row is None:
            return Plan("none", held=held.shown, reason="no_owner_row")
        return await _update(row, held, pushed, subject_of(q2), message, user_id, session_id, apply, "confirmed")
    return None


async def handle(message: str, user_id: str, session_id: str, *, speaker_verified: Optional[bool] = None,
                 allow_writes: bool = True) -> str:
    """The reply for the fast tier, or '' (the turn goes on to the brain untouched). ``shadow`` logs what it
    would do and returns ''; ``off`` reads nothing. NEVER raises."""
    m = mode()
    if m == "off":
        return ""
    try:
        p = await plan(message, user_id, session_id, speaker_verified=speaker_verified, apply=(m == "enforce" and allow_writes))
        if p is None:
            return ""
        logger.info("HOLD_THE_FACT mode=%s decision=%s reason=%s", m, p.action, p.reason)
        return p.reply if (m == "enforce" and p.action != "none") else ""
    except Exception as exc:  # noqa: BLE001
        logger.warning("hold_the_fact.handle failed (non-fatal): %s", type(exc).__name__)
        return ""
