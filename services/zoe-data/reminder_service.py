"""The reminder store: every entry point creates, edits, snoozes, completes and cancels through the ``*_reminder_record``
functions here, under ONE ownership rule (``load_for_actor``) and the household clock (``reminder_scan.zoe_now``).

It is also the reminders DOOR of the Zoe OS kernel (``ZOE_OS_REMINDERS=kernel``; ``_gate`` below): each of those functions asks
the kernel before its write and reports after it. The kernel decides and records ids only; the rows stay here."""

from __future__ import annotations

import contextvars
import json
import logging
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Mapping

from fastapi import HTTPException

import kernel_door
import os_shadow
import write_receipts
from guest_policy import require_feature_access
from models import ReminderCreate
from push import broadcaster

logger = logging.getLogger(__name__)

# Spoken replies for the reminder verbs the keyword path answers in code (short, the tone of "Reminder set: X.").
SPOKEN = {
    "past_day": "That day has already passed, so I haven't set the reminder.",
    "past_time": "That time has already passed today, so I haven't set the reminder.",
    "cancelled": "Reminder cancelled: {title}.",
    "cancel_nothing": "I haven't just set a reminder, so there's nothing to cancel.",
    "cancel_not_found": "I couldn't find that reminder, so nothing has changed.",
    "cancel_failed": "I couldn't cancel that reminder just now, so nothing has changed.",
}


# The brain's dispatch endpoint needs a past day or time as the 422 it is (its log and the brain's tool both read the
# status); a spoken fast-path turn needs the sentence. The endpoint sets this for the length of its call.
PAST_IS_AN_ERROR: contextvars.ContextVar[bool] = contextvars.ContextVar("reminder_past_is_an_error", default=False)


class ReminderPastError(HTTPException):
    """A 422 for a day or time that has already gone (the scan never fires those). ``spoken`` is what a person is told."""

    def __init__(self, detail: str, spoken: str):
        super().__init__(status_code=422, detail=detail)
        self.spoken = spoken


# ── The Zoe OS reminders door (ZOE_OS_REMINDERS=kernel; docs/architecture/zoe-os-cutover-plan.md, "Changed 2026-11-18") ──────────────
# Every ``*_reminder_record`` function (the screen's five and the brain tools' three) ASKS the kernel before its write (``_gate``) and REPORTS after it (``_reported``). The
# mechanism is the one every area shares (``kernel_door`` + the door section of ``os_shadow``); what is here is the reminders'
# own: the verbs, who a reminder belongs to (``reminders.user_id``: the person it is FOR; ``visibility = 'family'`` = addressed
# to the household), and the refusal sentences. With the flag at any other value: a flag read, no kernel call, no extra
# statement, and every write is exactly what it was. The door reads nothing of its own from the database: the row every edit
# already loads (``load_for_actor``) names the owner, and that load raises on a database fault like any other read.
# The edit and delete TOOLS only ever touch the member's own row (their statement says so), so the owner is the actor; the
# snooze tool calls ``snooze_reminder_record`` (the screen's snooze), whose row is already loaded.
class ReminderRefused(kernel_door.Refused):
    """The kernel refused this reminder change. FINAL: nothing was written and no job was touched. ``reason`` is a closed code
    (``os_shadow.DOOR_REFUSALS``); each caller turns it into its own refusal (``line()`` is the plain sentence they share)."""

    area = "reminders"
    lines = {
        "not_owner": "That reminder is someone else's, so I can't change it.",
        "adults_only": "Only the person that reminder is for, or a grown-up, can change or delete it.",
        "unknown_person": "I don't know who is asking, so I can't change that reminder.",
    }
    fallback = "I'm not allowed to change that reminder."


def stamps_now() -> dict | None:
    """The erase stamps as they are NOW, or None with every door off (``kernel_door.stamps_now``). Every ``*_reminder_record``
    function takes this as its FIRST statement, before anything it awaits (the row read that names the reminder's owner
    included). A caller of ``create_reminder_record`` TELLS the door who the reminder is for, so it takes its own as ITS first
    statement and passes it as ``stamps_at`` (``tests/test_reminders_kernel_door.py`` scans every caller)."""
    return kernel_door.stamps_now()


