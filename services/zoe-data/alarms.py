"""alarms - wake-up alarms for the household: schedule maths, storage and the ring engine (flag-dark ``ZOE_ALARMS``).

Design: ``docs/architecture/alarms.md``. Tables: alembic ``0063_alarms`` (``alarms``, ``alarm_events``).

``ZOE_ALARMS`` = off (DEFAULT) | on, read per call. Off = every ``/api/alarms`` route answers 404, the voice grammar is not
consulted and the ring loop idles.

One row is the whole truth. ``next_fire_at`` (epoch seconds) is when an idle alarm next rings; the engine claims a due row with a
compare-and-swap on that exact value, so two ticks (or two processes, or a restart mid-tick) can never ring one occurrence twice.
Lifecycle: ``idle`` -> ``ringing`` -> (``snoozed`` -> ``ringing`` ...) -> ``idle`` on dismiss or auto-stop. A ring older than
``RING_MAX_S`` stops by itself (logged); an occurrence missed while zoe-data was down by more than ``MISSED_GRACE_S`` is skipped
(logged), never rung late into the morning. A one-off disables itself as it rings; a repeating alarm advances to its next
occurrence as it rings. Times are local wall times in the household timezone (``time_utils.zoe_timezone``).

Visibility (``can_see`` / ``can_edit`` / ``can_silence``): a member owns their personal alarms and shares the household ones; the
wall panel sees household alarms in full, personal ones MASKED (time only), edits only household ones, and can silence any
RINGING alarm, because a ringing alarm must always be stoppable by whoever is in the room.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Optional

from time_utils import zoe_timezone

logger = logging.getLogger(__name__)

FLAG = "ZOE_ALARMS"
HOUSEHOLD = "__household__"  # == panel_principal.HOUSEHOLD_VIEWER: the owner of a household alarm
RING_MAX_S = 600  # a ring that nobody answers stops by itself after 10 minutes
MISSED_GRACE_S = 600  # a due occurrence found later than this (zoe-data was down) is skipped, not rung into the morning
MAX_SNOOZES = 8  # after this many snoozes tap-anywhere stops snoozing; only Turn off (or the auto-stop) ends the ring
MAX_ALARMS_PER_OWNER = 30
SNOOZE_RANGE = (1, 30)
RAMP_RANGE = (0, 300)
DEFAULT_SNOOZE_MIN = 9
DEFAULT_RAMP_S = 30
LABEL_MAX = 60
# The bundled tone ids (the audio itself lands in phase 3). An unknown id is refused, never stored.
TONES = {"sunrise": "Sunrise", "chime": "Chime", "bell": "Bell", "beep": "Beep"}
DEFAULT_TONE = "sunrise"
SOUND_KINDS = ("tone", "music")
MUSIC_ID_MAX = 200

COLS = ("id", "user_id", "label", "time_hhmm", "repeat_rule", "once_date", "sound_kind", "sound_id", "sound_name", "snooze_minutes",
        "ramp_seconds", "enabled", "next_fire_at", "state", "ring_started_at", "snooze_until", "snooze_count", "last_fired_at",
        "created_at", "updated_at")
_SELECT = f"SELECT {', '.join(COLS)} FROM alarms"
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_DAYS = {"mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6, "sun": 7}


# Listeners ``async (event, alarm)`` for what an alarm DID: rang | snoozed | dismissed | auto_stopped | missed | updated | deleted.
# alarm_music registers one so the clock-radio music starts with a ring and always stops with it. A listener never breaks a transition.
HOOKS: list = []
HOOK_TIMEOUT_S = 5.0


async def _emit(event: str, alarm: dict) -> None:
    for hook in list(HOOKS):
        try:
            await asyncio.wait_for(hook(event, alarm), HOOK_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 - includes a hook that hangs: the transition is already committed
            logger.warning("alarm hook failed on %s (%s)", event, type(exc).__name__)


class AlarmError(Exception):
    """A refusal the caller can show: ``status`` is the HTTP code, ``reason`` is safe to speak or display."""

    def __init__(self, reason: str, status: int = 400):
        super().__init__(reason)
        self.reason, self.status = reason, status


def enabled() -> bool:
    from typed_env import env_str

    return env_str("ZOE_ALARMS", "off").strip().lower() == "on"  # literal on purpose: tools/audit/flag_inventory.py greps it


# ── schedule maths ───────────────────────────────────────────────────────────────────────────────────────────────────────────


def normalise_time(value: Any) -> str:
    """``HH:MM`` (24 h) or raise. The panel and the voice grammar both hand over 24-hour wall time."""
    s = str(value or "").strip()
    m = re.match(r"^(\d{1,2}):(\d{2})$", s)
    if m:
        s = f"{int(m.group(1)):02d}:{m.group(2)}"
    if not _TIME_RE.match(s):
        raise AlarmError("That is not a time I can use.")
    return s


def normalise_repeat(value: Any) -> str:
    """once | daily | weekdays | weekends | days:<ISO weekday digits, comma separated, ascending> (Monday = 1)."""
    s = str(value or "once").strip().lower()
    if s in ("once", "daily", "weekdays", "weekends"):
        return s
    if s.startswith("days:"):
        raw = s[5:].replace(" ", "").split(",")
        try:
            days = sorted({_DAYS.get(p, None) or int(p) for p in raw if p})
        except ValueError:
            days = []
        if days and all(1 <= d <= 7 for d in days):
            return "days:" + ",".join(str(d) for d in days) if len(days) < 7 else "daily"
    raise AlarmError("That is not a repeat I can use.")


def repeat_days(rule: str) -> frozenset[int]:
    if rule == "daily":
        return frozenset(range(1, 8))
    if rule == "weekdays":
        return frozenset(range(1, 6))
    if rule == "weekends":
        return frozenset((6, 7))
    if rule.startswith("days:"):
        return frozenset(int(p) for p in rule[5:].split(",") if p)
    return frozenset()


def _at(d: date, hhmm: str) -> float:
    h, m = int(hhmm[:2]), int(hhmm[3:])
    return datetime(d.year, d.month, d.day, h, m, tzinfo=zoe_timezone()).timestamp()


def next_occurrence(hhmm: str, rule: str, once_date: Optional[str], after: float) -> Optional[float]:
    """The first firing time strictly after ``after`` (epoch seconds), or None (a one-off already in the past)."""
    if rule == "once":
        if not once_date:
            return None
        t = _at(date.fromisoformat(once_date), hhmm)
        return t if t > after else None
    days = repeat_days(rule)
    start = datetime.fromtimestamp(after, zoe_timezone()).date()
    for i in range(9):  # a week plus the turn of the day always contains the next hit
        d = start + timedelta(days=i)
        if d.isoweekday() in days:
            t = _at(d, hhmm)
            if t > after:
                return t
    return None


def next_once_date(hhmm: str, now: float) -> str:
    """"Wake me at 7" with no date: today if 7:00 is still ahead in the household's day, else tomorrow."""
    today = datetime.fromtimestamp(now, zoe_timezone()).date()
    return (today if _at(today, hhmm) > now else today + timedelta(days=1)).isoformat()


