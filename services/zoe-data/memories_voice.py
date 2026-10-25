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
DIFFERENT_REPLY = ("I have a note that says something a little different from that, so I haven't forgotten anything. "
                   "Say it the way I have it, or tell me more.")
RETRY_TTL_S = 300.0
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
    for w in re.findall(r"\d+|[a-z][a-z'’]*", str(text or "").lower()):   # numbers count: "12 Main Street" is not "14 Main Street"
        w = w.replace("’", "'")
        if w in _TOKEN_STOP:
            continue
        w = w[:-2] if w.endswith("'s") else w
        out.add(w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w)
    return out


def _norm(text: str) -> str:
    """The words of a row in order, lower-case, punctuation gone: two texts are 'the same words' only if this is equal. A token SET
    is not enough - "I love Sam" and "Sam loves me" share one."""
    return " ".join(re.findall(r"[a-z0-9']+", str(text or "").lower().replace("\u2019", "'")))


_SUFFIXES = (("ing", 3), ("ed", 3), ("es", 3), ("er", 4), ("ly", 4), ("s", 3))   # (suffix, shortest stem left after stripping it)


def _stem(w: str) -> str:
    """A word reduced to its inflectional stem so only TRUE variants compare equal (plan/plans/planned, love/loves/loved/loving) -
    never a bare prefix: "plan" is not "planet", and forgetting is permanent. Pure."""
    cut = ""
    for suf, keep in _SUFFIXES:
        if w.endswith(suf) and not (suf == "s" and w.endswith("ss")) and len(w) - len(suf) >= keep:
            w, cut = w[: -len(suf)], suf
            break
    if len(w) > 3 and w.endswith("e"):
        w = w[:-1]
    if cut in ("ing", "ed") and len(w) > 3 and w[-1] == w[-2] and w[-1] not in "aeiou":
        w = w[:-1]                                        # planned -> plann -> plan (but matter is not mat)
    return w


def _covers(want: set, have: set) -> bool:
    stems = {_stem(u) for u in have}
    return bool(want) and all(t in have or _stem(t) in stems for t in want)


_NEGATION_RE = re.compile(r"\b(?:not|no|never|none|nothing|nobody|cannot|without)\b|n['’]t\b", re.I)
#: a temporal qualifier changes WHEN a claim holds ("I used to like jazz" is not "I like jazz"); the stopwords drop most of them
_TEMPORAL_RE = re.compile(r"\bused\s+to\b|\bno\s+longer\b|\bany\s?more\b|\bback\s+then\b|\bpreviously\b|\bformerly\b|"
                          r"\bthese\s+days\b|\bnowadays\b|\bcurrently\b|\bat\s+the\s+moment\b|\bnow\b", re.I)
_ME_WORDS = frozenset({"i", "i'm", "im", "i've", "i'd", "me", "my", "mine", "myself", "we", "we're", "our", "user", "user's", "users"})


def _i_lead(text: str) -> Optional[bool]:
    """Whether the speaker ("I", "me", "my", the store's "User") comes BEFORE the first content word: "I love Sam" True, "Sam loves
    me" False, no first person at all None. It is who-did-what: the same words, the other way round, are a different fact."""
    words = re.findall(r"[a-z0-9']+", str(text or "").lower().replace("\u2019", "'"))
    me = next((n for n, w in enumerate(words) if w in _ME_WORDS), None)
    if me is None:
        return None
    content = next((n for n, w in enumerate(words) if w not in _TOKEN_STOP and w not in _ME_WORDS), None)
    return content is None or me < content


def _same_claim(said: str, row: str) -> bool:
    """False when ``row`` covers the words but claims something else: it is negated or time-qualified (used to, no longer, now) and the member's words are not (or the reverse),
    or the person named and "I" swap who does what. Forgetting is permanent, so a mismatch is asked about, never erased. Pure."""
    if bool(_NEGATION_RE.search(said)) != bool(_NEGATION_RE.search(row)):
        return False
    if bool(_TEMPORAL_RE.search(said)) != bool(_TEMPORAL_RE.search(row)):
        return False                                     # "used to like jazz" must not erase "likes jazz" (or the reverse)
    a, b = _i_lead(said), _i_lead(row)
    return a is None or b is None or a == b


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
    told = {e.key: e for e in pa.people_entries([i for i in people_items if not i.inferred], known.people)}
    for e in pa.people_entries(people_items, known.people):
        if not (e.name or e.rel):
            continue
        t = told.get(e.key)           # a fact no row the member told or confirmed supports is one Zoe worked out
        e.inferred = {k for _, k in e.facts if not (t and any(pa._same_fact(k, tk) for _, tk in t.facts))}
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


