"""Proactive trigger: birthday 7-day lookahead.

Fires once daily at 8am for contacts whose birthday is 1–7 days away.
Day-of is excluded to give lead time for planning.
"""
from __future__ import annotations

import logging
import os
import zoneinfo
from datetime import datetime, date

from proactive.triggers.base import ProactiveTrigger, TriggerResult

log = logging.getLogger(__name__)

_ZOE_TZ = zoneinfo.ZoneInfo(os.environ.get("ZOE_TIMEZONE", "Australia/Perth"))
_FIRE_HOUR = 8


def _next_occurrence(month: int, day: int, ref: date | None = None) -> date:
    ref = ref or date.today()
    try:
        candidate = date(ref.year, month, day)
    except ValueError:
        candidate = date(ref.year, month, min(day, 28))
    if candidate < ref:
        try:
            candidate = date(ref.year + 1, month, day)
        except ValueError:
            candidate = date(ref.year + 1, month, min(day, 28))
    return candidate


class PeopleBirthdayTrigger(ProactiveTrigger):
    """Daily at 8am: alert when a contact's birthday is 1–7 days away."""

    trigger_type = "people_birthday"

    async def check(self, db) -> list[TriggerResult]:
        now_local = datetime.now(_ZOE_TZ)
        if now_local.hour != _FIRE_HOUR:
            return []

        try:
            async with db.execute(
                """SELECT d.person_id, d.month, d.day, p.name, p.user_id
                   FROM person_important_dates d
                   JOIN people p ON p.id = d.person_id
                   WHERE d.date_type = 'birthday'
                     AND p.deleted = 0
                     AND d.month IS NOT NULL
                     AND d.day IS NOT NULL"""
            ) as cur:
                rows = await cur.fetchall()
        except Exception as exc:
            log.warning("PeopleBirthdayTrigger.check failed: %s", exc)
            return []

        today = date.today()
        results: list[TriggerResult] = []
        seen: set[tuple[str, int]] = set()  # deduplicate (person_id, days_until)

        for row in rows:
            d = dict(row)
            month, day = d.get("month"), d.get("day")
            if not month or not day:
                continue
            try:
                next_bday = _next_occurrence(int(month), int(day), today)
                days_until = (next_bday - today).days
            except Exception:
                continue

            if not (1 <= days_until <= 7):
                continue

            key = (d["person_id"], days_until)
            if key in seen:
                continue
            seen.add(key)

            s = "s" if days_until != 1 else ""
            msg = f"{d['name']}'s birthday is in {days_until} day{s}."
            results.append(TriggerResult(
                user_id=d["user_id"],
                message=msg,
                trigger_type=self.trigger_type,
                item_id=d["person_id"],
                context={
                    "person_id": d["person_id"],
                    "person_name": d["name"],
                    "days_until": days_until,
                    "birthday_month": month,
                    "birthday_day": day,
                },
            ))

        return results
