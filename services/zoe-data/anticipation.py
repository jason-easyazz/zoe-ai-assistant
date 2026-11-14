"""anticipation - Zoe thinks ahead about the member's calendar (phase 1, flag-dark ``ZOE_ANTICIPATION=off|shadow|on``).

Design: ``docs/architecture/anticipation-loop.md``. Owner direction 2026-11-06: "I see that you have added a flight to your calendar,
should I wake you up earlier?" - offline, private, never unprompted.

THE LOOP. Night (``memory_digest`` phase 1.65, the dreaming cycle): ``generate_for_user`` reads the member's own events for the next
``HORIZON_DAYS`` and, by CODE only (keywords and clock maths, no model, no network), makes at most three kinds of offer per event:
``wake`` (a one-off earlier alarm), ``leave`` (a leave-by reminder) and ``packing`` (a private packing list). Events are read through
``calendar_read.events_in_window`` (own events only): cancelled, EXDATE and overridden instances are gone, and a zoned event is seen at
its HOUSEHOLD date and time. A REPEATING event stands for its NEXT occurrence in the horizon that has not begun (owner decision
2026-11-13); a repeat rule that is not understood gets no offer. Offers are keyed ``<kind>:<event id>:<household date>[T<HH:MM>]``, so
each occurrence is its own offer and a night re-run never duplicates one. Day: on the member's
own chat brain turn, ``pick_for_turn`` returns at most ONE timely offer as a short "By the way - ..." line (and a Yes/No card).
Answer: a short yes/no that answers the line Zoe just said (``match_reply``) or a card tap (``execute_if_ours`` / ``on_dismissed``)
runs ``answer``: yes = the real tool (``alarms.create_alarm`` / ``reminder_service.create_reminder_record`` /
``list_service.create_list_record``), and the reply is composed from what the tool returned; no = a 24 h cooldown. Every answer is
an outcome (acted / dismissed / ignored) kept on the row and sent to ``proactive.outcome_edges`` when that module exists.

STORE. Rows of ``pending_suggestions`` with ``session_id = action_type = 'anticipation'`` (no migration; the session-scoped offer
readers never select them). ``description`` = the event title (what a forget matches), ``list_type`` = offer kind, ``when_hint`` =
expiry (UTC ISO), ``amount_hint`` = the dedupe key, ``pre_filled_slots`` = the facts. ``resolved``: see ``OPEN`` .. ``CLAIMED``.

PRIVACY. Only the event's owner, only a real member account, only a chat session (voice surfacing is phase 1b). A member who is not
positively an adult gets no alarm offers. Logs carry counts and kinds only - never a title, a place or a line. ``off`` = every entry
point returns before any I/O.
"""
from __future__ import annotations

import contextlib
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

ENV = "ZOE_ANTICIPATION"
SESSION = "anticipation"            # pending_suggestions.session_id sentinel
ACTION = "anticipation"             # pending_suggestions.action_type
HORIZON_DAYS = 14
KINDS = ("wake", "leave", "packing")
LEAVE_LEAD_MIN = {"flight": 150, "train": 45, "trip": 60, "appointment": 30}
PREP_MIN = 60                       # wake this long before the leave-by time
WAKE_MARGIN_MIN = 15                # only offer when that is at least this much earlier than the usual alarm
EARLIEST_WAKE_MIN = 3 * 60          # never propose an alarm before 03:00
SURFACE_FROM = {"wake": timedelta(days=3), "leave": timedelta(days=3), "packing": timedelta(days=7)}
COOLDOWN = timedelta(hours=24)      # after a no: no anticipation offer at all
PER_DAY = 2                         # different offers surfaced per local day
MAX_SURFACED = 2                    # the same offer asked at most twice, then it is "ignored"
ASKED_TTL_S = 900.0                 # a yes/no binds to an offer made this recently
PICK_BUDGET_S = 1.5
PACKING_BASICS = ("Phone charger", "Toiletries", "Medications", "Clothes")
PACKING_TRAVEL_DOCS = ("ID or passport",)

# resolved states (pending_suggestions.resolved; every other reader only ever selects resolved = 0)
OPEN, DONE, DECLINED, IGNORED, WITHDRAWN, UNAVAILABLE, CLAIMED = 0, 1, 2, 3, 4, 5, 6

_TS = "%Y-%m-%dT%H:%M:%SZ"
_TITLE_MAX = 200

