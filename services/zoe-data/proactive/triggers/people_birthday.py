"""Proactive trigger: birthday lead-up (person-centred memory, "the birthday fix"; owner decision D3, 2026-11-03).

Fires at 8am local, ONCE per person per day, on the lead days of a contact's birthday:
- default: 7 days before and on the day;
- inner / family circle: 14 and 3 days before and on the day;
- a per-date ``reminder_days_before`` other than the column default (7): that many days before and on the day.

Reads BOTH date sources: ``person_important_dates`` (the date home) and, read-through for one release (D9), the legacy
``people.birthday`` column for a person with no birthday row. Before this fix the trigger excluded the day itself, read only
``person_important_dates`` and, with a 5-minute slow loop, would have fired up to twelve times inside the 8am hour.
"""
from __future__ import annotations

import logging
from datetime import datetime

from proactive.triggers.base import ProactiveTrigger, TriggerResult

log = logging.getLogger(__name__)

_FIRE_HOUR = 8
COLUMN_DEFAULT_LEAD = 7
LEAD_DEFAULT = (7, 0)
LEAD_CLOSE = (14, 3, 0)
CLOSE_CIRCLES = frozenset({"inner", "family"})
MAX_LEAD = 60

# (user_id, person_id, local day) already fired by this process: the in-process half of the once-a-day rule (the
# proactive_pending read below is the half that survives a restart)
_fired: set = set()


def lead_days(circle: str | None, reminder_days_before: int | None = None) -> tuple:
    """The days-before on which to remind. Pure."""
    try:
        r = int(reminder_days_before) if reminder_days_before is not None else None
    except (TypeError, ValueError):
        r = None
    if r is not None and r != COLUMN_DEFAULT_LEAD and 0 < r <= MAX_LEAD:
        return (r, 0)
    return LEAD_CLOSE if str(circle or "").strip().lower() in CLOSE_CIRCLES else LEAD_DEFAULT


def message_for(name: str, days_until: int) -> str:
    if days_until == 0:
        return f"It's {name}'s birthday today."
    return f"{name}'s birthday is in {days_until} day{'s' if days_until != 1 else ''}."


async def _rows(db, sql: str, params: tuple = ()) -> list:
    async with db.execute(sql, params) as cur:
        return [dict(r) for r in await cur.fetchall()]


async def birthdays(db) -> list[dict]:
    """``{person_id, user_id, name, circle, month, day, lead}`` for every live person with a known birthday: the dates table
    first, the legacy column only for a person with no birthday row there."""
    from person_profile import parse_birthday

    out: dict[tuple, dict] = {}
    try:
        for d in await _rows(db, """SELECT d.person_id, d.month, d.day, d.reminder_days_before, p.name, p.user_id, p.circle
                                    FROM person_important_dates d JOIN people p ON p.id = d.person_id
                                    WHERE d.date_type = 'birthday' AND COALESCE(p.deleted, 0) = 0
                                      AND d.month IS NOT NULL AND d.day IS NOT NULL"""):
            out.setdefault((d["user_id"], d["person_id"]), {**d, "lead": lead_days(d.get("circle"), d.get("reminder_days_before"))})
    except Exception as exc:
        log.warning("PeopleBirthdayTrigger: dates read failed: %s", exc)
    try:
        legacy = await _rows(db, """SELECT id AS person_id, name, user_id, circle, birthday FROM people
                                    WHERE COALESCE(deleted, 0) = 0 AND birthday IS NOT NULL AND birthday <> ''""")
    except Exception as exc:
        log.warning("PeopleBirthdayTrigger: legacy birthday read failed: %s", exc)
        legacy = []
    for p in legacy:
        md = parse_birthday(p.get("birthday"))
        if md and (p["user_id"], p["person_id"]) not in out:
            out[(p["user_id"], p["person_id"])] = {**p, "month": md[0], "day": md[1], "lead": lead_days(p.get("circle"))}
    return list(out.values())


async def _fired_today(db, today: str, tz_key: str) -> set:
    """``(user_id, person_id)`` already reminded today (household local day). Empty on a failed read: the in-process set still
    holds the line for this process."""
    try:
        rows = await _rows(db, "SELECT user_id, item_id FROM proactive_pending WHERE trigger_type = ? "
                               "AND (created_at::timestamptz AT TIME ZONE ?)::date::text = ?",
                           (PeopleBirthdayTrigger.trigger_type, tz_key, today))
        return {(r["user_id"], r["item_id"]) for r in rows}
    except Exception as exc:
        log.debug("PeopleBirthdayTrigger: fired-today read skipped (%s)", type(exc).__name__)
        return set()


class PeopleBirthdayTrigger(ProactiveTrigger):
    """Daily at 8am: remind on a contact's birthday lead days and on the day."""

    trigger_type = "people_birthday"

    async def check(self, db, now: datetime | None = None) -> list[TriggerResult]:
        from person_profile import next_occurrence
        from time_utils import zoe_timezone

        now_local = now or datetime.now(zoe_timezone())   # per call: ZOE_TIMEZONE is not frozen at import
        if now_local.hour != _FIRE_HOUR:
            return []
        today = now_local.date()
        _fired.difference_update({k for k in _fired if k[2] != today})   # bounded: only today's keys are kept
        already = await _fired_today(db, today.isoformat(), getattr(now_local.tzinfo, "key", "UTC"))
        results: list[TriggerResult] = []
        for d in await birthdays(db):
            try:
                month, day = int(d["month"]), int(d["day"])
                days_until = (next_occurrence(month, day, today) - today).days
            except (TypeError, ValueError, KeyError):
                continue
            key = (d["user_id"], d["person_id"])
            if days_until not in d["lead"] or key in already or key + (today,) in _fired:
                continue
            _fired.add(key + (today,))
            results.append(TriggerResult(
                user_id=d["user_id"],
                message=message_for(d["name"], days_until),
                trigger_type=self.trigger_type,
                item_id=d["person_id"],
                context={"person_id": d["person_id"], "person_name": d["name"], "days_until": days_until,
                         "birthday_month": month, "birthday_day": day},
            ))
        return results