async def _gate(verb: str, *, actor: Mapping[str, object], reminder_id: str, owner: object, visibility: object,
                stamps_at: dict | None) -> "kernel_door.Decision | None":
    """The ask at the door: may ``actor`` do ``verb`` to this reminder? None with the flag off (``stamps_at`` is None when every
    door is off; else ONE more flag read). Raises ``ReminderRefused`` on a kernel refusal; a kernel that is down or slow is
    ``not_checked`` and the change proceeds. Only ids cross: the reminder's id, the acting member, the member it is for."""
    if stamps_at is None or not os_shadow.area_on("reminders"):
        return None
    user_id = str(actor.get("user_id") or "")
    if not user_id:
        os_shadow.door_unchecked(verb, "no_actor", "reminders")
        return None
    panel = kernel_door.is_panel(user_id)
    decision, reason, ask, stamps = await os_shadow.ask_door(
        "reminders", verb, "" if panel else user_id, panel=panel, target=str(reminder_id), owner=str(owner or "") or None,
        household=str(visibility or "") == "family", stamps_at=stamps_at)
    if decision == "refuse":
        raise ReminderRefused(reason)
    return kernel_door.Decision(decision, reason, ask, stamps=stamps, area="reminders")


_reported = kernel_door.reported      # around a door's own writes: ``done`` once they returned, ``failed`` if one raised


def normalize_due_date(raw: object, *, now_utc: datetime | None = None) -> str | None:
    """Resolve a reminder `due_date` to ISO `YYYY-MM-DD` at WRITE time.

    Relative days resolve against the HOUSEHOLD clock (`ZOE_TIMEZONE`, the same
    clock `reminder_scan` fires against — `reminder_scan.zoe_now`), never the
    server's local date: at 23:30 UTC a +08:00 household is already on the next
    day, and "today" must mean THEIR today. `now_utc` is for tests.

    Callers (the API, and the `reminder_create` intent the brain's `add_reminder`
    tool dispatches) pass whatever the model or user said — the 2026-09-25 audit
    found the literal string "tomorrow" stored as a due_date, which
    `reminder_scan` could never parse, so that reminder silently never fired.

    - None / blank → None (no date; time-only reminders are daily).
    - ISO date, or ISO datetime (date part kept) → as-is.
    - Relative day ("today", "tomorrow", "next friday", "june 3") → resolved via
      the same grammar the calendar quick-add already uses
      (`intent_router._parse_date`, imported lazily to keep this module light).
    - A bare month-day with NO year ("june 3") that is already past this year
      means NEXT year's — nobody sets a reminder for a day that has gone.
    - An explicit date in the past (ISO, or a phrase carrying a year) → 422:
      the scan never fires past-dated rows, so storing one is the silent-never-
      fires bug this function exists to prevent. Today is allowed.
    - Anything else → 422; a reminder with an unfireable date is worse than an
      error the caller can see.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    from proactive.triggers.reminder_scan import zoe_now

    today = zoe_now(now_utc).date()

    def _not_past(d: date) -> str:
        if d < today:
            raise ReminderPastError(
                f"due_date {text!r} is already past ({d.isoformat()}); a reminder for a past day can never fire",
                SPOKEN["past_day"],
            )
        return d.isoformat()

    # ISO date, or ISO datetime ("2026-06-15T09:00") — keep the date part.
    iso_candidate = text[:10] if len(text) > 10 and text[10] in "T " else text
    try:
        return _not_past(date.fromisoformat(iso_candidate))
    except ValueError:
        pass
    from intent_router import _parse_date  # lazy: intent_router is the heavy module

    relative = text.lower()
    # The spoken lead-ins a model copies from the request: "on the 15th of December" → "the 15th of december" (the grammar
    # drops a leading "the"); "December the 15th" → "december 15th".
    relative = re.sub(r"^on\s+", "", relative)
    relative = re.sub(r"^([a-z]+)\s+the\s+(?=\d)", r"\1 ", relative)
    if relative.startswith("next "):
        relative = relative[5:].strip()  # "next friday" → the weekday grammar
    resolved = _parse_date(relative, today=today)
    if resolved:
        # _parse_date recognises the PHRASE, not the calendar: "June 31" comes
        # back as "2026-06-31". Re-validate so an unfireable date is a 422 here,
        # never a stored row the scan can only warn about (Codex P2, #1686).
        try:
            d = date.fromisoformat(resolved)
        except ValueError:
            d = None
        if d is not None:
            has_year = re.search(r"\b\d{4}\b", relative) is not None
            if d < today and not has_year:
                # Bare "june 3" after June 3 → next year's June 3 (Greptile P1, #1689).
                try:
                    d = d.replace(year=d.year + 1)
                except ValueError:  # Feb 29 → the next leap year is not "next year"
                    raise HTTPException(status_code=422, detail=f"due_date {text!r} has no valid next occurrence")
            return _not_past(d)
    raise HTTPException(
        status_code=422,
        detail=f"due_date {text!r} is not a date; use YYYY-MM-DD or a day like 'tomorrow'",
    )


# Spoken clock forms the scanner's strict HH:MM parser does not read: "10am", "10 am", "10 a.m.", "10.30pm", "10:00:00".
_SPOKEN_MERIDIEM_RE = re.compile(r"^(\d{1,2})(?:[:.](\d{2}))?\s*([ap])\.?\s*m\.?$")
_CLOCK_SECONDS_RE = re.compile(r"^(\d{1,2}):(\d{2}):\d{2}$")
_NAMED_TIMES = {"noon": "12:00", "midday": "12:00", "midnight": "00:00"}


def _canonical_spoken_time(text: str) -> str | None:
    """'HH:MM' (24 h) for a spoken clock time the strict parser rejects, else None.

    Live 2026-11-04: "remind me on the 15th of December at 10am to book the car in for a service" failed twice on the chat
    lane - the brain's `add_reminder` dispatch came back 422 (only 'HH:MM' / 'HH:MM AM' counted as clock times, so the
    user's own "10am" did not), and the reply said the reminder service was "having trouble". An hour with am/pm must be
    1-12; no meridiem and no minutes ("10") stays a 422 - "7" could be morning or evening, and a reminder must not guess."""
    s = text.strip().lower()
    if s in _NAMED_TIMES:
        return _NAMED_TIMES[s]
    m = _SPOKEN_MERIDIEM_RE.match(s)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2) or 0)
        if not (1 <= hour <= 12 and 0 <= minute <= 59):
            return None
        if m.group(3) == "p" and hour != 12:
            hour += 12
        elif m.group(3) == "a" and hour == 12:
            hour = 0
        return f"{hour:02d}:{minute:02d}"
    m = _CLOCK_SECONDS_RE.match(s)
    if m and 0 <= int(m.group(1)) <= 23 and 0 <= int(m.group(2)) <= 59:
        return f"{int(m.group(1)):02d}:{m.group(2)}"
    return None


def normalize_due_time(raw: object) -> str | None:
    """Validate a reminder `due_time` at WRITE time: blank → None; a real clock
    time ('08:42', '10:25 PM') → kept as given; a spoken clock time the scan's
    parser does not read ('10am', '10 a.m.', 'noon', '10:00:00') → canonical
    'HH:MM'; anything else ('25:00', '09:99', a bare '10') → 422. Stored legacy
    rows are handled leniently by the scan; new writes must not create more of them."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    from proactive.triggers.reminder_scan import _parse_due_time

    if _parse_due_time(text) is None:
        spoken = _canonical_spoken_time(text)
        if spoken is not None:
            return spoken
        raise HTTPException(
            status_code=422,
            detail=f"due_time {text!r} is not a clock time; use HH:MM (00-23:00-59), optionally with AM/PM",
        )
    return text


