"""provenance_answers - "why did you say that?", "what do you know about me?", the one-sentence fix and "off the record" (BM5).

Why. Zoe answered from a recall packet and could not say where an answer came from; "what do you know about me" went to the
brain, which summarised whatever it had in context; a correction was an apology; and nothing let the owner say "this is not
for your memory". The best products converge on the same four verbs (ChatGPT's per-reply sources, Claude's view / edit /
pause, Gemini's "explain the information it used"); a household assistant that listens all day needs them more.

What. ONE deterministic tier (``fast_tiers.resolve``, before the router and the brain, on every channel that uses the core:
chat, voice, LiveKit, Telegram). Every answer is built from stored rows - never on the brain's say-so - and every one is
bounded and spoken-length:

1. **"why did you say that?" / "where did you get that?" / "how do you know that?"** - right after a reply. The ledger
   (``memory_provenance``) holds the ids of the rows that reply restated; the answer re-reads each row and names the best one's
   DAY and the OWNER'S OWN WORDS, verbatim (the row's ``source_excerpt`` for a writer whose excerpt is by contract the owner's
   turn; else the owner's own verbatim turn from the ``exact_words`` index; never a model's paraphrase, never another member's
   words, never a guest's, never an unverified voice's). A reply that used no memory says so plainly. No record of the previous
   reply (a lane that records nothing, a restart, a stale turn) says it cannot tell - it is never "no memory used".
2. **"what do you know about me?" / "what have you got on me?"** - a bounded, grouped summary of the owner's own rows: people,
   places, routines, preferences, recent threads; counts and the newest items. Voice: counts + one newest item per group and a
   hand-off to chat for the full list. Chat / Telegram: the longer list with dates. **Private classes (health, feelings, money,
   relationships in trouble, secrets) are counted, never read, unless pulled by name** ("what do you know about my health").
3. **The one-sentence fix** - "that's wrong, it's X" / "actually X" on the turn right after (1) edits the named row through the
   existing correction path (``correction_apply``, ``ZOE_CORRECTION_APPLY``) and says what it now holds. "forget it" on that
   same turn forgets THAT row (and the twin rows the same utterance left, and the owner's quoted turn in the exact-words index).
4. **Off the record** - "off the record: ...", "don't remember this", "this stays between us". The cue plus a payload marks THAT
   turn; a bare cue arms the NEXT turn (``memory_provenance``). A marked turn reaches no extractor, no digest, no person
   extractor, no exact-words index, no nightly catch-up (its transcript row carries ``off_record``) and no write of the
   ``MemoryService.ingest`` choke point; the only trace is one audit log line without the words.

Walls (each pinned by ``tests/test_provenance_answers.py``): a guest and an unregistered id are told so and nothing is read; a
speaker the voice gate rejected is told so and nothing is read; every read is scoped to the asking user; "what do you know about
<another household member>" is refused; a forgotten row is not quoted; a sensitive row's words are never spoken on the voice
channel.

Flag ``ZOE_MEMORY_PROVENANCE_ANSWERS`` (default ON; ``0|false|no|off`` = the tier does nothing). The fix verb additionally needs
``ZOE_CORRECTION_APPLY`` (the existing correction path's own flag, default OFF): without it the answer says plainly that it cannot
change a note by voice yet - it never claims a fix it did not make.

VOICE-PATH: runs inside ``fast_tiers.resolve`` (the voice channel calls it); a turn that is none of these shapes costs one
anchored regex; an answer is one bounded read of the owner's rows (<= ``SCAN_LIMIT``) plus up to 3 row gets.
"""
from __future__ import annotations

import asyncio
import datetime
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import memory_provenance as mp

logger = logging.getLogger(__name__)

ENV = mp.ENV
SCAN_LIMIT = 1000
QUOTE_MAX_CHARS = 120
ITEM_MAX_CHARS = 70
VOICE_CHANNELS = frozenset({"voice", "livekit"})
_GUEST_IDS = frozenset({"", "guest", "anonymous", "voice-guest", "voice-daemon"})
RECENT_WINDOW_S = 7 * 86400
#: rows read per get / lookups bound a turn: the whole answer must stay inside a voice turn
READ_BUDGET_S = 4.0


def enabled() -> bool:
    return mp.enabled()


def _is_guest(user_id: Optional[str]) -> bool:
    u = (user_id or "").strip().lower()
    return u in _GUEST_IDS or u.startswith("guest-")


# ── replies (fixed strings; the tests pin their promises) ─────────────────────

UNKNOWN_REPLY = ("I don't have a record of what that answer came from. Ask me right after one of my answers "
                 "and I'll tell you where it came from.")
NO_MEMORY_REPLY = ("I didn't use anything I'd remembered about you for that - it was just my answer to what you said.")
EXTRA_SENTENCES = {
    "offer": " The offer to add a contact was me following up on someone you'd mentioned.",
    "raise": " I also brought up something you'd told me earlier, on my own.",
    "brief": " I also mentioned something from your day that I'd noted.",
}
UNMATCHED_REPLY = ("I had some notes about you in front of me, but nothing in that answer came from them.")
FORGOTTEN_REPLY = "That came from a note I've since removed, so I can't show you where it came from."
NOT_YOURS_REPLY = ("That came from a note someone else in the house shared with me, so I won't repeat it.")
UNVERIFIED_ROW_REPLY = ("That came from something said at the panel that I couldn't tie to you, so I won't quote it.")
GUEST_REPLY = ("I don't keep anything about guests, so there's nothing for me to show. "
               "Sign in and I can tell you what I know about you.")
GUEST_ABOUT_OTHER_REPLY = "I don't share what I know about the people here with guests."
OTHER_MEMBER_REPLY = "I keep what I know about each person to that person, so I can't share that."
UNVERIFIED_SPEAKER_REPLY = ("I couldn't tell who was speaking, so I haven't shared anything - say it again once I know it's you.")
NOTHING_KNOWN_REPLY = ("I don't know much about you yet. Tell me something and say \"remember that\" and I'll keep it.")
STORE_DOWN_REPLY = "I couldn't reach my memory just now, so I can't say - try again in a moment."
FIX_TAIL = "If that's wrong, tell me the right answer, or say forget it."
ASK_FIX_REPLY = "Okay - what's the right answer?"
FIX_NEEDS_SENTENCE = ("I can't tell which part to change. Tell me the whole thing, for example "
                      "\"my sister is called Marisa\", and I'll fix it.")
FIX_OFF_REPLY = ("I can't change a note by voice yet. Say forget it to drop it, then tell me the right answer.")
FIX_GONE_REPLY = "That note's already gone, so there's nothing to fix."
FIX_TURN_REPLY = ("That was your own words from what you said, so there's no note of mine to correct. "
                  "Say forget it to drop it, or tell me the right thing and I'll keep that.")
FORGET_GONE_REPLY = "There's nothing there to forget - it's already gone."
OFF_RECORD_ACKS = {
    "off_the_record": "Okay, off the record - I won't keep your next message.",
    "between_us": "Understood, this stays between us - I won't keep your next message.",
    "dont_remember": "Okay, I won't remember your next message.",
}
PRIVATE_NOTE_VOICE = "There are also a few private things I only share if you ask me about them directly."
HANDOFF_VOICE = "For the whole list, ask me in chat or on Telegram."

_DIRECT_TEXT = {
    "identity": "That came from your account details, not from something you told me.",
    "provenance": "That was me showing where my last answer came from.",
    "correction": "That was me acting on what you'd just told me.",
    "ask_to_remember": "That was me acting on what you'd just asked me to keep.",
    "roster": "That was me checking the list you gave me, not something I'd remembered.",
}
_DIRECT_LOOKUP_DOMAINS = frozenset({"time", "weather", "lists", "calendar", "reminders", "timers", "music", "smart_home"})
_DIRECT_STORED_DOMAINS = frozenset({"memory", "people"})


