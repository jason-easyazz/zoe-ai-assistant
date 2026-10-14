"""Command SHAPES shared by the two deterministic matchers (``skybridge_service`` and ``intent_router``).

Pure and stdlib-only (importable from the hot voice path, no I/O, no clock). Each helper answers ONE question about
the words of a command, so both matchers read a command the same way:

* ``parse_duration_seconds`` - "5 minutes", "twenty-five minutes", "half an hour" (1800 s), "1 and a half hours",
  "an hour and a half", "a quarter of an hour", "1.5 hours".
* ``timer_adjust`` - "add 5 minutes to the timer", "take 1 minute off the pizza timer", "increase the 1 hour timer by
  30 seconds": a change to a RUNNING timer, never a new one.
* ``timer_pause_resume`` - "pause the 5 minute timer" / "resume the timer".
* ``delayed_command`` - "turn off the lights in 5 minutes", "at 10pm", "when I get home": a command for LATER, which a
  fast path must not run now.

Measured by the HA/OVOS phrasing corpus (``tests/test_ha_phrasing_corpus.py``); the research behind them is
``docs/research/common-questions-ha-intents-2026-10.md`` section 5.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

WORD_NUMBERS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
    "twenty": 20, "thirty": 30, "forty": 40,
    "fifty": 50, "sixty": 60, "ninety": 90,
}
# COMPOUND spoken numbers ("twenty five", "forty-five"), tried LONGEST-FIRST so "twenty" never matches first and
# leaves " five minutes" to be re-read as 5.
COMPOUND_WORD_NUMBERS = {
    f"{tens_w}{sep}{ones_w}": tens_v + ones_v
    for tens_w, tens_v in (("twenty", 20), ("thirty", 30), ("forty", 40), ("fifty", 50))
    for ones_w, ones_v in (("one", 1), ("two", 2), ("three", 3), ("four", 4),
                           ("five", 5), ("six", 6), ("seven", 7), ("eight", 8), ("nine", 9))
    for sep in (" ", "-")
}
_ALL_WORDS = {**COMPOUND_WORD_NUMBERS, **WORD_NUMBERS}
#: a number: digits (with an optional decimal part, so "1.5 hours" is never read as "5 hours") or a number word
NUM = r"\d+(?:\.\d+)?|" + "|".join(re.escape(w) for w in sorted(_ALL_WORDS, key=len, reverse=True))
UNIT = r"hours?|hrs?|minutes?|mins?|seconds?|secs?"

_DURATION_RE = re.compile(rf"\b({NUM})[\s-]*({UNIT})\b")
_SHORT_RE = re.compile(r"\b(\d+)\s*([hms])\b")
# Fractions, read (and removed) BEFORE the plain "<n> <unit>" pass so "an hour" inside "half an hour" or "an hour and
# a half" is not counted again. Order matters: "5 and a half minutes" must not be read as "half minutes".
_FRACTIONS: tuple[tuple["re.Pattern[str]", str], ...] = (
    (re.compile(rf"\b(?P<n>{NUM})\s+and\s+a\s+half[\s-]+(?P<u>{UNIT})\b"), "n+half"),
    (re.compile(rf"\b(?P<n>{NUM})[\s-]+(?P<u>{UNIT})\s+and\s+a\s+half\b"), "n+half"),
    (re.compile(r"\bthree\s+quarters?\s+of\s+an?\s+hour\b"), "2700"),
    (re.compile(r"\b(?:a\s+)?quarter\s+(?:of\s+)?(?:an?\s+)?hour\b"), "900"),
    (re.compile(rf"\b(?:a\s+)?half\s+(?:(?:an?|of\s+an?)\s+)?(?P<u>{UNIT})\b"), "half"),
)


def _unit_seconds(unit: str) -> int:
    u = unit.lower()
    if u.startswith("h"):
        return 3600
    if u.startswith("m"):
        return 60
    return 1


def _num(token: str) -> float:
    token = token.lower()
    try:
        return float(token)
    except ValueError:
        return float(_ALL_WORDS.get(token, 0))


def parse_duration_seconds(text: str) -> int:
    """Total seconds of every duration phrase in ``text`` (lower-cased or not), else 0."""
    rest = (text or "").lower()
    total = 0.0
    found = False
    for pattern, kind in _FRACTIONS:
        for m in pattern.finditer(rest):
            found = True
            if kind == "n+half":
                total += (_num(m.group("n")) + 0.5) * _unit_seconds(m.group("u"))
            elif kind == "half":
                total += 0.5 * _unit_seconds(m.group("u"))
            else:
                total += float(kind)
        rest = pattern.sub(" ", rest)
    for value, unit in _DURATION_RE.findall(rest):
        total += _num(value) * _unit_seconds(unit)
        found = True
    if found:
        return int(round(total))
    short = _SHORT_RE.search(rest)
    if short:
        return int(short.group(1)) * _unit_seconds(short.group(2))
    return 0



# ── Timers ───────────────────────────────────────────────────────────────────

_TIMER = r"\b(?:timers?|countdowns?)\b"
_INCREASE_VERBS = r"add|put|give|stick"
_DECREASE_VERBS = r"remove|take|subtract|knock|cut|shave"
# What may stand between "to/from" and the timer word: a determiner, a name, a length - never another clause
# ("put the pizza on and set a timer" is a new timer, not a change to one).
_TARGET = rf"(?P<tgt>(?:(?!\b(?:and|then|set|start|make|create)\b)[^,;])*?{_TIMER}.*)$"
# "add 5 minutes to the timer" - the amount comes first, the timer is the object of to/on/off/from.
_AMOUNT_FIRST = (
    (re.compile(rf"\b(?:{_INCREASE_VERBS})\b(?P<amt>.+?)\b(?:to|on|onto)\b{_TARGET}"), "increase"),
    (re.compile(rf"\b(?:{_DECREASE_VERBS})\b(?:\s+off\b)?(?P<amt>.+?)\b(?:off(?:\s+of)?|from)\b{_TARGET}"), "decrease"),
)
# "increase the timer by 5 minutes" - the timer first, the amount after "by".
_TIMER_FIRST = (
    (re.compile(rf"\b(?:increase|extend|lengthen|raise)\b(?P<tgt>.*?{_TIMER}.*?)\bby\b(?P<amt>.+)$"), "increase"),
    (re.compile(rf"\b(?:decrease|reduce|shorten|lower|cut)\b(?P<tgt>.*?{_TIMER}.*?)\bby\b(?P<amt>.+)$"), "decrease"),
)
# "make the timer 5 minutes longer", "another 5 minutes on the timer", "5 more minutes on the timer".
_LONGER_SHORTER = re.compile(rf"\bmake\b(?P<tgt>.*?{_TIMER})(?P<amt>.*?)\b(?P<dir>longer|shorter)\b")
_MORE_ON = re.compile(rf"^(?!\s*(?:is|are|how|what|does|do|can|will)\b)(?P<amt>(?:.*?\b)?(?:another|an?\s+extra|more)\b.*?)\b(?:on|to)\b(?P<tgt>.*{_TIMER}.*)$")
# An amount-ish phrase: a number, a unit, or a word that only ever means "some time".
_AMOUNTISH_RE = re.compile(rf"\b(?:\d+|{NUM}|{UNIT}|time|another|more|extra|few|couple|bit|little|half|quarter)\b")
_DETERMINERS = r"(?:the|my|our|your|a|an|that|this)"
_NOT_A_NAME = frozenset({"current", "running", "other", "first", "last", "only", "existing", "active", "same",
                         "second", "third", "fourth", "newest", "oldest", "latest", "earlier", "later", "new", "old",
                         "longer", "shorter", "one", "ones",
                         "all", "every", "both", "these", "those", "of"})
# "10 MORE minutes", "an EXTRA minute", "ANOTHER 5 minutes": amount words that are not part of the number.
_AMOUNT_NOISE_RE = re.compile(r"\b(?:more|extra|another|additional)\b")


def _amount_seconds(amt: str) -> int:
    """Seconds in an amount phrase: "another minute" is a minute, "5 more minutes" is 5 minutes."""
    words = re.sub(r"\banother\b", "a", amt)
    return parse_duration_seconds(" ".join(_AMOUNT_NOISE_RE.sub(" ", words).split()))


@dataclass(frozen=True)
class TimerTarget:
    """Which running timer a command means: by name and/or by the length it was set for; both empty = the only one."""

    label: str = ""
    start_seconds: int = 0


@dataclass(frozen=True)
class TimerAdjust:
    direction: str  # "increase" | "decrease" | "set" (seconds = the new time LEFT)
    seconds: int  # 0 = a change with no amount the words give ("add some time to the timer"): ask, never guess
    target: TimerTarget


def _strip_durations(words: str) -> str:
    rest = words
    for pattern, _kind in _FRACTIONS:
        rest = pattern.sub(" ", rest)
    rest = _DURATION_RE.sub(" ", rest)
    return " ".join(rest.split())


def timer_target(phrase: str) -> TimerTarget:
    """Read "the pizza timer" / "timer named pizza" / "the 5 minute timer" / "my 10 minute pasta timer" / "timer for
    1 hour" / "the timer for the pasta" / "timer in the kitchen" into a :class:`TimerTarget`. A name AND a length are
    both kept (both must match). A room ("in the kitchen") names no timer: Zoe's timers have no room."""
    p = " ".join((phrase or "").lower().split())
    m = re.search(r"\b(?:named|called)\s+(?:the\s+)?(?P<l>.+?)\s*$", p)
    if m:
        return TimerTarget(label=m.group("l").strip(" .!?"))
    m = re.search(rf"{_TIMER}\s+for\s+(?:the\s+|my\s+)?(?P<d>.+?)\s*$", p)
    if m:
        secs = parse_duration_seconds(m.group("d"))
        if secs:
            return TimerTarget(start_seconds=secs)
        return TimerTarget(label=m.group("d").strip(" .!?"))
    m = re.search(rf"(?P<pre>[a-z0-9' .-]*?)\s*{_TIMER}", p)
    words = (m.group("pre") if m else "").split()
    # The timer's own words follow the LAST determiner ("put the pizza timer" -> "pizza"); with no determiner the
    # whole run is the name ("pizza timer"). Ordinal / state adjectives name no timer.
    dets = [i for i, w in enumerate(words) if re.fullmatch(_DETERMINERS, w)]
    if dets:
        words = words[dets[-1] + 1:]
    pre = " ".join(w for w in words if w not in _NOT_A_NAME)
    if not pre:
        return TimerTarget()
    secs = parse_duration_seconds(pre)
    label = _strip_durations(pre) if secs else pre
    label = " ".join(w for w in label.split() if w not in ("minute", "minutes", "hour", "hours", "second", "seconds"))
    return TimerTarget(label=label, start_seconds=secs)


