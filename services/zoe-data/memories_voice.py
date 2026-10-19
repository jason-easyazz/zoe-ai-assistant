"""Memories by voice and on the panel card (memories-space phase 3): "what do you know about me?", the voice-session view,
and forget / correct by voice or by tap.

Flag-dark: ``ZOE_MEMORIES_VOICE`` (default OFF, read per call). Off = this tier returns before any work, the card routes
(``routers/memories_card.py``) are ABSENT and the phone page's edit section stays hidden. Record:
``docs/knowledge/memories-voice.md``; design ``docs/architecture/memories-space.md`` 2.2 and 11 (owner answers 2026-10-18/19).

WHAT IS NEW, AND WHAT IS NOT. The reads are the existing ones: ``provenance_answers.gather`` (the member's own approved rows,
private classes counted) and ``memory_space`` (the screen's grouping and provenance). The writes are the existing audited paths:
a row is forgotten by ``memory_space.forget_row`` (the voice "forget it" steps: reject + twins, physical erase, the owner's
verbatim words), corrected by ``memory_space.correct_row`` / ``provenance_answers.fix`` (``correction_apply`` -> ``review(edit)``,
which SUPERSEDES and keeps the old row as history), and a person is forgotten by the ``memory_forget_entity`` intent (the forget
ledger - the permanent shield - and the cascade). This module adds only the SHAPES, the spoken summary, the card and the
tap-to-"that" bridge. No SQL on a memory table, no delete of its own.

    "what do you know about me?" / "what do you remember about me?"   a short spoken summary + the memories card
    "what have you learned about me this week?"                      what is new in the window + the card of those rows
    "what do you remember about Sam?" (Sam is in MY people)          Sam's facts (at most two spoken) + Sam's card
    "what do you know about my health?" (on the wall)                never said aloud on the panel: "ask me on your phone"
    "forget that I like jazz"                                        forgets the ONE row that says it (voice and chat)
    tap "That's wrong", then say "it's on Saturday" / "forget it"    corrects / forgets THAT row (the next turn, same member,
                                                                     same panel, within ``TAP_TTL_S``)

WALLS. The member is the TURN's identity (``extra_ctx['scope_user']``: an open voice session or a fresh PIN sign-in on THIS panel;
never the panel's standing binding). With none, a read gets ``PRIVATE_REPLY`` and the privacy-safe locked card; a forget does
nothing. Told / confirmed come first; inferred rows are marked ("I worked this out") and never used as the spoken example.
Health, money, feelings, private and the restraint classes (D11) are never spoken and never on the card - a count only. The card
is the panel review cards' card (``panel_review_cards.mint``): an opaque token, card-local ids, honoured only for that member.

VOICE-PATH: called from ``fast_tiers._provenance_tier`` (every lane that runs the conversation tiers), so it is replay-gated. A turn
that is none of these shapes costs a few anchored regexes; a voice turn without a panel identity is not touched at all.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import memory_provenance as mp
import memory_space
import panel_review_cards as prc
import provenance_answers as pa

logger = logging.getLogger(__name__)

ENV = "ZOE_MEMORIES_VOICE"
TIER = "memories"
ENDPOINT = "/api/panel/memories"
TAP_TTL_S = 60.0
CARD_MAX_ITEMS = 12
CARD_PEOPLE_MAX = 4
CARD_TEXT_CHARS = 160
SPOKEN_FACTS = 2
RECENT_WINDOWS = {"today": 86400, "this week": 7 * 86400, "lately": 7 * 86400, "recently": 7 * 86400,
                  "in the last week": 7 * 86400, "this month": 31 * 86400}

PRIVATE_REPLY = prc.PRIVATE_REPLY
FORGET_PRIVATE_REPLY = ("I can only forget things for someone I know is here. Say it again so I know it's you, "
                        "or unlock with your PIN.")
TOPIC_WALL_REPLY = ("That's private, so I won't say it out loud here. Ask me on your phone or in chat.")
NOT_FOUND_REPLY = "I couldn't find that in what I know about you, so there was nothing to forget."
WHICH_REPLY = "I've got more than one note like that. Say a bit more about which one."
CURATED_REPLY = "A grown-up added that one for you, so I'll leave it as it is. Ask them to change it."
NOTHING_REPLY = pa.NOTHING_KNOWN_REPLY
SCREEN_TAIL = "It's on the screen. Tap one to fix it or forget it."
TITLES = {"me": "What I know about you", "recent": "What I learned {when}", "person": "{name}"}
GROUPS = {"people": "People", "preferences": "Preferences", "plans": "Plans", "about": "About you"}
GROUP_ORDER = ("people", "preferences", "plans", "about")
PHONE_HINT = "Scan with your phone to fix it there."

_ROW_ACTIONS = (
    {"key": "wrong", "label": "That's wrong",
     "choices": [{"key": "voice", "label": "I'll say it"}, {"key": "phone", "label": "Fix on my phone", "phone": True}]},
    {"key": "forget", "label": "Forget", "choices": [{"key": "yes", "label": "Yes, forget it"}]},
    {"key": "source", "label": "Where's this from?"},
)


def enabled() -> bool:
    from typed_env import env_bool

    return env_bool("ZOE_MEMORIES_VOICE", False)


# -- the words ---------------------------------------------------------------------------------------------------------------------
_RECENT_RE = re.compile(
    rf"^\s*{pa._LEAD}what\s+(?:have|did)\s+you\s+(?:learn(?:ed|t)?|pick(?:ed)?\s+up|f(?:ou|i)nd\s+out|work(?:ed)?\s+out|notice[d]?|"
    rf"remember(?:ed)?)\s+about\s+me\s+(?P<when>today|this\s+week|lately|recently|in\s+the\s+last\s+week|this\s+month){pa._TAIL}",
    re.IGNORECASE)
_FORGET_FACT_RE = re.compile(
    rf"^\s*{pa._LEAD}(?:please\s+)?(?:forget|delete|remove|erase)\s+(?:that|the\s+(?:fact|bit|part|note|thing)\s+(?:that|where))\s+"
    r"(?P<x>(?:i|i['’]m|i\s+am|i['’]ve|i\s+have|i['’]d|i\s+used\s+to|my|we|we['’]re|our)\b.{2,140}?)\s*[.!]*\s*$",
    re.IGNORECASE | re.DOTALL)
#: "forget that I said that" / "forget that I told you" are the existing forget-last / forget-it shapes, never a fact
_SAID_ONLY_RE = re.compile(r"^i\s+(?:just\s+)?(?:said|told\s+you|mentioned|asked)(?:\s+(?:that|it|this|so))?\s*$", re.IGNORECASE)
#: after a "That's wrong" tap the next turn is the answer - unless it is plainly something else: a command, or a cancel
_NOT_AN_ANSWER_RE = re.compile(
    r"^\W*(?:(?:hey|ok(?:ay)?|zoe)[\s,]+)*(?:turn|switch|lights?|stop|pause|resume|next|skip|volume|louder|quieter|lock|sign|log|"
    r"open|close|start|call|text|message|timer|alarm|good\s*night|good\s*morning|thanks?|thank\s+you)\b", re.IGNORECASE)
_CANCEL_RE = re.compile(r"^\W*(?:no|nope|cancel|never\s*mind|leave\s+it|it['’]?s\s+fine|forget\s+about\s+it|don['’]?t\s+worry)"
                        r"(?:[\s,]+(?:then|thanks|zoe))*\W*$", re.IGNORECASE)
LEAVE_IT_REPLY = "Okay, I'll leave it as it is."
_ON_WHEN_RE = re.compile(r"^(?:on|at)\s+(?P<v>(?:mon|tues|wednes|thurs|fri|satur|sun)day|\d{1,2}(?::\d{2})?\s?(?:am|pm)|\d{1,2}:\d{2})$",
                         re.IGNORECASE)
_PLAN_RE = re.compile(
    r"\b(?:plans?\s+to|planning|going\s+to|wants?\s+to|hopes?\s+to|intends?\s+to|next\s+(?:week|month|year|weekend)|"
    r"this\s+(?:weekend|week)|tomorrow|tonight|trip|holiday|appointment)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Ask:
    kind: str             # me | recent | person | topic | forget
    value: str = ""       # recent: the window phrase; person: the name; forget: what to forget


def parse(text: str) -> Optional[Ask]:
    """The shape of an utterance, or None. Whole-utterance anchored. Pure."""
    t = " ".join(str(text or "").split())
    if not t or len(t) > 200:
        return None
    m = _FORGET_FACT_RE.match(t)
    if m:
        x = m.group("x").strip()
        return None if _SAID_ONLY_RE.match(x) else Ask("forget", x)
    m = _RECENT_RE.match(t)
    if m:
        return Ask("recent", " ".join(m.group("when").lower().split()))
    st = pa.parse_stateless(t)
    if st is None or st.kind == "explain":
        return None
    if st.kind == "know_me":
        return Ask("me")
    if st.kind == "know_topic":
        return Ask("topic", st.topic)
    if st.kind == "know_other":
        return Ask("person", st.name)
    return None


_TOKEN_STOP = frozenset({
    "i", "i'm", "im", "me", "my", "mine", "we", "we're", "our", "a", "an", "the", "that", "this", "it", "is", "am", "are", "was",
    "were", "be", "been", "have", "has", "had", "do", "does", "did", "user", "user's", "users", "to", "of", "in", "on", "at", "for",
    "with", "and", "or", "really", "actually", "just", "so", "very", "you", "your", "about", "used", "i've", "i'd"})


def _tokens(text: str) -> set:
    out = set()
    for w in re.findall(r"\d+|[a-z][a-z'’]+", str(text or "").lower()):   # numbers count: "12 Main Street" is not "14 Main Street"
        w = w.replace("’", "'")
        if w in _TOKEN_STOP:
            continue
        w = w[:-2] if w.endswith("'s") else w
        out.add(w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w)
    return out


def _covers(want: set, have: set) -> bool:
    return bool(want) and all(any(t == u or (min(len(t), len(u)) >= 4 and (t.startswith(u) or u.startswith(t))) for u in have)
                              for t in want)


_FIRST_PERSON = ((re.compile(r"^i['’]m\b", re.I), "you're"), (re.compile(r"^i\s+am\b", re.I), "you are"),
                 (re.compile(r"^i['’]ve\b", re.I), "you've"), (re.compile(r"^i['’]d\b", re.I), "you'd"),
                 (re.compile(r"^i\b", re.I), "you"), (re.compile(r"\bmy\b", re.I), "your"),
                 (re.compile(r"\bmine\b", re.I), "yours"), (re.compile(r"\bmyself\b", re.I), "yourself"))


def second(text: str) -> str:
    """A stored row as Zoe says it to its owner, lower-case first ("you like jazz", "your birthday is on 14 March"): the store's
    third person through ``provenance_answers.second_person``, then a row kept in the owner's own first person. Pure."""
    t = pa.second_person(text or "").strip().rstrip(" .")
    for rx, sub in _FIRST_PERSON:
        t = rx.sub(sub, t)
    return t[:1].lower() + t[1:]


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


