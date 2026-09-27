"""Brief-on-arrival (B2.1 slice) — speak a missed morning brief when the member shows up.

The 07:30 ``morning_checkin`` brief is spoken only if its member is at a panel at
that moment. Otherwise it is created (push + ``proactive_pending``) but not heard,
or only the ``bound_guest`` teaser is spoken. This module speaks the full brief
ONCE, the first time that member is present as ``owner`` between 07:00 and 11:00
local, on the panel where they were seen.

Flag-dark: ``ZOE_PROACTIVE_BRIEF_ON_ARRIVAL`` (default OFF, read per call) AND the
master spoken switch ``ZOE_PROACTIVE_SPOKEN`` must both be on. With either off
every entry point returns at once — no DB access, no task.

"Present" is ``panel_presence_tier == owner``: a fresh member-owned foreground
``ui_panel_sessions`` row, which only a member's own session writes. The hook is
the kiosk executor's bind/sync in ``routers/ui_actions.py`` (the moment the row
becomes member-owned). Face/voice claims have no server-side record yet, so they
do not count (see docs/knowledge/synthetic-users-and-proactive-recipients.md).

Gates, in order (cheap first):
  * flag on; the id is not synthetic or a guest sentinel (``user_filters``);
  * 07:00 <= local hour < 11:00, and not in quiet hours (``engine._is_in_quiet_hours``);
  * presence is ``owner`` on THIS panel;
  * today's ``morning_checkin`` brief exists and was not opened in chat
    (``proactive_pending.claimed``);
  * the full brief was not already spoken (``voice_announcements.delivered_at``)
    and is not in flight (queued, unexpired) — a delivered guest teaser does NOT
    count as heard;
  * no user turn by the member in the last 2 minutes (do not talk over them);
  * the per-member, per-local-day claim row in ``proactive_responses`` inserts
    (UNIQUE — two panels or two workers can never both speak it).

The claim row is also the B2.2 reward signal: the slow loop's sweep
(``evaluate_pending_responses``) later records whether the member spoke to Zoe
within ``ZOE_PROACTIVE_ARRIVAL_RESPONSE_S`` (default 120 s) of the brief being
spoken — ``accepted`` / ``ignored`` — or ``undelivered`` if the daemon never
played it.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
import zoneinfo
from datetime import datetime, timedelta, timezone

from db_compat import get_compat_db as _get_compat_db
from user_filters import is_synthetic_user, message_owner_expr

log = logging.getLogger(__name__)

TRIGGER_TYPE = "morning_checkin_arrival"
BRIEF_TRIGGER = "morning_checkin"

_WINDOW_START_HOUR = 7
_WINDOW_END_HOUR = 11  # exclusive
_RECENT_TURN_S = 120
_DEFAULT_RESPONSE_S = 120
# A member's kiosk syncs every ~5 s; re-run the DB checks at most this often.
_RECHECK_S = 30
_TS_FMT = "%Y-%m-%dT%H:%M:%SZ"

_ZOE_TZ = zoneinfo.ZoneInfo(os.environ.get("ZOE_TIMEZONE", "Australia/Perth"))

# In-process throttles only. Correctness (once per member per day) rests on the
# UNIQUE claim row, never on these, so a restart or a second worker is safe.
_done_for_day: dict[str, str] = {}
_last_check: dict[str, float] = {}
_inflight: set[str] = set()
_tasks: set[asyncio.Task] = set()


def arrival_enabled() -> bool:
    """``ZOE_PROACTIVE_BRIEF_ON_ARRIVAL`` AND the master ``ZOE_PROACTIVE_SPOKEN``
    (``engine._spoken_enabled``). Both read per call, both default OFF."""
    raw = os.environ.get("ZOE_PROACTIVE_BRIEF_ON_ARRIVAL", "").strip().lower()
    if raw not in ("1", "true", "yes", "on"):
        return False
    from proactive.engine import _spoken_enabled

    return _spoken_enabled()


def _response_window_s() -> int:
    raw = os.environ.get("ZOE_PROACTIVE_ARRIVAL_RESPONSE_S", "").strip()
    try:
        value = int(raw) if raw else _DEFAULT_RESPONSE_S
    except ValueError:
        return _DEFAULT_RESPONSE_S
    return value if value > 0 else _DEFAULT_RESPONSE_S


def _now_utc() -> datetime:
    """The clock. Tests monkeypatch this one function."""
    return datetime.now(timezone.utc)


def _fmt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime(_TS_FMT)


def _parse_ts(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _in_window(local: datetime) -> bool:
    return _WINDOW_START_HOUR <= local.hour < _WINDOW_END_HOUR


def _day_start_utc(local: datetime) -> str:
    """Local midnight of ``local``'s day, as an ISO-Z UTC string."""
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return _fmt(midnight)