def normalize_recurrence_fields(
    raw_pattern: object, due_date: str | None, due_time: str | None, *, now_utc: datetime | None = None
) -> tuple[str | None, str | None]:
    """Resolve a reminder's recurrence at WRITE time → (rrule, anchor due_date).

    `raw_pattern` may be an RRULE or a spoken phrase ("every weekday"); it is
    stored as a canonical RRULE, never free text the scan cannot run. The
    returned due_date is the FIRST occurrence on/after the given date (or today)
    that is still in the future, which the scan then uses as the rule's anchor.
    Unsupported patterns → 422. No pattern → (None, due_date) unchanged."""
    from reminder_recurrence import first_occurrence_date, normalize_recurrence, parse_rrule

    try:
        rrule = normalize_recurrence(raw_pattern)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=f"recurring_pattern {str(raw_pattern)!r} is not a supported recurrence "
            "(RRULE FREQ=DAILY|WEEKLY|MONTHLY|YEARLY, or e.g. 'every weekday')",
        )
    if rrule is None:
        return None, due_date
    from proactive.triggers.reminder_scan import _ZOE_TZ, _default_due_hm, _parse_due_time, zoe_now

    now = zoe_now(now_utc)
    hour, minute = _parse_due_time(due_time or "") or _default_due_hm()
    start = date.fromisoformat(due_date) if due_date else now.date()
    first = first_occurrence_date(parse_rrule(rrule), start, hour, minute, now, _ZOE_TZ)
    if first is None:
        raise HTTPException(status_code=422, detail=f"recurrence {rrule!r} has no upcoming occurrence")
    return rrule, first.isoformat()


