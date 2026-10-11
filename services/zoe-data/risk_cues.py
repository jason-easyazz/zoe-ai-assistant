"""The risk-cue tier - code finds the problem in a plan, the 4B only has to voice it (flag-dark ``ZOE_RISK_CUES=off|shadow|on``).

THE MISS. The manner-block A/B (``docs/knowledge/manner-block.md``) measured "planted flaw touched" (person bench P5b.b) at
21/60 with the manner block off and 21-22/60 with it on: prompt wording does not make the small day brain notice the problem in a
risky plan ("That sounds like a really frustrating moment to send" - sympathy, never the flaw). The fix is structural
(``docs/research/person-likeness-2026-10-09.md`` section 5, ``docs/research/structural-floors-2026-10-09.md``): code detects the
risk and hands the brain a short, SPECIFIC note, so the 4B only has to say it kindly.

WHAT IT DOES. On an ADULT's turn that announces a plan ("I'm going to ...", "tomorrow I'll ...", "sending it now") and carries a
risk cue, the Flue seam (``zoe_flue_client._risk_cue_block``) adds ONE short block after the user's words::

    ## Before you answer, check
    - Safety: the plan says "driving" and they also said "awake since four" - that combination is risky.
    Mention the concern once, briefly and kindly, then help with the plan. Do not lecture, refuse or repeat it.

It never blocks, refuses or rewrites the request, it names at most ``MAX_NOTES`` (2) concerns from different categories, and the
same concern is raised ONCE per conversation (``RAISED_TTL_S``): a plan repeated on the next turn does not re-trigger the note.

CATEGORIES (everyday life, written as data - ``lexicons_data/<lang>.json`` key ``"risk_cues"``): ``safety_health`` (a hazard
with an impairment, an injury with a load, skipped protection, stopping medication, a meal that is not a meal),
``money_contract`` (everything on one bet, a "sure thing", quitting with no income, signing unread, paying a stranger up front,
handing over a code), ``irreversible`` (deleting or wiping without a backup, a hostile message that cannot be unsent, a rash
step taken in anger), ``prerequisite`` (a stated missing passport / ticket / booking, a big step nobody has been told about),
``timing`` (two things at one time; too little lead before a flight - the arithmetic is in code, the words are data).
English and Spanish exist; a language with no entry gets NO block (patterns are never guessed across languages).

WHY DATA. No English lives in this file: every word list, the plan gate, the notes, the heading and the rule are in the
lexicon files, one section per language, so a second language is data (``lexicons.py`` explains the contract; a lexicon is a
PRE-FILTER that can only ADD one short note - it never authorises anything, never changes the reply by itself, and a miss
costs nothing).

WHO GETS IT. Adults only, by the manner block's allowlist (``manner_block.eligible``: no minor / kid ``member_modes`` row, an
``auth_users`` role in ``ADULT_ROLES``, fail closed on any lookup error), PLUS never a guest and never a harness-minted
synthetic id (``user_filters.is_synthetic_user``). The id string is only ever used to REFUSE, never to allow. The restraint
tier is respected: no block on a goodbye / presence check / silence (``clean_goodbye``), on a spoken mute or release
(``restraint.parse_utterance``), on a turn the speaker gate did not confirm (``restraint.current_verdict() is False``), or - in
``ZOE_RESTRAINT=enforce`` - on a topic the member has muted.

MODES. ``off`` (default): nothing runs, the wire bytes are exactly today's. ``shadow``: everything runs, the line
``RISK_CUES mode=shadow ... n= cats=`` (counts and categories only, never message text) is logged, NOTHING is injected.
``on``: the block is injected.

Measured by ``scripts/perf/risk_cue_ab.py`` (``docs/knowledge/risk-cue-tier.md``); the flag flips only on a printed KEEP.
Nothing here stores anything but a bounded in-process "already raised" memory. Stdlib only apart from lazy imports.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass
from functools import lru_cache
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

FLAG = "ZOE_RISK_CUES"
MODES = ("off", "shadow", "on")
LEXICON_KEY = "risk_cues"
FALLBACK_LANG = "en"

#: a plan is a short utterance; a pasted essay is not scanned (and the regexes stay cheap on the voice path)
MAX_MESSAGE_CHARS = 500
#: at most this many concerns in one block, from DIFFERENT categories
MAX_NOTES = 2
#: the whole block (heading + notes + rule) stays inside this; a longer one drops its last note(s), never truncates mid-sentence
MAX_BLOCK_CHARS = 520
#: the words a note slot may carry from the user's own message
SLOT_CHARS = 40
#: a cold eligibility lookup is two primary-key reads; the turn never waits longer than this
LOOKUP_BUDGET_S = 0.5
#: the same concern is raised once per (member, conversation) inside this window
RAISED_TTL_S = 1800.0
RAISED_MAX = 512
#: the order concerns are chosen in when several fire: the one a person would least want to miss goes first
CATEGORY_ORDER = ("safety_health", "money_contract", "irreversible", "prerequisite", "timing")

_ON = frozenset({"on", "1", "true", "yes"})


def mode() -> str:
    """``ZOE_RISK_CUES`` - ``off`` (default) | ``shadow`` | ``on``; read per call, so a flip needs no restart. Anything else is ``off``."""
    from typed_env import env_str

    raw = env_str("ZOE_RISK_CUES", "off").lower()  # literal on purpose: tools/audit/flag_inventory.py greps it
    if raw in _ON:
        return "on"
    return "shadow" if raw == "shadow" else "off"


def enabled() -> bool:
    return mode() != "off"


# -- the lexicon, compiled -------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Cue:
    """One concern: its category, the cue id (for the log and the once-per-conversation memory) and the filled note."""
    category: str
    id: str
    note: str


@dataclass(frozen=True)
class _Spec:
    id: str
    category: str
    all: tuple
    absent: tuple
    note: str


@dataclass(frozen=True)
class _Timing:
    clock: "re.Pattern[str]"
    events: tuple          # ((pattern, lead_minutes), ...)
    depart: tuple
    day_words: Optional["re.Pattern[str]"]
    connectors: Optional["re.Pattern[str]"]
    tight: str
    late: str
    double: str


@dataclass(frozen=True)
class _Pack:
    lang: str
    heading: str
    rule: str
    intent: tuple
    when: tuple
    past: tuple
    veto: tuple
    specs: tuple
    timing: Optional[_Timing]
    skipped: int           # patterns that failed to compile (a test pins this at 0 for the shipped data)


def _rx(src: object) -> Optional["re.Pattern[str]"]:
    try:
        return re.compile(str(src), re.IGNORECASE)
    except re.error:
        logger.warning("RISK_CUES a pattern in the lexicon does not compile; it is skipped")
        return None


def _rxs(items: object) -> tuple[tuple, int]:
    out, bad = [], 0
    for it in items if isinstance(items, list) else []:
        rx = _rx(it)
        if rx is None:
            bad += 1
        else:
            out.append(rx)
    return tuple(out), bad


@lru_cache(maxsize=16)
def _pack(lang: str) -> Optional[_Pack]:
    """The compiled ``risk_cues`` section of ``lang``, or None (no section, or not ``enabled``). Cached; the files are data."""
    import lexicons

    data = (lexicons.load(lang) or {}).get(LEXICON_KEY)
    if not isinstance(data, dict) or not data.get("enabled"):
        return None
    skipped = 0
    plan = data.get("plan") or {}
    groups = {}
    for key in ("intent", "when", "past", "veto"):
        groups[key], bad = _rxs(plan.get(key))
        skipped += bad
    specs = []
    for raw in data.get("cues") or []:
        if not isinstance(raw, dict) or not raw.get("id") or not raw.get("category") or not raw.get("note"):
            skipped += 1
            continue
        all_, bad1 = _rxs(raw.get("all"))
        absent, bad2 = _rxs(raw.get("absent"))
        skipped += bad1 + bad2
        if bad1 or not all_:
            skipped += 0 if bad1 else 1       # a spec missing any required pattern could fire on less than it says: drop it whole
            continue
        specs.append(_Spec(str(raw["id"]), str(raw["category"]), all_, absent, str(raw["note"])))
    timing = None
    traw = data.get("timing")
    if isinstance(traw, dict) and traw.get("clock"):
        clock = _rx(traw["clock"])
        events = []
        for ev in traw.get("events") or []:
            rx = _rx(ev.get("rx")) if isinstance(ev, dict) else None
            if rx is not None:
                events.append((rx, int(ev.get("lead") or 0)))
            else:
                skipped += 1
        depart, bad = _rxs(traw.get("depart"))
        skipped += bad
        if clock is not None and events and depart:
            timing = _Timing(clock, tuple(events), depart, _rx(traw["day_words"]) if traw.get("day_words") else None,
                             _rx(traw["connectors"]) if traw.get("connectors") else None,
                             str(traw.get("tight") or ""), str(traw.get("late") or ""), str(traw.get("double") or ""))
        else:
            skipped += 1
    return _Pack(lang, str(data.get("heading") or "").strip(), str(data.get("rule") or "").strip(), groups["intent"],
                 groups["when"], groups["past"], groups["veto"], tuple(specs), timing, skipped)


def available_languages() -> tuple[str, ...]:
    import lexicons

    return tuple(code for code in lexicons.LANGS if _pack(code) is not None)


def data_digest() -> str:
    """sha256 of every language's ``risk_cues`` section: what the A/B harness fingerprints a run with (edit a cue => a new run id)."""
    import hashlib
    import json

    import lexicons

    blob = json.dumps({c: (lexicons.load(c) or {}).get(LEXICON_KEY) for c in lexicons.LANGS}, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def heading(lang: str) -> str:
    pack = _pack(lang)
    return pack.heading if pack else ""


# -- the turn's words -----------------------------------------------------------------------------------------

def _norm(message: str) -> str:
    """The user's words as one line: a leading ``[Intent hint: ...]`` prefix dropped, NFKC, curly apostrophes straight."""
    text = message or ""
    try:
        import clean_goodbye

        text = clean_goodbye._strip_intent_hint(text)       # the repo's one balanced-bracket reader
    except Exception:  # noqa: BLE001
        pass
    text = unicodedata.normalize("NFKC", text).replace("’", "'").replace("‘", "'").replace("ʼ", "'")
    return re.sub(r"\s+", " ", text).strip()


def _slot(text: str) -> str:
    """The words a pattern matched, as a note slot: one line, no double quotes (the notes quote their slots), bounded."""
    t = re.sub(r"\s+", " ", text or "").replace('"', "'").strip(" ,.;:!?'")
    return t if len(t) <= SLOT_CHARS else t[: SLOT_CHARS - 1].rstrip() + "…"


# -- is it an announced plan? (pure) -------------------------------------------------------------------------

def _any(rxs: Iterable["re.Pattern[str]"], text: str) -> bool:
    return any(rx.search(text) for rx in rxs)


def is_plan(text: str, pack: _Pack) -> bool:
    """An announced plan: no question / hypothetical / negated / past-tense plan (``veto``), and a first-person intent marker, or - with
    none - a forward time anchor in a message that is not a past narrative. A pure read of the user's words."""
    if _any(pack.veto, text):
        return False
    if _any(pack.intent, text):
        return True
    if _any(pack.past, text):
        return False
    return _any(pack.when, text)


# -- the cues (pure) ------------------------------------------------------------------------------------------

def _spec_cues(text: str, pack: _Pack) -> list[Cue]:
    out: list[Cue] = []
    for spec in pack.specs:
        slots: list[str] = []
        for rx in spec.all:
            m = rx.search(text)
            if m is None:
                break
            slots.append(_slot(m.group(0)))
        else:
            if _any(spec.absent, text):
                continue
            try:
                out.append(Cue(spec.category, spec.id, spec.note.format(*slots)))
            except (IndexError, KeyError, ValueError):
                logger.warning("RISK_CUES cue %s has a note that does not fit its patterns; skipped", spec.id)
    return out


@dataclass(frozen=True)
class _Clock:
    start: int
    end: int
    minutes: int
    explicit: bool     # am/pm given (or a 24-hour hour): the minutes are unambiguous
    text: str


def _clocks(text: str, t: _Timing) -> list[_Clock]:
    out: list[_Clock] = []
    for m in t.clock.finditer(text):
        g = m.groupdict()
        h = g.get("h1") or g.get("h2") or g.get("h3")
        if not h:
            continue
        hour, minute = int(h), int(g.get("m1") or g.get("m3") or 0)
        ap = (g.get("ap1") or g.get("ap2") or g.get("ap3") or "").lower()
        if hour > 24 or minute > 59:
            continue
        explicit = bool(ap) or hour >= 13
        if ap.startswith("p") and hour < 12:
            hour += 12
        elif ap.startswith("a") and hour == 12:
            hour = 0
        out.append(_Clock(m.start(), m.end(), (hour % 24) * 60 + minute, explicit, _slot(m.group(0))))
    return out


def _gap_minutes(event: _Clock, depart: _Clock) -> int:
    """Event time minus departure time. Both times unambiguous: the plain same-day difference; otherwise the signed difference mod 12 h."""
    if event.explicit and depart.explicit:
        return event.minutes - depart.minutes
    return ((event.minutes - depart.minutes + 360) % 720) - 360


def _near(text: str, clock: _Clock, kws: list, reach: int = 40):
    """The keyword a clock time belongs to, inside the same sentence (a ``.!?;`` between them cuts the link), or None. A time is
    introduced by the word BEFORE it ("leave at 7:45", "the flight is at 9") except when a keyword sits right behind it, where the
    time is an adjective of it ("the 9am flight"): adjacent-after wins, then nearest-before, then nearest-after."""
    adjacent = before = after = None
    for kw in kws:
        start, end = kw[0], kw[1]
        if end <= clock.start:
            gap, between = clock.start - end, text[end:clock.start]
        elif start >= clock.end:
            gap, between = start - clock.end, text[clock.end:start]
        else:
            continue
        if gap > reach or re.search(r"[.!?;]", between):
            continue
        if end <= clock.start:
            before = (gap, kw) if before is None or gap < before[0] else before
        else:
            after = (gap, kw) if after is None or gap < after[0] else after
            if gap <= 2:
                adjacent = (gap, kw) if adjacent is None or gap < adjacent[0] else adjacent
    for cand in (adjacent, before, after):
        if cand is not None:
            return cand[1]
    return None


def _day_of(text: str, clock: _Clock, t: _Timing) -> str:
    if t.day_words is None:
        return ""
    lo, hi = max(0, clock.start - 35), min(len(text), clock.end + 35)
    m = t.day_words.search(text, lo, hi)
    return m.group(0).lower() if m else ""


def _timing_cues(text: str, pack: _Pack) -> list[Cue]:
    """The clock arithmetic: a departure too close to (or after) a flight / train / exam, or two things set for the same time."""
    t = pack.timing
    if t is None:
        return []
    clocks = _clocks(text, t)
    if not clocks:
        return []
    kws: list[tuple] = []        # (start, end, kind, lead, word)
    for rx, lead in t.events:
        kws += [(m.start(), m.end(), "event", lead, m.group(0)) for m in rx.finditer(text)]
    for rx in t.depart:
        kws += [(m.start(), m.end(), "depart", 0, m.group(0)) for m in rx.finditer(text)]
    events, departs = [], []
    for c in clocks:
        kw = _near(text, c, kws)
        if kw is not None:
            (events if kw[2] == "event" else departs).append((c, kw))
    for ce, ke in events:
        for cd, kd in departs:
            gap = _gap_minutes(ce, cd)
            if -240 <= gap < ke[3]:
                ev_phrase = f"{_slot(ke[4])} ({ce.text})"
                dep_phrase = f"{_slot(kd[4])} ({cd.text})"
                template = t.late if gap <= 0 else t.tight
                if template:
                    try:
                        return [Cue("timing", "timing_late" if gap <= 0 else "timing_tight",
                                    template.format(ev_phrase, dep_phrase, str(gap)))]
                    except (IndexError, KeyError, ValueError):
                        return []
    if t.double:
        for i, a in enumerate(clocks):
            for b in clocks[i + 1:]:
                same = a.minutes == b.minutes if (a.explicit and b.explicit) else (a.minutes - b.minutes) % 720 == 0
                if not same or b.start - a.end < 6:
                    continue
                between = text[a.end:b.start]
                if t.connectors is None or not t.connectors.search(between):
                    continue
                da, db = _day_of(text, a, t), _day_of(text, b, t)
                if da and db and da != db:
                    continue
                try:
                    return [Cue("timing", "timing_double", t.double.format(a.text, b.text))]
                except (IndexError, KeyError, ValueError):
                    return []
    return []


def _languages(text: str, lang: Optional[str]) -> list[str]:
    """The languages whose cues are tried, in order. An explicit ``lang`` means only that one. Otherwise the language of the words
    when the lexicon has evidence for it (function words matched); with NO evidence (a short Spanish sentence full of cognates reads
    as English by default) every language that has cue data is tried, the default first - each pattern list is made of that
    language's own words, so a language's cues do not fire on another's text."""
    if lang:
        return [lang]
    import lexicons

    best, evidence = lexicons.detect_scored(text)
    if evidence > 0:
        return [best]
    return [best] + [c for c in available_languages() if c != best]


def detect_with_language(message: str, lang: Optional[str] = None) -> tuple[str, list[Cue]]:
    """``(language of the pack that fired, its concerns)`` for an announced plan; ``(FALLBACK_LANG, [])`` for anything else. Pure: no flag,
    no eligibility, no I/O - the unit under test."""
    text = _norm(message)
    if not text or len(text) > MAX_MESSAGE_CHARS:
        return FALLBACK_LANG, []
    for code in _languages(text, lang):
        pack = _pack(code)
        if pack is None or not is_plan(text, pack):
            continue
        cues = _spec_cues(text, pack) + _timing_cues(text, pack)
        if cues:
            return code, cues
    return FALLBACK_LANG, []


def detect(message: str, lang: Optional[str] = None) -> list[Cue]:
    """Every concern in an announced plan, in data order ([] for anything else)."""
    return detect_with_language(message, lang)[1]


def pick(cues: Iterable[Cue]) -> list[Cue]:
    """At most ``MAX_NOTES`` concerns, one per category, in ``CATEGORY_ORDER``."""
    first: dict[str, Cue] = {}
    for c in cues:
        first.setdefault(c.category, c)
    ordered = [first[k] for k in CATEGORY_ORDER if k in first] + [v for k, v in first.items() if k not in CATEGORY_ORDER]
    return ordered[:MAX_NOTES]


def render(cues: list[Cue], lang: str = FALLBACK_LANG) -> str:
    """The block body: heading, one ``- note`` line per concern, the one rule line. A block over ``MAX_BLOCK_CHARS`` drops its last
    concern(s); '' when there are none or even one does not fit (a block that does not fit is dropped, never truncated)."""
    pack = _pack(lang)
    if pack is None or not pack.heading or not pack.rule:
        return ""
    keep = list(cues)
    while keep:
        body = "\n".join([pack.heading] + [f"- {c.note}" for c in keep] + [pack.rule])
        if len(body) <= MAX_BLOCK_CHARS:
            return body
        keep.pop()
    return ""


# -- once per conversation --------------------------------------------------------------------------------------

_raised: "OrderedDict[tuple[str, str], dict[str, float]]" = OrderedDict()


def _fresh(user_id: str, session_id: str, cues: list[Cue]) -> list[Cue]:
    """The cues not yet raised in this (member, conversation) inside ``RAISED_TTL_S``."""
    seen = _raised.get((user_id, session_id or ""), {})
    now = time.monotonic()
    return [c for c in cues if now - seen.get(c.id, -RAISED_TTL_S - 1.0) >= RAISED_TTL_S]


def _remember(user_id: str, session_id: str, cues: list[Cue]) -> None:
    key = (user_id, session_id or "")
    seen = _raised.pop(key, {})
    now = time.monotonic()
    seen = {k: v for k, v in seen.items() if now - v < RAISED_TTL_S}
    seen.update({c.id: now for c in cues})
    _raised[key] = seen
    while len(_raised) > RAISED_MAX:
        _raised.popitem(last=False)


def _reset_state() -> None:
    _raised.clear()


# -- who gets it ------------------------------------------------------------------------------------------------

async def eligible(user_id: str, db=None) -> bool:
    """May this member's turn carry a risk cue? Adults only (the manner block's allowlist); never a guest and never a harness-minted
    synthetic id. The id string is only ever used to REFUSE. Fails closed on any error."""
    uid = (user_id or "").strip()
    try:
        import manner_block
        from user_filters import is_synthetic_user

        if not uid or manner_block.is_guest(uid) or is_synthetic_user(uid):
            return False
        return bool(await manner_block.eligible(uid, db=db))
    except Exception:  # noqa: BLE001 - not knowing a member is an adult means no block
        logger.warning("RISK_CUES eligibility failed; no block", exc_info=True)
        return False


def _restraint_quiet(text: str) -> bool:
    """True on a turn the restraint tier / clean-goodbye tier keeps quiet: a goodbye, a presence check, a silence, a spoken mute or
    release, a voice the speaker gate did not confirm. Pure reads; never raises (an unreadable tier is not a reason to speak)."""
    try:
        import clean_goodbye
        import restraint

        if clean_goodbye.classify(text):
            return True
        if restraint.parse_utterance(text) is not None:
            return True
        return restraint.current_verdict() is False
    except Exception:  # noqa: BLE001
        return True


async def _topic_muted(user_id: str, text: str) -> bool:
    """In ``ZOE_RESTRAINT=enforce``: does the plan touch a topic the member has muted? Fail-open to 'not muted' (a missing read is no mutes)."""
    try:
        import restraint

        if restraint.mode() != "enforce":
            return False
        mutes = await asyncio.wait_for(restraint.list_mutes(user_id), timeout=LOOKUP_BUDGET_S)
        return bool(mutes) and restraint.muted(text, "", mutes)
    except Exception:  # noqa: BLE001
        return False


async def block_for(user_id: str, message: str, session_id: str = "", db=None) -> str:
    """The block body for THIS member's turn, or ``""`` (= today's bytes). Never raises; flag ``off`` => ``""`` before any work;
    ``shadow`` computes and logs counts and categories but returns ``""``."""
    try:
        m = mode()
        if m == "off":
            return ""
        text = _norm(message)
        if not text or len(text) > MAX_MESSAGE_CHARS or _restraint_quiet(text):
            return ""
        lang, found = detect_with_language(text)
        uid = (user_id or "").strip()
        cues = _fresh(uid, session_id, found)
        if not cues:
            return ""
        if not await eligible(uid, db=db) or await _topic_muted(uid, text):
            return ""
        picked = pick(cues)
        body = render(picked, lang)
        if not body:
            return ""
        _remember(uid, session_id, picked)
        logger.info("RISK_CUES mode=%s user=%s lang=%s n=%d cats=%s", m, uid, lang, len(picked), ",".join(c.category for c in picked))
        return body if m == "on" else ""
    except Exception:  # noqa: BLE001 - a cue is optional; the turn is not
        logger.warning("RISK_CUES failed; no block", exc_info=True)
        return ""