def timer_adjust(text: str) -> Optional[TimerAdjust]:
    """A change of length to a running timer, or None. ``seconds`` is 0 when the words say "change it" but give no
    amount the parser can read ("add a few minutes to the timer"): the caller asks, and never starts a new timer."""
    t = " ".join((text or "").lower().split())
    if not re.search(_TIMER, t):
        return None
    for patterns in (_AMOUNT_FIRST, _TIMER_FIRST):
        for pattern, direction in patterns:
            m = pattern.search(t)
            if not m:
                continue
            amt = m.group("amt")
            secs = _amount_seconds(amt)
            if secs <= 0 and not _AMOUNTISH_RE.search(amt):
                continue  # "put the kettle on ... timer": not an amount at all
            return TimerAdjust(direction, max(0, secs), timer_target(m.group("tgt")))
    m = _LONGER_SHORTER.search(t)
    if m:
        secs = parse_duration_seconds(m.group("amt"))
        return TimerAdjust("increase" if m.group("dir") == "longer" else "decrease", secs, timer_target(m.group("tgt")))
    m = _MORE_ON.search(t)
    if m and not re.search(r"\b(?:set|start|create|new)\b", t):
        secs = _amount_seconds(m.group("amt"))
        return TimerAdjust("increase", secs, timer_target(m.group("tgt")))
    # "increase the timer to 10 minutes" / "reduce the pasta timer to 5 minutes": a NEW remaining time for a running
    # timer (direction "set"), never a second timer.
    m = re.search(rf"\b(?:increase|extend|lengthen|raise|decrease|reduce|shorten|lower|cut|change|adjust|reset)\b"
                  rf"(?P<tgt>.*?{_TIMER}.*?)\b(?:to|so\s+it\s+has)\b(?P<amt>.+)$", t)
    if m and _amount_seconds(m.group("amt")) > 0:
        return TimerAdjust("set", _amount_seconds(m.group("amt")), timer_target(m.group("tgt")))
    m = re.search(rf"\b(?P<dir>extend|lengthen|shorten)\b(?P<tgt>.*{_TIMER}.*)$", t)
    if m:
        return TimerAdjust("decrease" if m.group("dir") == "shorten" else "increase", 0, timer_target(m.group("tgt")))
    return None


