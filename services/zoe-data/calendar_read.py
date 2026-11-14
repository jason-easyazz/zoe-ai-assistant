"""A shared read path over the ``events`` table, plus the write rule for update and delete (gap audit 2026-11-13, lane D X3/X4).

Readers used to query ``events`` themselves and got it wrong the same ways: matched ``start_date`` only (a trip that began
before the window vanished), never expanded a repeat (a weekly event showed on its first date only), read ``cancelled``
events out as real, and let a voice/tool edit change an event its provider owns. ``events_in_window`` fixes all four for the
readers that use it: the calendar list and today routes, the MCP ``calendar_list_events`` / ``calendar_today`` tools, the
``intent_router`` calendar show and daily briefing, the Skybridge calendar show, the anticipation loop (night pass and its
re-checks, the member's OWN events), ``proactive/triggers/morning_checkin``, ``proactive/selector`` and the pendant day review's
duplicate check (``omi_day_review._on_calendar``). Still reading ``events`` directly, by design: the write paths (the routers,
the Skybridge move/delete target lookups, ``calendar_forget``, ``calendar_ical``) and the single-row ``GET /events/{id}``.
``load_for_write`` + ``is_writable`` / ``refusal`` are the rule every update and delete path uses.

Time (docs/architecture/calendar-data-model.md section 5.1): ``start_date`` / ``start_time`` are the wall clock in the
event's own ``timezone``; no zone = floating (read as household time); all-day = a date (inclusive ``end_date``). An
occurrence's ``date`` / ``start_time`` are the HOUSEHOLD view (``time_utils.zoe_timezone``), which is what the window is
matched on; a floating or all-day event's are its own wall clock unchanged.

Repeats: ``rrule`` (0052, RFC 5545) else the legacy ``recurring`` (an RRULE or one plain word: daily, weekly, fortnightly,
monthly, yearly), evaluated by ``reminder_recurrence`` (its subset, plus UNTIL / COUNT / WKST here). Expanded on read over a
bounded window, never materialised. An unparseable repeat yields its base occurrence only. RDATE adds dates (an RDATE-only
event is a series too); EXDATE removes dates and wins over RDATE. A RECURRENCE-ID override row replaces its master's
instance on its ORIGINAL date, wherever the override itself moved to.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any, Optional

MAX_WINDOW_DAYS = 400                 # a wider request is clamped to this many days after its start
MAX_OCCURRENCES_PER_EVENT = 200       # hard cap on one event's expansion inside one window
_COUNT_SCAN_DAYS = 3660               # a COUNT rule is counted from its anchor; never scan further than this
_OVERRIDE_PAD_DAYS = 31               # an override's original date this far before the window may still be inside it (multi-day)

#: Sources whose rows are a provider's copy: Zoe has no write-back, so an edit here would silently fork the event.
#: ``subscription`` = a read-only subscribed calendar feed (an ICS address); ``ics`` = a file imported once, which stays editable.
PROVIDER_SOURCES = frozenset(("ha", "caldav", "google", "microsoft", "subscription"))
SOURCE_LABELS = {"local": "Zoe", "ics": "an imported calendar file", "ha": "Home Assistant",
                 "caldav": "a CalDAV calendar", "google": "Google Calendar", "microsoft": "Outlook",
                 "subscription": "a subscribed calendar"}

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


def repeat_understood(row: dict) -> bool:
    """False when the row carries a repeat rule this module cannot read (it would show its base date only)."""
    raw = str(row.get("rrule") or "").strip() or str(row.get("recurring") or "").strip()
    return not raw or _rule_with_bounds(raw)[0] is not None


def is_recurring(row: dict) -> bool:
    return bool(str(row.get("rrule") or "").strip() or str(row.get("recurring") or "").strip() or _dates(row.get("rdates")))


def occurrence_dates(row: dict, first: date, last: date) -> "list[date]":
    """The event's start dates (in its own frame) inside ``[first, last]``, capped. A non-repeating or unparseable-repeat
    event yields its base date only (when it is inside the range)."""
    import reminder_recurrence as rr

    anchor = _date(row.get("start_date"))
    if anchor is None:
        return []
    raw = str(row.get("rrule") or "").strip() or str(row.get("recurring") or "").strip()
    rule, until, count = _rule_with_bounds(raw) if raw else (None, None, None)
    skip = _dates(row.get("exdates"))
    out: list = []
    if rule is None:                                   # no (understood) rule: the base date, plus any RDATEs below
        if first <= anchor <= last and anchor not in skip:
            out.append(anchor)
    stop = min(last, until) if until else last
    seen = 0
    d = anchor if count else max(anchor, first)
    while rule is not None and d <= stop and len(out) < MAX_OCCURRENCES_PER_EVENT:
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
        if first <= extra <= last and extra not in skip and extra not in out and len(out) < MAX_OCCURRENCES_PER_EVENT:
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


def instance_key(event_id: Any, day: date, hhmm: str = "") -> str:
    """One occurrence's stable key: ``<id>@<household date>[T<HH:MM>]``. Unique within a window (an event has at most one
    occurrence per start day) and identical across calls. ``id`` stays the series id that edits and deletes act on."""
    return f"{event_id}@{day.isoformat()}" + (f"T{hhmm}" if hhmm else "")


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
    """Where an event comes from, for a person: the provider for a synced row, else its calendar's name, else "Zoe"."""
    source = str(row.get("source") or "local").strip().lower()
    if source in PROVIDER_SOURCES:
        return SOURCE_LABELS[source]
    return str(row.get("calendar_name") or "") or SOURCE_LABELS.get(source, "another calendar")


