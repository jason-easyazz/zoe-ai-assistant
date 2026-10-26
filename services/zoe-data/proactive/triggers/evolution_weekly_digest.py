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

from proactive.recipients import admins_only, proactive_recipients
from proactive.triggers.base import ProactiveTrigger, TriggerResult

log = logging.getLogger(__name__)

_ZOE_TZ = zoneinfo.ZoneInfo(os.environ.get("ZOE_TIMEZONE", "Australia/Perth"))
_FIRE_HOUR = 18   # 6pm
_FIRE_DAY_OF_WEEK = 4  # Friday (0=Monday)


class EvolutionWeeklyDigestTrigger(ProactiveTrigger):
    """Friday 6pm: evolution summary with quick-approve buttons."""

    trigger_type = "evolution_weekly_digest"

    def __init__(self) -> None:
        super().__init__()
        self._last_fired_week: int = -1

    async def check(self, db) -> list[TriggerResult]:
        """Fire once per week on Friday at 6pm local time."""
        now = datetime.now(_ZOE_TZ)
        if now.weekday() != _FIRE_DAY_OF_WEEK:
            return []
        if now.hour != _FIRE_HOUR:
            return []
        week_number = now.isocalendar()[1]
        if week_number == self._last_fired_week:
            return []

        # Same recipient rule as the morning brief (proactive/recipients.py):
        # household members + recent turn owners, minus guest/synthetic ids.
        # The helper never raises; a failed arm just contributes nobody.
        # ADMINS ONLY: the digest carries proposal titles + approve/defer links,
        # and those actions are `Depends(require_admin)` (routers/system.py).
        users = await admins_only(
            db, await proactive_recipients(db, pass_name="evolution_weekly_digest"))

        if not users:
            return []

        # Query evolution stats once (shared across users)
        pending_count = 0
        validated_count = 0
        top: list = []
        try:
            from db_pool import get_db_ctx  # type: ignore[import]
            cutoff_7d = time.time() - 7 * 86400
            async with get_db_ctx() as pg_db:
                # the single queue (migration 0062): "pending" = open and undecided, "validated" = kept. The struggle
                # proposals are decided on their own admin page, so this legacy digest leaves them out.
                rows = await pg_db.fetch(
                    "SELECT status, decision, COUNT(*) as cnt FROM evolution_proposals "
                    "WHERE source <> 'struggle' GROUP BY status, decision"
                )
                for row in rows:
                    if row["status"] in ("proposed", "probed") and not row["decision"]:
                        pending_count += row["cnt"]
                    elif row["status"] == "kept" and not validated_count:
                        week_rows = await pg_db.fetch(
                            """SELECT COUNT(*) as cnt FROM evolution_proposals
                               WHERE status='kept' AND deployed_at >= $1 AND source <> 'struggle'""",
                            cutoff_7d,
                        )
                        validated_count = week_rows[0]["cnt"] if week_rows else 0

                top = await pg_db.fetch(
                    """SELECT id, title, type FROM evolution_proposals
                       WHERE status IN ('proposed', 'probed') AND decision = '' AND source <> 'struggle'
                       ORDER BY proposed_at DESC LIMIT 3"""
                )
        except Exception as exc:
            log.warning("EvolutionWeeklyDigest: DB query failed: %s", exc)

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

        # Mark as fired for this week
        self._last_fired_week = week_number

        results = []
        for user_id, _username in users:
            results.append(
                TriggerResult(
                    user_id=user_id,
                    message=message,
                    trigger_type="evolution_weekly_digest",
                    item_id=f"weekly_digest_{week_number}",
                )
            )
        return results