def refuse_past_due(due_date: str | None, due_time: str | None, *, now_utc: datetime | None = None) -> None:
    """A one-off for today at a clock time already gone never fires (the scan skips it), so it is refused. Time-only rows
    are daily, date-only rows have the same-day fallback, and recurring rows are anchored ahead: none is refused."""
    if not due_date or not due_time:
        return
    from proactive.triggers.reminder_scan import _parse_due_time, build_run_at

    hm = _parse_due_time(due_time)
    now = now_utc or datetime.now(timezone.utc)
    run_at = build_run_at(due_date, hm[0], hm[1], now) if hm else None
    if run_at is not None and run_at <= now:
        raise ReminderPastError(
            f"due_time {due_time!r} on {due_date} is already past; a reminder for a past time can never fire",
            SPOKEN["past_time"],
        )


def row_to_dict(row) -> dict | None:
    """Convert asyncpg/compat rows to a plain reminder dict."""
    if row is None:
        return None
    data = dict(row)
    for key in ("is_active", "acknowledged", "deleted"):
        if key in data and data[key] is not None:
            data[key] = bool(data[key])
    return data


async def _create_notification(db, *, user_id: str, notif_type: str, title: str, message: str, data: dict) -> None:
    await db.execute(
        """INSERT INTO notifications (id, user_id, type, title, message, data, delivered, created_at)
           VALUES (?, ?, ?, ?, ?, ?, 0, NOW())""",
        (
            str(uuid.uuid4()),
            user_id,
            notif_type,
            title,
            message,
            json.dumps(data or {}),
        ),
    )


async def create_reminder_record(payload: ReminderCreate, *, user: Mapping[str, object], db,
                                 now_utc: datetime | None = None, schedule: bool = True,
                                 after_commit: "list | None" = None, shadow_source: str = "service",
                                 stamps_at: dict | None = None) -> dict:
    """Create a reminder with the same policy, notification, and broadcast behavior as the API route. ``now_utc`` (a background caller
    that runs on an injected clock, e.g. ``commitments``) is the instant "today" is resolved against; none = the wall clock. ``schedule=False`` is for a caller that registers its OWN job for the row (commitments, +45 s): one scheduling path per reminder, never two.

    ``after_commit``: a caller that owns an OUTER transaction passes a list; the receipt, the scheduled job and the broadcast are
    then NOT run here but appended to it as coroutine functions of ONE argument, for the caller to run once its transaction has
    committed (a rolled-back reminder is never scheduled, announced or vouched for). The argument is the db handle the job
    check reads through: the caller runs these AFTER its transaction's connection is back in the pool (scheduling borrows a
    connection of its own, and a caller still holding one while it waits for another is how a full pool deadlocks), so it
    cannot be ``db``. ``db.commit()`` here is a no-op on Postgres,
    so only the caller's real transaction makes the row durable.

    ``stamps_at``: the caller's ``stamps_now()``, taken as the first thing the caller did. This is the one door whose reminder
    has no row to read the owner from, so the caller tells it who the reminder is for (``user``), and with it when it learned of them."""
    if stamps_at is None:
        stamps_at = stamps_now()
    await require_feature_access(db, user, feature="reminders", action="create")
    user_id = str(user["user_id"])
    reminder_id = str(uuid.uuid4())
    due_date = normalize_due_date(payload.due_date, now_utc=now_utc)
    due_time = normalize_due_time(payload.due_time)
    recurring_pattern, due_date = normalize_recurrence_fields(payload.recurring_pattern, due_date, due_time, now_utc=now_utc)
    if not recurring_pattern:
        refuse_past_due(due_date, due_time, now_utc=now_utc)
    reminder_type = payload.reminder_type
    if recurring_pattern and reminder_type == "one-time":
        reminder_type = "recurring"

    asked = await _gate("reminder.create", actor=user, reminder_id=reminder_id, owner=user_id, visibility=payload.visibility,
                        stamps_at=stamps_at)
    async with _reported(asked):
        await db.execute(
            """INSERT INTO reminders (
                id, user_id, title, description, reminder_type, category, priority,
                due_date, due_time, recurring_pattern, is_active, acknowledged,
                snoozed_until, visibility, deleted
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0, NULL, ?, 0)""",
            (
                reminder_id,
                user_id,
                payload.title,
                payload.description,
                reminder_type,
                payload.category,
                payload.priority,
                due_date,
                due_time,
                recurring_pattern,
                payload.visibility,
            ),
        )
        await _create_notification(
            db,
            user_id=user_id,
            notif_type="reminder_created",
            title="Reminder Created",
            message=f"Reminder added: {payload.title}",
            data={"reminder_id": reminder_id, "due_date": due_date, "due_time": due_time,
                  **({"recurring_pattern": recurring_pattern} if recurring_pattern else {})},
        )
        await db.commit()

    def _vouch() -> None:
        # The receipt the write-claim guard checks a reply against (write_receipts.py): the row is committed.
        write_receipts.record(user_id, "reminder", reminder_id, "create_reminder_record")
        # The Zoe OS shadow (os_shadow.py): after the commit, never raises, a no-op unless ZOE_OS_SHADOW_VERBS names reminder_create
        os_shadow.note_reminder_create(user_id, source=shadow_source, note=payload.title, due_date=due_date, due_time=due_time,
                                       recurring=bool(recurring_pattern))

    async def _announce(reminder: dict, jobs_db=db) -> None:
        # Schedule NOW rather than waiting for the slow-loop scan: that loop does nothing in quiet
        # hours (22:00-07:00) and at 07:00 treats an explicit time earlier that night as past-due, so
        # "remind me at 22:30" said at 22:15 was never scheduled. schedule_due_reminder is the same
        # idempotent entry the scan uses (skips when an unfired job exists), so nothing double-books.
        # The FIRE is still deferred out of quiet hours by fire_notification. Best-effort: the row is
        # already committed and the scan retries, so a scheduler failure must not fail the create.
        if schedule and reminder.get("id"):
            try:
                from proactive.triggers.reminder_scan import schedule_due_reminder
                with _jobs_on(jobs_db):
                    await schedule_due_reminder(jobs_db, reminder, now_utc=now_utc)
            except Exception:
                logger.exception("reminder %s created but not scheduled yet (slow loop will retry)", reminder_id)
        await broadcaster.broadcast("reminders", "reminder_created", reminder, user_id=user_id)

    if after_commit is None:          # the row is committed: exactly the order this function always ran in
        _vouch()
    cursor = await db.execute("SELECT * FROM reminders WHERE id = ?", [reminder_id])
    row = await cursor.fetchone()
    reminder = row_to_dict(row) or {}
    if after_commit is None:
        await _announce(reminder)
    else:                             # read back inside the caller's transaction; everything else waits for its commit
        async def _after(jobs_db) -> None:
            # The row is read AGAIN here, after the commit: the copy read inside the transaction can be stale by now (the
            # member's forget, or a delete, can land between the commit and this step). A reminder that has gone is not
            # scheduled, not broadcast and not told to the shadow; only the receipt of the write that did happen is kept.
            cur = await jobs_db.execute("SELECT * FROM reminders WHERE id = ? AND user_id = ? AND deleted = 0", [reminder_id, user_id])
            fresh = row_to_dict(await cur.fetchone())
            if not fresh:
                write_receipts.record(user_id, "reminder", reminder_id, "create_reminder_record")
                logger.info("reminder create: the row had gone before its after-commit step; nothing scheduled or announced")
                return
            _vouch()
            await _announce(fresh, jobs_db)

        after_commit.append(_after)
    return reminder