REPEATING_DELETE_LINE = "That's a repeating event. I can't remove just one yet, so change it on your phone."
REPEATING_MOVE_LINE = "That's a repeating event. I can't move just one yet, so change it on your phone."


def is_repeating_row(row: Optional[dict]) -> bool:
    """A STORED row (``load_for_write``) that belongs to a series: a master (``rrule`` / legacy ``recurring`` /
    ``rdates``) or an override (``recurrence_id`` / ``series_id``). Voice and card edits act on the row's id, and the
    lists now show one item per occurrence, so a "delete this one" or "move this one" there would change every
    occurrence (or, for an override, bring the master's instance back). Callers refuse with the lines above until
    per-occurrence edits exist."""
    if not row:
        return False
    return is_recurring(row) or bool(str(row.get("recurrence_id") or "").strip() or str(row.get("series_id") or "").strip())


def refusal(row: dict) -> str:
    """The honest answer when an edit is refused: where the event lives, and that the change belongs there."""
    source = str(row.get("source") or "local").strip().lower()
    if source in PROVIDER_SOURCES:
        return f"That one comes from {SOURCE_LABELS[source]}; change it there."
    name = str(row.get("calendar_name") or "")
    return f"That one is on a read-only calendar{f' ({name})' if name else ''}, so I can't change it."


async def _fetch(db, sql: str, params: list, pg_fetch: bool) -> "list[dict]":
    """Rows as dicts from the pooled ``?`` connection, or (``pg_fetch``) a raw asyncpg connection (``$n``, ``db.fetch``)."""
    if pg_fetch:
        parts = sql.split("?")
        sql = parts[0] + "".join(f"${i}{p}" for i, p in enumerate(parts[1:], 1))
        return [dict(r) for r in await db.fetch(sql, *params)]
    cur = await db.execute(sql, params)
    return [dict(r) for r in await cur.fetchall()]


async def load_for_write(db, user_id: str, event_id: Any, *, own_only: bool = False,
                         pg_fetch: bool = False) -> Optional[dict]:
    """The event an update / delete would touch, with its calendar's ``calendar_writable`` and ``calendar_name``, or None.

    Scoped to the caller exactly as the write paths scope it: the member's own events plus household (``family``) ones
    (``own_only``: own only). An id the caller cannot see is None - "not found", never a hint that it exists or where it
    came from."""
    scope = "e.user_id = ?" if own_only else "(e.visibility = 'family' OR e.user_id = ?)"
    rows = await _fetch(db, "SELECT e.*, c.writable AS calendar_writable, c.name AS calendar_name FROM events e"
                            " LEFT JOIN calendars c ON c.id = e.calendar_id"
                            f" WHERE e.id = ? AND {scope} AND COALESCE(e.deleted, 0) = 0",
                        [event_id, user_id], pg_fetch)
    return next((r for r in rows if r.get("id") == event_id), None)


def _visibility_sql(user_id: str, own_only: bool, include_busy: bool) -> "tuple[str, list]":
    if own_only:
        return "user_id = ?", [user_id]
    if include_busy:
        return "(visibility IN ('family', 'busy') OR user_id = ?)", [user_id]
    return "(visibility = 'family' OR user_id = ?)", [user_id]


