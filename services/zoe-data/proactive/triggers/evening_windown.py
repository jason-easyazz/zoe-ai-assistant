"""Evening wind-down trigger — journal prompt if user hasn't written in 3+ days."""
import os
import zoneinfo
from datetime import datetime

from proactive.triggers.base import ProactiveTrigger, TriggerResult

_ZOE_TZ = zoneinfo.ZoneInfo(os.environ.get("ZOE_TIMEZONE", "Australia/Perth"))


class EveningWindDownTrigger(ProactiveTrigger):
    trigger_type = "evening_windown"

    async def check(self, db) -> list[TriggerResult]:
        now = datetime.now(_ZOE_TZ)
        # Only fire between 21:00 and 22:00
        if now.hour != 21:
            return []

        today = now.date().isoformat()

        # Check already fired today
        async with db.execute(
            "SELECT user_id FROM proactive_pending WHERE trigger_type=? AND DATE(created_at)=?",
            ("evening_windown", today),
        ) as cur:
            already_fired = {row[0] async for row in cur}

        # Get active users (anyone who chatted in last 7 days)
        async with db.execute(
            "SELECT DISTINCT user_id FROM chat_sessions WHERE started_at > datetime('now', '-7 days')"
        ) as cur:
            users = [row[0] async for row in cur]

        results = []
        for user_id in users:
            if user_id in already_fired:
                continue

            # Check last journal-like message (rough: look for "journal", "diary", "feeling")
            async with db.execute(
                """SELECT COUNT(*) FROM chat_sessions
                   WHERE user_id=? AND started_at > datetime('now', '-3 days')
                   AND (summary LIKE '%journal%' OR summary LIKE '%diary%' OR summary LIKE '%feeling%')""",
                (user_id,),
            ) as cur:
                row = await cur.fetchone()
                recent_journal = row[0] if row else 0

            if recent_journal > 0:
                continue  # Already journalled recently

            results.append(
                TriggerResult(
                    user_id=user_id,
                    message="Evening! How's your day been? Sometimes it helps to jot things down — want to do a quick reflection?",
                    trigger_type="evening_windown",
                    item_id="evening_windown",
                    context={"hour": now.hour},
                )
            )
        return results