# ── shapes (pure) ─────────────────────────────────────────────────────────────

_LEAD = r"(?:(?:ok(?:ay)?|hey|hi|so|but|wait|hang\s+on|hold\s+on|um+|uh+|zoe|and|well|right|sorry|oh|please)[,.\s]+)*"
_TAIL = r"(?:\s+(?:just\s+now|exactly|again|please|zoe|then|though))*\s*[?.!]*\s*$"
_THAT = r"(?:that|this|it|so)"
_EXPLAIN_BODIES = (
    rf"why\s+(?:did|do|would)\s+you\s+(?:just\s+)?(?:say|tell\s+me|mention|think)\s+{_THAT}",
    r"why\s+did\s+you\s+bring\s+(?:that|it|this)\s+up",
    rf"why(?:'d|\s+would)\s+you\s+say\s+{_THAT}",
    rf"why\s+(?:are\s+you\s+)?saying\s+{_THAT}",
    r"where\s+(?:did|do|would)\s+you\s+(?:just\s+)?(?:get|got|find|hear|pull|learn)\s+(?:that|this|it)(?:\s+(?:from|info|information))?",
    r"where(?:'d|\s+did)\s+you\s+(?:get|find|hear)\s+(?:that|this|it)(?:\s+from)?",
    r"where\s+(?:is|was)\s+(?:that|this)\s+from",
    rf"how\s+(?:did|do|would|could)\s+you\s+(?:just\s+)?know\s+{_THAT}",
    rf"how\s+come\s+you\s+(?:said|know|knew)\s+{_THAT}",
    rf"what\s+(?:made|makes)\s+you\s+(?:say|think)\s+{_THAT}",
    r"what\s+(?:are\s+you|is\s+that)\s+based\s+on",
    r"what(?:'s|\s+is)\s+(?:that|your\s+answer)\s+based\s+on",
    r"what\s+are\s+you\s+basing\s+(?:that|this|it)\s+on",
    r"what(?:'s|\s+is)\s+your\s+source(?:\s+for\s+that)?",
    r"who\s+told\s+you\s+(?:that|this)",
    r"show\s+me\s+where\s+(?:you\s+got|that\s+came\s+from|you\s+heard)\s*(?:that|it)?",
)
_EXPLAIN_RE = re.compile(rf"^\s*{_LEAD}(?:{'|'.join(_EXPLAIN_BODIES)}){_TAIL}", re.IGNORECASE)

_KNOW_VERB = r"(?:know|got|have|remember|recall|stored?|kept?|keep(?:ing)?|learn(?:ed|t)?|hold(?:ing)?)"
_KNOW_ME_BODIES = (
    rf"what\s+(?:do|have|did|would|are)\s+you\s+(?:really\s+)?(?:got|have|{_KNOW_VERB})\s+(?:on|about)\s+me(?:\s+so\s+far|\s+now|\s+until\s+now)?",
    rf"what\s+(?:do|have|did)\s+you\s+(?:really\s+)?(?:got|have|{_KNOW_VERB})\s+(?:on|about)\s+me\s+(?:in\s+detail|in\s+full|altogether)",
    r"what\s+(?:have|did)\s+i\s+(?:told|tell)\s+you(?:\s+(?:so\s+far|about\s+(?:me|myself)|until\s+now))?",
    r"what(?:'s|\s+is)\s+in\s+(?:your|the)\s+memory\s+(?:of|about|for|on)\s+me",
    r"what(?:'s|\s+is)\s+in\s+my\s+memory",
    rf"(?:tell|show|give)\s+me\s+(?:everything|all|what)\s+(?:that\s+)?you\s+(?:{_KNOW_VERB})\s*(?:about|on)?\s*(?:me)?(?:\s+so\s+far)?",
    rf"show\s+me\s+what\s+you\s+(?:{_KNOW_VERB})\s+(?:about|on)\s+me",
    r"what\s+(?:information|info|data|details)\s+(?:do|have)\s+you\s+(?:have|got|kept?|stored?)\s+(?:on|about)\s+me",
)
_KNOW_ME_RE = re.compile(rf"^\s*{_LEAD}(?:{'|'.join(_KNOW_ME_BODIES)}){_TAIL}", re.IGNORECASE)
_MORE_RE = re.compile(r"\b(?:everything|all|in\s+detail|in\s+full|altogether)\b", re.IGNORECASE)
#: "what do you know about my health" - the private classes are pulled by name
_KNOW_TOPIC_RE = re.compile(
    rf"^\s*{_LEAD}what\s+(?:do|have|did)\s+you\s+(?:really\s+)?(?:got|have|{_KNOW_VERB})\s+(?:on|about)\s+my\s+"
    rf"(?P<topic>health|medical(?:\s+(?:stuff|history|conditions?))?|medications?|medicines?|mood|moods|feelings|emotions?|"
    rf"mental\s+health|finances|money|debts?)(?:\s+so\s+far)?{_TAIL}", re.IGNORECASE)
#: "what do you know about Jason" - a third person
_KNOW_OTHER_RE = re.compile(
    rf"^\s*{_LEAD}what\s+(?:do|have|did)\s+you\s+(?:really\s+)?(?:got|have|{_KNOW_VERB})\s+(?:on|about)\s+"
    rf"(?P<name>(?!me\b|myself\b|my\b|the\b|a\b|an\b|it\b|that\b|this\b|you\b|us\b|our\b|everything\b|anything\b)[A-Za-z][A-Za-z'’\-]{{1,30}}"
    rf"(?:\s+[A-Za-z][A-Za-z'’\-]{{1,30}})?)(?:\s+so\s+far)?{_TAIL}", re.IGNORECASE)

_FORGET_IT_RE = re.compile(
    rf"^\s*{_LEAD}(?:yes[,.\s]+|yeah[,.\s]+|yep[,.\s]+)?(?:please\s+)?(?:forget|delete|remove|erase|drop|scrap|ditch)\s+"
    rf"(?:that|it|this)(?:\s+(?:one|note|fact|memory))?\s*(?:please)?\s*[.!]*\s*$", re.IGNORECASE)

_WRONG = (r"(?:(?:that(?:'s|\s+is|\s+was)|it(?:'s|\s+is|\s+was)|this\s+is)\s+(?:just\s+)?(?:wrong|not\s+right|incorrect|not\s+correct|"
          r"not\s+true|false|out\s+of\s+date|not\s+quite\s+right)"
          r"|(?:that|it)\s+(?:isn['’]?t|is\s+not|wasn['’]?t)\s+(?:right|true|correct)"
          r"|you(?:'ve|\s+have|\s+got|\s+are)\s+(?:got\s+)?(?:that|it)?\s*wrong"
          r"|you(?:'re|\s+are)\s+wrong)")
_IT_IS = r"(?:it(?:'s|\s+is|\s+was)|its|that(?:'s|\s+is)|they(?:'re|\s+are))"
_FIX_WRONG_RE = re.compile(
    rf"^\s*{_LEAD}(?:no[,.\s]+|nope[,.\s]+)?{_WRONG}(?:\s*[,;.\-–—:]+\s*(?:actually\s+|really\s+)?(?:{_IT_IS}\s+)?(?P<x>.+?))?\s*[.!]*\s*$",
    re.IGNORECASE | re.DOTALL)
_FIX_ACTUALLY_RE = re.compile(
    rf"^\s*{_LEAD}(?:no[,.\s]+|nope[,.\s]+)?(?:actually|in\s+fact|really)[,\s]+(?:{_IT_IS}\s+)?(?P<x>.+?)\s*[.!]*\s*$",
    re.IGNORECASE | re.DOTALL)