async def events_in_window(db, user_id: str, start: Any, end: Any, *, include_cancelled: bool = False,
                           own_only: bool = False, include_busy: bool = False,
                           category: Optional[str] = None, pg_fetch: bool = False,
                           expand: bool = True) -> "list[dict]":
    """Occurrences overlapping the household-local dates ``[start, end]`` (inclusive), sorted: per day all-day first,
    then by time. Each is ``{event_id, instance_key, series_start_date, date, start_time, end_date, end_time, title, location, category, all_day,
    is_recurring, read_only, source, source_label, calendar_id, status, event}`` where ``event`` is the full row (its
    ``start_date`` / ``end_date`` moved to this occurrence, in the event's own frame).

    Visibility: the household's family events plus the member's own (``own_only``: the member's own only;
    ``include_busy``: also other members' busy-only events, which the caller must project). ``pg_fetch``: ``db`` is a
    raw asyncpg connection (``$n`` placeholders, ``db.fetch``), as the Skybridge resolvers hold.

    ``expand=False``: the STORED rows instead - each master and override row once, unshifted (``event`` is the row as
    stored, no override suppression), for any caller that exports, syncs, diffs or writes: an expanded occurrence carries
    the master's rrule / exdates under its own date and must never be written back. Same window, visibility and
    cancelled rules; a repeating master is included when any of its occurrences falls in the window."""
    w0, w1 = _date(start), _date(end)
    if w0 is None:
        raise ValueError("events_in_window needs a start date")
    w1 = w1 or w0
    if w1 < w0:
        return []
    w1 = min(w1, w0 + timedelta(days=MAX_WINDOW_DAYS))
    vis, params = _visibility_sql(user_id, own_only, include_busy)
    # a zoned event's own-frame date can sit one day either side of the household date: pad, then filter exactly below
    # An override row is fetched by its ORIGINAL date (recurrence_id) too, so one moved out of the window still suppresses
    # its master's instance there; one whose moved date overlaps the window is fetched like any event.
    sql = (f"SELECT * FROM events WHERE {vis} AND COALESCE(deleted, 0) = 0 AND ("
           "(start_date <= ? AND (COALESCE(NULLIF(end_date, ''), start_date) >= ? OR COALESCE(recurring, '') <> ''"
           " OR COALESCE(rrule, '') <> '' OR COALESCE(rdates, '') NOT IN ('', '[]')))"
           " OR (COALESCE(recurrence_id, '') <> '' AND substr(recurrence_id, 1, 10) BETWEEN ? AND ?))")
    params += [(w1 + timedelta(days=1)).isoformat(), (w0 - timedelta(days=1)).isoformat(),
               (w0 - timedelta(days=_OVERRIDE_PAD_DAYS)).isoformat(), (w1 + timedelta(days=1)).isoformat()]
    if category:
        sql += " AND category = ? AND (visibility <> 'busy' OR user_id = ?)"
        params += [category, user_id]
    rows = await _fetch(db, sql, params, pg_fetch)

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
            if expand and is_recurring(row) and (row.get("id"), d) in replaced:
                continue
            hd, ht = _household(d, str(row.get("start_time") or ""), row)
            if str(row.get("end_time") or "").strip():
                he, het = _household(d + timedelta(days=span), str(row["end_time"]), row, "end_timezone")
            else:                                                   # no end time: it ends on its (household) start day
                he, het = hd + timedelta(days=span), ""
            if hd > w1 or max(he, hd) < w0:
                continue
            event = {**row, "start_date": d.isoformat(),
                     "end_date": (d + timedelta(days=span)).isoformat() if row.get("end_date") else row.get("end_date")} \
                if expand else dict(row)
            all_day = _truthy(row.get("all_day"))
            out.append({"event_id": row.get("id"), "instance_key": instance_key(row.get("id"), hd, "" if all_day else ht),
                        "series_start_date": row.get("start_date"),
                        "date": hd.isoformat(), "start_time": ht, "end_date": he.isoformat(),
                        "end_time": het, "title": row.get("title"), "location": row.get("location"),
                        "category": row.get("category"), "all_day": all_day,
                        "is_recurring": is_recurring(row), "read_only": not is_writable(row),
                        "source": str(row.get("source") or "local"), "source_label": source_label(row),
                        "calendar_id": row.get("calendar_id"), "status": row.get("status") or "confirmed",
                        "event": event})
            if not expand:
                break                                               # stored mode: the row once
    out.sort(key=lambda o: (o["date"], 0 if o["all_day"] else 1, o["start_time"] or "99:99", str(o["title"] or "")))
    return out