_PAUSE_RE = re.compile(rf"\b(?:pause|freeze)\b.*{_TIMER}|{_TIMER}.*\b(?:pause|freeze|on\s+hold)\b")
_RESUME_RE = re.compile(rf"\b(?:resume|unpause|un-pause|continue|unfreeze)\b.*{_TIMER}|{_TIMER}.*\b(?:resume|unpause|continue)\b")


def timer_pause_resume(text: str) -> Optional[tuple[str, TimerTarget]]:
    """("pause"|"resume", target) for a pause/resume of a timer, else None."""
    t = " ".join((text or "").lower().split())
    for action, pattern in (("resume", _RESUME_RE), ("pause", _PAUSE_RE)):
        m = pattern.search(t)
        if m:
            tail = re.sub(r"^.*?\b(?:pause|freeze|resume|unpause|un-pause|continue|unfreeze)\b", "", t)
            return action, timer_target(tail or t)
    return None


# ── ALLOW-LISTED destructive shapes ─────────────────────────────────────────
# The default is inverted: a timer cancel / change or a list remove / tick-off ACTS only on one of the exact shapes
# below; any other wording around the verb or the object (an ordinal, "other", a subset, a quantifier with a
# qualifier, a negation, "except", a conjunction, an unknown word) gets a status read or a question, never a write.
# Every finding of the 2026-10-14 reviews was a phrasing nobody listed falling through to act.