def _told_facts(e: Any) -> list:
    """A person's facts, told or confirmed ones before those Zoe worked out (``e.inferred``)."""
    inf = getattr(e, "inferred", None) or set()
    return sorted(e.facts, key=lambda fk: fk[1] in inf)


def _inferred_count(people: list) -> int:
    return sum(1 for e in people for _, k in e.facts if k in (getattr(e, "inferred", None) or set()))


def _person_line(e: Any) -> str:
    facts = [pa.trim(f, 70) for f, _ in _told_facts(e)[:2]]
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
        out.append({"id": key, "group": "people", "text": e.label(), "meta": _person_line(e), "soft": bool(e.facts) and _inferred_count([e]) == len(e.facts),
                    "actions": acts})
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
    inferred = sum(1 for i in wall.items if i.inferred) + _inferred_count(wall.people)
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
    inferred = sum(1 for i in items if i.inferred) + _inferred_count(people)
    if inferred:
        s.append(f"{pa._plural(inferred, 'of them', 'of them')} I worked out myself"
                 + ("; they're marked on the screen." if on_screen else "."))
    if on_screen:
        s.append(SCREEN_TAIL)
    return " ".join(s)


def spoken_person(e: Any, *, on_screen: bool = True) -> str:
    subject = e.name or (f"your {e.rel}" if e.rel else "")
    inf = getattr(e, "inferred", None) or set()
    said = []
    for f, k in e.facts:
        if k in inf:
            continue                                      # an inferred fact is never said as something the member told me
        clause = pa.trim(f, 80).removesuffix("...").rstrip(" .")
        said.append(f"{subject} {clause}" if clause[:1].islower() and subject else clause)
        if len(said) >= SPOKEN_FACTS:
            break
    worked = _inferred_count([e])
    head = f"Here's what I know about {pa._spoken_person(e)}: "
    tail = f" {SCREEN_TAIL}" if on_screen else ""
    mine = ""
    if worked:
        mine = f" I worked out {pa._plural(worked, 'thing')} on my own" + ("; marked on the screen." if on_screen else ".")
    if not said:
        return (f"I know {pa._spoken_person(e)}, but nothing you've told me about them yet." if worked else head + "not much yet.") + mine + tail
    return head + "; ".join(said) + "." + mine + tail


async def _find_person(member: str, wall: Wall, name: str) -> Optional[Any]:
    parts = name.split()
    first = parts[0].lower()
    exact = [e for e in wall.people if e.name and e.name.lower() == name.lower()]
    if exact or len(parts) > 1:   # a spoken surname must match in full: "Sam Wilson" is not "Sam Patel"
        return exact[0] if len(exact) == 1 else None
    hits = [e for e in wall.people if e.name and e.name.split()[0].lower() == first]
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
    _RETRY.clear()


def _is_command(text: str) -> bool:
    """True when the words are a command the intent router already understands ("mute", "shuffle", "volume up", "turn the lights
    off"): the one matcher, not a hand list. A bare command is never a correction value. A matcher failure reads as a command -
    nothing is written on a doubt."""
    try:
        import intent_router

        return intent_router.detect_intent(text, log_miss=False) is not None
    except Exception:  # noqa: BLE001
        return True


