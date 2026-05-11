"""Morning check-in trigger — greets user with day summary at 7:30am."""
import os
import zoneinfo
from datetime import datetime, timedelta

from proactive.triggers.base import ProactiveTrigger, TriggerResult

_ZOE_TZ = zoneinfo.ZoneInfo(os.environ.get("ZOE_TIMEZONE", "Australia/Perth"))
_FIRE_HOUR = 7
_FIRE_MINUTE = 30


class MorningCheckInTrigger(ProactiveTrigger):
    trigger_type = "morning_checkin"

    async def check(self, db) -> list[TriggerResult]:
        now = datetime.now(_ZOE_TZ)
        # Only fire between 7:30 and 8:30am
        if not (_FIRE_HOUR <= now.hour < _FIRE_HOUR + 1 and now.minute >= _FIRE_MINUTE):
            return []

        today = now.date().isoformat()

        # Check already fired today for each active user
        async with db.execute(
            "SELECT user_id FROM proactive_pending WHERE trigger_type=? AND DATE(created_at)=? AND claimed=0",
            ("morning_checkin", today),
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
            results.append(
                TriggerResult(
                    user_id=user_id,
                    message="Good morning! Ready to start the day? I can check your calendar or catch you up on anything.",
                    trigger_type="morning_checkin",
                    item_id="morning_checkin",
                    context={"day": now.strftime("%A, %B %d")},
                )
            )
        return results