_POLITE_LEAD_RE = re.compile(
    r"^(?:(?:hey|hi|hello|ok|okay|so|um|uh|oh|well|zoe|zoey|please)\b[\s,.!?]*)*"
    r"(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?|please\s+|go\s+ahead\s+and\s+)?"
)
_POLITE_TAIL_RE = re.compile(r"(?:[\s,]+(?:please|for\s+me|thanks|thank\s+you|now|right\s+now|zoe))+\s*$")
#: words that change WHICH thing is meant - never part of a name in an allow-listed shape
MODIFIER_WORDS = frozenset({
    "other", "another", "first", "second", "third", "fourth", "fifth", "last", "next", "previous", "older", "newer",
    "oldest", "newest", "latest", "earlier", "later", "all", "every", "each", "both", "any", "some", "none", "no",
    "not", "never", "don't", "dont", "except", "but", "besides", "apart", "and", "or", "nor", "either", "neither",
    "also", "too", "rest", "remaining", "half", "few", "couple", "several", "many", "more", "less", "than", "longer",
    "shorter", "which", "that's", "whose", "if", "unless", "when", "until", "one", "ones", "same", "only", "just",
    "everything", "anything", "nothing", "whole", "entire", "these", "those", "them", "it", "they", "without", "with",
})
_ROOM = (
    r"(?:kitchen|bedroom|master\s+bedroom|guest\s+room|living\s+room|lounge|family\s+room|dining\s+room|office|study"
    r"|bathroom|laundry|garage|hallway|hall|entrance|entry|nursery|playroom|basement|attic|outside|garden|patio"
    r"|upstairs|downstairs|first\s+floor|second\s+floor|ground\s+floor|space|area|room|house|home|here)"
)
_TIMER_NAME = r"[a-z0-9'.-]+(?:\s+[a-z0-9'.-]+){0,2}"
_TIMER_DET = r"(?:the|my|our|that|this|your)"
_TIMER_OBJ = (
    rf"(?:{_TIMER_DET}\s+)?(?:(?P<pre>{_TIMER_NAME})\s+)?(?:timers?|countdowns?)"
    rf"(?:\s+(?:called|named|for)\s+(?:the\s+)?(?P<post>{_TIMER_NAME}))?"
    rf"(?:\s+(?:in|on)\s+(?:the\s+|my\s+|our\s+)?{_ROOM})?"
)
_CANCEL_VERB = r"(?:cancel|stop|clear|delete|dismiss|reset|turn\s+off|remove|kill|scrap|get\s+rid\s+of|end|switch\s+off)"
#: a cancel word anywhere in a timer utterance: the turn is destructive, so it acts only on an allow-listed shape
CANCEL_CUE = (
    r"(?:cancel|stop|clear|delete|dismiss|reset|turn\s+off|switch\s+off|remove|kill|scrap|get\s+rid\s+of"
    r"|end(?=\s+(?:the|my|that|this|all|every|both)\b)|(?:turn|switch)\s+(?:\S+\s+){0,4}?(?:timers?|countdowns?)\s+off)"
)
#: a change word anywhere in a timer utterance (not "another timer" / "a new timer": those are new timers), or an
#: add / take verb whose object comes BEFORE "to|on|off|from ... timer" ("add 5 minutes to both timers")
CHANGE_CUE = (
    r"(?:more(?!\s+timers?\b)|extra|another(?!\s+(?:timer|countdown|one)\b)|longer|shorter|bump|increase|extend"
    r"|lengthen|decrease|reduce|shorten|less|knock|subtract|shave"
    r"|(?:add|put|give|stick|take|remove|cut)\b(?:(?!\b(?:set|start|create|make|begin|run|new)\b|\ban?\s+timer\b).)*"
    r"\b(?:to|on|onto|off|from)\b(?:(?!\b(?:set|start|create|make|begin|run|new)\b|\ban?\s+timer\b).)*"
    r"\b(?:timers?|countdowns?))"
)
_AMOUNT_WORDS = frozenset(set(_ALL_WORDS) | {
    "and", "half", "quarter", "quarters", "of", "another", "extra", "more", "hour", "hours", "hr", "hrs", "minute",
    "minutes", "min", "mins", "second", "seconds", "sec", "secs",
})