async def _tapped(tp: Tap, text: str, member: str, session_id: str, panel_id: str, *, svc: Any = None) -> Optional[str]:
    t = " ".join(str(text or "").split())
    if _CANCEL_RE.match(t):
        return LEAVE_IT_REPLY
    refused = await review_refusal(member)                # forget / fix are memories.review actions
    if refused:
        return refused
    st = pa.parse_stateful(t)
    value = ""
    if st is None:
        if (tp.awaiting and t and len(t.split()) <= 14 and "?" not in t and not pa._NOT_A_FIX_RE.match(t)
                and not _NOT_AN_ANSWER_RE.match(t) and not parse(t) and not _is_command(t)):
            value = pa._clean_value(re.sub(rf"^(?:{pa._IT_IS}\s+)", "", t, flags=re.IGNORECASE))
        if not value:
            return None
    elif st.kind == "forget_it":
        reply = await _forget_one(member, tp.row_id, tp.text, svc=svc)
        if reply == pa.STORE_DOWN_REPLY:
            tap(panel_id, member, tp.row_id, tp.text)  # half done: repeating "forget it" must still have its target
        return reply
    else:
        value = st.value
    m = _ON_WHEN_RE.match(value)
    if m:
        value = m.group("v")                              # "on Saturday" / "at 10am": the value, not the preposition
    try:
        ref = await asyncio.wait_for((svc or pa._svc_default()).get(tp.row_id), pa.READ_BUDGET_S)
    except Exception as exc:  # noqa: BLE001 - the tap was spent before this read: keep it armed, the retry finishes the fix
        logger.warning("memories_voice: preflight read failed (%s)", type(exc).__name__)
        tap(panel_id, member, tp.row_id, tp.text)
        return pa.STORE_DOWN_REPLY
    if ref is not None and await memory_space.curated_locked(member, ref.metadata):
        return CURATED_REPLY
    exp = mp.Explained(seq=0, row_id=tp.row_id, ts=time.time(), text=tp.text)
    reply = await pa.fix(member, session_id, text, value, exp, svc=svc, strict_twins=True)
    if reply in (pa.ASK_FIX_REPLY, pa.FIX_NEEDS_SENTENCE, pa.STORE_DOWN_REPLY):
        tap(panel_id, member, tp.row_id, tp.text)          # the next turn is still the answer
    logger.info("MEMORIES_VOICE fix=1 user=%s", member)
    return reply


# -- who may change memories ---------------------------------------------------------------------------------------------------------
GROWN_UP_REPLY = "That's one for a grown-up to change."
NO_REVIEW_REPLY = "I can't change memories for this account."


async def review_verdict(member: str) -> str:
    """``"ok"`` | ``"child"`` | ``"denied"``: may this member change memories (the ``memories.review`` capability)? The ONE authority
    the phone routes, the card route and every spoken write share. Reads the AUTHORITATIVE account (zoe-auth's ``auth_users``, not
    the data-plane ``users`` mirror chat writes as 'member' for everyone) + ``require_feature_access`` + ``manner_block.is_adult``.
    Fail-closed: any lookup error or unknown account is ``"denied"``."""
    try:
        from fastapi import HTTPException

        from db_compat import get_compat_db
        from guest_policy import require_feature_access
        from manner_block import is_adult

        async with get_compat_db() as db:
            cur = await db.execute("SELECT role FROM auth_users WHERE user_id = ?", (member,))
            row = await cur.fetchone()
            role = (row[0] if row else None) or ""
            if not role:
                return "denied"
            try:
                await require_feature_access(db, {"user_id": member, "role": role}, feature="memories", action="review")
            except HTTPException:
                return "child" if role == "child" else "denied"
            if not await is_adult(member, db=db):          # a kid member_mode locks even an adult-looking role
                return "child"
            return "ok"
    except Exception as exc:  # noqa: BLE001
        logger.warning("memories_voice: role lookup failed (%s)", type(exc).__name__)
        return "denied"


async def review_refusal(member: str) -> Optional[str]:
    """None when ``member`` may change memories, else the gentle spoken refusal (nothing is written)."""
    v = await review_verdict(member)
    return None if v == "ok" else GROWN_UP_REPLY if v == "child" else NO_REVIEW_REPLY


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


_RETRY: dict = {}      # (member, the member's words) -> (row id, row text, monotonic ts): a forget that stopped half way