# -- the wall view -----------------------------------------------------------------------------------------------------------------
def wall_safe(text: str) -> bool:
    """True when ``text`` may be said aloud / shown on a shared panel: no private class of ``provenance_answers`` and none of the
    restraint classes the panel keeps off the wall (owner D11). Fail-closed (a classifier error is NOT safe)."""
    if pa.sensitive_class(text):
        return False
    return not prc._private(text)


@dataclass
class Wall:
    items: list = field(default_factory=list)      # wall-safe non-people Items, newest first
    people: list = field(default_factory=list)     # PersonEntry with a name or a relation, newest first
    private: int = 0                                # rows held back (private classes, restraint classes, feelings)
    unsure: int = 0                                 # inferred rows nothing the member said supports: never listed


async def gather_wall(member: str, *, svc: Any = None) -> Wall:
    """What may be shown or said on the wall about ``member`` (own rows only). Raises on a store failure."""
    known = await pa.gather(member, svc=svc)
    w = Wall(private=sum(len(v) for v in known.private.values()), unsure=int(known.unconfirmed or 0))
    people_items = []
    for it in known.items:
        if it.mtype == "emotional_moment" or not wall_safe(it.text):
            w.private += 1
            continue
        txt = " ".join((it.text or "").split())
        # a people-group row the person parser can name goes into that person's entry; any other shape ("my brother and I
        # went fishing") stays a row of its own, with its id, so it can be fixed or forgotten like every other row
        named = it.group == "people" and bool(pa._NL_PERSON_RE.match(txt) or (pa._DERIVED_RE.match(txt) and it.mtype in (
            "person", "relationship")))
        (people_items if named else w.items).append(it)
    for e in pa.people_entries(people_items, known.people):
        if not (e.name or e.rel):
            continue
        e.facts = [(f, k) for f, k in e.facts if wall_safe(f)]
        w.people.append(e)
    return w