def command_core(text: str) -> str:
    """The command without wake / politeness words and trailing punctuation, lower-cased ("Could you please cancel
    the timer for me?" -> "cancel the timer")."""
    t = " ".join((text or "").lower().replace("’", "'").split())
    t = re.sub(r"[.!?,;:]+$", "", t).strip()
    t = _POLITE_LEAD_RE.sub("", t, count=1)
    return _POLITE_TAIL_RE.sub("", t).strip()


def _plain_name(words: Optional[str]) -> bool:
    return not words or not any(w in MODIFIER_WORDS for w in words.split())


def _plain_amount(words: str) -> bool:
    toks = re.findall(r"[a-z]+|\d+(?:\.\d+)?", words)
    return bool(toks) and all(t in _AMOUNT_WORDS or re.fullmatch(r"\d+(?:\.\d+)?", t) for t in toks)


def _target_from(m: "re.Match[str]") -> Optional[TimerTarget]:
    if not (_plain_name(m.group("pre")) and _plain_name(m.group("post"))):
        return None
    phrase = " ".join(p for p in (m.group("pre"), "timer") if p)
    if m.group("post"):
        phrase += f" for {m.group('post')}"
    return timer_target(phrase)


_ALL_TIMERS_RE = re.compile(
    rf"^{_CANCEL_VERB}\s+(?P<q>all|every|both)(?:\s+of)?(?:\s+(?:the|my|our))?(?:\s+{_ROOM})?\s+(?:timers?|countdowns?)"
    rf"(?:\s+(?:in|on)\s+(?:the\s+|my\s+|our\s+)?{_ROOM})?$"
)
_ONE_TIMER_RES = (
    re.compile(rf"^{_CANCEL_VERB}\s+{_TIMER_OBJ}$"),
    re.compile(rf"^(?:turn|switch)\s+{_TIMER_OBJ}\s+off$"),
)