# ── The ONE ownership-and-visibility rule (the REST router's, the most complete of the five that were in the tree) ────────
# SEE own + family rows (another member's personal row: 404); EDIT / CANCEL: anyone who sees it (family rows are shared);
# SNOOZE / DONE: the owner only (403); VISIBILITY: only the owner changes it (dropped for others). ``own_only=True`` keeps a
# STRICTER entry point stricter (the MCP edit / delete tools, the spoken cancel): another member's family row is 404 there.
_OWNER_ONLY = {"snooze": "snooze", "done": "acknowledge"}


async def load_for_actor(db, actor: Mapping[str, object], reminder_id: str, *, verb: str, own_only: bool = False) -> dict:
    user_id = str(actor["user_id"])
    cursor = await db.execute(
        "SELECT * FROM reminders WHERE (visibility = 'family' OR user_id = ?) AND deleted = 0 AND id = ?",
        [user_id, reminder_id],
    )
    row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Reminder not found")
    row = dict(row)
    is_owner = row.get("user_id") == user_id
    if own_only and not is_owner:
        raise HTTPException(status_code=404, detail="Reminder not found")
    if verb in _OWNER_ONLY and not is_owner:
        raise HTTPException(status_code=403, detail=f"Not authorised to {_OWNER_ONLY[verb]} this reminder")
    return row


def is_rrule(pattern) -> bool:
    from reminder_recurrence import parse_rrule

    return bool(pattern) and parse_rrule(str(pattern)) is not None


def _jobs_on(db):
    """Around a door's job calls: they run on the door's OWN connection (``proactive.triggers.reminders.on_connection``), never
    on a second one borrowed while this one is held. That wait is how a full pool deadlocks: every door holds one and waits."""
    from proactive.triggers.reminders import on_connection

    return on_connection(db)


