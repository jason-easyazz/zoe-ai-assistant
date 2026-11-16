"""Proactive trigger: relationship health check.

Fires once daily at 9am for Inner Circle / friends whose health_score
has dropped below 0.3 and who have not been contacted in 21+ days.
"""
from __future__ import annotations

import logging
import os
import zoneinfo
from datetime import datetime, timezone

import nudge_claims
from proactive.triggers.base import ProactiveTrigger, TriggerResult

log = logging.getLogger(__name__)

_ZOE_TZ = zoneinfo.ZoneInfo(os.environ.get("ZOE_TIMEZONE", "Australia/Perth"))
_FIRE_HOUR = 9


def _days_since(last_contacted_at: str, now_utc: datetime) -> int:
    """Whole days from ``last_contacted_at`` to ``now_utc`` (the trigger's clock). A naive stamp is read as UTC."""
    dt = datetime.fromisoformat(last_contacted_at.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (now_utc - dt).days


def _weeks_since(last_contacted_at: str | None, now_utc: datetime | None = None) -> str:
    if not last_contacted_at:
        return "a long time"
    try:
        days = _days_since(last_contacted_at, now_utc or datetime.now(timezone.utc))
        if days < 7:
            return f"{days} day{'s' if days != 1 else ''}"
        weeks = days // 7
        return f"{weeks} week{'s' if weeks != 1 else ''}"
    except (ValueError, TypeError):
        return "a while"


async def _nudged_today(db, local_day: str) -> set | None:
    """``(user_id, person_id)`` already nudged on this household day, read from ``proactive_pending`` (the row
    ``fire_notification`` writes for a send that went through; the birthday and morning triggers read the same table).
    None when the read failed: the caller then sends nothing this tick rather than risk a repeat."""
    try:
        async with db.execute(
            "SELECT user_id, item_id FROM proactive_pending WHERE trigger_type = ? "
            "AND (created_at::timestamptz AT TIME ZONE ?)::date::text = ?",
            (PeopleHealthTrigger.trigger_type, getattr(_ZOE_TZ, "key", "UTC"), local_day),
        ) as cur:
            return {(r["user_id"], r["item_id"]) for r in await cur.fetchall()}
    except Exception as exc:
        log.warning("PeopleHealthTrigger: nudged-today read failed, skipping this tick: %s", type(exc).__name__)
        return None


class PeopleHealthTrigger(ProactiveTrigger):
    """Daily: fire for Inner Circle people whose health_score is low."""

    trigger_type = "people_health"
    # The "sent today" mark is its proactive_pending row, so a send that no lane delivered (and one that raised) removes it:
    # the next tick tries again; one lane getting through keeps it (not repeated).
    retract_if_undelivered = True

    async def check(self, db, now_utc: datetime | None = None) -> list[TriggerResult]:
        # Once per person per household day. The mark is the proactive_pending row of a send that went through, so it
        # survives a restart and a new instance, and a send that failed is retried on the next tick. Two processes checking in the same tick (a deploy overlap) cannot both send: each person's claim is an
        # ``app_settings`` row with a unique key (nudge_claims), released if the send does not go through.
        now_utc = now_utc or datetime.now(timezone.utc)
        now_local = now_utc.astimezone(_ZOE_TZ)
        if now_local.hour != _FIRE_HOUR:
            return []
        local_day = now_local.date().isoformat()
        already = await _nudged_today(db, local_day)
        if already is None:
            return []

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
                log.warning("PeopleHealthTrigger.check failed: %s", type(exc).__name__)
                return []

        results: list[TriggerResult] = []
        for row in rows:
            d = dict(row)
            if (d["user_id"], d["id"]) in already:
                continue
            last_c = d.get("last_contacted_at")
            # Enforce 21-day minimum in Python to avoid SQL dialect issues
            if last_c:
                try:
                    if _days_since(last_c, now_utc) < 21:
                        continue
                except (ValueError, TypeError):
                    pass

            # The exact once-a-day claim (nudge_claims): the pending-row read above is the cheap first check, the claim is what
            # two overlapping processes cannot both win. ``fire_notification`` releases it on a send that did not go through.
            key = nudge_claims.key_for(self.trigger_type, d["user_id"], d["id"], local_day)
            try:
                if not await nudge_claims.claim(key, now=now_utc):
                    continue
            except Exception as exc:  # noqa: BLE001
                log.warning("PeopleHealthTrigger: claim failed, skipping this person this tick: %s", type(exc).__name__)
                continue
            weeks = _weeks_since(last_c, now_utc)
            msg = f"You haven't spoken to {d['name']} in {weeks}."
            results.append(TriggerResult(
                user_id=d["user_id"],
                message=msg,
                trigger_type=self.trigger_type,
                item_id=d["id"],
                context={"person_id": d["id"], "person_name": d["name"], "claim_key": key},
            ))

        return results