_FLIGHT_RX = re.compile(r"\b(?:flights?|fly|flying|airport|plane|boarding)\b", re.IGNORECASE)
_FLIGHT_NO_RX = re.compile(r"\b[A-Z]{2}\d{2,4}\b")          # on the title as written: "QF642"
_TRAIN_RX = re.compile(r"\b(?:train|rail|railway)\b", re.IGNORECASE)
_TRIP_RX = re.compile(r"\b(?:trip|holidays?|vacation|getaway|travel(?:l?ing)?)\b", re.IGNORECASE)
_APPT_RX = re.compile(r"\b(?:doctors?|dr|gp|dentist|dental|physio(?:therapist)?|appointment|appt|clinic|hospital|specialist|"
                      r"optometrist|orthodontist|vet|meeting|interview)\b", re.IGNORECASE)
_HHMM_RX = re.compile(r"^\s*(\d{1,2}):(\d{2})")
_LABEL = {"flight": "flight", "train": "train", "trip": "trip", "appointment": "appointment"}


def mode() -> str:
    """``off`` (default) | ``shadow`` | ``on``, read per call. Anything else is off."""
    from typed_env import env_str

    m = (env_str("ZOE_ANTICIPATION", "off") or "off").strip().lower()  # literal on purpose: tools/audit/flag_inventory.py greps it
    return m if m in ("off", "shadow", "on") else "off"


# -- pure: events -> offers ---------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Event:
    id: str
    user_id: str
    title: str
    start_date: str
    start_time: str = ""
    end_date: str = ""
    location: str = ""
    all_day: bool = False


@dataclass
class Offer:
    kind: str
    event_id: str
    key: str
    title: str
    location: str
    expires_at: datetime
    surface_from: datetime
    slots: dict = field(default_factory=dict)


def _tz():
    from time_utils import zoe_timezone

    return zoe_timezone()


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime(_TS)


def _parse_iso(value: Any) -> Optional[datetime]:
    try:
        return datetime.strptime(str(value or ""), _TS).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _hhmm(raw: Any) -> Optional[int]:
    """Minutes after midnight from "HH:MM[:SS]", or None."""
    m = _HHMM_RX.match(str(raw or ""))
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    return h * 60 + mi if h < 24 and mi < 60 else None


def _fmt_hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _date(raw: Any) -> Optional[date]:
    try:
        return date.fromisoformat(str(raw or "")[:10])
    except ValueError:
        return None


def classify(ev: Event) -> Optional[str]:
    """``flight`` | ``train`` | ``trip`` | ``appointment`` | None, from the title and location words only."""
    text = f"{ev.title} {ev.location}"
    if _FLIGHT_RX.search(text) or _FLIGHT_NO_RX.search(ev.title or ""):
        return "flight"
    if _TRAIN_RX.search(text):
        return "train"
    if _TRIP_RX.search(ev.title or ""):
        return "trip"
    if _APPT_RX.search(ev.title or "") and (ev.location or "").strip():
        return "appointment"
    return None