def card_group(it: Any) -> str:
    g = memory_space.group_key(it)
    if g == "preferences":
        return "preferences"
    if g in ("routines", "dates") or _PLAN_RE.search(it.text or ""):
        return "plans"
    return "about"


def _told_first(items: list) -> list:
    return sorted(items, key=lambda i: (bool(i.inferred), -(i.epoch or 0.0)))


def _person_line(e: Any) -> str:
    facts = [pa.trim(f, 70) for f, _ in e.facts[:2]]
    return "; ".join(facts)


def build_card(panel_id: str, member: str, wall: Wall, *, title: str, people: Optional[list] = None,
               items: Optional[list] = None) -> dict:
    """The memories card for ``panel_id``: people first, then preferences, plans, about you; told before inferred; each row with
    its provenance and That's wrong / Forget / Where's this from. Card-local ids only (``p1``, ``m1``); the token names the rows."""
    people = wall.people if people is None else people
    rows = wall.items if items is None else items
    reg: dict = {}
    out: list = []
    shown_people = people[:CARD_PEOPLE_MAX]
    for n, e in enumerate(shown_people, 1):
        key = f"p{n}"
        acts = []
        if e.name:
            reg[key] = {"type": "person", "ref": e.name}
            acts = [{"key": "forget_person", "label": "Forget", "choices": [{"key": "yes", "label": f"Yes, forget {e.name}"}]}]
        out.append({"id": key, "group": "people", "text": e.label(), "meta": _person_line(e), "actions": acts})
    by_group: dict = {g: [] for g in GROUP_ORDER}
    for it in _told_first(rows):
        by_group[card_group(it)].append(it)
    room = max(0, CARD_MAX_ITEMS - len(out))
    n = 0
    shown_rows = 0
    for g in GROUP_ORDER[1:]:
        for it in by_group[g]:
            if shown_rows >= room:
                break
            n += 1
            key = f"m{n}"
            reg[key] = {"type": "memory", "ref": it.id, "text": it.text}
            out.append({"id": key, "group": g, "text": _cap(pa.trim(second(it.text), CARD_TEXT_CHARS)),
                        "meta": memory_space.provenance_label(it), "soft": bool(it.inferred),
                        "actions": [dict(a) for a in _ROW_ACTIONS]})
            shown_rows += 1
    more = (len(people) - len(shown_people)) + (len(rows) - shown_rows)
    token = prc.mint(panel_id, member, "memories", reg)
    sub = []
    if wall.private:
        sub.append(f"{pa._plural(wall.private, 'private thing')} {'is' if wall.private == 1 else 'are'} not shown here.")
    return {"panel_id": panel_id, "member_id": member, "kind": "memories", "private": True, "title": title,
            "subtitle": " ".join(sub), "items": out, "more": max(0, more),
            "more_label": f"{max(0, more)} more on your phone" if more > 0 else "",
            "empty": "Nothing to show yet.", "card_token": token, "groups": GROUPS, "endpoint": ENDPOINT,
            "phone_hint": PHONE_HINT}


