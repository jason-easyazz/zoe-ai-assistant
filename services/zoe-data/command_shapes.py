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
# "add 5 minutes to the timer" - the amount comes first, the timer is the object of to/from.
_AMOUNT_FIRST = (
    (re.compile(rf"\b(?:{_INCREASE_VERBS})\b(?P<amt>.+?)\b(?:to|on|onto)\b(?P<tgt>.*{_TIMER}.*)$"), "increase"),
    (re.compile(rf"\b(?:{_DECREASE_VERBS})\b(?P<amt>.+?)\b(?:off(?:\s+of)?|from)\b(?P<tgt>.*{_TIMER}.*)$"), "decrease"),
)
# "increase the timer by 5 minutes" - the timer first, the amount after "by".
_TIMER_FIRST = (
    (re.compile(rf"\b(?:increase|extend|lengthen|raise)\b(?P<tgt>.*?{_TIMER}.*?)\bby\b(?P<amt>.+)$"), "increase"),
    (re.compile(rf"\b(?:decrease|reduce|shorten|lower|cut)\b(?P<tgt>.*?{_TIMER}.*?)\bby\b(?P<amt>.+)$"), "decrease"),
)
_DETERMINERS = r"(?:the|my|our|your|a|an|that|this)"
_NOT_A_NAME = frozenset({"current", "running", "other", "first", "last", "only", "existing", "active"})
# "10 MORE minutes", "an EXTRA minute", "ANOTHER 5 minutes": amount words that are not part of the number.
_AMOUNT_NOISE_RE = re.compile(r"\b(?:more|extra|another|additional)\b")


@dataclass(frozen=True)
class TimerTarget:
    """Which running timer a command means: by name, by its original length, or (both empty) the only one."""

    label: str = ""
    start_seconds: int = 0


@dataclass(frozen=True)
class TimerAdjust:
    direction: str  # "increase" | "decrease"
    seconds: int
    target: TimerTarget


def timer_target(phrase: str) -> TimerTarget:
    """Read "the pizza timer" / "timer named pizza" / "the 5 minute timer" / "timer for 1 hour" / "timer in the
    kitchen" into a :class:`TimerTarget`. A room ("in the kitchen") names no timer: Zoe's timers have no room."""
    p = " ".join((phrase or "").lower().split())
    m = re.search(r"\b(?:named|called)\s+(?:the\s+)?(?P<l>.+?)\s*$", p)
    if m:
        return TimerTarget(label=m.group("l").strip(" .!?"))
    m = re.search(rf"{_TIMER}\s+for\s+(?P<d>.+?)\s*$", p)
    if m and parse_duration_seconds(m.group("d")):
        return TimerTarget(start_seconds=parse_duration_seconds(m.group("d")))
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
    if secs:
        return TimerTarget(start_seconds=secs)
    return TimerTarget(label=pre)


def timer_adjust(text: str) -> Optional[TimerAdjust]:
    """A change of length to a running timer, or None. Needs a timer word AND a parsable amount."""
    t = " ".join((text or "").lower().split())
    if not re.search(_TIMER, t):
        return None
    for patterns in (_AMOUNT_FIRST, _TIMER_FIRST):
        for pattern, direction in patterns:
            m = pattern.search(t)
            if not m:
                continue
            secs = parse_duration_seconds(" ".join(_AMOUNT_NOISE_RE.sub(" ", m.group("amt")).split()))
            if secs <= 0:
                continue
            return TimerAdjust(direction, secs, timer_target(m.group("tgt")))
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


# ── Delayed commands ─────────────────────────────────────────────────────────

_AMOUNT = rf"(?:another\s+|an?\s+extra\s+)?(?:(?:{NUM}|half\s+an?|a\s+half|a\s+few|few|a\s+couple(?:\s+of)?|couple(?:\s+of)?)[\s-]+)+(?:and\s+a\s+half\s+)?(?:{UNIT})"
_CLOCK = r"\d{1,2}(?::\d{2})?\s*(?:a\.?\s?m\.?|p\.?\s?m\.?|o'?\s?clock)|\d{1,2}:\d{2}|noon|midnight"
# Strict: an explicit amount of time or clock time ("in 5 minutes", "after an hour", "for 20 minutes", "at 10pm").
_DELAY_STRICT_RE = re.compile(
    rf"\bin\s+(?:{_AMOUNT}|half\s+an?\s+hour)\b"
    rf"|\bafter\s+(?:{_AMOUNT})\b"
    rf"|\bfor\s+(?:the\s+next\s+|another\s+)?(?:{_AMOUNT})\b"
    rf"|\b(?:at|by|until|till)\s+(?:{_CLOCK})(?!\s*(?:%|percent|per\s*cent))\b"
    rf"|\bat\s+\d{{1,2}}\s*$"
)
# Loose adds day words and conditions ("tonight", "tomorrow", "when I get home", "every morning"). Device commands
# only: a song or show title may well contain "tonight" ("play Tonight Tonight").
_DELAY_LOOSE_RE = re.compile(
    r"\b(?:tonight|tomorrow|later(?:\s+on)?|this\s+(?:evening|afternoon)|in\s+the\s+(?:morning|evening|afternoon)"
    r"|in\s+a\s+(?:bit|while|minute|moment|sec(?:ond)?))\b"
    r"|\b(?:when|once|as\s+soon\s+as|whenever|if)\s+(?:i|we|you|it|the|someone|everyone|they|he|she)\b"
    r"|\b(?:every|each)\s+(?:day|night|morning|evening|hour|weekday|week)\b"
)


#: What both fast paths say to a command for later: there is no scheduler for device commands yet, so nothing runs now
#: and nothing is queued. Honest, and never "Done" (running it now was the bug).
DELAYED_COMMAND_REPLY = "I can't schedule that for later yet, so I haven't changed anything. Ask me again when you want it done."


def delayed_command(text: str, *, strict: bool = False) -> bool:
    """True when the command is for LATER (or for a while, or on a condition), not now."""
    t = " ".join((text or "").lower().replace("’", "'").split())
    if _DELAY_STRICT_RE.search(t):
        return True
    return not strict and _DELAY_LOOSE_RE.search(t) is not None