def _reset_state() -> None:
    """Clear the in-process throttles (tests; simulates a restart)."""
    _done_for_day.clear()
    _last_check.clear()
    _inflight.clear()


def schedule_on_owner_presence(user_id: str, panel_id: str) -> None:
    """Hook for a member's own foreground panel bind/sync. Never raises, never blocks.

    Cheap gates run inline; the DB checks and the speaking run in a background
    task so the kiosk's sync request is not delayed.
    """
    try:
        if not arrival_enabled():
            return
        if not user_id or not panel_id or is_synthetic_user(user_id):
            return
        local = _now_utc().astimezone(_ZOE_TZ)
        if not _in_window(local):
            return
        today = local.date().isoformat()
        if _done_for_day.get(user_id) == today or user_id in _inflight:
            return
        mono = time.monotonic()
        if mono - _last_check.get(user_id, float("-inf")) < _RECHECK_S:
            return
        _last_check[user_id] = mono
        _inflight.add(user_id)
        task = asyncio.get_running_loop().create_task(
            _run_guarded(user_id, panel_id)
        )
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
    except Exception as exc:
        _inflight.discard(user_id)
        log.warning("brief-on-arrival: schedule failed for user=%s: %s", user_id, exc)


async def _run_guarded(user_id: str, panel_id: str) -> None:
    try:
        await maybe_speak_brief_on_arrival(user_id, panel_id)
    finally:
        _inflight.discard(user_id)


async def _todays_brief(db, user_id: str, day_start: str) -> dict | None:
    """Today's ``morning_checkin`` pending rows: latest message + whether any was opened."""
    async with db.execute(
        """SELECT id, message, claimed FROM proactive_pending
           WHERE user_id = ? AND trigger_type = ?
             AND created_at::timestamptz >= ?::timestamptz
           ORDER BY created_at::timestamptz DESC""",
        (user_id, BRIEF_TRIGGER, day_start),
    ) as cur:
        rows = await cur.fetchall()
    if not rows:
        return None
    return {
        "id": rows[0]["id"],
        "message": str(rows[0]["message"] or ""),
        "opened": any(int(r["claimed"] or 0) == 1 for r in rows),
    }


async def _spoken_state(db, user_id: str, day_start: str, message: str, now: datetime) -> str:
    """How today's 07:30 brief fared on the speaker.

    ``delivered`` (the full text was claimed by the daemon) > ``in_flight``
    (queued, unexpired) > ``guest_teaser`` (only the bound_guest line was
    played) > ``expired`` (queued, never played) > ``absent`` (never queued).
    """
    async with db.execute(
        """SELECT message, delivered_at, expired, expires_at FROM voice_announcements
           WHERE user_id = ? AND trigger_type = ?
             AND created_at::timestamptz >= ?::timestamptz""",
        (user_id, BRIEF_TRIGGER, day_start),
    ) as cur:
        rows = await cur.fetchall()
    full = message.strip()
    states: set[str] = set()
    for row in rows:
        is_full = str(row["message"] or "").strip() == full
        if row["delivered_at"]:
            states.add("delivered" if is_full else "guest_teaser")
        elif is_full:
            exp = _parse_ts(row["expires_at"])
            live = not int(row["expired"] or 0) and exp is not None and exp > now
            states.add("in_flight" if live else "expired")
    for state in ("delivered", "in_flight", "guest_teaser", "expired"):
        if state in states:
            return state
    return "absent"