async def _cancel_reminder_jobs_safe(reminder_id: str) -> None:
    """Cancel a reminder's pending jobs BEFORE a state change commits (B2). Best-effort: the in-job claim is the backstop."""
    try:
        from proactive.triggers.reminders import cancel_reminder_jobs
        await cancel_reminder_jobs(reminder_id)
    except Exception:
        logger.exception("reminder resync: cancel stale jobs failed for %s", reminder_id)


async def _reschedule_reminder_due_safe(db, reminder_id: str) -> None:
    """Reschedule at the CURRENT next fire time, AFTER the write: a future snooze (an edit keeps it), else the due time."""
    try:
        cursor = await db.execute("SELECT * FROM reminders WHERE id = ?", [reminder_id])
        row = await cursor.fetchone()
        if row is None:
            return
        r = dict(row)
        if not (r.get("is_active") and not r.get("acknowledged") and not r.get("deleted")):
            return
        snoozed = r.get("snoozed_until")
        if snoozed:
            try:
                snooze_at = datetime.fromisoformat(str(snoozed).replace("Z", "+00:00"))
                if snooze_at.tzinfo is None:
                    snooze_at = snooze_at.replace(tzinfo=timezone.utc)
            except Exception:
                snooze_at = None
            if snooze_at and snooze_at > datetime.now(timezone.utc):
                from proactive.triggers.reminders import schedule_reminder
                with _jobs_on(db):
                    await schedule_reminder(user_id=r.get("user_id"), message=r.get("title") or "Reminder",
                                            send_at=snooze_at, item_id=reminder_id)
            return  # snooze in the past → reminder_scan will re-pick it up
        if r.get("due_time") or r.get("due_date"):
            from proactive.triggers.reminder_scan import schedule_due_reminder
            with _jobs_on(db):
                await schedule_due_reminder(db, row)
    except Exception:
        logger.exception("reminder resync: reschedule failed for %s", reminder_id)


async def _reread(db, reminder_id: str) -> dict | None:
    cursor = await db.execute("SELECT * FROM reminders WHERE id = ?", [reminder_id])
    return row_to_dict(await cursor.fetchone())


async def update_reminder_record(db, actor: Mapping[str, object], reminder_id: str, changes: Mapping[str, object], *,
                                 own_only: bool = False, now_utc: datetime | None = None) -> dict:
    """Edit (the screen's PUT, statement for statement): dates and times normalised, an RRULE row re-anchored, the old job
    cancelled before and rescheduled after the write.

    The one addition is create's past-time rule: an edit that MOVES a one-off to a clock time already gone today is refused
    (``ReminderPastError``, nothing written), because the scan never fires it. A day or time re-sent unchanged is not a move (an
    edit form sends the whole row), so renaming or switching off a reminder whose time has passed is as it always was. A
    recurring row is never refused for its time: its date is re-anchored to the next occurrence."""
    stamps_at = stamps_now()
    await require_feature_access(db, actor, feature="reminders", action="update")
    user_id = str(actor["user_id"])
    existing = await load_for_actor(db, actor, reminder_id, verb="edit", own_only=own_only)
    is_owner = existing.get("user_id") == user_id
    data = dict(changes)
    if not is_owner:
        data.pop("visibility", None)
    recurring = is_rrule(existing.get("recurring_pattern"))
    if "due_date" in data and recurring:
        # A recurring row's due_date is the rule's ANCHOR, not a due day.
        if data["due_date"] is not None and str(data["due_date"]) == str(existing.get("due_date")):
            # An edit form re-sending the stored (usually past) anchor is not a date change: keep the anchor, never 422 it.
            data.pop("due_date")
        else:
            # Re-anchor to the first occurrence on/after the new date (today when cleared). A NULL anchor would make the scan
            # re-anchor to "today" every cycle, so a monthly or every-3-days rule would fire DAILY.
            due_time = normalize_due_time(data["due_time"]) if "due_time" in data else existing.get("due_time")
            _, data["due_date"] = normalize_recurrence_fields(
                existing["recurring_pattern"], normalize_due_date(data["due_date"], now_utc=now_utc), due_time, now_utc=now_utc
            )
    updates = []
    params = []
    stored = {}
    for key, value in data.items():
        if key == "is_active":
            updates.append("is_active = ?")
            params.append(1 if value else 0)
        elif key == "due_date":
            updates.append("due_date = ?")
            params.append(normalize_due_date(value, now_utc=now_utc))
            stored[key] = params[-1]
        elif key == "due_time":
            updates.append("due_time = ?")
            params.append(normalize_due_time(value))
            stored[key] = params[-1]
        else:
            updates.append(f"{key} = ?")
            params.append(value)

    if not updates:
        return row_to_dict(existing)
    if not recurring and any(str(value) != str(existing.get(key)) for key, value in stored.items()):
        refuse_past_due(stored.get("due_date", existing.get("due_date")), stored.get("due_time", existing.get("due_time")),
                        now_utc=now_utc)

    asked = await _gate("reminder.edit", actor=actor, reminder_id=reminder_id, owner=existing.get("user_id"),
                        visibility=existing.get("visibility"), stamps_at=stamps_at)
    updates.append("updated_at = NOW()")
    # Bump the schedule generation so any old-time job that already won its claim self-voids at fire time.
    updates.append("schedule_generation = COALESCE(schedule_generation, 0) + 1")
    params.append(reminder_id)
    with _jobs_on(db):
        await _cancel_reminder_jobs_safe(reminder_id)  # B2: before the state change commits
    async with _reported(asked):
        await db.execute(f"UPDATE reminders SET {', '.join(updates)} WHERE id = ?", params)
        await db.commit()
    await _reschedule_reminder_due_safe(db, reminder_id)
    reminder = await _reread(db, reminder_id)
    await broadcaster.broadcast("reminders", "reminder_updated", reminder, user_id=user_id)
    return reminder


