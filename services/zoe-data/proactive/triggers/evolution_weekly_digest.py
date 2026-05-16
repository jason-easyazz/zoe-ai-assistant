"""EvolutionWeeklyDigestTrigger — Friday 6pm weekly evolution summary.

Fires every Friday at 6pm local time. Pushes a proactive message showing:
- How many proposals are pending review
- How many were validated this week
- Intent miss rate summary
- Quick-approve/defer buttons for top pending proposals

This is the Zoe 'partnership moment' — she surfaces what she noticed,
the human decides what to act on.
"""
from __future__ import annotations

import logging
import os
import time
import zoneinfo
from datetime import datetime

from proactive.triggers.base import ProactiveTrigger, TriggerResult

log = logging.getLogger(__name__)

_ZOE_TZ = zoneinfo.ZoneInfo(os.environ.get("ZOE_TIMEZONE", "Australia/Perth"))
_FIRE_HOUR = 18   # 6pm
_FIRE_DAY_OF_WEEK = 4  # Friday (0=Monday)


class EvolutionWeeklyDigestTrigger(ProactiveTrigger):
    """Friday 6pm: evolution summary with quick-approve buttons."""

    def __init__(self) -> None:
        super().__init__()
        self._last_fired_week: int = -1

    async def should_fire(self, db, user_id: str, now: datetime) -> bool:
        now_local = now.astimezone(_ZOE_TZ)
        if now_local.weekday() != _FIRE_DAY_OF_WEEK:
            return False
        if now_local.hour != _FIRE_HOUR:
            return False
        week_number = now_local.isocalendar()[1]
        if week_number == self._last_fired_week:
            return False
        return True

    async def generate(self, db, user_id: str, now: datetime) -> list[TriggerResult]:
        now_local = now.astimezone(_ZOE_TZ)
        self._last_fired_week = now_local.isocalendar()[1]

        # Count pending proposals
        pending_count = 0
        validated_count = 0
        try:
            from db_pool import get_db_ctx  # type: ignore[import]
            cutoff_7d = time.time() - 7 * 86400
            async with get_db_ctx() as pg_db:
                rows = await pg_db.fetch(
                    "SELECT status, COUNT(*) as cnt FROM evolution_proposals GROUP BY status"
                )
                for row in rows:
                    if row["status"] == "pending":
                        pending_count = row["cnt"]
                    elif row["status"] == "validated" and True:
                        # Count only validated this week
                        week_rows = await pg_db.fetch(
                            """SELECT COUNT(*) as cnt FROM evolution_proposals
                               WHERE status='validated' AND deployed_at >= $1""",
                            cutoff_7d,
                        )
                        validated_count = week_rows[0]["cnt"] if week_rows else 0

                # Top 3 pending proposals for quick actions
                top = await pg_db.fetch(
                    """SELECT id, title, type FROM evolution_proposals
                       WHERE status='pending'
                       ORDER BY proposed_at DESC LIMIT 3"""
                )
        except Exception as exc:
            log.warning("EvolutionWeeklyDigest: DB query failed: %s", exc)
            top = []

        if pending_count == 0 and validated_count == 0:
            return []  # Nothing interesting to report

        lines = ["**Weekly Evolution Summary**\n"]
        if pending_count > 0:
            lines.append(f"- **{pending_count}** proposal{'s' if pending_count != 1 else ''} pending your review")
        if validated_count > 0:
            lines.append(f"- **{validated_count}** validated this week — great progress!")

        if top:
            lines.append("\n**Top proposals:**")
            for row in top:
                lines.append(
                    f"- {row['title'][:60]} "
                    f"[[approve]](/api/agent/evolution/proposals/{row['id']}/action) "
                    f"[[defer]](/api/agent/evolution/proposals/{row['id']}/action)"
                )

        lines.append(
            "\n_Say \"what needs improving?\" to review all proposals, "
            "or \"review proposals\" to act on them._"
        )

        message = "\n".join(lines)

        return [
            TriggerResult(
                message=message,
                user_id=user_id,
                push_notification=True,
            )
        ]
