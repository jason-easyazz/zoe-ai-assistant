"""Deterministic reminder recurrence: spoken phrase → RRULE → next fire time.

Recurrence is stored in `reminders.recurring_pattern` as an RFC 5545 RRULE
string (e.g. ``FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR``) so it is unambiguous and a
later swap to ``dateutil.rrule`` is a drop-in. Only the subset below is
supported; anything else is rejected rather than half-honoured:

  FREQ      DAILY | WEEKLY | MONTHLY | YEARLY
  INTERVAL  1..52
  BYDAY     WEEKLY: MO..SU list.  MONTHLY: one ordinal weekday (1MO, -1FR)
  BYMONTHDAY  MONTHLY only: 1..31 or -1 (last day of the month)

Evaluation needs an ANCHOR date (the reminder's stored `due_date`, which the
write path sets to the first occurrence): INTERVAL counts days / Monday-start
weeks / months / years from it, and a rule without BYDAY/BYMONTHDAY repeats on
the anchor's weekday / day of month.

Pure and stdlib-only: no clock reads (callers pass `after`), no I/O. The words
→ RRULE grammar lives here too so the intent path, the write path and tests
share one definition of what "every second tuesday" means.
"""

from __future__ import annotations

import calendar
import re
from datetime import date, datetime, timedelta, tzinfo

WEEKDAY_CODES = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
_DAY_WORDS = {
    "monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1, "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thurs": 3, "friday": 4, "fri": 4, "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}
_ORDINALS = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3,
             "fourth": 4, "4th": 4, "last": -1}
_NUMBER_WORDS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6}
_FREQS = ("DAILY", "WEEKLY", "MONTHLY", "YEARLY")
# Candidate search steps whole PERIODS (a day, a week, INTERVAL months or years),
# never a fixed day window — "every 3 years" or a Feb-29 yearly rule can be
# years away. Bounds: day/week rules hit within 7*INTERVAL+7 days; monthly
# BYMONTHDAY=31 / yearly Feb 29 can skip periods, but never more than these.
_MAX_MONTH_PERIODS = 24
_MAX_YEAR_PERIODS = 12


def parse_rrule(value: str) -> dict | None:
    """Parse + validate an RRULE string in the supported subset.

    Returns ``{"freq", "interval", "byday", "bymonthday"}`` where `byday` is a
    list of ``(ordinal_or_None, weekday_index)``; None when invalid."""
    if not value or not isinstance(value, str):
        return None
    text = value.strip().upper()
    if text.startswith("RRULE:"):
        text = text[6:]
    parts: dict[str, str] = {}
    for chunk in text.split(";"):
        if "=" not in chunk:
            return None
        key, _, val = chunk.partition("=")
        if key in parts or not val:
            return None
        parts[key] = val
    freq = parts.pop("FREQ", None)
    if freq not in _FREQS:
        return None
    try:
        interval = int(parts.pop("INTERVAL", "1"))
    except ValueError:
        return None
    if not 1 <= interval <= 52:
        return None
    byday: list[tuple[int | None, int]] = []
    for item in filter(None, parts.pop("BYDAY", "").split(",")):
        m = re.fullmatch(r"(-1|[1-4])?(MO|TU|WE|TH|FR|SA|SU)", item)
        if not m:
            return None
        byday.append((int(m.group(1)) if m.group(1) else None, WEEKDAY_CODES.index(m.group(2))))
    bymonthday = None
    if "BYMONTHDAY" in parts:
        try:
            bymonthday = int(parts.pop("BYMONTHDAY"))
        except ValueError:
            return None
        if not (1 <= bymonthday <= 31 or bymonthday == -1):
            return None
    if parts:  # COUNT, UNTIL, BYHOUR, … — unsupported, refuse rather than ignore
        return None
    ordinals = [o for o, _ in byday if o is not None]
    if freq == "WEEKLY" and (ordinals or bymonthday is not None):
        return None
    if freq == "MONTHLY" and (len(byday) > 1 or (byday and not ordinals)
                              or (byday and bymonthday is not None)):
        return None
    if freq in ("DAILY", "YEARLY") and (byday or bymonthday is not None):
        return None
    return {"freq": freq, "interval": interval, "byday": byday, "bymonthday": bymonthday}


def format_rrule(rule: dict) -> str:
    """Canonical string for a parsed rule (stable field order, INTERVAL=1 omitted)."""
    out = [f"FREQ={rule['freq']}"]
    if rule["interval"] != 1:
        out.append(f"INTERVAL={rule['interval']}")
    if rule["byday"]:
        out.append("BYDAY=" + ",".join(f"{o or ''}{WEEKDAY_CODES[d]}" for o, d in rule["byday"]))
    if rule["bymonthday"] is not None:
        out.append(f"BYMONTHDAY={rule['bymonthday']}")
    return ";".join(out)