# -- what is said ------------------------------------------------------------------------------------------------------------------
def _examples(items: list, n: int) -> list:
    """Up to ``n`` rows the member TOLD or CONFIRMED, as clauses that read after "like" ("you walk the dog every morning"): standing
    facts (places, routines, preferences) first, short ones first, cut at a word. An inferred row is never an example."""
    told = [i for i in items if not i.inferred]
    standing = [i for i in told if memory_space.group_key(i) in ("places", "routines", "preferences")]
    said = [second(i.text) for i in standing + [i for i in told if i not in standing]]
    short = [x for x in said if len(x) <= pa.SPOKEN_EXAMPLE_CHARS]
    picks = (short + [x for x in said if x not in short])[:n]
    return [x if len(x) <= pa.SPOKEN_EXAMPLE_CHARS else pa.trim(x, pa.SPOKEN_EXAMPLE_CHARS).removesuffix("...") for x in picks]


def spoken_me(wall: Wall, *, on_screen: bool = True) -> str:
    if not wall.items and not wall.people:
        if wall.private:
            return "What I know about you is private, so it stays off this screen. Ask me on your phone."
        return pa.UNCONFIRMED_ONLY_REPLY if wall.unsure else NOTHING_REPLY
    s = ["Here's the short version."]
    if wall.people:
        named = next((e for e in wall.people if e.name), None)
        newest = f", most recently {pa._spoken_person(named)}" if named else ""
        s.append(f"I know {pa._plural(len(wall.people), 'person', 'people')} in your life{newest}.")
    ex = _examples(wall.items, SPOKEN_FACTS)
    if ex:
        s.append(f"You've told me things like {pa._join_spoken(ex)}.")
    inferred = sum(1 for i in wall.items if i.inferred)
    if inferred:
        s.append(f"I've also worked out {pa._plural(inferred, 'thing')} on my own"
                 + ("; they're marked on the screen." if on_screen else "."))
    if wall.private:
        s.append("A few private things stay off this screen." if on_screen else pa.PRIVATE_NOTE_VOICE)
    if on_screen:
        s.append(SCREEN_TAIL)
    return " ".join(s)


