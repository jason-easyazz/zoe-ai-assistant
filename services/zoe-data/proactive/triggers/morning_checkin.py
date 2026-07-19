"""Morning check-in trigger — greets user with day summary at 7:30am.

Phase 3.4: enriched with open loops, emotional moments, calendar preview,
and portrait-informed personal note so every morning brief feels personal.
"""
import logging
import os
import zoneinfo
from datetime import datetime, timedelta

from proactive.triggers.base import ProactiveTrigger, TriggerResult

log = logging.getLogger(__name__)

_ZOE_TZ = zoneinfo.ZoneInfo(os.environ.get("ZOE_TIMEZONE", "Australia/Perth"))
_FIRE_HOUR = 7
_FIRE_MINUTE = 30


async def _build_morning_context(db, user_id: str, today: str) -> dict:
    """Collect open loops, emotional moments, calendar events, and portrait for the brief."""
    ctx: dict = {}

    # Open loops approaching follow-up time
    try:
        async with db.execute(
            """SELECT loop_text, follow_up_hint, emotional_weight
               FROM open_loops
               WHERE user_id=? AND resolved = false
                 AND (follow_up_after IS NULL OR follow_up_after <= CURRENT_TIMESTAMP + INTERVAL '1 day')
               ORDER BY emotional_weight DESC, created_at ASC
               LIMIT 3""",
            (user_id,),
        ) as cur:
            loops = await cur.fetchall()
        if loops:
            ctx["open_loops"] = [
                {"text": row[0], "hint": row[1] or "", "weight": row[2]}
                for row in loops
            ]
    except Exception as exc:
        log.debug("morning_checkin: open_loops load failed (non-fatal): %s", exc)

    # Today's calendar events
    try:
        async with db.execute(
            """SELECT title, start_time, end_time, location
               FROM events
               WHERE start_date=? AND user_id=? AND deleted=0
               ORDER BY start_time
               LIMIT 5""",
            (today, user_id),
        ) as cur:
            events = await cur.fetchall()
        if events:
            ctx["calendar"] = [
                {"title": row[0], "start": row[1] or "", "end": row[2] or "", "location": row[3] or ""}
                for row in events
            ]
    except Exception as exc:
        log.debug("morning_checkin: calendar load failed (non-fatal): %s", exc)

    # Recent emotional moments from MemPalace (last 3 days)
    try:
        from memory_service import get_memory_service
        svc = get_memory_service()
        refs = await svc.load_for_prompt(user_id, limit=30)
        emo_items = []
        for ref in refs:
            mt = (ref.metadata or {}).get("memory_type", "") or ""
            if mt == "emotional_moment":
                added_at = (ref.metadata or {}).get("added_at", "") or ""
                # Only surface moments from last 3 days
                try:
                    import datetime as _dt
                    added_dt = _dt.datetime.fromisoformat(added_at.rstrip("Z"))
                    delta = _dt.datetime.utcnow() - added_dt
                    if delta.days <= 3:
                        emo_items.append((ref.text or "")[:150])
                except Exception:
                    emo_items.append((ref.text or "")[:150])
            if len(emo_items) >= 2:
                break
        if emo_items:
            ctx["emotional_moments"] = emo_items
    except Exception as exc:
        log.debug("morning_checkin: emotional moments load failed (non-fatal): %s", exc)

    # Portrait snippet (first 200 chars — enough for personal note)
    try:
        async with db.execute(
            "SELECT portrait_text FROM user_portraits WHERE user_id=? ORDER BY last_generated DESC LIMIT 1",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
        if row and row[0]:
            ctx["portrait_snippet"] = row[0][:300]
    except Exception as exc:
        log.debug("morning_checkin: portrait load failed (non-fatal): %s", exc)

    # Multica board summary: pending proposals + flagged review items.
    # ADMIN-ONLY: the engineering board is operator state, not companion
    # content. Before this gate the brief told ANY user — the kiosk guest
    # included — "there are N open items on the board; agents will triage
    # automatically", which both leaks household engineering state to
    # non-admins and opens a companion good-morning with dev-console noise
    # (heard on the first live spoken brief, 2026-07-19). Doctrine: hide the
    # engines (docs/VISION.md; the HA/MA embed-don't-expose rule). Role lookup
    # failures fail CLOSED — no role proof, no board line.
    caller_is_admin = False
    try:
        cursor = await db.execute("SELECT role FROM users WHERE id = ?", (user_id,))
        role_row = await cursor.fetchone()
        caller_is_admin = bool(role_row) and str(role_row["role"] or "").lower() == "admin"
    except Exception as exc:
        log.debug("morning_checkin: role lookup failed (non-fatal, board hidden): %s", exc)
    if caller_is_admin:
        try:
            from multica_client import get_multica_client  # type: ignore[import]
            mc = get_multica_client()
            if mc.is_configured():
                todo_issues = await mc.list_issues(status="todo")
                in_prog = await mc.list_issues(status="in_progress")
                pending_count = len(todo_issues)
                in_prog_count = len(in_prog)
                if pending_count + in_prog_count > 0:
                    ctx["board_summary"] = {
                        "pending": pending_count,
                        "in_progress": in_prog_count,
                    }
        except Exception as exc:
            log.debug("morning_checkin: board summary load failed (non-fatal): %s", exc)

    return ctx


def _compose_morning_message(ctx: dict, user_name: str, day_str: str) -> str:
    """Build a rich morning brief message from context components."""
    parts = []

    # Greeting
    name_part = f" {user_name}" if user_name else ""
    parts.append(f"Good morning{name_part}! It's {day_str}.")

    # Calendar preview
    calendar = ctx.get("calendar", [])
    if calendar:
        if len(calendar) == 1:
            ev = calendar[0]
            time_str = f" at {ev['start']}" if ev["start"] else ""
            parts.append(f"You have {ev['title']}{time_str} today.")
        else:
            event_titles = ", ".join(ev["title"] for ev in calendar[:3])
            parts.append(f"You have {len(calendar)} things today: {event_titles}.")

    # Open loops worth following up
    loops = ctx.get("open_loops", [])
    if loops:
        top = loops[0]
        hint = top["hint"]
        if hint:
            parts.append(f"Following up: {hint}")
        else:
            parts.append(f"Checking in on: {top['text'][:80]}")

    # Emotional moments needing acknowledgment
    emo = ctx.get("emotional_moments", [])
    if emo and not loops:  # avoid double-stacking with loop follow-up
        parts.append(f"I've been thinking about you — {emo[0][:60]}...")

    # Multica board summary
    board = ctx.get("board_summary")
    if board and (board["pending"] + board["in_progress"]) > 0:
        total = board["pending"] + board["in_progress"]
        parts.append(
            f"There {'is' if total == 1 else 'are'} {total} open item{'s' if total > 1 else ''} "
            f"on the board — agents will triage automatically."
        )

    # Portrait-informed personal note (fallback generic)
    portrait = ctx.get("portrait_snippet", "")
    if not calendar and not loops and not emo and not board:
        if portrait:
            parts.append("Ready to start the day? I can check your calendar or help you plan.")
        else:
            parts.append("Ready to start the day? Let me know what you need.")

    return " ".join(parts)


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
            "SELECT user_id FROM proactive_pending WHERE trigger_type=? AND created_at::date = CURRENT_DATE AND claimed=0",
            ("morning_checkin",),
        ) as cur:
            already_fired = {row[0] async for row in cur}

        # Get active users with their display names (anyone who chatted in last 7 days)
        async with db.execute(
            """SELECT DISTINCT cs.user_id, u.name AS username
               FROM chat_sessions cs
               LEFT JOIN users u ON u.id = cs.user_id
               WHERE cs.created_at::timestamptz > (CURRENT_TIMESTAMP - INTERVAL '7 days')"""
        ) as cur:
            users = [(row[0], row[1] or "") async for row in cur]

        results = []
        for user_id, username in users:
            if user_id in already_fired:
                continue

            ctx = await _build_morning_context(db, user_id, today)
            ctx["day"] = now.strftime("%A, %B %d")
            ctx["user_id"] = user_id

            message = _compose_morning_message(ctx, username, now.strftime("%A, %B %d"))

            results.append(
                TriggerResult(
                    user_id=user_id,
                    message=message,
                    trigger_type="morning_checkin",
                    item_id="morning_checkin",
                    context=ctx,
                )
            )
        return results