def _at(d: date, minutes: int) -> datetime:
    return datetime(d.year, d.month, d.day, minutes // 60, minutes % 60, tzinfo=_tz())


def candidates(events: list[Event], *, now: datetime, usual_wake: Callable[[date], Optional[str]], adult: bool) -> list[Offer]:
    """Every offer the events support right now. Pure (``usual_wake(day)`` -> "HH:MM" of the member's usual alarm that day)."""
    out: list[Offer] = []
    today = now.astimezone(_tz()).date()
    for ev in events:
        kind = classify(ev)
        start_d = _date(ev.start_date)
        if kind is None or start_d is None:
            continue
        end_d = _date(ev.end_date)
        if not (today <= start_d <= today + timedelta(days=HORIZON_DAYS)):
            continue
        start_m = None if ev.all_day else _hhmm(ev.start_time)
        start_dt = _at(start_d, start_m) if start_m is not None else _at(start_d, 0)
        base = {"event_id": ev.id, "event_kind": kind, "label": _LABEL[kind], "location": (ev.location or "")[:_TITLE_MAX],
                "start_date": start_d.isoformat(),
                "start_time": _fmt_hhmm(start_m) if start_m is not None else ""}
        if start_m is not None:
            leave_m = start_m - LEAVE_LEAD_MIN[kind]
            if leave_m >= 0:
                leave_dt = _at(start_d, leave_m)
                if leave_dt > now:
                    out.append(Offer("leave", ev.id, f"leave:{ev.id}:{start_d.isoformat()}T{_fmt_hhmm(start_m)}", ev.title,
                                     ev.location, leave_dt, start_dt - SURFACE_FROM["leave"],
                                     {**base, "leave_time": _fmt_hhmm(leave_m)}))
                wake_m = (leave_m - PREP_MIN) // 5 * 5
                usual = _hhmm(usual_wake(start_d)) if adult else None
                if usual is not None and wake_m >= EARLIEST_WAKE_MIN and wake_m <= usual - WAKE_MARGIN_MIN:
                    wake_dt = _at(start_d, wake_m)
                    if wake_dt > now:
                        out.append(Offer("wake", ev.id, f"wake:{ev.id}:{start_d.isoformat()}T{_fmt_hhmm(start_m)}", ev.title,
                                         ev.location, wake_dt, start_dt - SURFACE_FROM["wake"],
                                         {**base, "wake_time": _fmt_hhmm(wake_m), "usual_time": _fmt_hhmm(usual)}))
        if kind in ("flight", "train", "trip") and end_d is not None and end_d > start_d and start_dt > now:
            out.append(Offer("packing", ev.id, f"packing:{ev.id}:{start_d.isoformat()}", ev.title, ev.location, start_dt,
                             start_dt - SURFACE_FROM["packing"], {**base, "end_date": end_d.isoformat()}))
    return out


def _clock(hhmm: str) -> str:
    m = _hhmm(hhmm) or 0
    return f"{(m // 60) % 12 or 12}:{m % 60:02d} {'am' if m < 720 else 'pm'}"


def _day(d: date, today: date) -> str:
    if d == today:
        return "today"
    if d == today + timedelta(days=1):
        return "tomorrow"
    if d < today + timedelta(days=7):
        return f"on {d.strftime('%A')}"
    return f"on {d.strftime('%A')} {d.day} {d.strftime('%B')}"


def phrase(kind: str, slots: dict, now: datetime) -> str:
    """The offer line, composed at the moment it is said (so "tomorrow" is never stale). Pure; names no title and no place."""
    today = now.astimezone(_tz()).date()
    d = _date(slots.get("start_date")) or today
    label = str(slots.get("label") or "event")
    when = _day(d, today)
    if kind == "wake":
        return (f"Your {label} {when} is at {_clock(slots['start_time'])}, and your alarm is usually {_clock(slots['usual_time'])}. "
                f"Want me to set a one-off alarm for {_clock(slots['wake_time'])} that morning?")
    if kind == "leave":
        return f"Your {label} {when} is at {_clock(slots['start_time'])}. Want a reminder to leave by {_clock(slots['leave_time'])}?"
    end = _date(slots.get("end_date")) or d
    return f"You're away {when} until {_day(end, today).removeprefix('on ')} for your {label}. Want me to start a private packing list?"


# -- who may hear it -----------------------------------------------------------------------------------------------------------
def is_member(uid: str) -> bool:
    """A real member account: not a guest, a panel principal, an unidentified voice or a synthetic id. Unknown is nobody."""
    try:
        import identity_facts
        from user_filters import is_synthetic_user, is_unidentified

        uid = (uid or "").strip()
        return (bool(uid) and not uid.startswith("panel") and not is_unidentified(uid) and not is_synthetic_user(uid)
                and identity_facts._is_account_id(uid))
    except Exception:  # noqa: BLE001
        return False


def _chat_session(session_id: str) -> bool:
    sid = str(session_id or "")
    # a voice turn (phase 1b) and any shared-screen session id (panel / kiosk / touch) never carry an offer, whoever is signed in
    return bool(sid) and sid != SESSION and not sid.lower().startswith(("voice", "panel", "kiosk", "touch"))


async def _is_adult(uid: str, db: Any = None) -> bool:
    try:
        import manner_block

        return await manner_block.is_adult(uid, db=db)
    except Exception:  # noqa: BLE001 - not knowing means not an adult
        return False


# -- db plumbing ---------------------------------------------------------------------------------------------------------------
@contextlib.asynccontextmanager
async def _conn(db: Any = None):
    if db is not None:
        yield db
        return
    from db_pool import get_db_ctx

    async with get_db_ctx() as conn:
        yield conn


async def _all(db: Any, sql: str, params: tuple) -> list:
    async with db.execute(sql, params) as cur:
        return list(await cur.fetchall())


def _missing(exc: BaseException) -> bool:
    s = str(exc)
    return "does not exist" in s or "no such table" in s


async def _occurrences(db: Any, uid: str, first: date, last: date) -> list[dict]:
    """The member's OWN occurrences over household dates ``[first, last]`` (the shared read path; never another member's)."""
    from calendar_read import events_in_window

    return await events_in_window(db, uid, first, last, own_only=True)


def _as_event(o: dict) -> Event:
    """One occurrence as an Event: its HOUSEHOLD date and start; an end date only when the row has one."""
    has_end = bool(str((o.get("event") or {}).get("end_date") or "").strip())
    return Event(str(o["event_id"]), str((o.get("event") or {}).get("user_id") or ""), str(o.get("title") or ""), str(o["date"]),
                 "" if o.get("all_day") else str(o.get("start_time") or ""), str(o["end_date"]) if has_end else "",
                 str(o.get("location") or ""), bool(o.get("all_day")))


async def _events(db: Any, uid: str, now: datetime) -> list[Event]:
    """One Event per event in the horizon: a one-off as itself, a repeating event as its NEXT occurrence that has not begun
    (today's counts only while its start is ahead of ``now``); a repeat rule that is not understood gives none."""
    from calendar_read import repeat_understood

    today = now.astimezone(_tz()).date()
    out: list[Event] = []
    seen: set = set()
    for o in await _occurrences(db, uid, today, today + timedelta(days=HORIZON_DAYS)):
        if o["is_recurring"]:
            ev = _as_event(o)
            start_m = None if ev.all_day else _hhmm(ev.start_time)
            if o["event_id"] in seen or not repeat_understood(o["event"]) or _at(_date(ev.start_date), start_m or 0) <= now:
                continue
            seen.add(o["event_id"])
        out.append(_as_event(o))
    return out


async def _usual_alarms(db: Any, uid: str) -> list[tuple[str, str]]:
    try:
        rows = await _all(db, "SELECT time_hhmm, repeat_rule FROM alarms WHERE user_id = ? AND enabled = 1 AND repeat_rule <> 'once'",
                          (uid,))
    except Exception as exc:  # noqa: BLE001 - no alarms table (migration not run) = no usual wake time = no wake offers
        if _missing(exc):
            return []
        raise
    return [(str(r[0]), str(r[1])) for r in rows]


def usual_wake_from(alarm_rows: list[tuple[str, str]]) -> Callable[[date], Optional[str]]:
    """The earliest repeating alarm that rings on a given day (``alarms.repeat_days``), or None."""
    import alarms

    def pick(d: date) -> Optional[str]:
        hits = sorted(t for t, rule in alarm_rows if d.isoweekday() in alarms.repeat_days(rule))
        return hits[0] if hits else None

    return pick


async def _rows(db: Any, uid: str) -> list[dict]:
    rows = await _all(db, "SELECT id, description, list_type, when_hint, amount_hint, offer_phrase, pre_filled_slots, resolved,"
                          " created_at FROM pending_suggestions WHERE user_id = ? AND action_type = ?", (uid, ACTION))
    out = []
    for r in rows:
        try:
            slots = json.loads(r[6] or "{}")
        except (TypeError, ValueError):
            slots = {}
        out.append({"id": str(r[0]), "title": str(r[1] or ""), "kind": str(r[2] or ""), "expires": _parse_iso(r[3]),
                    "key": str(r[4] or ""), "line": str(r[5] or ""), "slots": slots if isinstance(slots, dict) else {},
                    "resolved": int(r[7] or 0), "created_at": str(r[8] or "")})
    return out


async def _set(db: Any, row_id: str, uid: str, *, resolved: int, slots: dict, only_from: Optional[int] = None) -> bool:
    sql = "UPDATE pending_suggestions SET resolved = ?, pre_filled_slots = ?, turns_elapsed = ? WHERE id = ? AND user_id = ?"
    params: tuple = (resolved, json.dumps(slots, sort_keys=True), int(slots.get("surfaced_n") or 0), row_id, uid)
    if only_from is not None:
        sql += " AND resolved = ?"
        params += (only_from,)
    async with db.execute(sql, params) as cur:
        n = int(getattr(cur, "rowcount", 1) or 0)
    await db.commit()
    return n > 0


def _local_day(now: datetime) -> str:
    return now.astimezone(_tz()).date().isoformat()


async def _offered(db: Any, uid: str, slots: dict) -> Optional[Event]:
    """The offer's occurrence if it still exists exactly as offered (same event, household day and start), else None: a delete,
    a cancel, an EXDATE, an override that moved it or a re-time withdraws the offer."""
    d = _date(slots.get("start_date"))
    if d is None:
        return None
    for o in await _occurrences(db, uid, d, d):
        ev = _as_event(o)
        start_m = None if ev.all_day else _hhmm(ev.start_time)
        if (ev.id == str(slots.get("event_id") or "") and ev.start_date == d.isoformat()
                and (_fmt_hhmm(start_m) if start_m is not None else "") == str(slots.get("start_time") or "")):
            return ev
    return None


# -- outcome --------------------------------------------------------------------------------------------------------------------
async def _edge(db: Any, uid: str, row_id: str, kind: str, outcome: Optional[str], now: datetime, day: Optional[str] = None) -> None:
    """Open (``outcome`` None) or decide the outcome edge. Defensive: the edge module may not exist yet; it never breaks a turn."""
    try:
        from proactive import outcome_edges  # type: ignore[attr-defined]
    except ImportError:
        return
    try:
        ref = f"anticipation:{row_id}"
        if outcome is None:
            await outcome_edges.open_edge(db, user_id=uid, source_ref=ref, kind=f"anticipation_{kind}", touch_point="raise", now=now)
        else:
            source = "tap" if outcome in ("acted", "dismissed") else "ledger_sweep"
            await outcome_edges.set_outcome(db, user_id=uid, source_ref=ref, outcome=outcome, source=source, now=now, day=day)
    except Exception as exc:  # noqa: BLE001
        logger.debug("anticipation: outcome edge skipped (%s)", type(exc).__name__)


def _log_outcome(uid: str, kind: str, outcome: str) -> None:
    logger.info("ANTICIPATION_OUTCOME user=%s kind=%s outcome=%s", uid, kind, outcome)


# -- night: generate -------------------------------------------------------------------------------------------------------------
async def generate_for_user(user_id: str, *, now: Optional[datetime] = None, db: Any = None) -> Optional[dict]:
    """The night pass for one member. ``off`` = None with no I/O; ``shadow`` = counts logged, nothing stored; ``on`` = new offers
    stored (deduped per event and start), offers whose event is gone, moved or past withdrawn. Never raises."""
    m = mode()
    if m == "off":
        return None
    uid = (user_id or "").strip()
    if not is_member(uid):
        return None
    now = now or datetime.now(timezone.utc)
    counts = {"events": 0, "wake": 0, "leave": 0, "packing": 0, "stored": 0, "withdrawn": 0}
    try:
        async with _conn(db) as conn:
            events = await _events(conn, uid, now)
            offers = candidates(events, now=now, usual_wake=usual_wake_from(await _usual_alarms(conn, uid)),
                                adult=await _is_adult(uid, db=conn))
            counts["events"] = len(events)
            for o in offers:
                counts[o.kind] += 1
            if m == "on":
                existing = await _rows(conn, uid)
                for r in existing:
                    if r["resolved"] != OPEN:
                        continue
                    if await _offered(conn, uid, r["slots"]) is None or (r["expires"] is not None and r["expires"] <= now):
                        await _set(conn, r["id"], uid, resolved=WITHDRAWN, slots={**r["slots"], "outcome": "withdrawn"}, only_from=OPEN)
                        counts["withdrawn"] += 1
                keys = {r["key"] for r in existing}
                for o in offers:
                    if o.key in keys:
                        continue
                    slots = {**o.slots, "surface_from": _iso(o.surface_from), "surfaced_n": 0}
                    await conn.execute(
                        "INSERT INTO pending_suggestions (id, user_id, session_id, action_type, description, list_type, when_hint,"
                        " amount_hint, offer_phrase, pre_filled_slots, created_at, turns_elapsed, expire_after_turns, resolved)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 0)",
                        (str(uuid.uuid4()), uid, SESSION, ACTION, (o.title or "")[:_TITLE_MAX], o.kind, _iso(o.expires_at), o.key,
                         phrase(o.kind, o.slots, now), json.dumps(slots, sort_keys=True), _iso(now), MAX_SURFACED))
                    keys.add(o.key)
                    counts["stored"] += 1
                await conn.commit()
    except Exception as exc:  # noqa: BLE001 - the night pass must never break the digest
        logger.warning("ANTICIPATION mode=%s user=%s failed: %s", m, uid, type(exc).__name__)
        return {"status": "error", "mode": m}
    logger.info("ANTICIPATION mode=%s user=%s events=%d wake=%d leave=%d packing=%d stored=%d withdrawn=%d", m, uid, counts["events"],
                counts["wake"], counts["leave"], counts["packing"], counts["stored"], counts["withdrawn"])
    return {"status": "ok", "mode": m, **counts}


# -- day: surface -----------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Surfaced:
    id: str
    kind: str
    text: str           # the whole appended line: "By the way - ..."
    question: str       # the offer itself, what a yes/no must answer


_ASKED: dict[str, dict] = {}        # member -> {"id", "question", "at"}; in-process (zoe-data is one uvicorn worker)


def _clock_s() -> float:
    return time.monotonic()


async def pick_for_turn(user_id: str, session_id: str, *, now: Optional[datetime] = None, db: Any = None) -> Optional[Surfaced]:
    """ONE offer for this member's chat turn, or None. Marks it surfaced. ``off``/``shadow`` = None with no I/O. Never raises."""
    if mode() != "on":
        return None
    uid = (user_id or "").strip()
    if not is_member(uid) or not _chat_session(session_id):
        return None
    now = now or datetime.now(timezone.utc)
    day = _local_day(now)
    try:
        async with _conn(db) as conn:
            rows = await _rows(conn, uid)
            for r in rows:
                answered = _parse_iso(r["slots"].get("answered_at"))
                if r["resolved"] == DECLINED and answered is not None and now - answered < COOLDOWN:
                    logger.info("ANTICIPATION_SURFACE user=%s none reason=cooldown", uid)
                    return None
            if sum(1 for r in rows if r["slots"].get("last_day") == day) >= PER_DAY:
                return None
            open_rows = sorted((r for r in rows if r["resolved"] == OPEN),
                               key=lambda r: (r["expires"] or now, KINDS.index(r["kind"]) if r["kind"] in KINDS else 9))
            for r in open_rows:
                s = r["slots"]
                start = _parse_iso(s.get("surface_from"))
                if r["expires"] is None or r["expires"] <= now:
                    await _set(conn, r["id"], uid, resolved=WITHDRAWN, slots={**s, "outcome": "expired"}, only_from=OPEN)
                    continue
                if start is not None and now < start:
                    continue
                if s.get("last_day") == day or s.get("last_session") == str(session_id):
                    continue
                if int(s.get("surfaced_n") or 0) >= MAX_SURFACED:
                    await _set(conn, r["id"], uid, resolved=IGNORED, slots={**s, "outcome": "ignored", "answered_at": _iso(now)},
                               only_from=OPEN)
                    await _edge(conn, uid, r["id"], r["kind"], "ignored", now, day=s.get("last_day"))
                    _log_outcome(uid, r["kind"], "ignored")
                    continue
                if await _offered(conn, uid, s) is None:
                    await _set(conn, r["id"], uid, resolved=WITHDRAWN, slots={**s, "outcome": "withdrawn"}, only_from=OPEN)
                    continue
                question = phrase(r["kind"], s, now)
                s2 = {**s, "surfaced_n": int(s.get("surfaced_n") or 0) + 1, "last_day": day, "last_session": str(session_id),
                      "last_surfaced_at": _iso(now)}
                if not await _set(conn, r["id"], uid, resolved=OPEN, slots=s2, only_from=OPEN):
                    continue
                await _edge(conn, uid, r["id"], r["kind"], None, now)
                _ASKED[uid] = {"id": r["id"], "question": question, "at": _clock_s()}
                logger.info("ANTICIPATION_SURFACE user=%s kind=%s n=%d", uid, r["kind"], s2["surfaced_n"])
                return Surfaced(r["id"], r["kind"], f"By the way - {question}", question)
    except Exception as exc:  # noqa: BLE001 - an offer is optional; the turn is not
        logger.warning("ANTICIPATION_SURFACE user=%s failed: %s", uid, type(exc).__name__)
    return None


def card(s: Surfaced) -> dict:
    """The Yes/No card for a surfaced offer, on the existing pending-suggestion accept/dismiss routes."""
    yes = {"wake": "Set the alarm", "leave": "Remind me", "packing": "Start the list"}.get(s.kind, "Yes")
    return {"type": "action_card", "title": s.question,
            "actions": [{"label": yes, "action": "pending_suggestion_accept", "suggestion_id": s.id},
                        {"label": "No thanks", "action": "pending_suggestion_dismiss", "suggestion_id": s.id}]}


async def match_reply(user_id: str, text: str, previous_assistant: Callable[[str], Awaitable[Optional[str]]]) -> Optional[tuple[str, bool]]:
    """``(suggestion_id, accept)`` when a SHORT yes/no answers the offer Zoe JUST made (within ``ASKED_TTL_S`` and her previous
    message ends with it); None otherwise (normal routing). ``off`` = None with no I/O."""
    if mode() != "on":
        return None
    rec = _ASKED.get((user_id or "").strip())
    if rec is None or _clock_s() - rec["at"] > ASKED_TTL_S:
        return None
    import contacts_conversation as cc
    import intent_router

    kind = intent_router._offer_reply_kind(text, "")
    if kind is None or not cc.asked_in_message(await previous_assistant(user_id), rec["question"]):
        return None
    return rec["id"], kind == "accept"


# -- the answer -------------------------------------------------------------------------------------------------------------------
GONE = "That's no longer on your calendar like that, so I've left it."
FAILED = "I couldn't do that just now - nothing was changed. You can ask me again."
STALE = "That one's already been dealt with."
DECLINED_REPLY = "No problem - I'll leave it."
ALARMS_OFF = "Alarms aren't switched on here yet, so I haven't set that one - nothing has changed."


async def _own_row(conn: Any, uid: str, row_id: str) -> Optional[dict]:
    for r in await _rows(conn, uid):
        if r["id"] == row_id:
            return r
    return None


async def _do(conn: Any, uid: str, r: dict, ev: Event, now: datetime) -> tuple[Optional[str], dict]:
    """Run the real tool. ``(reply, receipt)``; reply None = unavailable (``ALARMS_OFF``)."""
    s, kind = r["slots"], r["kind"]
    today = now.astimezone(_tz()).date()
    when = _day(_date(s.get("start_date")) or today, today)
    if kind == "wake":
        import alarms

        if not alarms.enabled():
            return None, {}
        alarm = await alarms.create_alarm(conn, uid, time_hhmm=s["wake_time"], repeat="once", once_date=s["start_date"],
                                          label=f"{str(s.get('label') or 'Early').capitalize()} day", now=now.timestamp())
        return (f"Done - a one-off alarm for {_clock(s['wake_time'])} {when}. Your usual alarm stays as it is.",
                {"alarm_id": alarm.get("id")})
    if kind == "leave":
        from models import ReminderCreate
        from reminder_service import create_reminder_record

        rows = await _all(conn, "SELECT id, role, name FROM users WHERE id = ?", (uid,))
        if not rows:
            raise LookupError("no user row")
        user = {"user_id": str(rows[0][0]), "role": str(rows[0][1] or "user"), "username": str(rows[0][2] or "")}
        payload = ReminderCreate(title=f"Leave for your {s.get('label') or 'event'}", due_date=s["start_date"],
                                 due_time=s["leave_time"], visibility="personal")
        rem = await create_reminder_record(payload, user=user, db=conn, now_utc=now)
        return (f"Done - I'll remind you to leave by {_clock(s['leave_time'])} {when}.", {"reminder_id": rem.get("id")})
    if kind == "packing":
        import list_service
        import write_receipts

        name = f"Packing: {(ev.title or 'trip').strip()[:60]}"
        lst = await list_service.create_list_record(conn, user_id=uid, name=name, list_type="personal", visibility="personal")
        items = (PACKING_TRAVEL_DOCS if s.get("event_kind") in ("flight", "train") else ()) + PACKING_BASICS
        for text in items:
            await list_service.create_item_record(conn, list_id=lst["id"], text=text)
        await conn.commit()
        write_receipts.record(uid, "list", lst["id"], "anticipation")
        return ("Done - I've started a private packing list with a few basics. Only you can see it.", {"list_id": lst["id"]})
    raise ValueError(f"unknown offer kind {kind!r}")


async def answer(user_id: str, suggestion_id: str, accept: bool, *, now: Optional[datetime] = None, db: Any = None) -> str:
    """Act on a yes / record a no. Always returns what to say; never claims a write that did not land."""
    uid = (user_id or "").strip()
    now = now or datetime.now(timezone.utc)
    if _ASKED.get(uid, {}).get("id") == suggestion_id:
        _ASKED.pop(uid, None)
    try:
        async with _conn(db) as conn:
            r = await _own_row(conn, uid, suggestion_id)
            if r is None or r["resolved"] != OPEN:
                return STALE
            s = r["slots"]
            if not accept:
                await _set(conn, r["id"], uid, resolved=DECLINED, slots={**s, "outcome": "dismissed", "answered_at": _iso(now)},
                           only_from=OPEN)
                await _edge(conn, uid, r["id"], r["kind"], "dismissed", now, day=s.get("last_day"))
                _log_outcome(uid, r["kind"], "dismissed")
                return DECLINED_REPLY
            ev = await _offered(conn, uid, s)
            if ev is None or (r["expires"] is not None and r["expires"] <= now):
                await _set(conn, r["id"], uid, resolved=WITHDRAWN, slots={**s, "outcome": "withdrawn"}, only_from=OPEN)
                return GONE
            if not await _set(conn, r["id"], uid, resolved=CLAIMED, slots=s, only_from=OPEN):
                return STALE           # a second "yes" (the card and the voice at once): only one runs the tool
            try:
                reply, receipt = await _do(conn, uid, r, ev, now)  # type: ignore[arg-type]
            except Exception as exc:  # noqa: BLE001 - the write did not land: the offer stays open, the reply says so
                logger.warning("ANTICIPATION_ACT user=%s kind=%s failed: %s", uid, r["kind"], type(exc).__name__)
                await _set(conn, r["id"], uid, resolved=OPEN, slots=s, only_from=CLAIMED)
                return FAILED
            if reply is None:
                await _set(conn, r["id"], uid, resolved=UNAVAILABLE, slots={**s, "outcome": "unavailable", "answered_at": _iso(now)},
                           only_from=CLAIMED)
                _log_outcome(uid, r["kind"], "unavailable")
                return ALARMS_OFF
            await _set(conn, r["id"], uid, resolved=DONE, slots={**s, "outcome": "acted", "answered_at": _iso(now), "receipt": receipt},
                       only_from=CLAIMED)
            await _edge(conn, uid, r["id"], r["kind"], "acted", now, day=s.get("last_day"))
            _log_outcome(uid, r["kind"], "acted")
            return reply
    except Exception as exc:  # noqa: BLE001
        logger.warning("ANTICIPATION_ACT user=%s failed: %s", uid, type(exc).__name__)
        return FAILED


async def execute_if_ours(suggestion_id: str, user_id: str) -> Optional[dict]:
    """The card's accept route (``pending_suggestions.execute_suggestion``): our row -> the ``execute_suggestion`` result shape;
    any other row -> None (the caller carries on). ``off`` = None with no I/O."""
    if mode() == "off":
        return None
    try:
        async with _conn() as conn:
            r = await _own_row(conn, (user_id or "").strip(), suggestion_id)
    except Exception:  # noqa: BLE001
        return None
    if r is None:
        return None
    reply = await answer(user_id, suggestion_id, True)
    ok = reply not in (FAILED, STALE, GONE)
    return {"ok": ok, "action": ACTION, "result": {"reply": reply}} if ok else {"ok": False, "error": reply}


async def on_dismissed(suggestion_id: str, user_id: str) -> None:
    """The card's dismiss route already set ``resolved = 1``: record it as the decline it is (cooldown + outcome)."""
    uid = (user_id or "").strip()
    now = datetime.now(timezone.utc)
    try:
        async with _conn() as conn:
            r = await _own_row(conn, uid, suggestion_id)
            if r is None:
                return
            await _set(conn, r["id"], uid, resolved=DECLINED, slots={**r["slots"], "outcome": "dismissed", "answered_at": _iso(now)})
            await _edge(conn, uid, r["id"], r["kind"], "dismissed", now, day=r["slots"].get("last_day"))
        _log_outcome(uid, r["kind"], "dismissed")
    except Exception as exc:  # noqa: BLE001
        logger.warning("ANTICIPATION dismiss not recorded user=%s: %s", uid, type(exc).__name__)


# -- forget ------------------------------------------------------------------------------------------------------------------------
async def erase_entity(db: Any, user_id: str, rx: Any) -> int:
    """The forget cascade's ``anticipation_offers`` step: delete every offer (any state) whose event title, place or line names
    the forgotten thing. ``rx`` has ``.search`` (a regex or the forget ledger's pattern). Raises on a store error (the cascade
    records the step as failed). Deliberately NOT flag-gated: forgotten means forever, so a forget still reaches offers made while
    the flag was on (with the flag never on this is one empty SELECT inside a forget, never on a turn)."""
    rows = await _all(db, "SELECT id, description, offer_phrase, pre_filled_slots FROM pending_suggestions WHERE user_id = ? AND"
                          " action_type = ?", (user_id, ACTION))
    n = 0
    for r in rows:
        if any(t and rx.search(str(t)) for t in (r[1], r[2], r[3])):
            await db.execute("DELETE FROM pending_suggestions WHERE id = ? AND user_id = ?", (r[0], user_id))
            n += 1
    if n:
        await db.commit()
        _ASKED.pop(user_id, None)
    return n


def reset_state() -> None:
    _ASKED.clear()