def recent_rows(wall: Wall, window: str, now: Optional[float] = None) -> tuple:
    cutoff = (time.time() if now is None else now) - RECENT_WINDOWS.get(window, 7 * 86400)
    items = [i for i in wall.items if i.epoch and i.epoch >= cutoff]
    people = [e for e in wall.people if e.epoch and e.epoch >= cutoff]
    return people, items


def spoken_recent(people: list, items: list, window: str, *, on_screen: bool = True) -> str:
    when = "today" if window == "today" else "this month" if window == "this month" else "this week"
    n = len(items) + len(people)
    if not n:
        return f"I haven't picked up anything new about you {when}."
    s = [f"{when[:1].upper() + when[1:]} I picked up {pa._plural(n, 'thing')}."]
    ex = _examples(items, SPOKEN_FACTS)
    if ex:
        s.append(f"Like {pa._join_spoken(ex)}.")
    elif people:
        named = next((e for e in people if e.name), None)
        if named:
            s.append(f"Like {pa._spoken_person(named)}.")
    inferred = sum(1 for i in items if i.inferred)
    if inferred:
        s.append(f"{pa._plural(inferred, 'of them', 'of them')} I worked out myself"
                 + ("; they're marked on the screen." if on_screen else "."))
    if on_screen:
        s.append(SCREEN_TAIL)
    return " ".join(s)


def spoken_person(e: Any, *, on_screen: bool = True) -> str:
    subject = e.name or (f"your {e.rel}" if e.rel else "")
    said = []
    for f, _ in e.facts[:SPOKEN_FACTS]:
        clause = pa.trim(f, 80).removesuffix("...").rstrip(" .")
        said.append(f"{subject} {clause}" if clause[:1].islower() and subject else clause)
    head = f"Here's what I know about {pa._spoken_person(e)}: "
    tail = f" {SCREEN_TAIL}" if on_screen else ""
    return head + "; ".join(said) + "." + tail


async def _find_person(member: str, wall: Wall, name: str) -> Optional[Any]:
    first = name.split()[0].lower()
    hits = [e for e in wall.people if e.name and (e.name.lower() == name.lower() or e.name.split()[0].lower() == first)]
    return hits[0] if len(hits) == 1 else None


# -- the tapped row ("that") -------------------------------------------------------------------------------------------------------
@dataclass
class Tap:
    member: str
    row_id: str
    text: str
    ts: float
    awaiting: bool = True


_TAPS: dict = {}


def tap(panel_id: str, member: str, row_id: str, text: str, *, now: Optional[float] = None) -> None:
    """A tap on "That's wrong" on ``panel_id``: the NEXT spoken turn there, by the same member, within ``TAP_TTL_S``, is the
    correction ("it's on Saturday") or "forget it". Any other turn on that panel clears it."""
    _TAPS[str(panel_id)] = Tap(str(member), str(row_id), str(text or ""), time.monotonic() if now is None else now)


def _take_tap(panel_id: str, now: Optional[float] = None) -> Optional[Tap]:
    t = _TAPS.pop(str(panel_id or ""), None)
    if t is None:
        return None
    if (time.monotonic() if now is None else now) - t.ts > TAP_TTL_S:
        return None
    return t


def reset() -> None:
    _TAPS.clear()