_FIX_NO_ITS_RE = re.compile(rf"^\s*{_LEAD}(?:no|nope)[,.\s]+{_IT_IS}\s+(?P<x>.+?)\s*[.!]*\s*$", re.IGNORECASE | re.DOTALL)
_NOT_Y_RE = re.compile(r"[,;]?\s*\bnot\s+.+$", re.IGNORECASE)
_NOT_A_FIX_RE = re.compile(
    r"^(?:can|could|would|will|please|set|add|remind|play|turn|what|when|where|who|why|how|is|are|do|does|did|tell|show|call|text|open)\b",
    re.IGNORECASE)


@dataclass(frozen=True)
class Ask:
    kind: str                # explain | know_me | know_topic | know_other | forget_it | fix
    topic: str = ""          # know_topic: health | mood | money
    name: str = ""           # know_other
    more: bool = False       # know_me: asked for everything
    value: str = ""          # fix: the corrected value / sentence ("" = "that's wrong" alone)


def _topic_class(raw: str) -> str:
    r = raw.lower()
    if r.startswith(("health", "medic", "mental")) or "condition" in r:
        return "health"
    if r.startswith(("mood", "feel", "emotion")):
        return "mood"
    return "money"


def parse_stateless(text: str) -> Optional[Ask]:
    """The shapes that need no conversation state: explain / know-me / know-topic / know-other. Pure."""
    t = (text or "").strip()
    if not t or len(t) > 200 or t.count("\n") > 1:
        return None
    if _EXPLAIN_RE.match(t):
        return Ask("explain")
    m = _KNOW_TOPIC_RE.match(t)
    if m:
        return Ask("know_topic", topic=_topic_class(m.group("topic")))
    if _KNOW_ME_RE.match(t):
        return Ask("know_me", more=bool(_MORE_RE.search(t)))
    m = _KNOW_OTHER_RE.match(t)
    if m:
        return Ask("know_other", name=m.group("name").strip())
    return None


def parse_stateful(text: str) -> Optional[Ask]:
    """The shapes that mean the row the previous answer named: "forget it", "that's wrong, it's X", "actually X". Only ever
    consulted on the turn right after an answer named a row. Pure."""
    t = (text or "").strip()
    if not t or len(t) > 240 or t.count("\n") > 1:
        return None
    if _FORGET_IT_RE.match(t):
        return Ask("forget_it")
    m = _FIX_WRONG_RE.match(t)
    if m:
        return Ask("fix", value=_clean_value(m.group("x") or ""))
    for rx in (_FIX_NO_ITS_RE, _FIX_ACTUALLY_RE):
        m = rx.match(t)
        if m:
            v = _clean_value(m.group("x") or "")
            if v and not _NOT_A_FIX_RE.match(v) and "?" not in v:
                return Ask("fix", value=v)
    return None


def _clean_value(x: str) -> str:
    x = re.sub(r"\s+", " ", x or "").strip(" \t,;.!-–—")
    x = _NOT_Y_RE.sub("", x).strip(" \t,;.!-–—")
    return x[:200]


# ── dates ─────────────────────────────────────────────────────────────────────

_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November",
           "December")


def spoken_day(epoch: float, now: Optional[float] = None) -> str:
    """"earlier today" | "yesterday" | "on Tuesday" (within a week) | "on 3 October" (+ year when it is not this one), in the
    household timezone. Pure (``now`` injectable)."""
    from time_utils import zoe_timezone

    tz = zoe_timezone()
    when = datetime.datetime.fromtimestamp(epoch, tz)
    today = datetime.datetime.fromtimestamp(time.time() if now is None else now, tz)
    days = (today.date() - when.date()).days
    if days <= 0:
        return "earlier today"
    if days == 1:
        return "yesterday"
    if days < 7:
        return f"on {_WEEKDAYS[when.weekday()]}"
    out = f"on {when.day} {_MONTHS[when.month - 1]}"
    return out + (f" {when.year}" if when.year != today.year else "")


def short_day(epoch: float, now: Optional[float] = None) -> str:
    """"3 Oct" / "3 Oct 2025" for the chat list."""
    from time_utils import zoe_timezone

    tz = zoe_timezone()
    when = datetime.datetime.fromtimestamp(epoch, tz)
    today = datetime.datetime.fromtimestamp(time.time() if now is None else now, tz)
    s = f"{when.day} {_MONTHS[when.month - 1][:3]}"
    return s + (f" {when.year}" if when.year != today.year else "")


# ── sensitivity (pure) ────────────────────────────────────────────────────────

_HEALTH_RE = re.compile(
    r"\b(?:health|medical|medications?|medicines?|prescri\w+|diagnos\w+|doctor|surgery|hospital|therap\w+|psychiatr\w+|"
    r"psycholog\w+|counsell?\w+|depress\w+|anxi\w+|panic|migraines?|cancer|chemo\w*|diabet\w+|insulin|asthma|epilep\w+|allerg\w+|"
    r"pregnan\w+|miscarr\w+|fertility|ivf|menopaus\w+|blood\s+pressure|cholesterol|disorder|addict\w+|rehab|alcoholi\w+|"
    r"self[- ]harm|suicid\w+|grief|bereave\w+|funeral|passed\s+away|sick|illness|disease|condition|symptoms?|injur\w+)\b", re.IGNORECASE)
_MONEY_RE = re.compile(
    r"\b(?:salary|wages?|income|debts?|loans?|mortgage|bankrupt\w*|credit\s+score|tax\s+return|savings|owes?|owing|pay\s*cut|"
    r"redundan\w+|fired|laid\s+off|overdrawn|broke)\b", re.IGNORECASE)
_PRIVATE_RE = re.compile(
    r"\b(?:sexual\w*|gay|lesbian|bisexual|transgender|religio\w+|christian|muslim|jewish|hindu|atheist|affair|cheat\w+|divorc\w+|"
    r"separated|separation|abus\w+|assault\w*|arrest\w*|convict\w*|prison|immigration|visa|password|passcode|pin\s+number|"
    r"secret|private|confidential)\b", re.IGNORECASE)


def sensitive_classes(text: str, meta: Optional[dict] = None) -> frozenset:
    """Every private class a row falls in (``health`` | ``mood`` | ``money`` | ``private``); empty for an ordinary fact. A worry about
    a diagnosis is health AND mood - a pull by either name finds it. Conservative on purpose: a row that MIGHT be private is
    counted, not read. Pure."""
    meta = meta or {}
    out = set()
    mtype = str(meta.get("memory_type") or "").lower()
    if mtype == "health":
        out.add("health")
    if mtype == "emotional_moment":
        out.add("mood")
    try:
        from memory_service import MemoryRef, is_emotional_memory

        if is_emotional_memory(MemoryRef(id="", text=text or "", metadata=meta)):
            out.add("mood")
    except Exception:  # noqa: BLE001
        pass
    t = text or ""
    if _HEALTH_RE.search(t):
        out.add("health")
    if _MONEY_RE.search(t):
        out.add("money")
    if _PRIVATE_RE.search(t):
        out.add("private")
    return frozenset(out)


_CLASS_ORDER = ("health", "money", "private", "mood")


def sensitive_class(text: str, meta: Optional[dict] = None) -> str:
    """The primary private class of a row ("" for an ordinary fact): health, then money, private, mood. Pure."""
    got = sensitive_classes(text, meta)
    return next((c for c in _CLASS_ORDER if c in got), "")


# ── grouping (pure) ───────────────────────────────────────────────────────────

_RELATION_RE = re.compile(
    r"\b(?:sister|brother|mother|father|mum|mom|dad|wife|husband|partner|girlfriend|boyfriend|son|daughter|grand\w+|aunt|uncle|"
    r"cousin|niece|nephew|friend|colleague|boss|neighbou?r|flatmate|roommate|fianc\w+|in-law|kids?|children|baby)\b",
    re.IGNORECASE)