async def _first_user_turn(db, user_id: str, start: str, end: str):
    """Earliest user turn owned by ``user_id`` in ``[start, end]``, else None."""
    async with db.execute(
        f"""SELECT MIN(cm.created_at::timestamptz) AS first_turn
            FROM chat_messages cm
            JOIN chat_sessions cs ON cm.session_id = cs.id
            WHERE cm.role = 'user'
              AND cm.created_at::timestamptz >= ?::timestamptz
              AND cm.created_at::timestamptz <= ?::timestamptz
              AND ({message_owner_expr()}) = ?""",
        (start, end, user_id),
    ) as cur:
        row = await cur.fetchone()
    return _parse_ts(row["first_turn"]) if row else None


async def _claim_today(
    db, *, user_id: str, panel_id: str, local_date: str, pending_id: str,
    missed: str, now: datetime,
) -> str | None:
    """Insert today's claim row; None if one already exists (once per member per day)."""
    claim_id = uuid.uuid4().hex[:16]
    async with db.execute(
        """INSERT INTO proactive_responses
               (id, user_id, trigger_type, local_date, panel_id, pending_id,
                missed, response_window_s, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (user_id, trigger_type, local_date) DO NOTHING
           RETURNING id""",
        (claim_id, user_id, TRIGGER_TYPE, local_date, panel_id, pending_id,
         missed, _response_window_s(), _fmt(now)),
    ) as cur:
        row = await cur.fetchone()
    await db.commit()
    return row["id"] if row else None


async def maybe_speak_brief_on_arrival(user_id: str, panel_id: str) -> str:
    """Speak today's missed brief on ``panel_id`` if every gate passes.

    Returns a short outcome word (logged; used by tests). Never raises.
    ``done:*`` outcomes stop further checks for this member today.
    """
    try:
        if not arrival_enabled():
            return "disabled"
        if not user_id or not panel_id or is_synthetic_user(user_id):
            return "synthetic"
        now = _now_utc()
        local = now.astimezone(_ZOE_TZ)
        if not _in_window(local):
            return "outside_window"
        from proactive import engine as _engine
        from proactive import presence as _presence

        if _engine._is_in_quiet_hours(now):
            return "quiet_hours"
        today = local.date().isoformat()
        tier, seen_panel = await _presence.panel_presence_tier(user_id)
        if tier != _presence.TIER_OWNER:
            return f"tier_{tier}"
        if seen_panel != panel_id:
            return "other_panel"

        day_start = _day_start_utc(local)
        async with _get_compat_db() as db:
            brief = await _todays_brief(db, user_id, day_start)
            if brief is None:
                return "no_brief"
            if brief["opened"]:
                _done_for_day[user_id] = today
                return "done:opened_in_chat"
            state = await _spoken_state(db, user_id, day_start, brief["message"], now)
            if state == "delivered":
                _done_for_day[user_id] = today
                return "done:already_heard"
            if state == "in_flight":
                return "in_flight"
            recent = await _first_user_turn(
                db, user_id, _fmt(now - timedelta(seconds=_RECENT_TURN_S)), _fmt(now),
            )
            if recent is not None:
                return "recent_turn"
            claim_id = await _claim_today(
                db, user_id=user_id, panel_id=panel_id, local_date=today,
                pending_id=brief["id"], missed=state, now=now,
            )
        if claim_id is None:
            _done_for_day[user_id] = today
            return "done:already_fired"
        # The claim is taken: from here the member is done for today whatever
        # the lanes report (at most once — a failed speak is not retried).
        _done_for_day[user_id] = today

        # No pooled connection is held across the lanes (they open their own).
        panel_outcome, daemon_outcome, ann_id = await _engine._speak_on_panel(
            user_id=user_id, panel_id=panel_id, message=brief["message"],
            trigger_type=TRIGGER_TYPE,
        )
        try:
            async with _get_compat_db() as db:
                await db.execute(
                    "UPDATE proactive_responses SET announcement_id = ?, spoken_at = ? WHERE id = ?",
                    (ann_id, _fmt(now), claim_id),
                )
                await db.commit()
        except Exception as exc:
            log.warning("brief-on-arrival: could not record announcement for claim %s: %s",
                        claim_id, exc)
        log.info("PROACTIVE_SPOKEN trigger=%s user=%s panel=%s outcome=%s daemon_queue=%s "
                 "tier=%s missed=%s", TRIGGER_TYPE, user_id, panel_id, panel_outcome,
                 daemon_outcome, tier, state)
        return "spoken"
    except Exception as exc:
        log.warning("PROACTIVE_SPOKEN trigger=%s user=%s outcome=error err=%s",
                    TRIGGER_TYPE, user_id, exc)
        return "error"


