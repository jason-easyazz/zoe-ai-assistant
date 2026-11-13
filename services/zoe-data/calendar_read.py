"""The ONE read path over the ``events`` table, plus the read-only write rule (gap audit 2026-11-13, lane D X3/X4).

Every reader used to query ``events`` itself and got it wrong the same ways: matched ``start_date`` only (a trip that began
before the window vanished), never expanded a repeat (a weekly event showed on its first date only), read ``cancelled``
events out as real, and let a voice/tool edit change an event its provider owns. ``events_in_window`` fixes all four for
every caller; ``is_writable`` / ``refusal`` are the one rule for update and delete.

Time (docs/architecture/calendar-data-model.md section 5.1): ``start_date`` / ``start_time`` are the wall clock in the
event's own ``timezone``; no zone = floating (read as household time); all-day = a date (inclusive ``end_date``). An
occurrence's ``date`` / ``start_time`` are the HOUSEHOLD view (``time_utils.zoe_timezone``), which is what the window is
matched on; a floating or all-day event's are its own wall clock unchanged.

Repeats: ``rrule`` (0052, RFC 5545) else the legacy ``recurring`` (an RRULE or one plain word: daily, weekly, fortnightly,
monthly, yearly), evaluated by ``reminder_recurrence`` (its subset, plus UNTIL / COUNT / WKST here). Expanded on read over a
bounded window, never materialised. An unparseable repeat yields its base occurrence only. EXDATE and RDATE are honoured; a
RECURRENCE-ID override row replaces its master's instance on that date.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any, Optional

MAX_WINDOW_DAYS = 400                 # a wider request is clamped to this many days after its start
MAX_OCCURRENCES_PER_EVENT = 200       # hard cap on one event's expansion inside one window
_COUNT_SCAN_DAYS = 3660               # a COUNT rule is counted from its anchor; never scan further than this

#: Sources whose rows are a provider's copy: Zoe has no write-back, so an edit here would silently fork the event.
PROVIDER_SOURCES = frozenset(("ha", "caldav", "google", "microsoft"))
SOURCE_LABELS = {"local": "Zoe", "ics": "an imported calendar file", "ha": "Home Assistant",
                 "caldav": "a CalDAV calendar", "google": "Google Calendar", "microsoft": "Outlook"}

_REPEAT_WORDS = {"daily": ("DAILY", 1), "weekly": ("WEEKLY", 1), "fortnightly": ("WEEKLY", 2), "biweekly": ("WEEKLY", 2),
                 "monthly": ("MONTHLY", 1), "yearly": ("YEARLY", 1), "annually": ("YEARLY", 1), "annual": ("YEARLY", 1)}


def repeat_rule(raw: Any) -> Optional[dict]:
    """A repeat as a ``reminder_recurrence`` rule: an RRULE in its subset, or one plain word. None = not understood."""
    import reminder_recurrence as rr

    text = str(raw or "").strip()
    word = _REPEAT_WORDS.get(text.lower())
    if word:
        return {"freq": word[0], "interval": word[1], "byday": [], "bymonthday": None}
    return rr.parse_rrule(text)


def _rule_with_bounds(raw: Any) -> "tuple[Optional[dict], Optional[date], Optional[int]]":
    """``(rule, until, count)``: UNTIL / COUNT / WKST are taken off before the subset parser sees the rule."""
    text = str(raw or "").strip()
    upper = text.upper()
    if upper.startswith("RRULE:"):
        upper = upper[6:]
    if "=" not in upper:
        return repeat_rule(text), None, None
    keep, until, count = [], None, None
    for chunk in upper.split(";"):
        key, _, val = chunk.partition("=")
        try:
            if key == "UNTIL":
                until = date(int(val[0:4]), int(val[4:6]), int(val[6:8]))
            elif key == "COUNT":
                count = int(val)
            elif key != "WKST":
                keep.append(chunk)
        except ValueError:
            return None, None, None
    if count is not None and count < 1:
        return None, None, None
    return repeat_rule(";".join(keep)), until, count


def _dates(raw: Any) -> set:
    """``exdates`` / ``rdates`` (JSON list of ISO dates or instants) as a set of dates; garbage = empty."""
    try:
        items = json.loads(raw) if isinstance(raw, str) else (raw or [])
    except ValueError:
        return set()
    out = set()
    for item in items if isinstance(items, list) else []:
        d = _date(item)
        if d is not None:
            out.add(d)
    return out


def _date(raw: Any) -> Optional[date]:
    try:
        return date.fromisoformat(str(raw or "").strip()[:10])
    except ValueError:
        return None


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() in ("1", "true", "t", "yes")


def is_recurring(row: dict) -> bool:
    return bool(str(row.get("rrule") or "").strip() or str(row.get("recurring") or "").strip())


def occurrence_dates(row: dict, first: date, last: date) -> "list[date]":
    """The event's start dates (in its own frame) inside ``[first, last]``, capped. A non-repeating or unparseable-repeat
    event yields its base date only (when it is inside the range)."""
    import reminder_recurrence as rr

    anchor = _date(row.get("start_date"))
    if anchor is None:
        return []
    raw = str(row.get("rrule") or "").strip() or str(row.get("recurring") or "").strip()
    rule, until, count = _rule_with_bounds(raw) if raw else (None, None, None)
    if rule is None:
        return [anchor] if first <= anchor <= last else []
    skip = _dates(row.get("exdates"))
    stop = min(last, until) if until else last
    out: list = []
    seen = 0
    d = anchor if count else max(anchor, first)
    while d <= stop and len(out) < MAX_OCCURRENCES_PER_EVENT:
        if d == anchor or rr.occurs_on(rule, anchor, d):     # RFC 5545: DTSTART is always the first instance
            seen += 1
            if count and seen > count:
                break
            if d >= first and d not in skip:
                out.append(d)
        d += timedelta(days=1)
        if count and (d - anchor).days > _COUNT_SCAN_DAYS:
            break
    for extra in sorted(_dates(row.get("rdates"))):
        if first <= extra <= last and extra not in out and len(out) < MAX_OCCURRENCES_PER_EVENT:
            out.append(extra)
    return sorted(out)


def _household(d: date, hhmm: str, row: dict, tz_key: str = "timezone") -> "tuple[date, str]":
    """``(date, "HH:MM")`` of a wall clock in the event's zone, seen in the household zone. Floating / all-day / untimed
    rows keep their own wall clock (they have no instant)."""
    from calendar_service import parse_time
    from time_utils import zoe_timezone

    zone = row.get(tz_key) or row.get("timezone")
    t = parse_time(hhmm)
    if not zone or t is None or _truthy(row.get("all_day")):
        return d, (hhmm or "")[:5]
    from zoneinfo import ZoneInfo

    try:
        local = datetime.combine(d, t).replace(tzinfo=ZoneInfo(str(zone))).astimezone(zoe_timezone())
    except (ValueError, KeyError, OSError):
        return d, (hhmm or "")[:5]
    return local.date(), local.strftime("%H:%M")


def is_writable(row: Optional[dict]) -> bool:
    """May Zoe (voice, a tool, the API) update or delete this event? Not when it is read-only, comes from a provider
    calendar (no write-back exists, so an edit would fork it), or sits in a calendar marked not writable."""
    if not row:
        return False
    if _truthy(row.get("read_only")):
        return False
    if str(row.get("source") or "local").strip().lower() in PROVIDER_SOURCES:
        return False
    if "calendar_writable" in row and row["calendar_writable"] is not None and not _truthy(row["calendar_writable"]):
        return False
    return True


def source_label(row: dict) -> str:
    source = str(row.get("source") or "local").strip().lower()
    return str(row.get("calendar_name") or "") or SOURCE_LABELS.get(source, "another calendar")


def refusal(row: dict) -> str:
    """The honest answer when an edit is refused: where the event lives, and that the change belongs there."""
    if str(row.get("source") or "local").strip().lower() in ("local", "") and not row.get("calendar_name"):
        return "That one is on a read-only calendar, so I can't change it."
    return f"That one comes from {source_label(row)}; change it there."


def _visibility_sql(user_id: str, own_only: bool, include_busy: bool) -> "tuple[str, list]":
    if own_only:
        return "user_id = ?", [user_id]
    if include_busy:
        return "(visibility IN ('family', 'busy') OR user_id = ?)", [user_id]
    return "(visibility = 'family' OR user_id = ?)", [user_id]


async def events_in_window(db, user_id: str, start: Any, end: Any, *, include_cancelled: bool = False,
                           own_only: bool = False, include_busy: bool = False,
                           category: Optional[str] = None, pg_fetch: bool = False) -> "list[dict]":
    """Occurrences overlapping the household-local dates ``[start, end]`` (inclusive), sorted: per day all-day first,
    then by time. Each is ``{event_id, date, start_time, end_date, end_time, title, location, category, all_day,
    is_recurring, read_only, source, source_label, calendar_id, status, event}`` where ``event`` is the full row (its
    ``start_date`` / ``end_date`` moved to this occurrence, in the event's own frame).

    Visibility: the household's family events plus the member's own (``own_only``: the member's own only;
    ``include_busy``: also other members' busy-only events, which the caller must project). ``pg_fetch``: ``db`` is a
    raw asyncpg connection (``$n`` placeholders, ``db.fetch``), as the Skybridge resolvers hold."""
    w0, w1 = _date(start), _date(end)
    if w0 is None:
        raise ValueError("events_in_window needs a start date")
    w1 = w1 or w0
    if w1 < w0:
        return []
    w1 = min(w1, w0 + timedelta(days=MAX_WINDOW_DAYS))
    vis, params = _visibility_sql(user_id, own_only, include_busy)
    # a zoned event's own-frame date can sit one day either side of the household date: pad, then filter exactly below
    sql = (f"SELECT * FROM events WHERE {vis} AND COALESCE(deleted, 0) = 0 AND start_date <= ?"
           " AND (COALESCE(NULLIF(end_date, ''), start_date) >= ? OR COALESCE(recurring, '') <> ''"
           " OR COALESCE(rrule, '') <> '' OR COALESCE(recurrence_id, '') <> '')")
    params += [(w1 + timedelta(days=1)).isoformat(), (w0 - timedelta(days=1)).isoformat()]
    if category:
        sql += " AND category = ? AND (visibility <> 'busy' OR user_id = ?)"
        params += [category, user_id]
    if pg_fetch:
        parts = sql.split("?")
        sql = parts[0] + "".join(f"${i}{p}" for i, p in enumerate(parts[1:], 1))
        rows = [dict(r) for r in await db.fetch(sql, *params)]
    else:
        cur = await db.execute(sql, params)
        rows = [dict(r) for r in await cur.fetchall()]

    replaced = {(r.get("series_id"), _date(r.get("recurrence_id"))) for r in rows
                if r.get("series_id") and _date(r.get("recurrence_id"))}
    out: list = []
    for row in rows:
        if not include_cancelled and str(row.get("status") or "").lower() == "cancelled":
            continue
        d0 = _date(row.get("start_date"))
        if d0 is None:
            continue
        span = max(0, ((_date(row.get("end_date")) or d0) - d0).days)
        for d in occurrence_dates(row, w0 - timedelta(days=span + 1), w1 + timedelta(days=1)):
            if is_recurring(row) and (row.get("id"), d) in replaced:
                continue
            hd, ht = _household(d, str(row.get("start_time") or ""), row)
            if str(row.get("end_time") or "").strip():
                he, het = _household(d + timedelta(days=span), str(row["end_time"]), row, "end_timezone")
            else:                                                   # no end time: it ends on its (household) start day
                he, het = hd + timedelta(days=span), ""
            if hd > w1 or max(he, hd) < w0:
                continue
            event = {**row, "start_date": d.isoformat(),
                     "end_date": (d + timedelta(days=span)).isoformat() if row.get("end_date") else row.get("end_date")}
            out.append({"event_id": row.get("id"), "date": hd.isoformat(), "start_time": ht, "end_date": he.isoformat(),
                        "end_time": het, "title": row.get("title"), "location": row.get("location"),
                        "category": row.get("category"), "all_day": _truthy(row.get("all_day")),
                        "is_recurring": is_recurring(row), "read_only": not is_writable(row),
                        "source": str(row.get("source") or "local"), "source_label": source_label(row),
                        "calendar_id": row.get("calendar_id"), "status": row.get("status") or "confirmed",
                        "event": event})
    out.sort(key=lambda o: (o["date"], 0 if o["all_day"] else 1, o["start_time"] or "99:99", str(o["title"] or "")))
    return out