_PLACE_RE = re.compile(
    r"\b(?:lives?\s+in|living\s+in|moved\s+(?:to|from)|from|born\s+in|grew\s+up\s+in|works?\s+(?:at|in|for)|office|home\s+is|"
    r"house|apartment|flat|suburb|city|town|school|hometown|address|based\s+in|flying\s+(?:in\s+)?from)\b", re.IGNORECASE)
_ROUTINE_RE = re.compile(
    r"\b(?:every\s+(?:day|morning|evening|night|week|weekend|monday|tuesday|wednesday|thursday|friday|saturday|sunday)|each\s+"
    r"(?:morning|evening|day|week)|daily|weekly|usually|always|routine|on\s+(?:mondays|tuesdays|wednesdays|thursdays|fridays|"
    r"saturdays|sundays|weekdays|weekends)|walks?\s+the\s+dog|night\s+shifts?|shifts?|gym|runs?\s+(?:every|at)|wakes?\s+up|"
    r"gets?\s+up|bedtime|commut\w+)\b", re.IGNORECASE)
_PREF_RE = re.compile(
    r"\b(?:likes?|loves?|prefers?|favou?rite|hates?|dislikes?|can't\s+stand|enjoys?|into|fan\s+of|vegetarian|vegan|pescatarian|"
    r"drinks?|eats?|doesn't\s+(?:like|eat|drink))\b", re.IGNORECASE)
GROUPS = ("people", "places", "routines", "preferences", "other")


def group_of(text: str, meta: Optional[dict] = None) -> str:
    """people | places | routines | preferences | other. First match wins in that order. Pure."""
    meta = meta or {}
    mtype = str(meta.get("memory_type") or "").lower()
    etype = str(meta.get("entity_type") or "").lower()
    if etype in ("person", "person_pending") or mtype in ("person", "relationship") or _RELATION_RE.search(text or ""):
        return "people"
    if _PLACE_RE.search(text or ""):
        return "places"
    if mtype == "habit" or _ROUTINE_RE.search(text or ""):
        return "routines"
    if mtype == "preference" or _PREF_RE.search(text or ""):
        return "preferences"
    return "other"


_USER_POSS_RE = re.compile(r"\buser['’]s\b", re.IGNORECASE)


def second_person(text: str) -> str:
    """"User's sister is named Marisol" -> "your sister is named Marisol"; "User lives in Perth" -> "you live in Perth". A light
    rewrite for the spoken list; text that does not start like a third-person note is returned as it is. Pure."""
    t = re.sub(r"\s+", " ", (text or "").strip())
    t = re.sub(r"^user['’]s\b", "your", t, flags=re.IGNORECASE)
    t = _USER_POSS_RE.sub("your", t)
    m = re.match(r"^user\s+(is|has|was|had|will|does|doesn't|can|can't|works|lives|likes|loves|hates|prefers|plays|walks|goes|"
                 r"drinks|eats|owns|studies|wants|needs|uses|speaks|takes|runs)\b(.*)$", t, re.IGNORECASE | re.DOTALL)
    if m:
        verb = m.group(1).lower()
        verb = {"is": "are", "has": "have", "was": "were", "does": "do", "doesn't": "don't", "works": "work", "lives": "live",
                "likes": "like", "loves": "love", "hates": "hate", "prefers": "prefer", "plays": "play", "walks": "walk",
                "goes": "go", "drinks": "drink", "eats": "eat", "owns": "own", "studies": "study", "wants": "want",
                "needs": "need", "uses": "use", "speaks": "speak", "takes": "take", "runs": "run"}.get(verb, verb)
        t = f"you {verb}{m.group(2)}"
    t = t.rstrip(" .")
    return t[:1].lower() + t[1:] if t[:1].isupper() and not re.match(r"^[A-Z]{2}", t) and not t.startswith("I ") else t


def trim(text: str, limit: int = ITEM_MAX_CHARS) -> str:
    t = re.sub(r"\s+", " ", (text or "").strip())
    if len(t) <= limit:
        return t
    cut = t[: limit - 1]
    return (cut.rsplit(" ", 1)[0] if " " in cut else cut).rstrip(" ,;:.-") + "..."