async def evaluate_pending_responses() -> int:
    """Record accepted/ignored/undelivered for arrival briefs whose window has closed.

    Called from the engine slow loop. A no-op (no DB) with the flag off. Returns
    the number of rows evaluated. Never raises.
    """
    if not arrival_enabled():
        return 0
    evaluated = 0
    try:
        now = _now_utc()
        async with _get_compat_db() as db:
            async with db.execute(
                """SELECT r.id, r.user_id, r.announcement_id, r.response_window_s,
                          r.spoken_at, r.created_at,
                          va.delivered_at, va.expired, va.expires_at
                   FROM proactive_responses r
                   LEFT JOIN voice_announcements va ON va.id = r.announcement_id
                   WHERE r.trigger_type = ? AND r.evaluated_at IS NULL""",
                (TRIGGER_TYPE,),
            ) as cur:
                rows = await cur.fetchall()
            for row in rows:
                window = int(row["response_window_s"] or _DEFAULT_RESPONSE_S)
                delivered = _parse_ts(row["delivered_at"])
                outcome: str | None = None
                responded: int | None = None
                responded_at: str | None = None
                if delivered is None:
                    exp = _parse_ts(row["expires_at"])
                    created = _parse_ts(row["created_at"]) or now
                    never_queued = not row["announcement_id"]
                    gone = bool(int(row["expired"] or 0)) or exp is None or exp <= now
                    # A claim with no queued row (the lane failed) is undelivered
                    # once the response window has passed since the claim.
                    if (never_queued and created + timedelta(seconds=window) <= now) or (
                        not never_queued and gone
                    ):
                        outcome = "undelivered"
                elif delivered + timedelta(seconds=window) <= now:
                    first = await _first_user_turn(
                        db, row["user_id"], _fmt(delivered),
                        _fmt(delivered + timedelta(seconds=window)),
                    )
                    responded = 1 if first is not None else 0
                    responded_at = _fmt(first) if first is not None else None
                    outcome = "accepted" if first is not None else "ignored"
                if outcome is None:
                    continue  # window still open / announcement still queued
                await db.execute(
                    """UPDATE proactive_responses
                       SET outcome = ?, responded = ?, responded_at = ?, evaluated_at = ?
                       WHERE id = ? AND evaluated_at IS NULL""",
                    (outcome, responded, responded_at, _fmt(now), row["id"]),
                )
                evaluated += 1
                log.info("PROACTIVE_RESPONSE trigger=%s user=%s outcome=%s window_s=%d",
                         TRIGGER_TYPE, row["user_id"], outcome, window)
            await db.commit()
    except Exception as exc:
        log.warning("brief-on-arrival: response sweep failed: %s", exc)
    return evaluated
