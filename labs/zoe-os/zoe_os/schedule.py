"""When a reminder fires next: ONE pure function (``reminder_next_fire``) and the recurrence maths under it. No clock, no I/O.

The recurrence half is production's ``services/zoe-data/reminder_recurrence.py`` MIRRORED (read, never imported: the lab is
stdlib-only and must not reach into zoe-data). ``tests/test_schedule.py`` runs both on the same grid whenever the production file
is in the tree, so a drift is a failing test, not a surprise. The subset, exactly production's:

  FREQ        DAILY | WEEKLY | MONTHLY | YEARLY          INTERVAL  1..52
  BYDAY       WEEKLY: a list of MO..SU.  MONTHLY: one ordinal weekday (1MO .. 4MO, -1FR)
  BYMONTHDAY  MONTHLY only: 1..31 or -1 (the last day). Day 31 skips a 30-day month; a yearly Feb 29 fires in leap years only.

Anything else (COUNT, UNTIL inside the rule, BYHOUR...) is refused, never half-honoured. The kernel's own END is a separate
``until`` date (the last day an occurrence may fall on): production cannot store one (its parser refuses UNTIL).

Time zones and daylight changes, as production: wall times are the household's (a SETTING, ``tz``), compared as UTC instants. A
wall time inside a spring-forward gap is read with the offset before the gap (02:30 in a 02:00->03:00 gap fires at 03:30: shifted,
never skipped); an ambiguous fall-back wall time is its FIRST instance (``fold=0``).
"""
from __future__ import annotations

import calendar
import re
from datetime import date, datetime, timedelta, timezone, tzinfo

WEEKDAY_CODES = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
_FREQS = ("DAILY", "WEEKLY", "MONTHLY", "YEARLY")
_MAX_MONTH_PERIODS, _MAX_YEAR_PERIODS = 24, 12
HHMM = r"([01]\d|2[0-3]):[0-5]\d"
DAY = r"\d{4}-\d{2}-\d{2}"


# ---- the RRULE subset (production: reminder_recurrence.py parse_rrule .. first_occurrence_date) -------------------------------
def parse_rrule(value: str) -> dict | None:
    """``{"freq", "interval", "byday": [(ordinal|None, weekday)], "bymonthday"}`` or None when outside the subset."""
    if not value or not isinstance(value, str):
        return None
    text = value.strip().upper().removeprefix("RRULE:")
    parts: dict[str, str] = {}
    for chunk in text.split(";"):
        key, eq, val = chunk.partition("=")
        if not eq or key in parts or not val:
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
    raw = parts.pop("BYDAY", None)
    for item in raw.split(",") if raw is not None else ():
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
    if parts:
        return None
    ordinals = [o for o, _ in byday if o is not None]
    if freq == "WEEKLY" and (ordinals or bymonthday is not None):
        return None
    if freq == "MONTHLY" and (len(byday) > 1 or (byday and not ordinals) or (byday and bymonthday is not None)):
        return None
    if freq in ("DAILY", "YEARLY") and (byday or bymonthday is not None):
        return None
    return {"freq": freq, "interval": interval, "byday": byday, "bymonthday": bymonthday}


def format_rrule(rule: dict) -> str:
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


def _nth_weekday(day: date, ordinal: int, weekday: int) -> bool:
    if day.weekday() != weekday:
        return False
    if ordinal == -1:
        return day.day + 7 > calendar.monthrange(day.year, day.month)[1]
    return (day.day - 1) // 7 + 1 == ordinal


def _day_matches(rule: dict, anchor: date, day: date, interval: int) -> bool:
    freq = rule["freq"]
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
            return _nth_weekday(day, *rule["byday"][0])
        target = rule["bymonthday"] if rule["bymonthday"] is not None else anchor.day
        last = calendar.monthrange(day.year, day.month)[1]
        return day.day == (last if target == -1 else target)
    return (day.year - anchor.year) % interval == 0 and (day.month, day.day) == (anchor.month, anchor.day)


def _month_candidate(rule: dict, anchor: date, year: int, month: int) -> date | None:
    last = calendar.monthrange(year, month)[1]
    if rule["byday"]:
        ordinal, weekday = rule["byday"][0]
        day = (last - (date(year, month, last).weekday() - weekday) % 7 if ordinal == -1
               else 1 + (weekday - date(year, month, 1).weekday()) % 7 + 7 * (ordinal - 1))
        return date(year, month, day) if day <= last else None
    target = rule["bymonthday"] if rule["bymonthday"] is not None else anchor.day
    if target == -1:
        return date(year, month, last)
    return date(year, month, target) if target <= last else None