def _plural(n: int, one: str, many: Optional[str] = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


# ── reading rows ──────────────────────────────────────────────────────────────

def _svc_default():
    from memory_service import get_memory_service

    return get_memory_service()


def _owns(meta: dict, user_id: str) -> bool:
    return str((meta or {}).get("user_id") or (meta or {}).get("wing") or "").strip().lower() == (user_id or "").strip().lower()


def _row_epoch(meta: dict) -> Optional[float]:
    import recall_evidence

    return recall_evidence.row_epoch(meta)


@dataclass
class Item:
    id: str
    text: str
    epoch: Optional[float]
    group: str
    sensitive: str = ""
    inferred: bool = False
    classes: frozenset = frozenset()


@dataclass
class Known:
    items: list = field(default_factory=list)       # public items, newest first
    private: dict = field(default_factory=dict)     # class -> [Item]
    people: list = field(default_factory=list)      # [(name, relationship, epoch)] newest first (the people table)
    total: int = 0


async def load_people(user_id: str) -> list:
    """The owner's people-table rows, newest first: ``[(name, relationship, epoch)]``. Own rows only, never a deleted one.
    Never raises ([] on any failure)."""
    try:
        from db_pool import get_db_ctx  # type: ignore[import]

        async with get_db_ctx() as db:
            cur = await db.execute(
                "SELECT name, relationship, updated_at FROM people WHERE user_id = ? AND COALESCE(deleted, 0) = 0 "
                "ORDER BY updated_at DESC LIMIT 200", (user_id,))
            rows = await cur.fetchall()
        out = []
        for r in rows:
            ep = None
            try:
                ep = datetime.datetime.fromisoformat(str(r[2]).replace("Z", "+00:00")).timestamp()
            except Exception:  # noqa: BLE001
                pass
            out.append((str(r[0] or "").strip(), str(r[1] or "").strip(), ep))
        return [p for p in out if p[0]]
    except Exception as exc:  # noqa: BLE001
        logger.debug("provenance_answers: people read skipped (%s)", type(exc).__name__)
        return []


async def gather(user_id: str, *, svc: Any = None) -> Known:
    """The owner's approved rows, sorted into public items (grouped) and private ones (counted by class), plus the people table.
    Own rows only; pasted, instruction-shaped, unverified and recorded-change rows are left out. Raises on a store failure
    (the caller says so)."""
    import memory_authority as auth
    import own_words

    svc = svc or _svc_default()
    rows = await asyncio.wait_for(svc.list_by_status(user_id=user_id, status="approved", limit=SCAN_LIMIT), READ_BUDGET_S)
    known = Known()
    for ref in rows:
        meta = ref.metadata or {}
        text = (ref.text or "").strip()
        if not text or not _owns(meta, user_id):
            continue
        if str(meta.get("memory_type") or "") == "state_change":
            continue
        if own_words.is_pasted_row(meta) or own_words.instruction_shaped(text) or auth.is_unverified(meta):
            continue
        try:
            inferred = auth.row_rank(meta, text) < auth.USER_RANK
        except Exception:  # noqa: BLE001
            inferred = True
        classes = sensitive_classes(text, meta)
        primary = next((c for c in _CLASS_ORDER if c in classes), "")
        it = Item(ref.id, text, _row_epoch(meta), group_of(text, meta), primary, inferred, classes)
        known.total += 1
        if it.sensitive:
            known.private.setdefault(it.sensitive, []).append(it)
        else:
            known.items.append(it)
    known.items.sort(key=lambda i: i.epoch or 0.0, reverse=True)
    for lst in known.private.values():
        lst.sort(key=lambda i: i.epoch or 0.0, reverse=True)
    known.people = await load_people(user_id)
    return known


def _by_group(known: Known) -> dict:
    out: dict = {g: [] for g in GROUPS}
    for it in known.items:
        out[it.group].append(it)
    return out


def _person_label(name: str, rel: str) -> str:
    return f"{name} ({rel.lower()})" if rel and rel.lower() not in ("contact", "other", "unknown") else name


def render_summary(known: Known, *, voice: bool, more: bool = False, now: Optional[float] = None) -> str:
    """The grouped summary. Voice: counts + one newest item per group, bounded, with a hand-off to chat. Chat / Telegram: the longer
    list with dates. Private classes are counted, never read. Pure (``now`` injectable)."""
    groups = _by_group(known)
    n_people = len(known.people) or len(groups["people"])
    private_n = sum(len(v) for v in known.private.values())
    if not known.items and not known.people and not private_n:
        return NOTHING_KNOWN_REPLY
    cutoff = (time.time() if now is None else now) - RECENT_WINDOW_S
    recent = [i for i in known.items if i.epoch and i.epoch >= cutoff]
    if voice:
        parts = []
        if n_people:
            newest = _person_label(known.people[0][0], known.people[0][1]) if known.people else trim(second_person(groups["people"][0].text))
            parts.append(f"{_plural(n_people, 'person', 'people')}, newest {newest}")
        for key, one, many in (("places", "place", "places"), ("routines", "routine", "routines"),
                               ("preferences", "preference", "preferences")):
            if groups[key]:
                parts.append(f"{_plural(len(groups[key]), one, many)}, like {trim(second_person(groups[key][0].text))}")
        if groups["other"]:
            parts.append(_plural(len(groups["other"]), "other thing"))
        out = "Here's the short version. I know " + "; ".join(parts) + "." if parts else "Here's the short version."
        if recent:
            out += f" Most recently, {trim(second_person(recent[0].text))}."
        if private_n:
            out += " " + PRIVATE_NOTE_VOICE
        return out + " " + HANDOFF_VOICE
    cap = 8 if more else 5
    lines = ["Here's what I've got on you, newest first."]
    if n_people:
        names = [_person_label(n, r) for n, r, _e in known.people[:cap]] or [trim(second_person(i.text)) for i in groups["people"][:cap]]
        lines.append(f"**People** ({n_people}): " + ", ".join(names) + (" ..." if n_people > len(names) else ""))
    for key, title in (("places", "Places"), ("routines", "Routines"), ("preferences", "Preferences"), ("other", "Other")):
        items = groups[key]
        if not items:
            continue
        lines.append(f"**{title}** ({len(items)}):")
        for it in items[:cap]:
            when = f" ({short_day(it.epoch, now)})" if it.epoch else ""
            lines.append(f"- {trim(second_person(it.text), 140)}{when}")
        if len(items) > cap:
            lines.append(f"- ... and {len(items) - cap} more")
    if recent:
        lines.append("**Recently** (last week):")
        for it in recent[:3]:
            lines.append(f"- {trim(second_person(it.text), 140)} ({short_day(it.epoch, now)})")
    inferred = sum(1 for i in known.items if i.inferred)
    if inferred:
        lines.append(f"{_plural(inferred, 'of these')} I worked out from our chats rather than hearing you say it.")
    if private_n:
        kinds = ", ".join(sorted(known.private))
        lines.append(f"I've also kept {_plural(private_n, 'private thing')} ({kinds}). I only share those if you ask directly, "
                     "for example \"what do you know about my health\".")
    lines.append("Ask \"why did you say that?\" right after any answer to see where it came from, say \"forget\" and what to drop, "
                 "or say \"off the record\" before something you don't want kept.")
    return "\n".join(lines)


def render_topic(known: Known, topic: str, *, voice: bool, now: Optional[float] = None) -> str:
    """The private rows of ONE class, pulled by name (owner's rows only). Voice: at most two, short; chat: up to eight."""
    items = [i for lst in known.private.values() for i in lst if topic in (i.classes or {i.sensitive})]
    items.sort(key=lambda i: i.epoch or 0.0, reverse=True)
    label = {"health": "your health", "mood": "how you've been feeling", "money": "your money"}.get(topic, topic)
    if not items:
        return f"I haven't kept anything about {label}."
    cap = 2 if voice else 8
    if voice:
        out = f"About {label}: " + "; ".join(trim(second_person(i.text), 100) for i in items[:cap]) + "."
        return out + (f" That's {len(items)} things; ask me in chat for the rest." if len(items) > cap else "")
    lines = [f"Here's what I've kept about {label}:"]
    for it in items[:cap]:
        when = f" ({short_day(it.epoch, now)})" if it.epoch else ""
        lines.append(f"- {trim(second_person(it.text), 160)}{when}")
    if len(items) > cap:
        lines.append(f"- ... and {len(items) - cap} more")
    return "\n".join(lines)


# ── the owner's own words behind a row ────────────────────────────────────────

def _excerpt_writers() -> frozenset:
    import memory_authority as auth
    import recall_evidence

    return frozenset(recall_evidence.QUOTABLE_WRITERS | auth.USER_TURN_WRITERS | {"conversation_correction"})


def _pick_sentence(excerpt: str, fact: str) -> str:
    import recall_evidence

    excerpt = recall_evidence._one_line(excerpt)
    if len(excerpt) <= QUOTE_MAX_CHARS:
        return excerpt
    facts = recall_evidence._content_words(fact)
    best, best_score = excerpt, -1
    for sentence in recall_evidence._SENTENCE_RE.split(excerpt):
        score = len(recall_evidence._content_words(sentence) & facts)
        if score > best_score:
            best, best_score = sentence, score
    return trim(best, QUOTE_MAX_CHARS)


def owner_quote(meta: dict, fact: str) -> str:
    """The owner's OWN words behind a row, or "". Only when the row's excerpt is by contract the owner's turn (a per-turn writer),
    after the own-words wall (the owner's portion only; never pasted, never instruction-shaped, never an unverified voice). A
    teach row (the row text IS the owner's clause) falls back to its text. Never a model's paraphrase. Pure."""
    import memory_authority as auth
    import own_words
    import recall_evidence

    meta = meta or {}
    if auth.is_unverified(meta) or own_words.is_pasted_row(meta):
        return ""
    writer = recall_evidence.effective_writer(meta)
    excerpt = str(meta.get("source_excerpt") or "").strip()
    if excerpt and writer in _excerpt_writers():
        own = own_words.analyze(excerpt)
        words = (own.text or "").strip() if own.has_own else ""
        if words and not own_words.instruction_shaped(words):
            return _pick_sentence(words, fact)
    if writer in auth.TEACH_WRITERS and fact and not own_words.instruction_shaped(fact):
        return trim(re.sub(r"\s+", " ", fact), QUOTE_MAX_CHARS)
    return ""


async def _verbatim_turn(user_id: str, fact: str, meta: dict) -> tuple:
    """(words, said_at) from the owner's own verbatim turns (``exact_words``) for a row with no usable excerpt, or ("", 0). A hit
    must have been said no later than the row was captured and not more than ~40 h before it (a nightly row covers a day)."""
    try:
        import exact_words

        hits = await exact_words.lookup(user_id, fact, k=1)
        if not hits:
            return "", 0.0
        h = hits[0]
        ep = _row_epoch(meta)
        if ep is not None and not (ep - 40 * 3600 <= h.said_at <= ep + 300):
            return "", 0.0
        return trim(h.text, QUOTE_MAX_CHARS), float(h.said_at)
    except Exception as exc:  # noqa: BLE001
        logger.debug("provenance_answers: verbatim lookup skipped (%s)", type(exc).__name__)
        return "", 0.0


@dataclass
class Evidence:
    row_id: str = ""
    turn_id: str = ""            # set when the words are one of the owner's verbatim turns (no row)
    quote: str = ""
    said_at: Optional[float] = None
    wording: str = ""            # row text, kept in memory one turn for a fragment fix
    refusal: str = ""            # a fixed reply when the row cannot be named (not yours, forgotten, unverified)
    sensitive: str = ""
    pasted: bool = False
    batch: bool = False


async def evidence_for_row(user_id: str, row_id: str, *, svc: Any = None) -> Evidence:
    """Re-read one row and decide what may be said about it. A row that is gone, not approved, not the asker's or unverified
    comes back with ``refusal`` set and no words."""
    import memory_authority as auth
    import own_words
    import recall_evidence

    svc = svc or _svc_default()
    ref = await asyncio.wait_for(svc.get(row_id), READ_BUDGET_S)
    ev = Evidence(row_id=row_id)
    if ref is None:
        ev.refusal = FORGOTTEN_REPLY
        return ev
    meta = ref.metadata or {}
    if str(meta.get("status") or "approved").lower() != "approved":
        ev.refusal = FORGOTTEN_REPLY
        return ev
    try:
        import memory_forgotten

        if await memory_forgotten.matches(user_id, ref.text or ""):
            ev.refusal = FORGOTTEN_REPLY
            return ev
    except Exception:  # noqa: BLE001
        pass
    if not _owns(meta, user_id):
        ev.refusal = NOT_YOURS_REPLY
        return ev
    if auth.is_unverified(meta):
        ev.refusal = UNVERIFIED_ROW_REPLY
        return ev
    ev.wording = (ref.text or "").strip()
    ev.sensitive = sensitive_class(ev.wording, meta)
    ev.pasted = own_words.is_pasted_row(meta)
    ev.batch = recall_evidence.effective_writer(meta) in recall_evidence.BATCH_WRITERS
    if not ev.pasted:
        ev.quote = owner_quote(meta, ev.wording)
        if not ev.quote:
            ev.quote, said = await _verbatim_turn(user_id, ev.wording, meta)
            if ev.quote and said:
                ev.said_at = said
    if ev.said_at is None:
        ep = _row_epoch(meta)
        ev.said_at = None if (ep is None or ev.batch) else ep
    return ev


async def evidence_for_turn(user_id: str, turn_id: str) -> Evidence:
    """One of the owner's own verbatim turns (an ``exact_words`` source): its words and the day it was said. A turn that names an
    entity the owner has since asked Zoe to forget is not quoted (the ledger is consulted on every read)."""
    import exact_words

    got = await asyncio.wait_for(exact_words.get_backend().get(user_id, [turn_id]), READ_BUDGET_S)
    if not got:
        return Evidence(refusal=FORGOTTEN_REPLY)
    _tid, text, said_at = got[0]
    try:
        import memory_forgotten

        if await memory_forgotten.matches(user_id, text):
            return Evidence(refusal=FORGOTTEN_REPLY)
    except Exception:  # noqa: BLE001
        pass
    return Evidence(turn_id=turn_id, quote=trim(text, QUOTE_MAX_CHARS), said_at=float(said_at), wording=text)


def render_explanation(ev: Evidence, *, more: int, voice: bool, now: Optional[float] = None) -> str:
    """The spoken answer for one named source. Pure."""
    if ev.refusal:
        return ev.refusal
    day = spoken_day(ev.said_at, now) if ev.said_at else ""
    quote = (ev.quote or "").rstrip(" .!?,;")
    if ev.sensitive and voice and quote:
        return (f"That came from something private you told me{(' ' + day) if day else ''}. I won't read it out loud - "
                "ask me in chat and I'll show you. " + FIX_TAIL)
    if ev.pasted:
        return (f"That came from something you pasted in{(' ' + day) if day else ''}, so I won't read it back. " + FIX_TAIL)
    extra = f" I also used {_plural(more, 'other thing')} I'd noted." if more > 0 else ""
    if quote and day:
        return f"I said that because {day} you told me, \"{quote}\".{extra} {FIX_TAIL}"
    if quote:
        return f"I said that because you told me, \"{quote}\".{extra} {FIX_TAIL}"
    if ev.batch:
        return ("I said that from a note I put together from our chats, so I don't have your exact words or the day."
                f"{extra} {FIX_TAIL}")
    if day:
        return (f"I said that from a note I made {day}, but I don't have your exact words for it.{extra} {FIX_TAIL}")
    return f"I said that from something I'd noted about you, but I can't tell you when or in what words.{extra} {FIX_TAIL}"


def direct_explanation(tier: str, domain: str = "") -> str:
    t = (tier or "").lower()
    if t in _DIRECT_TEXT:
        return _DIRECT_TEXT[t]
    d = (domain or "").lower()
    if d in _DIRECT_STORED_DOMAINS:
        return ("That answer came from my stored notes about you and your people, but I didn't keep track of which note. "
                "Ask me what I know about you and I'll show you.")
    if t in ("tier0",) or d in _DIRECT_LOOKUP_DOMAINS:
        label = {"time": "the clock", "weather": "the weather", "lists": "your lists", "calendar": "your calendar",
                 "reminders": "your reminders", "timers": "your timers", "music": "the music player",
                 "smart_home": "the house"}.get(d, "a direct lookup")
        return f"That came straight from {label}, not from anything I'd remembered about you."
    return UNKNOWN_REPLY


async def explain(user_id: str, *, channel: Optional[str] = None, svc: Any = None, now: Optional[float] = None) -> str:
    """"why did you say that?": the previous reply's source, in the owner's words. NEVER raises."""
    voice = (channel or "") in VOICE_CHANNELS
    rec = mp.previous_reply(user_id, now=now)
    if rec is None:
        return UNKNOWN_REPLY
    if rec.kind == "direct":
        return direct_explanation(rec.tier, rec.domain)
    extra = "".join(EXTRA_SENTENCES[k] for k in rec.extra if k in EXTRA_SENTENCES)
    if rec.served == 0:
        return NO_MEMORY_REPLY + extra
    if not rec.sources:
        return UNMATCHED_REPLY + extra
    try:
        first: Optional[Evidence] = None
        named = 0
        for src in rec.sources[:3]:
            ev = (await evidence_for_turn(user_id, src.id)) if src.kind == "xw" else await evidence_for_row(user_id, src.id, svc=svc)
            if ev.refusal and first is None:
                first = ev            # keep the first refusal; try the next source for something nameable
                continue
            if ev.refusal:
                continue
            named = len(rec.sources) - 1
            if ev.row_id or ev.turn_id:
                mp.mark_explained(user_id, ev.row_id, text=ev.wording, quote=ev.quote, turn_id=ev.turn_id, now=now)
            return render_explanation(ev, more=named, voice=voice, now=now)
        return first.refusal if first is not None else UNKNOWN_REPLY
    except Exception as exc:  # noqa: BLE001
        logger.warning("provenance_answers: explain failed (%s)", type(exc).__name__)
        return STORE_DOWN_REPLY


# ── the fix and the forget ────────────────────────────────────────────────────

async def _speculation_barrier() -> None:
    """A write on a SPECULATIVE voice turn waits for the daemon's verdict (and is dropped on cancel) - the rule every other write on
    the voice path follows (``expert_dispatch.dispatch``, ``ask_to_remember``). A no-op when nothing is bound."""
    try:
        import voice_speculation as _vs
    except Exception:  # noqa: BLE001 - in-tree; a missing module means no speculation exists
        return
    if _vs.bound_gate() is not None:
        await _vs.await_commit("provenance_answers")


_WEEKDAY_RE = re.compile(r"\b(?:mon|tues?|wed(?:nes)?|thu(?:rs)?|fri|sat(?:ur)?|sun)(?:day)?s?\b", re.IGNORECASE)
_TIME_RE = re.compile(r"\b\d{1,2}(?::\d{2})?\s?(?:am|pm)\b|\b\d{1,2}:\d{2}\b", re.IGNORECASE)
_NUMBER_RE = re.compile(r"\b\d+(?:[.,]\d+)?\b")
_CAPS_SPAN_RE = re.compile(r"(?<![.!?]\s)(?<!^)\b[A-Z][a-zA-Z'’\-]+(?:\s+[A-Z][a-zA-Z'’\-]+){0,2}")
_SENTENCE_FIX_RE = re.compile(r"^(?:my|our|i|i['’]m|i['’]ve|we|we['’]re|the|his|her|their|your)\b", re.IGNORECASE)


def fix_text(old: str, value: str) -> str:
    """The row text after the correction, or "" when it cannot be decided safely.

    * the value is a whole statement ("my sister is called Marisa") -> that statement, as the owner said it;
    * the value is one weekday / time / number / name and the old text holds EXACTLY one of that kind -> swapped in place.
    Anything else is not guessed. Pure."""
    v = re.sub(r"\s+", " ", value or "").strip(" .!")
    o = (old or "").strip()
    if not v or not o:
        return ""
    if len(v.split()) >= 4 and _SENTENCE_FIX_RE.match(v):
        return v[:1].upper() + v[1:] if v[:1].islower() and not v.startswith("i ") else v
    for rx, needs in ((_TIME_RE, _TIME_RE), (_WEEKDAY_RE, _WEEKDAY_RE)):
        if needs.fullmatch(v):
            found = rx.findall(o)
            if len(found) == 1:
                return rx.sub(v, o, count=1)
            return ""
    if _NUMBER_RE.fullmatch(v):
        found = _NUMBER_RE.findall(o)
        return _NUMBER_RE.sub(v, o, count=1) if len(found) == 1 else ""
    if len(v.split()) <= 3 and re.fullmatch(r"[A-Za-z][A-Za-z'’\- ]*", v):
        spans = [m.group(0) for m in _CAPS_SPAN_RE.finditer(o)]
        spans = [s for s in spans if s.lower() not in ("user", "users")]
        if len(spans) == 1:
            new = " ".join(w[:1].upper() + w[1:] for w in v.split()) if v.islower() else v
            return o.replace(spans[0], new, 1)
    return ""


async def fix(user_id: str, session_id: str, utterance: str, value: str, exp: "mp.Explained", *, svc: Any = None,
              speaker_verified: Optional[bool] = None, now: Optional[float] = None) -> str:
    """"that's wrong, it's X": correct the row the previous answer named, through the correction path. NEVER raises."""
    import correction_apply

    if speaker_verified is False:
        return UNVERIFIED_SPEAKER_REPLY
    if not exp.row_id:      # the answer quoted a turn of the owner's own: words they said are not a note Zoe made
        return FIX_TURN_REPLY
    if not value:
        mp.mark_explained(user_id, exp.row_id, text=exp.text, quote=exp.quote, awaiting_fix=True, now=now)
        # the answer to "what's the right answer?" is the next turn
        return ASK_FIX_REPLY
    if not correction_apply.enabled():
        return FIX_OFF_REPLY
    try:
        svc = svc or _svc_default()
        ev = await evidence_for_row(user_id, exp.row_id, svc=svc)
        if ev.refusal:
            return FIX_GONE_REPLY if ev.refusal == FORGOTTEN_REPLY else ev.refusal
        new = fix_text(ev.wording, value)
        if not new:
            mp.mark_explained(user_id, exp.row_id, text=exp.text, quote=exp.quote, awaiting_fix=True, now=now)
            return FIX_NEEDS_SENTENCE
        old_ref = await asyncio.wait_for(svc.get(exp.row_id), READ_BUDGET_S)
        twins = await twins_of(user_id, old_ref, svc=svc) if old_ref is not None else []
        await _speculation_barrier()
        res = await correction_apply.apply_row_correction(user_id, exp.row_id, new, utterance=utterance, session_id=session_id,
                                                          svc=svc)
        if res is None:
            return FIX_GONE_REPLY
        for tw in twins:   # the twin rows one utterance left behind still hold the old value
            try:
                await svc.review(tw.id, decision="reject", actor=user_id, note="superseded by a correction")
            except Exception as exc:  # noqa: BLE001
                logger.debug("provenance_answers: twin retire skipped (%s)", type(exc).__name__)
        mp.clear_explained(user_id)
        return res.reply
    except Exception as exc:  # noqa: BLE001
        logger.warning("provenance_answers: fix failed (%s)", type(exc).__name__)
        return STORE_DOWN_REPLY


_TWIN_WINDOW_S = 300.0
_TWIN_OVERLAP = 0.5


async def twins_of(user_id: str, ref: Any, *, svc: Any = None) -> list:
    """The other approved rows ONE utterance left behind (the extractor's twin and the digest's): same owner, same evidence
    excerpt or turn id, or captured within five minutes of it with most of the same content words - so "forget it" does not leave
    the fact standing in a sibling. Never raises."""
    try:
        svc = svc or _svc_default()
        meta = ref.metadata or {}
        ep = _row_epoch(meta)
        words = mp.content_words(ref.text or "")
        excerpt = str(meta.get("source_excerpt") or "").strip().lower()
        turn = str(meta.get("user_turn_id") or "")
        out = []
        for r in await asyncio.wait_for(svc.list_by_status(user_id=user_id, status="approved", limit=SCAN_LIMIT), READ_BUDGET_S):
            if r.id == ref.id or not _owns(r.metadata or {}, user_id):
                continue
            m = r.metadata or {}
            same_turn = (turn and str(m.get("user_turn_id") or "") == turn) or (
                excerpt and str(m.get("source_excerpt") or "").strip().lower() == excerpt)
            r_ep = _row_epoch(m)
            near = ep is not None and r_ep is not None and abs(r_ep - ep) <= _TWIN_WINDOW_S
            if not (same_turn or near):
                continue
            rw = mp.content_words(r.text or "")
            if words and rw and len(words & rw) / min(len(words), len(rw)) >= _TWIN_OVERLAP:
                out.append(r)
        return out
    except Exception as exc:  # noqa: BLE001
        logger.debug("provenance_answers: twin lookup skipped (%s)", type(exc).__name__)
        return []


async def forget_it(user_id: str, exp: "mp.Explained", *, svc: Any = None, speaker_verified: Optional[bool] = None) -> str:
    """"forget it" right after an answer named a row: reject THAT row and its twins, erase the text for real (when physical erase is
    on), and delete the owner's quoted turn from the exact-words index. NEVER raises."""
    if speaker_verified is False:
        return UNVERIFIED_SPEAKER_REPLY
    if not exp.row_id:
        return await _forget_turn(user_id, exp, svc=svc)
    try:
        svc = svc or _svc_default()
        ref = await asyncio.wait_for(svc.get(exp.row_id), READ_BUDGET_S)
        if ref is None or not _owns(ref.metadata or {}, user_id) or str((ref.metadata or {}).get("status") or "approved").lower() != "approved":
            mp.clear_explained(user_id)
            return FORGET_GONE_REPLY
        twins = await twins_of(user_id, ref, svc=svc)
        await _speculation_barrier()
        done = await svc.review(ref.id, decision="reject", actor=user_id, note="forget_explained")
        if done is None:
            return STORE_DOWN_REPLY
        gone = [ref.id]
        for tw in twins:
            if await svc.review(tw.id, decision="reject", actor=user_id, note="forget_explained") is not None:
                gone.append(tw.id)
        if hasattr(svc, "erase_rows"):
            try:
                from memory_service import physical_erase_enabled

                if physical_erase_enabled():
                    await svc.erase_rows(user_id, gone, actor=user_id, reason="forgotten by request (explained)")
            except Exception as exc:  # noqa: BLE001 - the reject already hid the rows
                logger.warning("provenance_answers: physical erase failed (%s) - rows stay rejected", type(exc).__name__)
        try:
            import exact_words

            for words in {exp.quote, ref.text or ""}:
                if words:
                    await exact_words.erase_text(user_id, words)
        except Exception as exc:  # noqa: BLE001
            logger.warning("provenance_answers: exact-words erase failed (%s)", type(exc).__name__)
        mp.clear_explained(user_id)
        logger.info("PROVENANCE_FORGET user=%s rows=%d", user_id, len(gone))
        return f"Done - I forgot: \"{trim((ref.text or '').strip(), 80)}\"."
    except Exception as exc:  # noqa: BLE001
        logger.warning("provenance_answers: forget failed (%s)", type(exc).__name__)
        return STORE_DOWN_REPLY


async def _forget_turn(user_id: str, exp: "mp.Explained", *, svc: Any = None) -> str:
    """"forget it" after an answer that quoted one of the owner's verbatim turns: delete that turn from the exact-words index and
    retract the rows the same utterance produced (their evidence is that turn). NEVER raises."""
    try:
        svc = svc or _svc_default()
        import exact_words

        await _speculation_barrier()
        erased = await exact_words.erase_text(user_id, exp.quote or exp.text)
        gone = 0
        key = re.sub(r"\s+", " ", exp.text or exp.quote).strip().lower()
        if key:
            for r in await asyncio.wait_for(svc.list_by_status(user_id=user_id, status="approved", limit=SCAN_LIMIT), READ_BUDGET_S):
                ex = re.sub(r"\s+", " ", str((r.metadata or {}).get("source_excerpt") or "")).strip().lower()
                if _owns(r.metadata or {}, user_id) and ex and (ex == key or key in ex):
                    if await svc.review(r.id, decision="reject", actor=user_id, note="forget_explained") is not None:
                        gone += 1
        mp.clear_explained(user_id)
        logger.info("PROVENANCE_FORGET user=%s turn=1 rows=%d erased=%d", user_id, gone, erased)
        if not erased and not gone:
            return FORGET_GONE_REPLY
        return "Done - I forgot what you said there."
    except Exception as exc:  # noqa: BLE001
        logger.warning("provenance_answers: forget turn failed (%s)", type(exc).__name__)
        return STORE_DOWN_REPLY


# ── who is another member ─────────────────────────────────────────────────────

_account_cache: dict = {"ts": 0.0, "names": frozenset()}
_ACCOUNT_TTL_S = 120.0


async def account_names() -> frozenset:
    """Lower-case first names / usernames / display names of the registered accounts (any member). Cached; [] on failure."""
    now = time.monotonic()
    if now - _account_cache["ts"] < _ACCOUNT_TTL_S:
        return _account_cache["names"]
    names: set = set()
    try:
        from db_pool import get_db_ctx  # type: ignore[import]

        async with get_db_ctx() as db:
            cur = await db.execute("SELECT a.username, u.name FROM auth_users a LEFT JOIN users u ON u.id = a.user_id")
            for r in await cur.fetchall():
                for raw in (r[0], r[1]):
                    s = str(raw or "").strip().lower()
                    if s:
                        names.add(s)
                        names.add(s.split()[0])
    except Exception as exc:  # noqa: BLE001
        logger.debug("provenance_answers: account names skipped (%s)", type(exc).__name__)
    _account_cache["ts"], _account_cache["names"] = now, frozenset(names)
    return _account_cache["names"]


async def _own_names(user_id: str) -> frozenset:
    """The asker's own first names (account name, preferred name), lower-case. Never raises."""
    try:
        import identity_facts

        ident = await identity_facts.resolve_identity(user_id, budget_s=1.0)
        if ident is None:
            return frozenset()
        out = set()
        for raw in (getattr(ident, "name", ""), getattr(ident, "account_name", "")):
            if str(raw or "").strip():
                out.add(str(raw).strip().lower().split()[0])
        return frozenset(out)
    except Exception:  # noqa: BLE001
        return frozenset()


# ── the entry point ───────────────────────────────────────────────────────────

async def handle(text: str, user_id: str, session_id: str = "", *, channel: Optional[str] = None,
                 speaker_verified: Optional[bool] = None, svc: Any = None, now: Optional[float] = None) -> Optional[str]:
    """The reply for a provenance / memory-control shape in ``text``, or None (flag off, not one of these shapes, or a turn that
    belongs to the brain - including an off-the-record turn WITH a payload, which is marked here and answered by the brain). NEVER
    raises (``CancelledError`` still propagates, so a cancelled speculative turn writes nothing)."""
    try:
        if not enabled():
            return None
        t = (text or "").strip()
        if not t:
            return None
        uid = (user_id or "").strip()
        guest = _is_guest(uid)
        voice = (channel or "") in VOICE_CHANNELS

        # (4) off the record: any registered member. A bare cue is answered here; a cue with a payload marks the turn and goes on to
        # the brain, which answers the content.
        if not guest:
            cue = mp.parse_off_record(t)
            if cue is not None:
                mp.claim_turn(uid, t, now=now)
                if not cue.payload:
                    return OFF_RECORD_ACKS.get(cue.cue, OFF_RECORD_ACKS["off_the_record"])
                return None

        # (3) / forget-it: only on the turn right after an answer named a row
        if not guest:
            exp = mp.explained(uid, now=now)
            if exp is not None:
                if exp.awaiting_fix:
                    # "what's the right answer?" - this whole turn is the answer
                    if (len(t.split()) <= 14 and "?" not in t and not mp.parse_off_record(t)
                            and not _FORGET_IT_RE.match(t)):
                        v = _clean_value(re.sub(rf"^(?:{_IT_IS}\s+)", "", t, flags=re.IGNORECASE))
                        if v:
                            return await fix(uid, session_id, t, v, exp, svc=svc, speaker_verified=speaker_verified, now=now)
                st = parse_stateful(t)
                if st is not None:
                    import correction_apply

                    if st.kind == "forget_it":
                        return await forget_it(uid, exp, svc=svc, speaker_verified=speaker_verified)
                    if st.kind == "fix" and not correction_apply.is_correction_turn(t):
                        return await fix(uid, session_id, t, st.value, exp, svc=svc, speaker_verified=speaker_verified, now=now)

        ask = parse_stateless(t)
        if ask is None:
            return None
        if guest:
            return GUEST_ABOUT_OTHER_REPLY if ask.kind == "know_other" else GUEST_REPLY
        if ask.kind == "know_other":
            first = ask.name.split()[0].lower()
            # the asker's own first name is "me"; another household member's file is theirs; a friend in the owner's own people
            # list is the brain's (the named-person floor)
            own = await _own_names(uid)
            if first in own:
                ask = Ask("know_me")
            elif first in await account_names() and first != "zoe":
                return OTHER_MEMBER_REPLY
            else:
                return None
        if speaker_verified is False:
            return UNVERIFIED_SPEAKER_REPLY
        if ask.kind == "explain":
            return await explain(uid, channel=channel, svc=svc, now=now)
        try:
            known = await gather(uid, svc=svc)
        except Exception as exc:  # noqa: BLE001
            logger.warning("provenance_answers: gather failed (%s)", type(exc).__name__)
            return STORE_DOWN_REPLY
        if ask.kind == "know_topic":
            return render_topic(known, ask.topic, voice=voice, now=now)
        return render_summary(known, voice=voice, more=ask.more, now=now)
    except Exception as exc:  # noqa: BLE001 - a turn is never broken by this tier
        logger.warning("provenance_answers failed (non-fatal): %s", type(exc).__name__)
        return None