def allowed_timer_cancel(text: str) -> Optional[tuple[str, TimerTarget]]:
    """("all"|"both"|"one", target) when the cancel is one of the allow-listed shapes, else None."""
    t = command_core(text)
    m = _ALL_TIMERS_RE.match(t)
    if m:
        return ("both" if m.group("q") == "both" else "all"), TimerTarget()
    for pattern in _ONE_TIMER_RES:
        m = pattern.match(t)
        if m:
            target = _target_from(m)
            return ("one", target) if target is not None else None
    return None


_AMT = r"(?P<amt>[a-z0-9.\s-]+?)"
_ADJUST_RES: tuple[tuple["re.Pattern[str]", str], ...] = (
    (re.compile(rf"^(?:add|put|give|stick)\s+{_AMT}(?:\s+more)?\s+(?:to|on|onto)\s+{_TIMER_OBJ}$"), "increase"),
    (re.compile(rf"^(?:another\s+)?{_AMT}(?:\s+more)?\s+on\s+{_TIMER_OBJ}$"), "increase"),
    (re.compile(rf"^(?:take|remove|knock|cut|shave|subtract)\s+(?:off\s+)?{_AMT}\s+(?:off(?:\s+of)?|from)\s+{_TIMER_OBJ}$"),
     "decrease"),
    (re.compile(rf"^(?:increase|extend|lengthen|raise)\s+{_TIMER_OBJ}\s+by\s+{_AMT}$"), "increase"),
    (re.compile(rf"^(?:decrease|reduce|shorten|lower|cut)\s+{_TIMER_OBJ}\s+by\s+{_AMT}$"), "decrease"),
    (re.compile(rf"^(?:increase|extend|lengthen|raise|decrease|reduce|shorten|lower|cut|change|adjust|reset)\s+"
                rf"{_TIMER_OBJ}\s+to\s+{_AMT}$"), "set"),
    (re.compile(rf"^make\s+{_TIMER_OBJ}\s+{_AMT}\s+longer$"), "increase"),
    (re.compile(rf"^make\s+{_TIMER_OBJ}\s+{_AMT}\s+shorter$"), "decrease"),
)


def allowed_timer_adjust(text: str) -> Optional[TimerAdjust]:
    """A change to ONE timer in an allow-listed shape with a plain, parsable amount, else None."""
    t = command_core(text)
    for pattern, direction in _ADJUST_RES:
        m = pattern.match(t)
        if not m or not _plain_amount(m.group("amt")):
            continue
        secs = _amount_seconds(m.group("amt"))
        target = _target_from(m)
        if secs > 0 and target is not None:
            return TimerAdjust(direction, secs, target)
    return None


#: words that make a list ITEM mean something other than one item ("all but milk", "the second item", "everything")
_LIST_ITEM_MODIFIERS = frozenset({
    "other", "another", "second", "third", "fourth", "fifth", "all", "every", "each", "both", "any", "some", "none",
    "not", "never", "don't", "dont", "except", "but", "besides", "apart", "either", "neither", "rest", "remaining",
    "everything", "anything", "nothing", "whole", "entire", "these", "those", "them", "it", "they", "that", "this",
    "one", "ones",
})