async def _tapped(tp: Tap, text: str, member: str, session_id: str, panel_id: str, *, svc: Any = None) -> Optional[str]:
    t = " ".join(str(text or "").split())
    if _CANCEL_RE.match(t):
        return LEAVE_IT_REPLY
    st = pa.parse_stateful(t)
    value = ""
    if st is None:
        if (tp.awaiting and t and len(t.split()) <= 14 and "?" not in t and not pa._NOT_A_FIX_RE.match(t)
                and not _NOT_AN_ANSWER_RE.match(t) and not parse(t)):
            value = pa._clean_value(re.sub(rf"^(?:{pa._IT_IS}\s+)", "", t, flags=re.IGNORECASE))
        if not value:
            return None
    elif st.kind == "forget_it":
        return await _forget_one(member, tp.row_id, tp.text, svc=svc)
    else:
        value = st.value
    m = _ON_WHEN_RE.match(value)
    if m:
        value = m.group("v")                              # "on Saturday" / "at 10am": the value, not the preposition
    ref = await asyncio.wait_for((svc or pa._svc_default()).get(tp.row_id), pa.READ_BUDGET_S)
    if ref is not None and await memory_space.curated_locked(member, ref.metadata):
        return CURATED_REPLY
    exp = mp.Explained(seq=0, row_id=tp.row_id, ts=time.time(), text=tp.text)
    reply = await pa.fix(member, session_id, text, value, exp, svc=svc, strict_twins=True)
    if reply in (pa.ASK_FIX_REPLY, pa.FIX_NEEDS_SENTENCE, pa.STORE_DOWN_REPLY):
        tap(panel_id, member, tp.row_id, tp.text)          # the next turn is still the answer
    logger.info("MEMORIES_VOICE fix=1 user=%s", member)
    return reply


# -- forgetting ----------------------------------------------------------------------------------------------------------------------
async def _forget_one(member: str, row_id: str, text: str, *, svc: Any = None) -> str:
    await pa._speculation_barrier()                       # a speculative voice turn waits for the daemon's verdict before a write
    try:
        outcome = await memory_space.forget_row(member, row_id, svc=svc)
    except Exception as exc:  # noqa: BLE001 - half done: say so, the retry finishes it
        logger.warning("memories_voice: forget failed (%s)", type(exc).__name__)
        return pa.STORE_DOWN_REPLY
    if outcome == "protected":
        return CURATED_REPLY
    if outcome != "forgotten":
        return pa.FORGET_GONE_REPLY
    logger.info("MEMORIES_VOICE forget=1 user=%s", member)
    said = pa.trim(second(text), 80).removesuffix("...").rstrip(" .")
    if not said or not wall_safe(text):
        return "Done - I've forgotten that."
    return f"Done - I've forgotten that {said}."


async def forget_fact(member: str, words: str, *, svc: Any = None) -> str:
    """"forget that I like jazz": the ONE row of the member's that says it (with its twins) goes through the audited row forget.
    Two different rows -> ask which; none -> say so. A private row is never read back."""
    want = _tokens(words)
    if not want:
        return NOT_FOUND_REPLY
    try:
        known = await pa.gather(member, svc=svc)
    except Exception as exc:  # noqa: BLE001
        logger.warning("memories_voice: gather failed (%s)", type(exc).__name__)
        return pa.STORE_DOWN_REPLY
    pool = list(known.items) + [i for lst in known.private.values() for i in lst]
    hits = [i for i in pool if _covers(want, _tokens(i.text))]
    if not hits:
        return NOT_FOUND_REPLY
    hits.sort(key=lambda i: (len(_tokens(i.text) - want), -(i.epoch or 0.0)))
    first = hits[0]
    if len(hits) > 1 and _tokens(first.text) - want:
        # more than one row says it and none says EXACTLY that: the twins of one utterance are one fact, anything else is asked
        svc = svc or pa._svc_default()
        ref = await asyncio.wait_for(svc.get(first.id), pa.READ_BUDGET_S)
        twins = {t.id for t in await pa.twins_of(member, ref, svc=svc)} if ref is not None else set()
        if [h for h in hits[1:] if h.id not in twins]:
            return WHICH_REPLY
    return await _forget_one(member, first.id, first.text, svc=svc)


async def forget_person(member: str, name: str) -> str:
    """Forget a person through the EXISTING ``memory_forget_entity`` intent: the permanent forget ledger, the verbatim words,
    the cascade (people row, edges, loops, the learned voice). The member's own calendar events and list items stay (owner D6)."""
    from intent_router import Intent, execute_intent

    return str(await execute_intent(Intent("memory_forget_entity", {"name": name}), member) or "")