def _candidates(rule: dict, anchor: date, start: date, interval: int):
    freq = rule["freq"]
    if freq in ("DAILY", "WEEKLY"):
        for i in range(7 * interval + 8):
            day = start + timedelta(days=i)
            if _day_matches(rule, anchor, day, interval):
                yield day
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
        except ValueError:            # Feb 29 in a common year: skipped
            hit = None
        if hit and hit >= start:
            yield hit
        k += interval


def next_occurrence(rule: dict, anchor: date, hour: int, minute: int, after: datetime, tz: tzinfo, *,
                    use_interval: bool = True) -> datetime | None:
    """First local fire time strictly after ``after`` (aware), never before ``anchor``; None only if the rule never fires."""
    after_utc = after.astimezone(timezone.utc)
    interval = rule["interval"] if use_interval else 1
    for day in _candidates(rule, anchor, max(anchor, after_utc.astimezone(tz).date()), interval):
        at = datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz).astimezone(timezone.utc)
        if at > after_utc:
            return at.astimezone(tz)
    return None


def first_occurrence_date(rule: dict, start: date, hour: int, minute: int, now: datetime, tz: tzinfo) -> date | None:
    """The anchor a new recurring reminder is stored with: its first occurrence on/after ``start`` still in the future (INTERVAL
    ignored here, so "every second tuesday" starts on the coming Tuesday)."""
    floor = max(now, datetime(start.year, start.month, start.day, tzinfo=tz) - timedelta(microseconds=1))
    hit = next_occurrence(rule, start, hour, minute, floor, tz, use_interval=False)
    return hit.date() if hit else None


# ---- the reminder ----------------------------------------------------------------------------------------------------------------
def hm(text: str | None) -> tuple[int, int] | None:
    return (int(text[:2]), int(text[3:])) if text and re.fullmatch(HHMM, text) else None


def local_at(day: str | date, hour: int, minute: int, tz: tzinfo) -> float:
    d = date.fromisoformat(day) if isinstance(day, str) else day
    return datetime(d.year, d.month, d.day, hour, minute, tzinfo=tz).timestamp()


def reminder_next_fire(row: dict, after: float, tz: tzinfo, default_hm: tuple[int, int]) -> float | None:
    """When ``row`` next fires strictly after ``after`` (epoch seconds), or None: it never fires again.

    The row's forms, each exactly production's meaning (``reminder_scan.schedule_due_reminder`` / ``build_run_at``):
      ``at``                 one instant ("in 30 minutes");
      ``date`` + ``time``    one wall time on one day;
      ``date`` only          that day at the household DEFAULT time; when it was set for today after that time had passed, the
                             write stored ``grace_at`` (a moment shortly after the write), production's same-day fallback;
      ``time`` only          DAILY at that time until it is done (production: no date = "today, else tomorrow", every day);
      ``repeat`` (RRULE)     anchored at ``date`` (the write stored the first occurrence), at ``time`` or the default time, up to
                             the optional ``until`` day.
    The row's own ``after``: nothing at or before it fires (the write, a done, a snooze or an edit moved it).
    ``snoozed_until`` in the future wins over everything (production's scan leaves a snoozed row alone until then). A row that is
    inactive or done never fires."""
    if not row.get("active", 1) or row.get("done"):
        return None
    floor = max(after, row.get("after", 0.0))
    snoozed = row.get("snoozed_until")
    # A snooze writes the row's floor AT its own time, so a snooze equal to the floor is the live snooze and fires. One BEHIND
    # the floor was overtaken by a later write (a done, an edit) and must never fire.
    if snoozed and snoozed > after and snoozed >= row.get("after", 0.0):
        return snoozed
    if row.get("at") is not None:
        return row["at"] if row["at"] > floor else None
    day, clock = row.get("date"), hm(row.get("time"))
    if row.get("repeat"):
        rule = parse_rrule(row["repeat"])
        if rule is None or not day:
            return None
        h, m = clock or default_hm
        nxt = next_occurrence(rule, date.fromisoformat(day), h, m, datetime.fromtimestamp(floor, timezone.utc), tz)
        if nxt is None or (row.get("until") and nxt.date() > date.fromisoformat(row["until"])):
            return None
        return nxt.timestamp()
    if day:
        t = local_at(day, *(clock or default_hm), tz)
        if t > floor:
            return t
        grace = row.get("grace_at") if clock is None else None
        return grace if grace and grace > floor else None
    if clock:
        today = datetime.fromtimestamp(floor, tz).date()
        t = local_at(today, *clock, tz)
        return t if t > floor else local_at(today + timedelta(days=1), *clock, tz)
    return None
