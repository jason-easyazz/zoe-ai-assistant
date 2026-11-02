"""Shared timezone helpers for Zoe runtime services."""

from __future__ import annotations

import os
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_ZOE_TIMEZONE = "Australia/Perth"


def zoe_timezone() -> ZoneInfo:
    """Return Zoe's configured timezone, falling back to the canonical default."""
    name = (os.environ.get("ZOE_TIMEZONE") or DEFAULT_ZOE_TIMEZONE).strip() or DEFAULT_ZOE_TIMEZONE
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(DEFAULT_ZOE_TIMEZONE)


def today_for_zoe_tz(now: datetime | None = None) -> date:
    """Return today's date in Zoe's configured timezone.

    `now` is injectable so tests and follow-up call sites can verify boundaries
    without relying on the host timezone.
    """
    tz = zoe_timezone()
    if now is None:
        return datetime.now(tz).date()
    if now.tzinfo is None:
        return now.replace(tzinfo=tz).date()
    return now.astimezone(tz).date()


# ── Household-local wording for "when" ───────────────────────────────────────
# ONE place that turns a timestamp into words relative to now, in the household
# timezone, with day boundaries at LOCAL midnight. Before it, each surface rolled its
# own (or handed the brain a bare fact and let it say "this morning").

_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_MONTHS_FULL = ("January", "February", "March", "April", "May", "June", "July", "August",
                "September", "October", "November", "December")
_NUM_WORDS = {2: "two", 3: "three", 4: "four"}
_SKEW_S = 600  # a timestamp this far in the future is clock skew, not a plan


def as_local(when, tz: ZoneInfo | None = None) -> datetime | None:
    """epoch seconds | aware/naive datetime | ISO string -> aware local datetime, or None.
    A naive value is UTC (every store here writes UTC)."""
    tz = tz or zoe_timezone()
    try:
        if isinstance(when, datetime):
            dt = when
        elif isinstance(when, (int, float)):
            return datetime.fromtimestamp(float(when), tz)
        else:
            dt = datetime.fromisoformat(str(when).strip().replace("Z", "+00:00"))
    except (ValueError, TypeError, OverflowError, OSError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tz)


def _daypart(hour: int) -> str:
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 17:
        return "afternoon"
    if 17 <= hour < 21:
        return "evening"
    return "night"


def humanize_when(then, now=None, tz: ZoneInfo | None = None) -> str:
    """Words for ``then`` relative to ``now`` in the household timezone ("" if unparseable).

    Reads after "you mentioned it": just now / a little while ago / this morning /
    this afternoon / this evening / earlier tonight / last night / yesterday morning /
    yesterday afternoon / yesterday evening / yesterday / on Monday / last week /
    two weeks ago / on 12 October. Day boundaries are local midnight, never UTC."""
    tz = tz or zoe_timezone()
    t = as_local(then, tz)
    n = as_local(now, tz) if now is not None else datetime.now(tz)
    if t is None or n is None:
        return ""
    delta = (n - t).total_seconds()
    if delta < 0:
        if -delta <= _SKEW_S:
            return "just now"
        # Future-dated: never claim it was said in the past; give the plain date.
        return f"on {t.day} {_MONTHS_FULL[t.month - 1]}"
    if delta < 180:
        return "just now"
    days = (n.date() - t.date()).days
    if days == 0:
        if delta < 3600:
            return "a little while ago"
        if t.hour < 5:  # small hours: "last night" once it is day, "earlier tonight" while still night
            return "last night" if n.hour >= 5 else "earlier tonight"
        part = _daypart(t.hour)
        return "earlier tonight" if part == "night" else f"this {part}"
    if days == 1:
        part = _daypart(t.hour)
        if t.hour >= 21:
            return "last night"
        if part == "night":  # 00:00-04:59 yesterday
            return "yesterday"
        return f"yesterday {part}"
    if days < 7:
        return f"on {_WEEKDAYS[t.weekday()]}"
    if days < 14:
        return "last week"
    if days < 28:
        return f"{_NUM_WORDS.get(days // 7, str(days // 7))} weeks ago"
    year = f" {t.year}" if t.year != n.year else ""
    return f"on {t.day} {_MONTHS_FULL[t.month - 1]}{year}"


def clock_stamp(when=None, tz: ZoneInfo | None = None) -> str:
    """Exact local stamp for a prompt: ``Mon 2 Nov 2026 08:00 (UTC+8, Australia/Perth)``.
    ``when`` None = now. "" if unparseable."""
    tz = tz or zoe_timezone()
    t = datetime.now(tz) if when is None else as_local(when, tz)
    if t is None:
        return ""
    off = t.utcoffset()
    mins = int(off.total_seconds() // 60) if off is not None else 0
    sign = "+" if mins >= 0 else "-"
    h, m = divmod(abs(mins), 60)
    utc = f"UTC{sign}{h}" + (f":{m:02d}" if m else "")
    return (f"{_WEEKDAYS[t.weekday()][:3]} {t.day} {_MONTHS_FULL[t.month - 1][:3]} {t.year} "
            f"{t.hour:02d}:{t.minute:02d} ({utc}, {getattr(tz, 'key', 'local')})")


def now_line(now=None, tz: ZoneInfo | None = None) -> str:
    """One compact line telling the brain the household's current local time."""
    stamp = clock_stamp(now, tz)
    return f"[Now: {stamp}]" if stamp else ""