async def delete_reminder_record(db, actor: Mapping[str, object], reminder_id: str, *, own_only: bool = False) -> dict:
    """Cancel a reminder (soft delete): the pending job is cancelled BEFORE the row is marked deleted."""
    stamps_at = stamps_now()
    await require_feature_access(db, actor, feature="reminders", action="delete")
    row = await load_for_actor(db, actor, reminder_id, verb="cancel", own_only=own_only)
    asked = await _gate("reminder.delete", actor=actor, reminder_id=reminder_id, owner=row.get("user_id"),
                        visibility=row.get("visibility"), stamps_at=stamps_at)
    with _jobs_on(db):
        await _cancel_reminder_jobs_safe(reminder_id)
    async with _reported(asked):
        await db.execute("UPDATE reminders SET deleted = 1, updated_at = NOW() WHERE id = ?", [reminder_id])
        await db.commit()
    await broadcaster.broadcast("reminders", "reminder_deleted", {"id": reminder_id}, user_id=str(actor["user_id"]))
    return {"ok": True, "id": reminder_id}


async def snooze_reminder_record(db, actor: Mapping[str, object], reminder_id: str, minutes: int) -> dict:
    """Snooze (the screen's, owner only): ``snoozed_until`` is a UTC instant it re-fires at; the due date, the time, the rule
    and the done mark are untouched. The ONE snooze: the brain's ``reminder_snooze`` tool calls this too."""
    stamps_at = stamps_now()
    await require_feature_access(db, actor, feature="reminders", action="snooze")
    user_id = str(actor["user_id"])
    row = await load_for_actor(db, actor, reminder_id, verb="snooze")
    owner_id = row.get("user_id") or user_id
    title = row.get("title") or "Reminder"
    snoozed_until = (datetime.utcnow() + timedelta(minutes=minutes)).isoformat() + "Z"
    asked = await _gate("reminder.snooze", actor=actor, reminder_id=reminder_id, owner=row.get("user_id"),
                        visibility=row.get("visibility"), stamps_at=stamps_at)
    # B2: cancel the OLD-time job before the state change commits; the generation bump voids an already-claimed one.
    with _jobs_on(db):
        await _cancel_reminder_jobs_safe(reminder_id)
    async with _reported(asked):
        await db.execute(
            "UPDATE reminders SET snoozed_until = ?, updated_at = NOW(), "
            "schedule_generation = COALESCE(schedule_generation, 0) + 1 WHERE id = ?",
            [snoozed_until, reminder_id],
        )
        await _create_notification(db, user_id=user_id, notif_type="reminder_snoozed", title="Reminder Snoozed",
                                   message=f"Reminder snoozed for {minutes} minutes",
                                   data={"reminder_id": reminder_id, "snoozed_until": snoozed_until})
        await db.commit()
    try:  # a one-shot at snoozed_until so the reminder re-fires when the snooze ends
        from proactive.triggers.reminders import schedule_reminder
        snooze_at = datetime.fromisoformat(snoozed_until.replace("Z", "+00:00"))
        if snooze_at.tzinfo is None:
            snooze_at = snooze_at.replace(tzinfo=timezone.utc)
        with _jobs_on(db):
            await schedule_reminder(user_id=owner_id, message=title, send_at=snooze_at, item_id=reminder_id)
    except Exception:
        logger.exception("reminder snooze: reschedule at snoozed_until failed for %s", reminder_id)
    reminder = await _reread(db, reminder_id)
    await broadcaster.broadcast("reminders", "reminder_snoozed", reminder, user_id=user_id)
    return reminder


