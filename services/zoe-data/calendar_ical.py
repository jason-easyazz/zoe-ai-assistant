"""RFC 5545 iCalendar import / export for the 0052 calendar data model.

Adopted, not rebuilt: the parsing and serialising is the maintained ``icalendar`` library (collective/icalendar,
BSD-2, pure Python; 7.3.0, pinned in CI's validate.yml only until the connectors land - see the doc, section 7). This module only MAPS between its components and Zoe's rows, using
the field table in ``docs/architecture/calendar-data-model.md`` section 3. Nothing here contacts a server: it is
the shape a CalDAV / .ics connector will store through, and the round-trip test (``tests/test_calendar_data_model.py``)
is what proves the table holds an iCalendar event without loss.

Conventions (decided in the doc, section 5):
  * ``start_date`` / ``start_time`` are the wall clock in the event's own ``timezone``; no timezone = floating.
  * An all-day ``end_date`` is INCLUSIVE in Zoe (how people say it); iCalendar's DTEND is exclusive (+1 day).
  * RDATE / EXDATE / RECURRENCE-ID are stored as ISO text: ``YYYY-MM-DD`` (a date), ``...Z`` (an instant, UTC),
    or a naive ``YYYY-MM-DDTHH:MM:SS`` (floating).
  * An import never links an attendee to a person (no inferred person_id); the TEXT it carries (summary,
    description, location, names) is untrusted input and is stored, never executed or prompted with unfenced.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone as dt_timezone
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import calendar_service as cal

PRODID = "-//Zoe//Calendar data model 0052//EN"

_CLASS_TO_VISIBILITY = {"PUBLIC": "family", "PRIVATE": "personal", "CONFIDENTIAL": "busy"}
_VISIBILITY_TO_CLASS = {"family": "PUBLIC", "personal": "PRIVATE", "busy": "CONFIDENTIAL"}


def _ical():
    import icalendar  # deferred: only the import/export path needs it; not installed on the box yet

    return icalendar


# ── value helpers ────────────────────────────────────────────────────────────────────────────────────

def _iso_value(v) -> str:
    """A DATE / DATE-TIME as Zoe stores it in rdates / exdates / recurrence_id."""
    if isinstance(v, datetime):
        if v.tzinfo is None:
            return v.strftime("%Y-%m-%dT%H:%M:%S")
        return cal.utc_iso(v)
    return v.isoformat()


def _from_iso(s: str):
    if len(s) == 10:
        return date.fromisoformat(s)
    if s.endswith("Z"):
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt_timezone.utc)
    return datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")


def _hhmm(t: time) -> str:
    return t.strftime("%H:%M") if t.second == 0 else t.strftime("%H:%M:%S")


def _tz_name(dt: datetime) -> Optional[str]:
    """The IANA zone of an aware DATE-TIME (None for floating). Windows / custom TZIDs icalendar could resolve
    come back as their Olson name; one it could not is pinned to UTC so the instant is still right."""
    if dt.tzinfo is None:
        return None
    key = getattr(dt.tzinfo, "key", None)
    if not key:
        from icalendar.timezone.tzid import tzid_from_dt

        key = tzid_from_dt(dt)
    if not key or key in ("UTC", "Etc/UTC", "Z"):
        return "UTC"
    try:
        ZoneInfo(key)
        return key
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        return "UTC"


def _local(dt: datetime, tz: Optional[str]) -> datetime:
    return dt.astimezone(ZoneInfo(tz)) if tz and dt.tzinfo is not None else dt


def _decoded_list(component, name: str) -> list:
    """Every value of a multi-valued DATE / DATE-TIME property (RDATE, EXDATE), flattened."""
    raw = component.get(name)
    if raw is None:
        return []
    out = []
    for prop in raw if isinstance(raw, list) else [raw]:
        for d in getattr(prop, "dts", []):
            value = d.dt
            if isinstance(value, tuple):                   # an RDATE PERIOD (start, end|duration): its start
                value = value[0]
            if isinstance(value, (date, datetime)):
                out.append(value)
    return out


def _or_default(fn, value, default):
    if value is None or str(value).strip() == "":
        return default
    try:
        return fn(str(value))
    except ValueError:
        return default


def _param(params: dict, name: str, allowed, default: str) -> str:
    """An ATTENDEE parameter inside Zoe's vocabulary, else the default (X- values, IANA-registered extensions)."""
    value = str(params.get(name) or "").strip().lower()
    return value if value in allowed else default


