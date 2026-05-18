"""Proactive trigger: relationship health check.

Fires once daily at 9am for Inner Circle / friends whose health_score
has dropped below 0.3 and who have not been contacted in 21+ days.
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
    """Daily: fire for Inner Circle / friends whose health_score is low."""

    trigger_type = "people_health"

    async def check(self, db) -> list[TriggerResult]:
        now_local = datetime.now(_ZOE_TZ)
        if now_local.hour != _FIRE_HOUR:
            return []

        try:
            async with db.execute(
                """SELECT p.id, p.name, p.user_id, p.last_contacted_at
                   FROM people p
                   WHERE p.deleted = 0
                     AND p.circle IN ('inner', 'friends')
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
                    "SELECT id, name, user_id, last_contacted_at FROM people "
                    "WHERE deleted = 0 AND circle IN ('inner', 'friends') AND health_score < 0.3 "
                    "ORDER BY health_score ASC LIMIT 10"
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
