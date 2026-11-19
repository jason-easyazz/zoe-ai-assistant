"""Proactive trigger: relationship health check.

Fires once daily at 9am for Inner Circle / friends whose health_score
has dropped below 0.3 and who have not been contacted in 21+ days.

ONCE A DAY PER PERSON. The gate used to be the hour alone, and the slow loop runs every five minutes, so the same "you haven't
spoken to ..." nudge went out up to twelve times inside the 9 o'clock hour. The rule is the birthday trigger's
(``people_birthday``): a (member, person) already nudged on the household's local day is skipped, read from the
``proactive_pending`` row every nudge leaves, with an in-process set holding the line when that read fails or the row has gone.
"""
from __future__ import annotations

import logging
import os
import zoneinfo
from datetime import datetime, date

from proactive.triggers.base import ProactiveTrigger, TriggerResult

log = logging.getLogger(__name__)

_ZOE_TZ = zoneinfo.ZoneInfo(os.environ.get("ZOE_TIMEZONE", "Australia/Perth"))
_FIRE_HOUR = 9
#: (member id, person id, local day) nudged by THIS process today: the line holds when the fired-today read below cannot be made
_fired: set = set()


async def _fired_today(db, today: str, tz_key: str) -> set:
    """``(user_id, person_id)`` already nudged today (household local day). Empty on a failed read: ``_fired`` still holds."""
    try:
        async with db.execute("SELECT user_id, item_id FROM proactive_pending WHERE trigger_type = ? "
                              "AND (created_at::timestamptz AT TIME ZONE ?)::date::text = ?",
                              (PeopleHealthTrigger.trigger_type, tz_key, today)) as cur:
            return {(dict(r)["user_id"], dict(r)["item_id"]) for r in await cur.fetchall()}
    except Exception as exc:  # noqa: BLE001
        log.debug("PeopleHealthTrigger: fired-today read skipped (%s)", type(exc).__name__)
        return set()


def _weeks_since(last_contacted_at: str | None) -> str:
    if not last_contacted_at:
        return "a long time"
    try:
        dt = datetime.fromisoformat(last_contacted_at.replace("Z", "+00:00"))
        days = (datetime.now(dt.tzinfo) - dt).days
        if days < 7:
            return f"{days} day{'s' if days != 1 else ''}"
        weeks = days // 7
        return f"{weeks} week{'s' if weeks != 1 else ''}"
    except (ValueError, TypeError):
        return "a while"


class PeopleHealthTrigger(ProactiveTrigger):
    """Daily: fire for Inner Circle people whose health_score is low."""

    trigger_type = "people_health"

    async def check(self, db, now: datetime | None = None) -> list[TriggerResult]:
        now_local = now or datetime.now(_ZOE_TZ)
        if now_local.hour != _FIRE_HOUR:
            return []
        local_day = now_local.date()
        _fired.difference_update({k for k in _fired if k[2] != local_day})   # bounded: only today's keys are kept
        already = await _fired_today(db, local_day.isoformat(), getattr(now_local.tzinfo, "key", "UTC"))

        try:
            async with db.execute(
                """SELECT p.id, p.name, p.user_id, p.last_contacted_at, p.context
                   FROM people p
                   WHERE p.deleted = 0
                     AND p.circle = 'inner'
                     AND (p.is_partial = 0 OR p.is_partial IS NULL)
                     AND p.health_score < 0.3
                     AND (
                         p.last_contacted_at IS NULL
                         OR p.last_contacted_at < NOW()::TEXT
                     )
                   ORDER BY p.health_score ASC
                   LIMIT 10""",
            ) as cur:
                rows = await cur.fetchall()
        except Exception:
            # Fallback: skip 21-day check in SQL, handle in Python
            try:
                async with db.execute(
                    "SELECT id, name, user_id, last_contacted_at, context FROM people "
                    "WHERE deleted = 0 AND circle = 'inner' AND (is_partial = 0 OR is_partial IS NULL) "
                    "AND health_score < 0.3 ORDER BY health_score ASC LIMIT 10"
                ) as cur:
                    rows = await cur.fetchall()
            except Exception as exc:
                log.warning("PeopleHealthTrigger.check failed: %s", exc)
                return []

        results: list[TriggerResult] = []
        today = date.today()
        for row in rows:
            d = dict(row)
            last_c = d.get("last_contacted_at")
            # Enforce 21-day minimum in Python to avoid SQL dialect issues
            if last_c:
                try:
                    last_dt = datetime.fromisoformat(last_c.replace("Z", "+00:00"))
                    if (datetime.now(last_dt.tzinfo) - last_dt).days < 21:
                        continue
                except (ValueError, TypeError):
                    pass

            key = (d["user_id"], d["id"])
            if key in already or key + (local_day,) in _fired:
                continue                     # this person was already nudged about today
            _fired.add(key + (local_day,))
            weeks = _weeks_since(last_c)
            msg = f"You haven't spoken to {d['name']} in {weeks}."
            results.append(TriggerResult(
                user_id=d["user_id"],
                message=msg,
                trigger_type=self.trigger_type,
                item_id=d["id"],
                context={"person_id": d["id"], "person_name": d["name"]},
            ))

        return results