def plain_list_item(item: str) -> bool:
    """True when ``item`` names ONE thing (a conjunction is allowed: "salt and pepper" may be one item - the remove
    resolver then needs an EXACT match), never a subset, an ordinal or a pointer."""
    words = re.findall(r"[a-z0-9'-]+", (item or "").lower())
    return bool(words) and len(words) <= 6 and not any(w in _LIST_ITEM_MODIFIERS for w in words)


# ── Questions and statements that only MENTION a write ──────────────────────

_LEAD_IN = r"^\s*(?:(?:hey|hi|ok|okay|so|um|uh|oh|well|zoe|zoey)\b[\s,.!?]*)*"
# "should I add 5 minutes to the timer?", "why did you cancel the timer?", "I was going to remove milk from my list",
# "would you like to complete milk on my list": the words of a write, but no request to do it.
_ASKING_RE = re.compile(
    _LEAD_IN + r"(?:should|shall|why|did|does|was|were|has|had|what\s+if|how\s+(?:do|did|does|would|can)"
    r"|do\s+(?:i|we|you|they)|have\s+(?:i|we|you|they)|would\s+you\s+(?:like|want|mind)|do\s+you\s+(?:want|think)"
    r"|i\s+(?:was|were|wonder|think|thought|already|just|have|had|did|might|may)|we\s+(?:were|already|just|have|had))\b",
    re.IGNORECASE,
)
# A permission question about a DESTRUCTIVE change ("can I remove milk from my list?") is not a request to delete.
_ASKING_PERMISSION_RE = re.compile(_LEAD_IN + r"(?:can|could|may)\s+(?:i|we)\b", re.IGNORECASE)


def asks_or_mentions(text: str, *, destructive: bool = False) -> bool:
    """True when ``text`` is a question or a statement ABOUT a write rather than a request for one. ``destructive``
    (a delete / tick-off / timer change) also counts "can I ..." as a question."""
    t = (text or "").strip()
    if _ASKING_RE.match(t):
        return True
    return destructive and (_ASKING_PERMISSION_RE.match(t) is not None or _NEGATED_RE.search(t) is not None)


# "don't cancel the timer", "do not remove milk from the list": a delete that is explicitly NOT wanted.
_NEGATED_RE = re.compile(r"\b(?:don'?t|do\s+not|never|no\s+need\s+to|not\s+to)\b", re.IGNORECASE)


# ── Delayed commands ─────────────────────────────────────────────────────────

