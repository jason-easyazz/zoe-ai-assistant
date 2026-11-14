"""anticipation_facts - read one member's ``anticipation_situations.Facts`` from the store (the engine's only reader).

Everything the situation model reasons over comes through here, read-only:
  * events: ``calendar_read.events_in_window`` (the shared reader: repeats expanded, cancelled dropped, zoned events on their
    household day) with ``include_busy``, i.e. the member's own events, the household's ``family`` ones and other members'
    ``busy`` blocks. Another member's ``personal`` event is never read at all; ``anticipation_situations.view`` clamps again.
    One ``Ev`` per OCCURRENCE, its id the reader's ``instance_key`` (so a daily event's two days are two facts);
  * alarms (the member's enabled ones), reminders (theirs: live, un-acknowledged), their contacts' birthdays
    (``proactive.triggers.people_birthday.birthdays``), their usual number of timed events a day (the last 28 days);
  * members: the real household accounts (``anticipation.is_member``), adult or not (``manner_block.is_adult``).

Not read yet (the rule stays dormant on the box): event ATTENDEES, so ``clash`` cannot fire from live data.
A missing table is an empty list, never an error: the night pass must not break on a box that lacks one.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import anticipation as _p1
import anticipation_situations as S

WINDOW_DAYS = 2                    # today + 2 covers the 36-hour horizon from any time of day
TYPICAL_DAYS = 28


async def _rows(db: Any, sql: str, params: tuple) -> list:
    try:
        return await _p1._all(db, sql, params)
    except Exception as exc:  # noqa: BLE001
        if _p1._missing(exc):
            return []
        raise


def _ev(occ: dict) -> S.Ev:
    row = occ.get("event") or {}
    timed = not occ.get("all_day") and bool(occ.get("start_time"))
    return S.Ev(id=str(occ["instance_key"]), owner=str(row.get("user_id") or ""), title=str(occ.get("title") or ""),
                day=str(occ["date"]), start=str(occ["start_time"])[:5] if timed else "",
                end=str(occ.get("end_time") or "")[:5] if timed else "",
                end_day=str(occ["end_date"]) if str(occ.get("end_date") or "") > str(occ["date"]) else "",
                place=str(occ.get("location") or ""), visibility=str(row.get("visibility") or "family"),
                repeats=bool(occ.get("is_recurring")))


async def load(db: Any, user_id: str, now: datetime) -> S.Facts:
    """The member's facts at ``now`` (any aware instant; read in the household zone)."""
    import alarms
    from calendar_read import events_in_window

    local = now.astimezone(_p1._tz())
    today = local.date()
    members = []
    for r in await _rows(db, "SELECT id, name, role FROM users", ()):
        uid, role = str(r[0]), str(r[2] or "")
        if role == "guest":
            members.append(S.Member(uid, adult=False, guest=True, name=str(r[1] or "")))
        elif _p1.is_member(uid):
            members.append(S.Member(uid, adult=await _p1._is_adult(uid, db=db), name=str(r[1] or "")))
    events = [_ev(o) for o in await events_in_window(db, user_id, today, today + timedelta(days=WINDOW_DAYS), include_busy=True)]
    past = await events_in_window(db, user_id, today - timedelta(days=TYPICAL_DAYS), today - timedelta(days=1), own_only=True)
    typical = sum(1 for o in past if not o.get("all_day") and o.get("start_time")) / TYPICAL_DAYS
    alarm_rows = await _rows(db, "SELECT id, time_hhmm, repeat_rule, once_date FROM alarms WHERE user_id = ? AND enabled = 1", (user_id,))
    alarm_facts = tuple(S.Alarm(str(r[0]), user_id, str(r[1]), frozenset() if str(r[2]) == "once" else alarms.repeat_days(str(r[2])),
                                str(r[3] or "")) for r in alarm_rows)
    reminder_rows = await _rows(db, "SELECT id, title, due_date, due_time FROM reminders WHERE user_id = ? AND COALESCE(deleted, 0) = 0"
                                    " AND COALESCE(is_active, 1) = 1 AND COALESCE(acknowledged, 0) = 0", (user_id,))
    reminders = tuple(S.Reminder(str(r[0]), user_id, str(r[1] or ""), str(r[2] or "")[:10], str(r[3] or "")[:5]) for r in reminder_rows)
    try:
        from proactive.triggers.people_birthday import birthdays as _birthdays

        born = [b for b in await _birthdays(db) if str(b.get("user_id")) == user_id]
    except Exception:  # noqa: BLE001 - no people tables = no birthday situations
        born = []
    birthdays = tuple(S.Birthday(str(b["person_id"]), user_id, str(b.get("name") or ""), int(b["month"]), int(b["day"])) for b in born)
    return S.Facts(now=local, members=tuple(members), events=tuple(events), alarms=alarm_facts, reminders=reminders,
                   birthdays=birthdays, typical={user_id: typical})