def _months_between(a: date, b: date) -> int:
    return (b.year - a.year) * 12 + (b.month - a.month)


def _nth_weekday_matches(day: date, ordinal: int, weekday: int) -> bool:
    if day.weekday() != weekday:
        return False
    if ordinal == -1:
        return day.day + 7 > calendar.monthrange(day.year, day.month)[1]
    return (day.day - 1) // 7 + 1 == ordinal


def _day_matches(rule: dict, anchor: date, day: date, *, use_interval: bool = True) -> bool:
    freq, interval = rule["freq"], rule["interval"] if use_interval else 1
    if freq == "DAILY":
        return (day - anchor).days % interval == 0
    if freq == "WEEKLY":
        weekdays = {d for _, d in rule["byday"]} or {anchor.weekday()}
        weeks = ((day - timedelta(days=day.weekday())) - (anchor - timedelta(days=anchor.weekday()))).days // 7
        return day.weekday() in weekdays and weeks % interval == 0
    if freq == "MONTHLY":
        if _months_between(anchor, day) % interval:
            return False
        if rule["byday"]:
            ordinal, weekday = rule["byday"][0]
            return _nth_weekday_matches(day, ordinal, weekday)
        target = rule["bymonthday"] if rule["bymonthday"] is not None else anchor.day
        last = calendar.monthrange(day.year, day.month)[1]
        # Day 31 in a 30-day month is skipped (RFC 5545), -1 is always the last day.
        return day.day == (last if target == -1 else target)
    # YEARLY — Feb 29 anchors only fire in leap years (RFC 5545 skips invalid dates).
    return ((day.year - anchor.year) % interval == 0
            and (day.month, day.day) == (anchor.month, anchor.day))


def _month_candidate(rule: dict, anchor: date, year: int, month: int) -> date | None:
    last = calendar.monthrange(year, month)[1]
    if rule["byday"]:
        ordinal, weekday = rule["byday"][0]
        if ordinal == -1:
            day = last - (date(year, month, last).weekday() - weekday) % 7
        else:
            day = 1 + (weekday - date(year, month, 1).weekday()) % 7 + 7 * (ordinal - 1)
        return date(year, month, day) if day <= last else None
    target = rule["bymonthday"] if rule["bymonthday"] is not None else anchor.day
    if target == -1:
        return date(year, month, last)
    return date(year, month, target) if target <= last else None  # 31st skips short months


def _candidate_dates(rule: dict, anchor: date, start: date, interval: int):
    """Matching dates >= `start`, in order, bounded per frequency."""
    freq = rule["freq"]
    if freq in ("DAILY", "WEEKLY"):
        day = start
        for _ in range(7 * interval + 8):
            if _day_matches(rule, anchor, day, use_interval=interval != 1):
                yield day
            day += timedelta(days=1)
        return
    if freq == "MONTHLY":
        k = _months_between(anchor, start)
        k += (-k) % interval
        for _ in range(_MAX_MONTH_PERIODS):
            year, month0 = divmod(anchor.month - 1 + k, 12)
            hit = _month_candidate(rule, anchor, anchor.year + year, month0 + 1)
            if hit and hit >= start:
                yield hit
            k += interval
        return
    k = start.year - anchor.year
    k += (-k) % interval
    for _ in range(_MAX_YEAR_PERIODS):
        try:
            hit = date(anchor.year + k, anchor.month, anchor.day)
        except ValueError:  # Feb 29 in a common year — RFC 5545 skips it
            hit = None
        if hit and hit >= start:
            yield hit
        k += interval


def next_occurrence(rule: dict, anchor: date, hour: int, minute: int, after: datetime,
                    tz: tzinfo, *, use_interval: bool = True) -> datetime | None:
    """First local fire time strictly after `after` (aware) that the rule
    produces, never before `anchor`. None only if the rule can never fire."""
    after_local = after.astimezone(tz)
    interval = rule["interval"] if use_interval else 1
    for day in _candidate_dates(rule, anchor, max(anchor, after_local.date()), interval):
        candidate = datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz)
        if candidate > after_local:
            return candidate
    return None


def first_occurrence_date(rule: dict, start: date, hour: int, minute: int,
                          now: datetime, tz: tzinfo) -> date | None:
    """The date a new recurring reminder should be anchored to: its first
    occurrence on/after `start` that is still in the future. INTERVAL is ignored
    here so "every second tuesday" starts on the coming Tuesday, then the stored
    anchor makes every later occurrence two weeks apart."""
    floor = max(now, datetime(start.year, start.month, start.day, tzinfo=tz) - timedelta(microseconds=1))
    hit = next_occurrence(rule, start, hour, minute, floor, tz, use_interval=False)
    return hit.date() if hit else None