_AMOUNT_WORD = (
    r"(?:three\s+quarters\s+of\s+an?|(?:a\s+)?quarter\s+of\s+an?|a\s+quarter|"
    rf"{NUM}|half\s+an?|a\s+half|a\s+few|few|a\s+couple(?:\s+of)?|couple(?:\s+of)?)"
)
# BOUNDED repetition: an unbounded "(word[\s-]+)+" over overlapping alternatives ("twenty five" / "twenty") is
# exponential on a looping transcript.
_AMOUNT = rf"(?:another\s+|an?\s+extra\s+)?{_AMOUNT_WORD}(?:[\s-]+{_AMOUNT_WORD}){{0,3}}[\s-]+(?:and\s+a\s+half\s+)?(?:{UNIT})"
_HOUR_WORD = r"(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|noon|midnight)"
_CLOCK = r"\d{1,2}(?::\d{2})?\s*(?:a\.?\s?m\.?|p\.?\s?m\.?|o'?\s?clock)|\d{1,2}:\d{2}|noon|midnight"
# Strict: an explicit wait or clock time ("in 5 minutes", "after an hour", "at 10pm"). NOT "for 20 minutes": that is
# a command for NOW (play white noise for 8 hours), it just lasts.
_DELAY_STRICT_RE = re.compile(
    rf"\bin\s+(?:{_AMOUNT}|half\s+an?\s+hour)\b"
    rf"|\bafter\s+(?:{_AMOUNT})\b"
    rf"|\b(?:at|by|until|till)\s+(?:{_CLOCK})(?!\s*(?:%|percent|per\s*cent))\b"
    rf"|\bat\s+\d{{1,2}}\s*$"
    # spoken clock times ("at seven", "at seven thirty", "at half past six", "at quarter to nine", "at ten pm")
    rf"|\bat\s+(?:half\s+past|(?:a\s+)?quarter\s+(?:past|to)|\w+\s+(?:past|to))\s+{_HOUR_WORD}\b"
    rf"|\bat\s+{_HOUR_WORD}(?:\s+(?:o'?\s?clock|thirty|fifteen|forty[\s-]five|oh\s+\w+|twenty(?:[\s-]\w+)?|ten|five))?"
    r"(?:\s*(?:a\.?\s?m\.?|p\.?\s?m\.?|in\s+the\s+(?:morning|evening|afternoon)|tonight))?\s*$"
    rf"|\bat\s+{_HOUR_WORD}(?:\s+\w+)?\s*(?:a\.?\s?m\.?|p\.?\s?m\.?|o'?\s?clock)\b"
)
# ANY time cue anywhere in a device command means "not now" (the default is inverted: an unrecognised time phrase must
# never run the command). Only a word that LOOKS like a time but plainly is not one is let through: "at one end of the
# room", "the morning room", "if you can".
_TIME_CUE_RE = re.compile(
    rf"\b(?:at|by|before|until|till|after|from)\s+(?:\d(?!\d*\s*(?:%|percent|per\s*cent))|half\s+past|(?:a\s+)?quarter\s+(?:past|to)"
    rf"|{_HOUR_WORD}\b(?!\s+(?:end|side|of|point|corner|another|more|time|light|lamp|room|door|window))"
    r"|dawn|dusk|sunset|sunrise|sundown|bedtime|night|dinner|lunch|breakfast|the\s+(?:end|top)\s+of)"
    rf"|\b(?:in|after|within)\s+(?:{_AMOUNT}|half\s+an?\s+hour|a\s+(?:bit|while|minute|moment|sec(?:ond)?)|an?\s+(?:hour|minute))\b"
    r"|\b(?:tonight|tomorrow|later|overnight|o'?\s?clock|noon|midnight)\b"
    r"|\bthis\s+(?:evening|afternoon|morning|weekend)\b|\bin\s+the\s+(?:morning|evening|afternoon|night)\b(?!\s+room)"
    r"|\d\s*(?:a\.?\s?m\b\.?|p\.?\s?m\b\.?)|\b\d{1,2}:\d{2}\b|\b(?:half\s+past|quarter\s+(?:to|past))\b"
    r"|\b(?:when|once|whenever|as\s+soon\s+as)\b(?!\s+(?:you\s+(?:can|could|get|like|want)|possible)\b)"
    r"|\b(?:every|each)\s+(?:day|night|morning|evening|hour|minute|weekday|week|\w+day)\b"
    r"|\bif\s+(?!you\s+(?:can|could|would|please|want|like|don'?t|do|are|have)\b)(?:i|we|it|the|someone|everyone|they|he|she)\b"
    r"|\b(?:mon|tues|wednes|thurs|fri|satur|sun)day\b"
)


#: What both fast paths say to a command for later: there is no scheduler for device commands yet, so nothing runs now
#: and nothing is queued. Honest, and never "Done" (running it now was the bug).
DELAYED_COMMAND_REPLY = "I can't schedule that for later yet, so I haven't changed anything. Ask me again when you want it done."


def delayed_command(text: str, *, strict: bool = False) -> bool:
    """True when a device / playback command carries ANY time cue (it is for later, or on a condition), so it must
    not run now. ``strict`` (music PLAY: a title may say "tonight") counts only an explicit wait or clock time."""
    t = " ".join((text or "").lower().replace("’", "'").split())
    if _DELAY_STRICT_RE.search(t):
        return True
    return not strict and _TIME_CUE_RE.search(t) is not None