def _address(addr) -> "tuple[Optional[str], dict]":
    value = str(addr)
    email = value[7:] if value.lower().startswith("mailto:") else None
    return (email or None), dict(getattr(addr, "params", {}) or {})


# ── import ───────────────────────────────────────────────────────────────────────────────────────────

def household_tz() -> str:
    """The household zone (``ZOE_TIMEZONE``, as reminders and the digest read it)."""
    import os

    return cal.validate_timezone(os.environ.get("ZOE_TIMEZONE") or "Australia/Perth") or "Australia/Perth"


def parse_ics(data: "bytes | str", *, default_visibility: str = "family", home_tz: Optional[str] = None) -> list:
    """Parse an iCalendar stream into ``[{"event": {...events columns...}, "attendees": [...], "alarms": [...]}]``.

    ``event`` holds the 0001 columns plus the 0052 ones (``external_uid``, ``rrule`` ...); nothing is written.
    VTODO / VJOURNAL / VFREEBUSY are ignored.

    A DATE-TIME given in UTC (``...Z``: an instant with no wall clock of its own, how airlines and many exporters
    write) is stored in the household zone ``home_tz`` (default ``ZOE_TIMEZONE``), so ``start_date`` / ``start_time``
    are the household's wall clock - what every pre-0052 reader assumes. A named zone keeps its own wall clock."""
    ical = _ical()
    home = home_tz or household_tz()
    out = []
    for cal_component in ical.Calendar.from_ical(data, multiple=True):
        for ve in cal_component.walk("VEVENT"):
            if ve.get("DTSTART") is None:      # RFC 5545 allows it only under METHOD; nothing to place on a day
                continue
            out.append(_parse_vevent(ve, default_visibility, home))
    return out