async def acknowledge_reminder_record(db, actor: Mapping[str, object], reminder_id: str) -> dict:
    """Mark a reminder done (owner only). A one-off is done for good; an RRULE row is done for THIS occurrence only."""
    stamps_at = stamps_now()
    await require_feature_access(db, actor, feature="reminders", action="acknowledge")
    user_id = str(actor["user_id"])
    row = await load_for_actor(db, actor, reminder_id, verb="done")
    asked = await _gate("reminder.done", actor=actor, reminder_id=reminder_id, owner=row.get("user_id"),
                        visibility=row.get("visibility"), stamps_at=stamps_at)
    recurring = is_rrule(row.get("recurring_pattern"))
    async with _reported(asked):
        if recurring:
            # The row stays live; clear a snooze and bump the generation BEFORE the cancel (after commit), so a racing scan
            # never leaves a stale-generation job that would self-void and skip the next occurrence.
            await db.execute(
                "UPDATE reminders SET snoozed_until = NULL, updated_at = NOW(), "
                "schedule_generation = COALESCE(schedule_generation, 0) + 1 WHERE id = ?",
                [reminder_id],
            )
        else:
            with _jobs_on(db):
                await _cancel_reminder_jobs_safe(reminder_id)  # B2: before the state change commits
            await db.execute("UPDATE reminders SET acknowledged = 1, updated_at = NOW() WHERE id = ?", [reminder_id])
        await _create_notification(db, user_id=user_id, notif_type="reminder_acknowledged", title="Reminder Done",
                                   message=f"Reminder acknowledged: {row.get('title', 'Reminder')}",
                                   data={"reminder_id": reminder_id})
        await db.commit()
    if recurring:
        with _jobs_on(db):
            await _cancel_reminder_jobs_safe(reminder_id)
        await _reschedule_reminder_due_safe(db, reminder_id)
    reminder = await _reread(db, reminder_id)
    await broadcaster.broadcast("reminders", "reminder_acknowledged", reminder, user_id=user_id)
    return reminder


# ── The brain's MCP reminder tools (``mcp_server``: reminder_update / reminder_delete / reminder_snooze) ────────────────────
# The edit and delete tools have always written DIFFERENT rows from the screen's routes (no normalising, a delete that also
# switches the reminder off). Unifying them with the screen is a separate, later decision; until then each tool's row is written
# HERE, statement for statement, so this module stays the one writer of the table. The tools
# keep their own lookups, UI notice and job cancelling in ``mcp_server``.
TOOL_EDIT_FIELDS = ("title", "due_date", "due_time", "priority")


async def tool_update_reminder_record(db, *, user_id: str, reminder_id: str, fields: Mapping[str, object],
                                      stamps_at: dict | None = None) -> bool:
    """The edit tool's row: the given fields as given, on the member's OWN reminder. False (nothing written) with no field."""
    if stamps_at is None:
        stamps_at = stamps_now()
    updates, params = [], []
    for field in TOOL_EDIT_FIELDS:
        if field in fields:
            updates.append(f"{field}=?")
            params.append(fields[field])
    if not updates:
        return False
    # the statement touches only the member's own row (``AND user_id=?``): the reminder is theirs, or nothing is written
    asked = await _gate("reminder.edit", actor={"user_id": user_id}, reminder_id=reminder_id, owner=user_id, visibility=None,
                        stamps_at=stamps_at)
    updates.append("updated_at=NOW()")
    params.extend([reminder_id, user_id])
    async with _reported(asked):
        await db.execute(f"UPDATE reminders SET {','.join(updates)} WHERE id=? AND user_id=?", params)
    return True


async def tool_delete_reminder_record(db, *, user_id: str, reminder_id: str, stamps_at: dict | None = None) -> None:
    """The delete tool's row: deleted AND switched off, on the member's OWN reminder."""
    if stamps_at is None:
        stamps_at = stamps_now()
    asked = await _gate("reminder.delete", actor={"user_id": user_id}, reminder_id=reminder_id, owner=user_id, visibility=None,
                        stamps_at=stamps_at)
    async with _reported(asked):
        await db.execute("UPDATE reminders SET deleted=1, is_active=0, updated_at=NOW() WHERE id=? AND user_id=?", (reminder_id, user_id))