def describe_rrule(rule: dict) -> str:
    """Short spoken form: 'every weekday', 'every 2 weeks on Tuesday', …"""
    names = [calendar.day_name[d] for _, d in rule["byday"]]
    freq, n = rule["freq"], rule["interval"]
    if freq == "DAILY":
        return "every day" if n == 1 else f"every {n} days"
    if freq == "WEEKLY":
        if sorted(d for _, d in rule["byday"]) == [0, 1, 2, 3, 4]:
            base = "every weekday"
        elif sorted(d for _, d in rule["byday"]) == [5, 6]:
            base = "every weekend"
        elif names:
            base = "every " + " and ".join(names)
        else:
            base = "every week"
        if n == 1:
            return base
        return f"every {n} weeks" + (f" on {' and '.join(names)}" if names else "")
    if freq == "MONTHLY":
        every = "every month" if n == 1 else f"every {n} months"
        if rule["byday"]:
            ordinal, d = rule["byday"][0]
            word = {1: "first", 2: "second", 3: "third", 4: "fourth", -1: "last"}[ordinal]
            return f"the {word} {calendar.day_name[d]} of {every}"
        if rule["bymonthday"] == -1:
            return f"the last day of {every}"
        if rule["bymonthday"]:
            return f"{every} on the {_ordinal_suffix(rule['bymonthday'])}"
        return every
    return "every year" if n == 1 else f"every {n} years"


