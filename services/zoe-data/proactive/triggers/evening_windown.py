"""Evening wind-down trigger — journal prompt if user hasn't written in 3+ days."""
import os
import zoneinfo
from datetime import datetime

from proactive.recipients import proactive_recipients
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
            "SELECT user_id FROM proactive_pending WHERE trigger_type=? AND created_at::date = CURRENT_DATE",
            ("evening_windown",),
        ) as cur:
            already_fired = {row[0] async for row in cur}

        # Active users only (a user turn in the last 7 days), minus guest and
        # synthetic ids. Unlike the morning brief, a quiet household member is
        # not nudged here — the evening prompt is for people actively talking.
        users = [
            uid for uid, _name in await proactive_recipients(
                db, pass_name="evening_windown", include_panel_members=False)
        ]

        results = []
        for user_id in users:
            if user_id in already_fired:
                continue

            # Check last journal-like message (rough: look for "journal", "diary", "feeling")
            async with db.execute(
                """SELECT COUNT(*) FROM chat_sessions
                   WHERE user_id=? AND created_at::timestamptz > (CURRENT_TIMESTAMP - INTERVAL '3 days')
                   AND (title LIKE '%journal%' OR title LIKE '%diary%' OR title LIKE '%feeling%')""",
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