async def forget_fact(member: str, words: str, *, svc: Any = None) -> str:
    """"forget that I like jazz": the ONE row of the member's that says it (with its twins) goes through the audited row forget.
    Two different rows -> ask which; none -> say so; a row that says the opposite or the other way round -> ask, never erase.
    A private row is never read back."""
    want = _tokens(words)
    if not want:
        return NOT_FOUND_REPLY
    said = _norm(words)
    key = (str(member), said)
    pend = _RETRY.pop(key, None)
    if pend is not None and time.monotonic() - pend[2] <= RETRY_TTL_S:
        # the last attempt rejected the row but the twin cleanup failed: the row is no longer "approved", so a fresh search would
        # say NOT_FOUND and the cleanup would never finish. Repeating the same words finishes the SAME row.
        reply = await _forget_one(member, pend[0], pend[1], svc=svc)
        if reply == pa.STORE_DOWN_REPLY:
            _RETRY[key] = (pend[0], pend[1], time.monotonic())
        return reply
    try:
        known = await pa.gather(member, svc=svc)
    except Exception as exc:  # noqa: BLE001
        logger.warning("memories_voice: gather failed (%s)", type(exc).__name__)
        return pa.STORE_DOWN_REPLY
    pool = list(known.items) + [i for lst in known.private.values() for i in lst]
    covered = [i for i in pool if _covers(want, _tokens(i.text))]
    if not covered:
        return NOT_FOUND_REPLY
    hits = [i for i in covered if _norm(i.text) == said or _same_claim(words, i.text)]
    if not hits:
        return DIFFERENT_REPLY
    exact = {i.id for i in hits if _norm(i.text) == said}
    hits.sort(key=lambda i: (i.id not in exact, len(_tokens(i.text) - want), -(i.epoch or 0.0)))
    first = hits[0]
    # Forgetting is permanent, so the first-sorted row goes alone only when it is PROVEN the one: it says something beyond the
    # words (then it is not the tie), or it is the single row whose text is exactly the member's words. Equal token sets alone
    # prove nothing ("I love Sam" / "Sam loves me"): more than one row says it and none is proven -> the twins of one utterance
    # are one fact, anything else is asked.
    tied = any(not (_tokens(h.text) - want) for h in hits[1:])
    proven = first.id in exact and len(exact) == 1
    if len(hits) > 1 and (_tokens(first.text) - want or (tied and not proven)):
        svc = svc or pa._svc_default()
        ref = await asyncio.wait_for(svc.get(first.id), pa.READ_BUDGET_S)
        twins = {t.id for t in await pa.twins_of(member, ref, svc=svc)} if ref is not None else set()
        if [h for h in hits[1:] if h.id not in twins]:
            return WHICH_REPLY
    reply = await _forget_one(member, first.id, first.text, svc=svc)
    if reply == pa.STORE_DOWN_REPLY:
        _RETRY[key] = (first.id, first.text, time.monotonic())
    return reply


async def forget_person(member: str, name: str) -> str:
    """Forget a person through the EXISTING ``memory_forget_entity`` intent: the permanent forget ledger, the verbatim words,
    the cascade (people row, edges, loops, the learned voice). The member's own calendar events and list items stay (owner D6)."""
    from intent_router import Intent, execute_intent

    refused = await review_refusal(member)
    if refused:
        return refused
    return str(await execute_intent(Intent("memory_forget_entity", {"name": name}), member) or "")


# -- the turn ------------------------------------------------------------------------------------------------------------------------
async def _push(frame: Optional[dict]) -> int:
    """Send the card; how many screens got it (0 = none, so the reply must not say "it's on the screen")."""
    try:
        import voice_cards

        return int(await voice_cards.push(frame) or 0)
    except Exception:  # noqa: BLE001 - the card is an addition to the spoken answer
        return 0


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
        refused = await review_refusal(member)
        if refused:
            return refused
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
        shown = True                                      # a dry turn pushes nothing and claims nothing it cannot check
        if allow_writes:
            shown = await _push(build_card(panel_id, member, wall, title=TITLES["person"].format(name=e.label()), people=[e], items=[])) > 0
        logger.info("MEMORIES_VOICE read=person user=%s", member)
        return spoken_person(e, on_screen=shown)
    if ask.kind == "recent":
        people, items = recent_rows(wall, ask.value)
        shown = True
        if allow_writes:
            when = "today" if ask.value == "today" else "this month" if ask.value == "this month" else "this week"
            shown = await _push(build_card(panel_id, member, wall, title=TITLES["recent"].format(when=when), people=people, items=items)) > 0
        logger.info("MEMORIES_VOICE read=recent user=%s", member)
        return spoken_recent(people, items, ask.value, on_screen=shown)
    shown = True
    if allow_writes:
        shown = await _push(build_card(panel_id, member, wall, title=TITLES["me"])) > 0
    logger.info("MEMORIES_VOICE read=me user=%s", member)
    return spoken_me(wall, on_screen=shown)


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