# -- the turn ------------------------------------------------------------------------------------------------------------------------
async def _push(frame: Optional[dict]) -> None:
    try:
        import voice_cards

        await voice_cards.push(frame)
    except Exception:  # noqa: BLE001 - the card is an addition to the spoken answer
        pass


async def answer(text: str, user_id: str, session_id: str = "", *, channel: str, extra_ctx: Optional[dict],
                 allow_writes: bool = True, svc: Any = None) -> Optional[str]:
    ctx = extra_ctx or {}
    panel_id = str(ctx.get("panel_id") or "") if channel == "voice" else ""
    on_panel = bool(panel_id) and "scope_user" in ctx
    if channel == "voice" and not on_panel:
        return None                                       # a voice lane with no panel identity: unchanged
    member = None if ctx.get("speaker_verified") is False else prc._member(user_id, channel, ctx)
    if on_panel and allow_writes:
        tp = _take_tap(panel_id)                          # the very next turn on this panel uses it, or it is gone
        if tp is not None and member == tp.member:
            out = await _tapped(tp, text, member, session_id, panel_id, svc=svc)
            if out:
                return out
    ask = parse(text)
    if ask is None:
        return None
    if ask.kind == "forget":
        if member is None:
            return FORGET_PRIVATE_REPLY
        if not allow_writes:
            return None                                   # a dry / speculative turn never forgets
        return await forget_fact(member, ask.value, svc=svc)
    if not on_panel:
        return None                                       # chat keeps the existing answers (provenance_answers)
    if member is None:
        if ask.kind == "person":
            return None                                   # not addressed to anything we could check: unchanged
        if allow_writes:
            try:
                import voice_cards

                await voice_cards.push(voice_cards.locked(panel_id))
            except Exception:  # noqa: BLE001
                pass
        return PRIVATE_REPLY
    if ask.kind == "topic":
        return TOPIC_WALL_REPLY
    if ask.kind == "person":
        first = ask.value.split()[0].lower()
        if first in await pa._own_names(member):
            ask = Ask("me")
        elif first in await pa.account_names():
            return None                                   # another member's file: provenance_answers refuses it
    try:
        wall = await gather_wall(member, svc=svc)
    except Exception as exc:  # noqa: BLE001
        logger.warning("memories_voice: gather failed (%s)", type(exc).__name__)
        return pa.STORE_DOWN_REPLY
    if ask.kind == "person":
        e = await _find_person(member, wall, ask.value)
        if e is None or not e.facts:
            return None                                   # not one of MY people (or nothing to say): the recall path answers
        if allow_writes:
            await _push(build_card(panel_id, member, wall, title=TITLES["person"].format(name=e.label()), people=[e], items=[]))
        logger.info("MEMORIES_VOICE read=person user=%s", member)
        return spoken_person(e)
    if ask.kind == "recent":
        people, items = recent_rows(wall, ask.value)
        if allow_writes:
            when = "today" if ask.value == "today" else "this month" if ask.value == "this month" else "this week"
            await _push(build_card(panel_id, member, wall, title=TITLES["recent"].format(when=when), people=people, items=items))
        logger.info("MEMORIES_VOICE read=recent user=%s", member)
        return spoken_recent(people, items, ask.value)
    if allow_writes:
        await _push(build_card(panel_id, member, wall, title=TITLES["me"]))
    logger.info("MEMORIES_VOICE read=me user=%s", member)
    return spoken_me(wall)


async def tier(text: str, user_id: str, session_id: str = "", *, channel: Optional[str] = None,
               extra_ctx: Optional[dict[str, Any]] = None, allow_writes: bool = True):
    """The fast-tier hook (voice and chat): a ``DispatchResult`` or None. Off = None before any work. NEVER raises."""
    try:
        ch = (channel or "").strip().lower()
        if ch not in ("voice", "chat") or not enabled():
            return None
        reply = await answer(text, user_id, session_id, channel=ch, extra_ctx=extra_ctx, allow_writes=allow_writes)
        if not reply:
            return None
        import expert_dispatch as _xd

        return _xd.DispatchResult(domain="memory", reply=reply, intent="memories", tier=TIER)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("memories_voice: tier failed (non-fatal): %s", type(exc).__name__)
        return None