def clock_phrase(hhmm: str) -> str:
    h, m = int(hhmm[:2]), int(hhmm[3:])
    return f"{h % 12 or 12}:{m:02d} {'am' if h < 12 else 'pm'}"


def when_phrase(ts: float, now: Optional[float] = None) -> str:
    """"7:00 am today" / "7:00 am tomorrow" / "7:00 am on Friday" in the household timezone."""
    tz = zoe_timezone()
    dt = datetime.fromtimestamp(ts, tz)
    today = datetime.fromtimestamp(time.time() if now is None else now, tz).date()
    clock = clock_phrase(dt.strftime("%H:%M"))
    if dt.date() == today:
        return f"{clock} today"
    if dt.date() == today + timedelta(days=1):
        return f"{clock} tomorrow"
    return f"{clock} on {dt.strftime('%A')}"


def repeat_phrase(rule: str) -> str:
    if rule == "once":
        return ""
    if rule in ("daily", "weekdays", "weekends"):
        return {"daily": "every day", "weekdays": "on weekdays", "weekends": "on weekends"}[rule]
    names = {1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu", 5: "Fri", 6: "Sat", 7: "Sun"}
    return "on " + ", ".join(names[d] for d in sorted(repeat_days(rule)))


# ── who may do what ──────────────────────────────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Viewer:
    user_id: str  # a member id, or "" for the panel principal
    panel: bool = False  # the wall panel / a guest at it (household rules)

    @property
    def owner_id(self) -> str:
        """Who a NEW alarm belongs to: the member, or the household when the panel (a guest) sets it."""
        return HOUSEHOLD if self.panel or not self.user_id else self.user_id


def can_see(alarm: dict, v: Viewer) -> bool:
    return alarm["user_id"] == HOUSEHOLD or alarm["user_id"] == v.user_id or v.panel


def can_edit(alarm: dict, v: Viewer) -> bool:
    if alarm["user_id"] == HOUSEHOLD:
        return bool(v.panel or v.user_id)
    return bool(v.user_id) and alarm["user_id"] == v.user_id and not v.panel


def can_silence(alarm: dict, v: Viewer) -> bool:
    """Snooze / turn off a RINGING alarm: its owner, anyone for a household alarm, and the panel for any (the person in the room)."""
    return v.panel or can_edit(alarm, v)


def present(alarm: dict, v: Viewer, now: Optional[float] = None) -> dict:
    """The API shape for one viewer: personal alarms are masked for the panel (time and state only, no label/sound/owner)."""
    out = {k: alarm[k] for k in ("id", "label", "time_hhmm", "repeat_rule", "once_date", "sound_kind", "sound_id", "sound_name",
                                 "snooze_minutes", "ramp_seconds", "enabled", "next_fire_at", "state", "snooze_until",
                                 "snooze_count")}
    out["enabled"] = bool(out["enabled"])
    out["household"] = alarm["user_id"] == HOUSEHOLD
    out["mine"] = alarm["user_id"] == v.user_id
    out["editable"] = can_edit(alarm, v)
    out["repeat_phrase"] = repeat_phrase(alarm["repeat_rule"])
    out["when"] = when_phrase(alarm["next_fire_at"], now) if alarm["next_fire_at"] and alarm["enabled"] else ""
    if v.panel and alarm["user_id"] != HOUSEHOLD:
        out.update(label="", sound_kind="tone", sound_id="", sound_name="", masked=True)
    return out


# ── storage ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────


def _row(r: Any) -> dict:
    return dict(zip(COLS, tuple(r)))


async def _rowcount(db: Any, sql: str, args: tuple) -> int:
    async with db.execute(sql, args) as cur:
        return int(getattr(cur, "rowcount", 0) or 0)


async def log_event(db: Any, alarm_id: str, event: str, detail: str = "", *, now: Optional[float] = None) -> None:
    await db.execute("INSERT INTO alarm_events (id, alarm_id, ts, event, detail) VALUES (?, ?, ?, ?, ?)",
                     (uuid.uuid4().hex, alarm_id, time.time() if now is None else now, event, detail[:200]))


async def get_alarm(db: Any, alarm_id: str) -> Optional[dict]:
    async with db.execute(f"{_SELECT} WHERE id = ?", (alarm_id,)) as cur:
        r = await cur.fetchone()
    return _row(r) if r else None


async def list_alarms(db: Any, v: Viewer) -> list[dict]:
    if v.panel:
        sql, args = f"{_SELECT} ORDER BY time_hhmm, created_at", ()
    else:
        sql, args = f"{_SELECT} WHERE user_id IN (?, ?) ORDER BY time_hhmm, created_at", (v.user_id, HOUSEHOLD)
    async with db.execute(sql, args) as cur:
        rows = await cur.fetchall()
    return [_row(r) for r in rows]


def _clean_label(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:LABEL_MAX]


def _clean_sound(kind: Any, sound_id: Any, name: Any) -> tuple[str, str, str]:
    kind = str(kind or "tone").strip().lower()
    if kind not in SOUND_KINDS:
        raise AlarmError("That is not a sound I can use.")
    sid = str(sound_id or "").strip()
    if kind == "tone":
        sid = sid or DEFAULT_TONE
        if sid not in TONES:
            raise AlarmError("I don't have that tone.")
        return kind, sid, TONES[sid]
    if not sid or len(sid) > MUSIC_ID_MAX:
        raise AlarmError("Pick the music to wake to.")
    return kind, sid, _clean_label(name) or sid[:LABEL_MAX]


def _clamp(value: Any, lo_hi: tuple[int, int], default: int, what: str) -> int:
    if value is None:
        return default
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise AlarmError(f"That is not a valid {what}.") from None
    if not lo_hi[0] <= n <= lo_hi[1]:
        raise AlarmError(f"The {what} must be between {lo_hi[0]} and {lo_hi[1]}.")
    return n


def _schedule(hhmm: str, rule: str, once_date: Optional[str], now: float) -> tuple[Optional[str], Optional[float]]:
    """(once_date to store, next_fire_at). A one-off with no date takes the next occurrence of the time; a one-off in the past is refused."""
    if rule == "once":
        if once_date:
            try:
                date.fromisoformat(str(once_date))
            except ValueError:
                raise AlarmError("That is not a date I can use.") from None
        else:
            once_date = next_once_date(hhmm, now)
        nxt = next_occurrence(hhmm, rule, once_date, now)
        if nxt is None:
            raise AlarmError("That time has already passed.")
        return str(once_date), nxt
    return None, next_occurrence(hhmm, rule, None, now)


async def create_alarm(db: Any, owner: str, *, time_hhmm: Any, repeat: Any = "once", once_date: Optional[str] = None, label: Any = "",
                       sound_kind: Any = "tone", sound_id: Any = "", sound_name: Any = "", snooze_minutes: Any = None,
                       ramp_seconds: Any = None, now: Optional[float] = None) -> dict:
    now = time.time() if now is None else now
    hhmm, rule = normalise_time(time_hhmm), normalise_repeat(repeat)
    kind, sid, sname = _clean_sound(sound_kind, sound_id, sound_name)
    snooze = _clamp(snooze_minutes, SNOOZE_RANGE, DEFAULT_SNOOZE_MIN, "snooze length")
    ramp = _clamp(ramp_seconds, RAMP_RANGE, DEFAULT_RAMP_S, "volume ramp")
    stored_date, nxt = _schedule(hhmm, rule, once_date, now)
    async with db.execute("SELECT COUNT(*) FROM alarms WHERE user_id = ?", (owner,)) as cur:
        if int((await cur.fetchone())[0]) >= MAX_ALARMS_PER_OWNER:
            raise AlarmError("That is the most alarms I can keep.", 409)
    aid = uuid.uuid4().hex[:12]
    await db.execute(
        "INSERT INTO alarms (id, user_id, label, time_hhmm, repeat_rule, once_date, sound_kind, sound_id, sound_name, snooze_minutes,"
        " ramp_seconds, enabled, next_fire_at, state, snooze_count, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, 'idle', 0, ?, ?)",
        (aid, owner, _clean_label(label), hhmm, rule, stored_date, kind, sid, sname, snooze, ramp, nxt, now, now))
    await log_event(db, aid, "created", f"{hhmm} {rule}", now=now)
    await db.commit()
    return await get_alarm(db, aid)  # type: ignore[return-value]


_EDITABLE = ("time_hhmm", "repeat", "once_date", "label", "sound_kind", "sound_id", "sound_name", "snooze_minutes", "ramp_seconds", "enabled")


async def update_alarm(db: Any, alarm: dict, changes: dict, *, now: Optional[float] = None) -> dict:
    """Apply an edit and reschedule. Editing or re-enabling always lands on idle with a fresh ``next_fire_at``: a ring in progress
    is stopped by it (turning an alarm off while it rings must silence it)."""
    now = time.time() if now is None else now
    ch = {k: v for k, v in (changes or {}).items() if k in _EDITABLE}
    hhmm = normalise_time(ch["time_hhmm"]) if "time_hhmm" in ch else alarm["time_hhmm"]
    rule = normalise_repeat(ch["repeat"]) if "repeat" in ch else alarm["repeat_rule"]
    once_date = ch.get("once_date", alarm["once_date"]) if rule == "once" else None
    if rule == "once" and ("time_hhmm" in ch or "repeat" in ch) and "once_date" not in ch:
        once_date = None  # a changed time re-picks "the next one"; keeping the old date could land in the past
    kind, sid, sname = alarm["sound_kind"], alarm["sound_id"], alarm["sound_name"]
    if any(k in ch for k in ("sound_kind", "sound_id", "sound_name")):
        kind, sid, sname = _clean_sound(ch.get("sound_kind", kind), ch.get("sound_id", sid if "sound_kind" not in ch else ""),
                                        ch.get("sound_name", sname if "sound_kind" not in ch else ""))
    snooze = _clamp(ch.get("snooze_minutes"), SNOOZE_RANGE, alarm["snooze_minutes"], "snooze length")
    ramp = _clamp(ch.get("ramp_seconds"), RAMP_RANGE, alarm["ramp_seconds"], "volume ramp")
    label = _clean_label(ch["label"]) if "label" in ch else alarm["label"]
    on = bool(ch["enabled"]) if "enabled" in ch else bool(alarm["enabled"])
    stored_date, nxt = (alarm["once_date"], None)
    if on:
        stored_date, nxt = _schedule(hhmm, rule, once_date, now)
    await db.execute(
        "UPDATE alarms SET label = ?, time_hhmm = ?, repeat_rule = ?, once_date = ?, sound_kind = ?, sound_id = ?, sound_name = ?,"
        " snooze_minutes = ?, ramp_seconds = ?, enabled = ?, next_fire_at = ?, state = 'idle', ring_started_at = NULL,"
        " snooze_until = NULL, snooze_count = 0, updated_at = ? WHERE id = ?",
        (label, hhmm, rule, stored_date, kind, sid, sname, snooze, ramp, 1 if on else 0, nxt, now, alarm["id"]))
    await log_event(db, alarm["id"], "updated", "on" if on else "off", now=now)
    await db.commit()
    await _emit("updated", alarm)
    return await get_alarm(db, alarm["id"])  # type: ignore[return-value]


async def delete_alarm(db: Any, alarm_id: str) -> bool:
    n = await _rowcount(db, "DELETE FROM alarms WHERE id = ?", (alarm_id,))
    if n:
        await db.execute("DELETE FROM alarm_events WHERE alarm_id = ?", (alarm_id,))
    await db.commit()
    if n:
        await _emit("deleted", {"id": alarm_id})
    return n > 0


# ── ring lifecycle ───────────────────────────────────────────────────────────────────────────────────────────────────────────


async def ringing(db: Any) -> list[dict]:
    async with db.execute(f"{_SELECT} WHERE state = 'ringing' ORDER BY ring_started_at") as cur:
        return [_row(r) for r in await cur.fetchall()]


async def snooze(db: Any, alarm: dict, *, minutes: Optional[int] = None, now: Optional[float] = None) -> dict:
    """Ringing -> snoozed for the alarm's own snooze minutes. Refused when it is not ringing or the snooze budget is spent."""
    now = time.time() if now is None else now
    if alarm["state"] != "ringing":
        raise AlarmError("That alarm is not ringing.", 409)
    if alarm["snooze_count"] >= MAX_SNOOZES:
        raise AlarmError("That alarm has been snoozed as much as it can be.", 409)
    mins = minutes if minutes and SNOOZE_RANGE[0] <= minutes <= SNOOZE_RANGE[1] else alarm["snooze_minutes"]
    until = now + mins * 60
    won = await _rowcount(db, "UPDATE alarms SET state = 'snoozed', snooze_until = ?, snooze_count = snooze_count + 1, ring_started_at = NULL,"
                               " updated_at = ? WHERE id = ? AND state = 'ringing'", (until, now, alarm["id"]))
    if won:
        await log_event(db, alarm["id"], "snoozed", f"{mins}m", now=now)
    await db.commit()
    if won:
        await _emit("snoozed", alarm)
    return await get_alarm(db, alarm["id"]) or alarm


async def dismiss(db: Any, alarm: dict, *, now: Optional[float] = None) -> dict:
    """Ringing or snoozed -> idle (the next occurrence stays scheduled; a one-off is already off). Idle alarms are left alone."""
    now = time.time() if now is None else now
    won = await _rowcount(db, "UPDATE alarms SET state = 'idle', ring_started_at = NULL, snooze_until = NULL, snooze_count = 0,"
                               " updated_at = ? WHERE id = ? AND state IN ('ringing', 'snoozed')", (now, alarm["id"]))
    if won:
        await log_event(db, alarm["id"], "dismissed", "", now=now)
    await db.commit()
    if won:
        await _emit("dismissed", alarm)
    return await get_alarm(db, alarm["id"]) or alarm


async def tick(db: Any, now: Optional[float] = None) -> list[tuple[str, dict]]:
    """Advance every alarm one step; returns ``[(event, alarm), ...]`` for what changed (``rang`` rows are what the sound layer plays).

    Every transition is an UPDATE guarded by the state it expects, so a lost race simply changes nothing."""
    now = time.time() if now is None else now
    out: list[tuple[str, dict]] = []

    async def fetch(where: str, args: tuple) -> list[dict]:
        async with db.execute(f"{_SELECT} WHERE {where}", args) as cur:
            return [_row(r) for r in await cur.fetchall()]

    for a in await fetch("state = 'ringing' AND ring_started_at <= ?", (now - RING_MAX_S,)):
        if await _rowcount(db, "UPDATE alarms SET state = 'idle', ring_started_at = NULL, snooze_count = 0, updated_at = ?"
                                " WHERE id = ? AND state = 'ringing' AND ring_started_at = ?", (now, a["id"], a["ring_started_at"])):
            await log_event(db, a["id"], "auto_stopped", f"no answer after {RING_MAX_S // 60} min", now=now)
            out.append(("auto_stopped", a))
    for a in await fetch("state = 'snoozed' AND snooze_until <= ?", (now,)):
        if await _rowcount(db, "UPDATE alarms SET state = 'ringing', ring_started_at = ?, snooze_until = NULL, updated_at = ?"
                                " WHERE id = ? AND state = 'snoozed' AND snooze_until = ?", (now, now, a["id"], a["snooze_until"])):
            await log_event(db, a["id"], "rang", "after snooze", now=now)
            out.append(("rang", {**a, "state": "ringing", "ring_started_at": now}))
    for a in await fetch("enabled = 1 AND state = 'idle' AND next_fire_at <= ?", (now,)):
        due = a["next_fire_at"]
        if due < now - MISSED_GRACE_S:  # found late (zoe-data was down): skip this occurrence, schedule the next from NOW
            nxt = next_occurrence(a["time_hhmm"], a["repeat_rule"], a["once_date"], now)
            won = await _rowcount(db, "UPDATE alarms SET next_fire_at = ?, enabled = ?, updated_at = ? WHERE id = ? AND state = 'idle'"
                                       " AND next_fire_at = ?", (nxt, 1 if nxt else 0, now, a["id"], due))
            if won:
                await log_event(db, a["id"], "missed", f"due {int(now - due)}s ago, zoe-data was not running", now=now)
                out.append(("missed", a))
            continue
        nxt = next_occurrence(a["time_hhmm"], a["repeat_rule"], a["once_date"], due)  # after the occurrence that is ringing now
        won = await _rowcount(db, "UPDATE alarms SET state = 'ringing', ring_started_at = ?, last_fired_at = ?, snooze_count = 0,"
                                   " next_fire_at = ?, enabled = ?, updated_at = ? WHERE id = ? AND state = 'idle' AND next_fire_at = ?",
                              (now, now, nxt, 1 if nxt else 0, now, a["id"], due))
        if won:
            await log_event(db, a["id"], "rang", "late" if now - due > 5 else "", now=now)
            out.append(("rang", {**a, "state": "ringing", "ring_started_at": now, "next_fire_at": nxt, "enabled": 1 if nxt else 0}))
    if out:
        await db.commit()
        for event, alarm in out:
            await _emit(event, alarm)
    return out


async def tick_once() -> list[tuple[str, dict]]:
    """One engine step on a pooled connection (the loop body; separate so a test can drive it)."""
    from db_pool import get_db_ctx

    async with get_db_ctx() as db:
        return await tick(db)


async def run_loop(interval_s: float = 1.0) -> None:
    """Background ring engine (started from the lifespan). Idles while the flag is off; never dies on one bad tick."""
    import asyncio

    while True:
        try:
            if enabled():
                for event, alarm in await tick_once():
                    logger.info("alarm %s %s (%s)", alarm["id"], event, "household" if alarm["user_id"] == HOUSEHOLD else "personal")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - the next tick retries
            logger.warning("alarm tick failed (%s)", type(exc).__name__)
        await asyncio.sleep(interval_s)