def _ordinal_suffix(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


# ── Words → RRULE ────────────────────────────────────────────────────────────

_DAY_ALT = "|".join(sorted(_DAY_WORDS, key=len, reverse=True))
_DAY_LIST = rf"(?:{_DAY_ALT})s?(?:\s*(?:,|and|&)\s*(?:{_DAY_ALT})s?)*"
# A bare adverb ("daily", "weekly", …) only counts as recurrence at the END of
# the request or right before its time — "buy the daily paper" and "the monthly
# report" are titles, not schedules.
_ADVERB_END = r"(?=\s*(?:$|[,.!?]|at\b|in\s+the\b|from\b|starting\b))"
_PART_OF_DAY = {"morning": "in the morning", "evening": "in the evening",
                "night": "at night", "afternoon": "in the afternoon", "arvo": "in the arvo"}

# (pattern, builder) — most specific first. Each builder returns (rrule_dict,
# replacement_text); the replacement keeps a part-of-day word so the time
# parser downstream still sees "in the morning".
_PHRASES: list[tuple[re.Pattern, callable]] = []


def _phrase(pattern: str):
    def register(fn):
        _PHRASES.append((re.compile(pattern, re.IGNORECASE), fn))
        return fn
    return register


def _days(text: str) -> list[tuple[None, int]]:
    found = [_DAY_WORDS[w] for w in re.findall(_DAY_ALT, text.lower())]
    return [(None, d) for d in sorted(set(found))]


def _rule(freq: str, interval: int = 1, byday=None, bymonthday=None) -> dict:
    return {"freq": freq, "interval": interval, "byday": byday or [], "bymonthday": bymonthday}


@_phrase(rf"\b(?:on\s+)?(?:the\s+)?(?P<ord>first|second|third|fourth|last|1st|2nd|3rd|4th)\s+"
         rf"(?P<day>{_DAY_ALT})\s+of\s+(?:every|each|the)\s+month\b")
def _nth_weekday_of_month(m):
    return _rule("MONTHLY", byday=[(_ORDINALS[m["ord"].lower()], _DAY_WORDS[m["day"].lower()])]), ""


@_phrase(r"\b(?:on\s+)?the\s+last\s+day\s+of\s+(?:every|each|the)\s+month\b")
def _last_day_of_month(m):
    return _rule("MONTHLY", bymonthday=-1), ""


@_phrase(r"\b(?:on\s+)?the\s+(?P<n>\d{1,2})(?:st|nd|rd|th)?\s+of\s+(?:every|each|the)\s+month\b"
         r"|\b(?:every|each)\s+month\s+on\s+the\s+(?P<n2>\d{1,2})(?:st|nd|rd|th)?\b")
def _month_day(m):
    n = int(m["n"] or m["n2"])
    return (_rule("MONTHLY", bymonthday=n), "") if 1 <= n <= 31 else None


@_phrase(rf"\b(?:every|each)\s+(?:other|second|alternate)\s+(?P<days>{_DAY_LIST})\b"
         rf"|\b(?:every|each)\s+fortnight(?:\s+on\s+(?P<fdays>{_DAY_LIST}))?\b|\bfortnightly\b" + _ADVERB_END)
def _fortnight(m):
    return _rule("WEEKLY", 2, _days(m["days"] or m["fdays"] or "")), ""


@_phrase(r"\b(?:every|each)\s+(?:other|second)\s+(?P<unit>day|week|month|year)\b"
         r"|\b(?:every|each)\s+(?P<n>\d{1,2}|two|three|four|five|six)\s+(?P<unit2>days|weeks|months|years)\b")
def _every_n(m):
    raw_n = m["n"]
    n = 2 if raw_n is None else (int(raw_n) if raw_n.isdigit() else _NUMBER_WORDS[raw_n.lower()])
    unit = (m["unit"] or m["unit2"]).lower().rstrip("s")
    freq = {"day": "DAILY", "week": "WEEKLY", "month": "MONTHLY", "year": "YEARLY"}[unit]
    return (_rule(freq, n), "") if 1 <= n <= 52 else None


@_phrase(r"\b(?:every|each)\s+week\s?days?\b|\b(?:on\s+)?weekdays\b")
def _weekdays(m):
    return _rule("WEEKLY", byday=[(None, d) for d in range(5)]), ""


@_phrase(r"\b(?:every|each)\s+weekend\b|\b(?:on\s+)?weekends\b")
def _weekends(m):
    return _rule("WEEKLY", byday=[(None, 5), (None, 6)]), ""


@_phrase(rf"\b(?:every|each)\s+(?P<days>{_DAY_LIST})\b|\bon\s+(?P<plural>(?:{_DAY_ALT})s(?:\s*(?:,|and|&)\s*(?:{_DAY_ALT})s)*)\b")
def _weekly_days(m):
    return _rule("WEEKLY", byday=_days(m["days"] or m["plural"])), ""


@_phrase(r"\b(?:every|each)\s+(?P<pod>morning|evening|night|afternoon|arvo)\b|\bnightly\b" + _ADVERB_END)
def _daily_part(m):
    pod = (m["pod"] or "night").lower()
    return _rule("DAILY"), _PART_OF_DAY[pod]


@_phrase(r"\b(?:every|each)\s+day\b|\bdaily\b" + _ADVERB_END)
def _daily(m):
    return _rule("DAILY"), ""


@_phrase(r"\b(?:every|each)\s+week\b|\bweekly\b" + _ADVERB_END)
def _weekly(m):
    return _rule("WEEKLY"), ""


@_phrase(r"\b(?:every|each)\s+month\b|\bmonthly\b" + _ADVERB_END)
def _monthly(m):
    return _rule("MONTHLY"), ""


@_phrase(r"\b(?:every|each)\s+year\b|\byearly\b" + _ADVERB_END + r"|\bannually\b" + _ADVERB_END)
def _yearly(m):
    return _rule("YEARLY"), ""


def extract_recurrence(text: str) -> tuple[str, str] | None:
    """Find a recurrence phrase in `text`.

    Returns ``(rrule_string, text_without_the_phrase)`` or None. The remainder
    keeps everything else verbatim (title, clock time) for the normal slot
    parsers; only the recurrence words are removed."""
    if not text:
        return None
    for pattern, build in _PHRASES:
        m = pattern.search(text)
        if not m:
            continue
        built = build(m)
        if built is None:
            continue
        rule, replacement = built
        rest = (text[:m.start()] + (" " + replacement + " " if replacement else " ") + text[m.end():])
        rest = re.sub(r"\s+", " ", rest).strip(" ,.")
        return format_rrule(rule), rest
    return None


def normalize_recurrence(raw: object) -> str | None:
    """Write-time normaliser for `recurring_pattern`: blank → None; a valid RRULE
    or a spoken phrase ("every weekday") → canonical RRULE; else ValueError."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    rule = parse_rrule(text)
    if rule is None:
        hit = extract_recurrence(text)
        rule = parse_rrule(hit[0]) if hit and not hit[1] else None
    if rule is None:
        raise ValueError(f"unsupported recurrence {text!r}")
    return format_rrule(rule)


_UNIT_WORDS = (r"(?:minute|hour|day|night|morning|evening|afternoon|arvo|week|weekday|weekend|"
               rf"fortnight|month|quarter|year|{_DAY_ALT})s?")
_UNSUPPORTED_CUE = re.compile(
    rf"\b(?:every|each)\s+(?:(?:\d+|[a-z]+)\s+){{0,2}}?{_UNIT_WORDS}\b"
    rf"|\b(?:hourly|quarterly|fortnightly|nightly|daily|weekly|monthly|yearly|annually)\b{_ADVERB_END}",
    re.IGNORECASE,
)


def find_unsupported_recurrence(text: str) -> str | None:
    """A recurrence cue `extract_recurrence` could NOT turn into a supported rule
    ("every 53 days", "every hour", "every third monday"). Callers must refuse it
    rather than store a one-off — silently dropping the repeat is the bug this
    module exists to fix. Call only after `extract_recurrence` returned None."""
    m = _UNSUPPORTED_CUE.search(text or "")
    return m.group(0) if m else None