def _parse_vevent(ve, default_visibility: str, home: str) -> dict:
    start = ve.decoded("DTSTART")
    ev: dict = {
        "title": str(ve.get("SUMMARY") or "").strip() or "(no title)",
        "description": str(ve.get("DESCRIPTION")) if ve.get("DESCRIPTION") is not None else None,
        "location": str(ve.get("LOCATION")) if ve.get("LOCATION") is not None else None,
        "external_uid": str(ve.get("UID")) if ve.get("UID") is not None else None,
        "sequence": int(ve.get("SEQUENCE") or 0),
        # a value outside the vocabulary (X- extensions, STATUS:NEEDS-ACTION, a webcal:/mailto: URL) falls back to
        # the default: one odd property never aborts the import of a whole file
        "status": _or_default(cal.normalize_status, ve.get("STATUS"), "confirmed"),
        "transparency": _or_default(cal.normalize_transparency, ve.get("TRANSP"), "opaque"),
        "url": _or_default(cal.validate_link, ve.get("URL"), None),
        "visibility": _CLASS_TO_VISIBILITY.get(str(ve.get("CLASS") or "").upper(), default_visibility),
        "category": "general",
    }
    cats = ve.get("CATEGORIES")
    if cats is not None:
        first = getattr(cats[0] if isinstance(cats, list) else cats, "cats", None)
        if first:
            ev["category"] = str(first[0]).strip().lower() or "general"
    conf = ve.get("CONFERENCE")
    if conf is not None:
        conf = conf[0] if isinstance(conf, list) else conf
        try:
            ev["conference_url"] = cal.validate_link(str(conf), cal.CONFERENCE_SCHEMES)
        except ValueError:
            ev["conference_url"] = None

    if isinstance(start, datetime):
        tz = _tz_name(start)
        tz = home if tz == "UTC" else tz
        s_local = _local(start, tz)
        ev.update({"all_day": False, "timezone": tz, "start_date": s_local.date().isoformat(),
                   "start_time": _hhmm(s_local.time())})
        end = ve.decoded("DTEND") if ve.get("DTEND") is not None else None
        if isinstance(end, datetime) and (end.tzinfo is None) != (start.tzinfo is None):
            # a floating end with a zoned start (or the reverse): read the naive side in the other's zone
            end = end.replace(tzinfo=start.tzinfo) if end.tzinfo is None else end.astimezone(start.tzinfo).replace(tzinfo=None)
        elif isinstance(end, date) and not isinstance(end, datetime):
            end = None                                       # a DATE end on a DATE-TIME start: invalid, ignore it
        if end is None and ve.get("DURATION") is not None:
            end = start + ve.decoded("DURATION")
        if isinstance(end, datetime):
            etz = _tz_name(end) if end.tzinfo is not None else None
            etz = home if etz == "UTC" else etz
            e_local = _local(end, etz)
            ev.update({"end_date": e_local.date().isoformat(), "end_time": _hhmm(e_local.time())})
            if etz and etz != tz:
                ev["end_timezone"] = etz
            ev["duration"] = int((end - start).total_seconds() // 60)
    else:
        ev.update({"all_day": True, "timezone": None, "start_date": start.isoformat(), "start_time": None})
        end = ve.decoded("DTEND") if ve.get("DTEND") is not None else None
        if end is None and ve.get("DURATION") is not None:
            end = start + ve.decoded("DURATION")
        if isinstance(end, date) and not isinstance(end, datetime) and end - start > timedelta(days=1):
            ev["end_date"] = (end - timedelta(days=1)).isoformat()   # DTEND is exclusive; Zoe's all-day end is inclusive

    if ve.get("RRULE") is not None:
        rr = ve.get("RRULE")
        ev["rrule"] = (rr[0] if isinstance(rr, list) else rr).to_ical().decode()
    rdates = [_iso_value(v) for v in _decoded_list(ve, "RDATE")]
    exdates = [_iso_value(v) for v in _decoded_list(ve, "EXDATE")]
    ev["rdates"] = rdates or None
    ev["exdates"] = exdates or None
    if ve.get("RECURRENCE-ID") is not None:
        ev["recurrence_id"] = _iso_value(ve.decoded("RECURRENCE-ID"))
    ev["start_at"], ev["end_at"] = cal.derive_instants(ev)

    attendees: list = []
    by_email: dict = {}
    org = ve.get("ORGANIZER")
    if org is not None:
        email, params = _address(org)
        a = cal.normalize_attendee({"display_name": params.get("CN"), "email": email, "is_organizer": True,
                                    "role": "non-participant"} if (email or params.get("CN")) else
                                   {"display_name": "Organizer", "is_organizer": True, "role": "non-participant"}, 0)
        attendees.append(a)
        if email:
            by_email[email.lower()] = a
    raw_att = ve.get("ATTENDEE")
    for addr in (raw_att if isinstance(raw_att, list) else [raw_att] if raw_att is not None else []):
        email, params = _address(addr)
        fields = {
            "display_name": params.get("CN"),
            "email": email,
            "role": _param(params, "ROLE", cal.ATTENDEE_ROLES, "req-participant"),
            "partstat": _param(params, "PARTSTAT", cal.PARTSTATS, "needs-action"),
            "kind": _param(params, "CUTYPE", cal.ATTENDEE_KINDS, "individual"),
            "rsvp": str(params.get("RSVP") or "").upper() == "TRUE",
        }
        if email and email.lower() in by_email:            # the organizer also attends: one row
            org_row = by_email[email.lower()]
            merged = cal.normalize_attendee({**fields, "is_organizer": True,
                                             "display_name": fields["display_name"] or org_row["display_name"]},
                                            org_row["position"])
            org_row.update(merged)
            continue
        if not (fields["display_name"] or email):
            fields["display_name"] = "Guest"
        attendees.append(cal.normalize_attendee(fields, len(attendees)))

    alarms = []
    for va in ve.walk("VALARM"):
        trig = va.decoded("TRIGGER") if va.get("TRIGGER") is not None else None
        related = str((va.get("TRIGGER").params or {}).get("RELATED") or "START").lower() if trig is not None else "start"
        alarm = {"action": str(va.get("ACTION") or "DISPLAY").lower(), "related": related,
                 "description": str(va.get("DESCRIPTION")) if va.get("DESCRIPTION") is not None else None,
                 "offset_minutes": None, "trigger_at": None}
        if isinstance(trig, timedelta):
            alarm["offset_minutes"] = int(-trig.total_seconds() // 60)
        elif isinstance(trig, datetime):
            alarm["trigger_at"] = cal.utc_iso(trig) if trig.tzinfo else _iso_value(trig)
        alarms.append(alarm)

    return {"event": ev, "attendees": attendees, "alarms": alarms}


async def store_parsed(db, parsed: list, *, user_id: str, calendar_id: Optional[str] = None, source: str = "ics",
                       read_only: bool = False) -> list:
    """Write ``parse_ics`` output as ``user_id``'s events (masters first, then overridden instances linked to their
    master by ``series_id``). Returns the new event ids in input order. The caller commits."""
    import json

    from calendar_service import create_event_record

    if source not in cal.SOURCES:
        raise ValueError(f"source must be one of: {', '.join(cal.SOURCES)}")
    order = sorted(range(len(parsed)), key=lambda i: parsed[i]["event"].get("recurrence_id") is not None)
    ids: dict = {}
    masters: dict = {}
    base_cols = ("title", "start_date", "start_time", "end_date", "end_time", "duration", "category", "location",
                 "all_day", "visibility")
    for i in order:
        ev = dict(parsed[i]["event"])
        extra = {k: ev[k] for k in cal.EXTENDED_EVENT_COLUMNS if ev.get(k) is not None}
        for key in ("rdates", "exdates"):
            if key in extra:
                extra[key] = json.dumps(extra[key])
        extra.update({"source": source, "read_only": 1 if read_only else 0})
        if calendar_id:
            extra["calendar_id"] = calendar_id
        if ev.get("recurrence_id") and ev.get("external_uid") in masters:
            extra["series_id"] = masters[ev["external_uid"]]
        rec = await create_event_record(db, user_id=user_id, extra=extra,
                                        **{k: ev.get(k) for k in base_cols if ev.get(k) is not None})
        ids[i] = rec["id"]
        if not ev.get("recurrence_id") and ev.get("external_uid"):
            masters[ev["external_uid"]] = rec["id"]
        if parsed[i]["attendees"]:
            await cal.replace_attendees(db, rec["id"], user_id, parsed[i]["attendees"])
        if parsed[i]["alarms"]:
            await cal.replace_alarms(db, rec["id"], user_id, parsed[i]["alarms"])
    return [ids[i] for i in range(len(parsed))]


# ── export ───────────────────────────────────────────────────────────────────────────────────────────

def _dt_value(d: Optional[str], t: Optional[str], tz: Optional[str]):
    day = date.fromisoformat(d)
    if not t:
        return day
    parsed = cal.parse_time(t)
    naive = datetime.combine(day, parsed)
    return naive.replace(tzinfo=ZoneInfo(tz)) if tz else naive


def to_ics(events: list, *, now: Optional[datetime] = None) -> bytes:
    """Serialise API-shaped events (``routers/calendar.py`` output: the row plus ``attendees`` / ``alarms``) as one
    VCALENDAR. A busy-only projection exports as a busy block (CLASS:CONFIDENTIAL, SUMMARY "Busy")."""
    ical = _ical()
    calendar = ical.Calendar()
    calendar.add("prodid", PRODID)
    calendar.add("version", "2.0")
    stamp = now or datetime.now(dt_timezone.utc)
    for e in events:
        calendar.add_component(_vevent(ical, e, stamp))
    calendar.add_missing_timezones()
    return calendar.to_ical()


def _vevent(ical, e: dict, stamp: datetime):
    ve = ical.Event()
    ve.add("uid", e.get("external_uid") or f"{e['id']}@zoe.local")
    ve.add("dtstamp", stamp)
    ve.add("summary", e.get("title") or "")
    tz = e.get("timezone")
    if e.get("all_day") or not e.get("start_time"):
        ve.add("dtstart", date.fromisoformat(e["start_date"]))
        last = date.fromisoformat(e.get("end_date") or e["start_date"])
        ve.add("dtend", last + timedelta(days=1))           # exclusive
    else:
        ve.add("dtstart", _dt_value(e["start_date"], e["start_time"], tz))
        if e.get("end_time"):
            ve.add("dtend", _dt_value(e.get("end_date") or e["start_date"], e["end_time"], e.get("end_timezone") or tz))
        elif e.get("duration"):
            ve.add("duration", timedelta(minutes=int(e["duration"])))
    for key, prop in (("description", "description"), ("location", "location"), ("url", "url")):
        if e.get(key):
            ve.add(prop, e[key])
    if e.get("category") and e.get("category") != "general":
        ve.add("categories", [e["category"]])
    if e.get("conference_url"):
        ve.add("conference", e["conference_url"], parameters={"VALUE": "URI"})
    ve.add("status", (e.get("status") or "confirmed").upper())
    ve.add("transp", (e.get("transparency") or "opaque").upper())
    ve.add("class", _VISIBILITY_TO_CLASS.get(e.get("visibility") or "family", "PUBLIC"))
    ve.add("sequence", int(e.get("sequence") or 0))
    if e.get("rrule"):
        ve.add("rrule", ical.vRecur.from_ical(e["rrule"]))
    for key, prop in (("rdates", "rdate"), ("exdates", "exdate")):
        values = e.get(key) or []
        if values:
            ve.add(prop, [_from_iso(v) for v in values])
    if e.get("recurrence_id"):
        ve.add("recurrence-id", _from_iso(e["recurrence_id"]))

    for i, a in enumerate(e.get("attendees") or []):
        address = f"mailto:{a['email']}" if a.get("email") else f"urn:x-zoe:attendee:{i}"
        params = {}
        if a.get("display_name"):
            params["CN"] = a["display_name"]
        if a.get("is_organizer"):
            ve.add("organizer", ical.vCalAddress(address), parameters=dict(params))
            if a.get("role") == "non-participant":
                continue
        params.update({"ROLE": (a.get("role") or "req-participant").upper(),
                       "PARTSTAT": (a.get("partstat") or "needs-action").upper(),
                       "CUTYPE": (a.get("kind") or "individual").upper()})
        if a.get("rsvp"):
            params["RSVP"] = "TRUE"
        ve.add("attendee", ical.vCalAddress(address), parameters=params)

    for al in e.get("alarms") or []:
        va = ical.Alarm()
        va.add("action", (al.get("action") or "display").upper())
        if al.get("trigger_at"):
            va.add("trigger", _from_iso(al["trigger_at"]))
        else:
            params = {"RELATED": "END"} if (al.get("related") or "start") == "end" else {}
            va.add("trigger", timedelta(minutes=-int(al.get("offset_minutes") or 0)), parameters=params)
        va.add("description", al.get("description") or e.get("title") or "Reminder")
        ve.add_component(va)
    return ve
