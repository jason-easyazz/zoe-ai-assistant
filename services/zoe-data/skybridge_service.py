"""Server-side resolver for Skybridge data cards."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import command_shapes
import conversation_cues
import panel_principal
import write_receipts
from calendar_utils import row_to_event
from card_contract import CardContractError, validate_component
from card_service import card_service
from database import get_db_ctx
from people_utils import row_to_person
from time_utils import today_for_zoe_tz
from weather_speech import WeatherAsk, classify_weather_ask, speak_weather

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SkybridgeIntent:
    domain: str
    action: str
    range_label: str = ""
    start_date: date | None = None
    end_date: date | None = None
    query: str = ""
    list_type: str = ""
    context: str = ""
    circle: str = ""
    item_text: str = ""
    list_name: str = ""
    target_time: str = ""
    all_day: bool = False
    title: str = ""
    target_text: str = ""
    person_name: str = ""
    fact_text: str = ""
    relationship: str = ""  # people/create: "add {name} as my {relationship}"
    birthday: str = ""
    duration_seconds: int = 0
    start_seconds: int = 0  # timer/increase|decrease|pause|resume: "the 5 minute timer" = the timer SET for 300 s
    completed: bool | None = None
    entity_id: str = ""  # exact HA entity a smart_home TILE tap targets (never fuzzy)
    entity_ids: tuple[str, ...] = ()  # exact HA entities a smart_home FOLLOW-UP ("turn it back on") targets: what the last command controlled
    radio_mode: bool = False  # music/play: "play <x> radio" → MA endless station


# ── Timers ───────────────────────────────────────────────────────────────────
# A REAL countdown timer (not the old speak-only stub). The store is the
# authoritative source for spoken "how long left" / "cancel" queries; the panel
# ticks and rings from the absolute expires_at so firing is immediate and
# survives a reload. In-memory is intentional: timers are short-lived and the
# panel persists its own copy, so a service restart losing one is acceptable.

# The number vocabulary and the duration parser live in command_shapes (shared with
# intent_router, so the two matchers never disagree about a duration).
_WORD_NUMBERS = command_shapes.WORD_NUMBERS
_COMPOUND_WORD_NUMBERS = command_shapes.COMPOUND_WORD_NUMBERS

MAX_TIMER_SECONDS = 24 * 3600


class _TimerStore:
    """Process-local registry of active countdown timers, keyed by owner."""

    def __init__(self) -> None:
        self._by_owner: dict[str, list[dict[str, Any]]] = {}

    def _prune(self, owner: str) -> None:
        now = time.time()
        live = [t for t in self._by_owner.get(owner, []) if t["expires_at"] > now]
        if live:
            self._by_owner[owner] = live
        else:
            self._by_owner.pop(owner, None)

    def create(self, owner: str, label: str, seconds: int) -> dict[str, Any]:
        self._prune(owner)
        now = time.time()
        timer = {
            "timer_id": uuid.uuid4().hex[:12],
            "label": (label or "Timer").strip() or "Timer",
            "duration_seconds": int(seconds),
            "set_seconds": int(seconds),  # the length it was SET for: "the 5 minute timer" still means it after an adjust
            "created_at": now,
            "expires_at": now + int(seconds),
        }
        self._by_owner.setdefault(owner, []).append(timer)
        write_receipts.record(owner, "timer", timer["timer_id"], "timer_store")
        return timer

    def list(self, owner: str) -> list[dict[str, Any]]:
        self._prune(owner)
        return sorted(self._by_owner.get(owner, []), key=lambda t: t["expires_at"])

    def cancel(self, owner: str, label: str | None = None) -> dict[str, Any] | None:
        self._prune(owner)
        timers = self._by_owner.get(owner, [])
        if not timers:
            return None
        target = None
        if label:
            wanted = label.strip().lower()
            target = next((t for t in timers if t["label"].lower() == wanted), None)
        if target is None:  # no name (or no match) → cancel the soonest-expiring
            target = min(timers, key=lambda t: t["expires_at"])
        timers.remove(target)
        self._prune(owner)
        return target

    def find(self, owner: str, label: str = "", start_seconds: int = 0) -> tuple[dict[str, Any] | None, str]:
        """The ONE running timer a command means, or (None, why): ``none`` (nothing running), ``no_match`` (the
        named / sized timer is not running) or ``ambiguous`` (several running and nothing says which). Never a
        guess: a change to the wrong timer is a wrong write."""
        timers = self.list(owner)
        if not timers:
            return None, "none"
        hits = self.matching(owner, label, start_seconds)
        if not hits:
            return None, "no_match"
        if len(hits) > 1:
            return None, "ambiguous"
        return hits[0], ""

    def matching(self, owner: str, label: str = "", start_seconds: int = 0) -> list[dict[str, Any]]:
        """Every running timer the name and/or set length describe (all of them when neither is given)."""
        hits = self.list(owner)
        if label:
            wanted = label.strip().lower()
            named = [t for t in hits if t["label"].lower() == wanted]
            if not named:  # "the egg timer" for a timer called "Eggs"
                stem = wanted.rstrip("s")
                named = [t for t in hits if t["label"].lower().rstrip("s") == stem]
            if not named and len(hits) == 1 and hits[0]["label"].lower() == "timer":
                # "the kitchen timer" when the ONE running timer has no name: nothing the words say contradicts it.
                named = hits
            hits = named
        if start_seconds:  # a name AND a length ("my 10 minute pasta timer") must BOTH match
            hits = [t for t in hits if int(t.get("set_seconds", t["duration_seconds"])) == int(start_seconds)]
        return hits

    def adjust(self, owner: str, timer_id: str, delta_seconds: int) -> dict[str, Any] | None:
        """Move a running timer's end by ``delta_seconds`` (negative = shorter). The panel reads ``expires_at`` from
        ``GET /api/skybridge/timers`` (``active_timers_for``), so the new end reaches it on its next poll."""
        self._prune(owner)
        timer = next((t for t in self._by_owner.get(owner, []) if t["timer_id"] == timer_id), None)
        if timer is None:
            return None
        timer["expires_at"] += int(delta_seconds)
        timer["duration_seconds"] = int(timer["duration_seconds"]) + int(delta_seconds)
        write_receipts.record(owner, "timer", timer["timer_id"], "timer_store")
        return timer

    def cancel_by_id(self, owner: str, timer_id: str) -> dict[str, Any] | None:
        self._prune(owner)
        timers = self._by_owner.get(owner, [])
        target = next((t for t in timers if t["timer_id"] == timer_id), None)
        if target is not None:
            timers.remove(target)
            self._prune(owner)
        return target


_timers = _TimerStore()


def _parse_timer_duration(text: str) -> int:
    """Total seconds from a timer phrase (digits, number-words, halves: "half an hour" is 1800), else 0."""
    return command_shapes.parse_duration_seconds(text)


def _strip_trailing_duration(cand: str) -> str:
    """Remove a trailing duration phrase from a label candidate.

    "pasta for five minutes" -> "pasta". Uses the SAME number vocabulary as
    _parse_timer_duration (compounds first) so the two never disagree about
    what counts as a duration.
    """
    all_words = {**_COMPOUND_WORD_NUMBERS, **_WORD_NUMBERS}
    num = r"\d+|" + "|".join(
        re.escape(w) for w in sorted(all_words, key=len, reverse=True)
    )
    unit = r"hours?|hrs?|minutes?|mins?|seconds?|secs?"
    # Anchored at the END with no `.*` tail: only a genuinely TRAILING duration
    # is stripped. An interior one is part of the name
    # ("called pasta five minutes sauce" keeps every word).
    trailing = re.compile(
        r"(?i)\s*\b(?:for\s+)?(?:" + num + r")[\s-]*(?:" + unit + r")\b[\s.!?]*$"
    )
    stripped = trailing.sub("", cand).strip().strip(".!?")
    # If the whole name IS duration-shaped ("a timer called five minutes"), the
    # user explicitly asked for that name — keep it rather than erasing the
    # label to a generic "Timer". Only strip when something survives.
    return stripped or cand.strip().strip(".!?")


def _parse_timer_label(text: str) -> str:
    """Extract an optional timer name ('for the eggs', 'called pasta'); never a
    duration ('for 5 minutes').

    TWO tiers, because they carry different certainty:

    * EXPLICIT ``called``/``named`` — everything after the LAST such keyword is
      the label verbatim, including digits and number words ("called 5" -> 5,
      "called five" -> Five). Anchoring on the last keyword also stops a
      duration-first phrase from swallowing the label ("timer for ten minutes
      called pasta" -> Pasta, not "ten minutes called pasta").
    * BARE ``for`` — ambiguous with a duration, so the old conservative rules
      still apply: no digits, no unit words, no bare number words.
    """
    t = text.strip()
    m = re.search(r"(?si)\b(?:called|named)\s+(?:the\s+)?(.+?)\s*$", t)
    if m:
        cand = m.group(1).strip().strip(".!?")
        # A label-first phrase puts the duration AFTER the name ("timer called
        # pasta for five minutes") — the end-anchored capture swallows it, so
        # strip a trailing duration (with its optional leading "for").
        cand = _strip_trailing_duration(cand)
        return cand.title() if cand else ""
    m = re.search(r"\bfor\s+(?:the\s+)?(.+?)\s*$", t)
    if not m:
        return ""
    cand = m.group(1).strip().strip(".!?")
    if not cand or re.search(r"\d", cand):
        return ""
    if re.search(r"\b(hours?|hrs?|minutes?|mins?|seconds?|secs?|timer)\b", cand):
        return ""
    if cand in _WORD_NUMBERS or cand in _COMPOUND_WORD_NUMBERS:
        return ""
    return cand.title()


def _format_duration(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    parts = []
    if hours:
        parts.append(f"{hours} hour" + ("s" if hours != 1 else ""))
    if minutes:
        parts.append(f"{minutes} minute" + ("s" if minutes != 1 else ""))
    if secs and not hours:
        parts.append(f"{secs} second" + ("s" if secs != 1 else ""))
    return " and ".join(parts) if parts else "0 seconds"


def _timer_card(timer: dict[str, Any], *, status: str = "running") -> dict[str, Any]:
    return {
        "component": "timer",
        "props": {
            "id": timer["timer_id"],
            "timer_id": timer["timer_id"],
            "title": timer["label"],
            "label": timer["label"],
            "source": "timer",
            "status": status,
            "duration_seconds": int(timer["duration_seconds"]),
            "expires_at_ms": int(timer["expires_at"] * 1000),
        },
    }


def active_timers_for(owner: str) -> list[dict[str, Any]]:
    """Public helper for the reload-resume endpoint: the panel's authoritative set."""
    return [_timer_card(t)["props"] for t in _timers.list(owner or "guest")]


def cancel_timer_by_id(owner: str, timer_id: str) -> dict[str, Any] | None:
    """Cancel a specific timer (used by the panel's per-card tap-cancel)."""
    return _timers.cancel_by_id(owner or "guest", timer_id)


def create_timer_direct(user_id: str, *, minutes: Any = 5, label: str = "") -> dict[str, Any]:
    """Create a real countdown timer from already-parsed slots (minutes/label).

    Used by non-voice dispatch paths (e.g. intent_router.execute_intent's
    timer_create branch, reached via POST /api/system/intent-dispatch) that
    never go through the Skybridge text classifier, so they'd otherwise only
    speak a confirmation without registering a timer in `_timers`. Reuses the
    same in-memory store/creation logic as the voice/Skybridge path so both
    surfaces produce one real, poll-able timer.
    """
    try:
        mins = float(minutes)
    except (TypeError, ValueError):
        mins = 5.0
    seconds = max(1, int(round(mins * 60)))
    intent = SkybridgeIntent(domain="timer", action="create", duration_seconds=seconds, title=str(label or ""))
    return _resolve_timer(intent, user_id or "guest")


def _timer_reply(intent: SkybridgeIntent, owner: str, spoken: str, **extra: Any) -> dict[str, Any]:
    timers = _timers.list(owner)
    cards = [_timer_card(t) for t in timers] or [_status_card("No timers", "Ask me to set a timer to get started.")]
    return {"handled": True, "intent": _intent_dict(intent), "spoken_summary": spoken, "cards": cards, "actions": [],
            **extra}


def _cancelled_line(gone: list[dict[str, Any]], qualified: bool) -> str:
    if len(gone) == 1:
        named = "" if gone[0]["label"].lower() == "timer" else f"{gone[0]['label']} "
        return f"Cancelled your {named}timer."
    return f"Cancelled {len(gone)} timers." if qualified else f"Cancelled all {len(gone)} timers."


def _which_timer_line(intent: SkybridgeIntent, owner: str, why: str) -> str:
    """The honest line when a change cannot be pinned to ONE running timer (never a guess)."""
    if why == "none":
        return "You have no timers running."
    if why == "no_match":
        if intent.title:
            return f"I couldn't find a {intent.title.lower()} timer."
        return f"I couldn't find a {_format_duration(intent.start_seconds)} timer."
    names = [t["label"] for t in _timers.list(owner)]
    named = [n for n in names if n.lower() != "timer"]
    if len(named) == len(names):
        return f"You have {len(names)} timers running: {', '.join(names[:-1])} and {names[-1]}. Which one?"
    return f"You have {len(names)} timers running. Which one?"


def _resolve_timer_change(intent: SkybridgeIntent, owner: str) -> dict[str, Any]:
    """"add 5 minutes to the timer" / "take a minute off the pizza timer" / "pause the timer": a change to a RUNNING
    timer. It never creates one (HA's corpus: 74 of 78 increase phrasings used to start a NEW timer) and never guesses
    which timer when several run. Pause/resume is not supported (the panel has no paused timer), so it says so."""
    target, why = _timers.find(owner, intent.title, intent.start_seconds)
    if intent.action in ("pause", "resume"):
        if target is None and why == "none":
            return _timer_reply(intent, owner, "You have no timers running.")
        verb = "pause" if intent.action == "pause" else "resume"
        return _timer_reply(intent, owner, f"I can't {verb} timers yet. I can cancel a timer or add time to it.")
    if target is None:
        return _timer_reply(intent, owner, _which_timer_line(intent, owner, why))
    named = "" if target["label"].lower() == "timer" else f"{target['label']} "
    remaining = target["expires_at"] - time.time()
    delta = int(intent.duration_seconds)
    if delta <= 0:  # "add some time to the timer": no amount in the words - ask, never guess one
        verb = "add to" if intent.action == "increase" else "take off"
        return _timer_reply(intent, owner, f"How much time should I {verb} your {named}timer?")
    if intent.action == "set":  # "reduce the timer to 5 minutes": the new time LEFT
        if delta > MAX_TIMER_SECONDS:
            return _timer_reply(intent, owner, "That would take the timer past 24 hours, so I left it alone.")
        _timers.adjust(owner, target["timer_id"], int(round(delta - remaining)))
        return _timer_reply(intent, owner, f"Your {named}timer now has {_format_duration(delta)} left.",
                            timer_adjusted_id=target["timer_id"])
    if intent.action == "decrease":
        if delta >= remaining:
            return _timer_reply(intent, owner, f"Your {named}timer only has {_format_duration(remaining)} left, "
                                               "so I left it alone.")
        delta = -delta
    elif remaining + delta > MAX_TIMER_SECONDS:
        return _timer_reply(intent, owner, "That would take the timer past 24 hours, so I left it alone.")
    _timers.adjust(owner, target["timer_id"], delta)
    left = _format_duration(remaining + delta)
    if delta > 0:
        spoken = f"Added {_format_duration(delta)} to your {named}timer. {left[0].upper()}{left[1:]} left."
    else:
        spoken = f"Took {_format_duration(-delta)} off your {named}timer. {left[0].upper()}{left[1:]} left."
    return _timer_reply(intent, owner, spoken, timer_adjusted_id=target["timer_id"])


def _resolve_timer(intent: SkybridgeIntent, user_id: str) -> dict[str, Any]:
    owner = (user_id or "guest")

    if intent.action in ("increase", "decrease", "set", "pause", "resume"):
        return _resolve_timer_change(intent, owner)

    if intent.action == "cancel":
        qualified = bool(intent.title or intent.start_seconds)
        if intent.query == "both" and not qualified and len(_timers.list(owner)) != 2:
            return _timer_reply(intent, owner, _which_timer_line(intent, owner, "ambiguous")
                                if len(_timers.list(owner)) > 2 else
                                ("You have no timers running." if not _timers.list(owner) else
                                 "You only have one timer running."))
        if intent.query in ("all", "both"):  # "cancel all timers" (HA HassCancelAllTimers)
            # Every timer the qualifier fits ("every pasta timer"), or every timer when there is none.
            targets = _timers.matching(owner, intent.title, intent.start_seconds)
            if qualified and not targets and _timers.list(owner):
                return _timer_reply(intent, owner, _which_timer_line(intent, owner, "no_match"))
            gone = [t for t in targets if _timers.cancel_by_id(owner, t["timer_id"])]
            if gone:
                return _timer_reply(intent, owner,
                                    _cancelled_line(gone, qualified),
                                    timer_cancelled_id=gone[0]["timer_id"],
                                    timer_cancelled_ids=[t["timer_id"] for t in gone])
            cancelled = None
        else:
            # The ONE timer meant - by name, by length, or the only one running. Several running and nothing says
            # which = ask: it used to cancel the soonest one, a delete the words never asked for.
            target, why = _timers.find(owner, intent.title, intent.start_seconds)
            if target is None and why != "none":
                return _timer_reply(intent, owner, _which_timer_line(intent, owner, why))
            cancelled = _timers.cancel_by_id(owner, target["timer_id"]) if target else None
        if not cancelled:
            return {
                "handled": True, "intent": _intent_dict(intent),
                "spoken_summary": "You have no timers running.",
                "cards": [_status_card("No timers", "There are no active timers to cancel.")],
                "actions": [],
            }
        named = "" if cancelled["label"].lower() == "timer" else f"{cancelled['label']} "
        remaining = _timers.list(owner)
        # Show the timers that are still running; only fall back to a status card
        # when the last one was cancelled.
        cards = [_timer_card(t) for t in remaining] or [
            _status_card("Timer cancelled", f"Your {named}timer was stopped.")
        ]
        return {
            "handled": True, "intent": _intent_dict(intent),
            "spoken_summary": f"Cancelled your {named}timer.",
            "cards": cards,
            "actions": [],
            "timer_cancelled_id": cancelled["timer_id"],
        }

    if intent.action == "status":
        timers = _timers.list(owner)
        if not timers:
            return {
                "handled": True, "intent": _intent_dict(intent),
                "spoken_summary": "You have no timers running.",
                "cards": [_status_card("No timers", "Ask me to set a timer to get started.")],
                "actions": [],
            }
        soonest = timers[0]
        remaining = soonest["expires_at"] - time.time()
        named = "" if soonest["label"].lower() == "timer" else f"{soonest['label']} "
        return {
            "handled": True, "intent": _intent_dict(intent),
            "spoken_summary": f"Your {named}timer has {_format_duration(remaining)} left.",
            "cards": [_timer_card(t) for t in timers],
            "actions": [],
        }

    # create
    seconds = max(1, min(int(intent.duration_seconds or 300), MAX_TIMER_SECONDS))
    timer = _timers.create(owner, intent.title or "Timer", seconds)
    named = "" if timer["label"].lower() == "timer" else f"{timer['label']} "
    # Return every running timer so a second/third one appears alongside the rest
    # instead of replacing them — a kitchen runs several at once.
    cards = [_timer_card(t) for t in _timers.list(owner)]
    extra = "" if len(cards) <= 1 else f" That makes {len(cards)} timers running."
    return {
        "handled": True, "intent": _intent_dict(intent),
        "spoken_summary": f"Your {named}timer is set for {_format_duration(seconds)}.{extra}",
        "cards": cards,
        "actions": [],
    }


MONTHS = {
    "january": 1,
    "jan": 1,
    "february": 2,
    "feb": 2,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "may": 5,
    "june": 6,
    "jun": 6,
    "july": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sep": 9,
    "sept": 9,
    "october": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "december": 12,
    "dec": 12,
}
MONTH_PATTERN = "|".join(sorted(MONTHS, key=len, reverse=True))
LIST_TYPES = ("shopping", "personal", "work", "bucket", "tasks")
DEFAULT_USER_LISTS = (
    ("shopping", "Shopping", "Shared groceries and household shopping.", "family"),
    ("work", "Work", "Work tasks and follow-ups.", "personal"),
    ("personal", "Personal", "Personal todos and errands.", "personal"),
)
LIST_TYPE_ALIASES = {
    "shopping": "shopping",
    "groceries": "shopping",
    "grocery": "shopping",
    "personal": "personal",
    "work": "work",
    "bucket": "bucket",
    "tasks": "tasks",
    "task": "tasks",
    "todo": "tasks",
    "todos": "tasks",
    "to do": "tasks",
    "to dos": "tasks",
}
PEOPLE_CONTEXTS = ("personal", "work")
PEOPLE_CIRCLES = ("inner", "circle", "public")


def _host_clock_timezone() -> str | None:
    tz_env = (os.environ.get("TZ") or "").strip()
    if tz_env and not tz_env.startswith(":"):
        return tz_env

    try:
        timezone_file = Path("/etc/timezone")
        if timezone_file.exists():
            timezone_name = timezone_file.read_text(encoding="utf-8").strip()
            if timezone_name:
                return timezone_name
    except OSError:
        pass

    try:
        localtime = Path("/etc/localtime")
        if localtime.is_symlink():
            target = str(localtime.resolve())
            marker = "/zoneinfo/"
            if marker in target:
                timezone_name = target.split(marker, 1)[1]
                if timezone_name:
                    return timezone_name
    except OSError:
        pass

    return None


def _default_clock_timezone() -> str:
    return os.environ.get("ZOE_SKYBRIDGE_TIMEZONE") or _host_clock_timezone() or "UTC"


def _today() -> date:
    return today_for_zoe_tz()


def _clock_now(timezone_name: str | None = None) -> tuple[datetime, str]:
    name = timezone_name or _default_clock_timezone()
    try:
        tz = ZoneInfo(name)
    except ZoneInfoNotFoundError:
        name = "UTC"
        tz = ZoneInfo("UTC")
    return datetime.now(tz), name


async def _get_current(lat, lon, city, country):
    from routers.weather import _get_current as get_current

    return await get_current(lat, lon, city, country)


async def _get_forecast(lat, lon):
    from routers.weather import _get_forecast as get_forecast

    return await get_forecast(lat, lon)


async def _get_system_default_location(db):
    from routers.weather import _get_system_default_location as get_system_default_location

    return await get_system_default_location(db)


def _resolve_location(prefs, *, fallback):
    from routers.weather import _resolve_location as resolve_location

    return resolve_location(prefs, fallback=fallback)


def _row_to_prefs(row):
    from routers.weather import _row_to_prefs as row_to_prefs

    return row_to_prefs(row)


_WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6}
_WEEKDAY_RE = re.compile(r"\b(?:(?:next|this|coming|on)\s+)?(?P<day>" + "|".join(_WEEKDAYS) + r")\b")


def _calendar_date_from_text(text: str, today: date) -> tuple[str, date, date] | None:
    if "tomorrow" in text:
        target = today + timedelta(days=1)
        return "tomorrow", target, target
    if any(term in text for term in (" next week ", " following week ")):
        days_until_monday = (7 - today.weekday()) % 7 or 7
        start = today + timedelta(days=days_until_monday)
        return "next week", start, start + timedelta(days=6)
    if any(term in text for term in (" this week ", " week ", " upcoming ", " next few days ")):
        return "this week", today, today + timedelta(days=7)

    match = re.search(
        rf"\b(?:on\s+)?(?:the\s+)?(?P<day>\d{{1,2}})(?:st|nd|rd|th)?(?:\s+of)?\s+(?P<month>{MONTH_PATTERN})(?:\s+(?P<year>\d{{4}}))?\b",
        text,
    )
    if match:
        day = int(match.group("day"))
        month_name = match.group("month")
        month = MONTHS.get(month_name)
        if month is None:
            return None
        year = int(match.group("year") or today.year)
        try:
            target = date(year, month, day)
        except ValueError:
            return None
        label = f"{target.day} {target.strftime('%B %Y')}"
        return label, target, target

    iso_match = re.search(r"\b(?P<year>\d{4})-(?P<month>\d{1,2})-(?P<day>\d{1,2})\b", text)
    if iso_match:
        try:
            target = date(
                int(iso_match.group("year")),
                int(iso_match.group("month")),
                int(iso_match.group("day")),
            )
        except ValueError:
            return None
        return target.isoformat(), target, target

    # After the EXPLICIT dates above, which win ("Saturday 3 October" is that day, not next Saturday).
    weekday = _WEEKDAY_RE.search(text)
    if weekday:
        # A bare weekday is the NEXT one (never today) - the same rule as intent_router._parse_date.
        target = today + timedelta(days=((_WEEKDAYS[weekday.group("day")] - today.weekday()) % 7) or 7)
        return weekday.group("day"), target, target
    if re.search(r"\b(?:today|tonight)\b", text):
        return "today", today, today

    return None


def _list_type_from_text(text: str) -> str:
    for key, value in LIST_TYPE_ALIASES.items():
        if f" {key} " in text:
            return value
    return ""


def _default_list_name(list_type: str) -> str:
    for default_type, name, _description, _visibility in DEFAULT_USER_LISTS:
        if default_type == list_type:
            return name
    return (list_type or "List").replace("_", " ").title()


def _list_type_for_name(name: str, explicit_type: str = "") -> str:
    if explicit_type:
        return explicit_type
    text = f" {name.lower()} "
    detected = _list_type_from_text(text)
    if detected:
        return detected
    return "personal"


def _list_create_from_text(message: str, context: dict[str, Any] | None = None) -> tuple[str, str] | None:
    if _context_domain(context) == "lists" and _context_action(context) == "create_list":
        candidate = _clean_action_text(message)
        if re.fullmatch(r"[a-z0-9][a-z0-9 '&-]{1,48}", candidate, flags=re.IGNORECASE):
            return candidate[:48].strip(), _list_type_for_name(candidate, _context_list_type(context))
    if not re.search(r"\b(?:new|create|make|add)\s+(?:a\s+|my\s+)?(?:new\s+)?(?:shopping|grocery|groceries|personal|work|task|todo|tasks|todos)?\s*list\b", message, re.IGNORECASE):
        if _context_domain(context) != "lists" or not re.search(r"\b(?:call|name)\s+(?:it\s+)?", message, re.IGNORECASE):
            return None
    list_type = _list_type_from_text(f" {message.lower()} ")
    name = ""
    patterns = (
        r"\b(?:called|named|name it|call it)\s+(?P<name>[a-z0-9][a-z0-9 '&-]{1,48})\b",
        r"\b(?:new|create|make|add)\s+(?:a\s+|my\s+)?(?:new\s+)?(?P<name>[a-z0-9][a-z0-9 '&-]{1,48})\s+list\b",
    )
    for pattern in patterns:
        match = re.search(pattern, message, re.IGNORECASE)
        if match:
            name = _clean_action_text(match.group("name"))
            break
    if name.lower() in {"shopping", "grocery", "groceries", "personal", "work", "task", "tasks", "todo", "todos", "new"}:
        name = _default_list_name(_list_type_for_name(name, list_type))
    if not name and re.search(r"\b(?:new|create|make|add)\b", message, re.IGNORECASE):
        return "", list_type or "personal"
    if not name:
        return None
    return name[:48].strip(), _list_type_for_name(name, list_type)


def _context_domain(context: dict[str, Any] | None) -> str:
    if not isinstance(context, dict):
        return ""
    intent = context.get("intent") if isinstance(context.get("intent"), dict) else {}
    return str(intent.get("domain") or context.get("domain") or "").strip().lower()


def _context_action(context: dict[str, Any] | None) -> str:
    if not isinstance(context, dict):
        return ""
    intent = context.get("intent") if isinstance(context.get("intent"), dict) else {}
    return str(intent.get("action") or "").strip().lower()


def _context_list_type(context: dict[str, Any] | None) -> str:
    if not isinstance(context, dict):
        return ""
    intent = context.get("intent") if isinstance(context.get("intent"), dict) else {}
    return str(intent.get("list_type") or "").strip().lower()


def _context_cards(context: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(context, dict):
        return []
    cards = context.get("cards")
    return cards if isinstance(cards, list) else []


def _context_calendar_date(context: dict[str, Any] | None) -> date:
    for card in _context_cards(context):
        content = card.get("content") if isinstance(card, dict) else {}
        if isinstance(content, dict) and content.get("source") == "calendar_show":
            raw = content.get("start_date") or content.get("date")
            if raw:
                try:
                    return date.fromisoformat(str(raw)[:10])
                except ValueError:
                    pass
    return _today()


def _context_events(context: dict[str, Any] | None) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for card in _context_cards(context):
        content = card.get("content") if isinstance(card, dict) else {}
        if isinstance(content, dict) and content.get("source") == "calendar_show":
            raw_events = content.get("events") or []
            if isinstance(raw_events, list):
                events.extend(item for item in raw_events if isinstance(item, dict))
    return events


def _context_list_hint(context: dict[str, Any] | None) -> tuple[str, str]:
    for card in _context_cards(context):
        content = card.get("content") if isinstance(card, dict) else {}
        if isinstance(content, dict) and content.get("source") == "list_show":
            list_id = str(content.get("list_id") or "")
            if list_id and list_id != "lists-overview":
                return list_id, str(content.get("list_type") or "")
            lists = content.get("lists")
            if isinstance(lists, list) and len(lists) == 1 and isinstance(lists[0], dict):
                return str(lists[0].get("id") or ""), str(lists[0].get("list_type") or content.get("list_type") or "")
            return "", str(content.get("list_type") or "")
    return "", ""


def _parse_time(text: str) -> str:
    match = re.search(r"\b(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<ampm>a\.?m\.?|p\.?m\.?)?\b", text, re.IGNORECASE)
    if not match:
        return ""
    hour = int(match.group("hour"))
    minute = int(match.group("minute") or "0")
    ampm = (match.group("ampm") or "").lower()
    if hour > 23 or minute > 59:
        return ""
    if ampm.startswith("p") and hour < 12:
        hour += 12
    elif ampm.startswith("a") and hour == 12:
        hour = 0
    elif not ampm and 1 <= hour <= 11:
        return ""
    return f"{hour:02d}:{minute:02d}"


def _parse_contextual_time(text: str, target: str, context: dict[str, Any] | None) -> str:
    parsed = _parse_time(text)
    if parsed:
        return parsed
    match = re.search(r"\b(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*\b", text, re.IGNORECASE)
    if not match:
        return ""
    hour = int(match.group("hour"))
    minute = int(match.group("minute") or "0")
    if not (1 <= hour <= 11) or minute > 59:
        return ""
    anchor_time = _parse_time(target)
    if not anchor_time:
        scored = sorted(((_score_event_for_target(event, target), event) for event in _context_events(context)), reverse=True, key=lambda pair: pair[0])
        if scored and scored[0][0] > 0 and sum(1 for score, _event in scored if score == scored[0][0]) == 1:
            anchor_time = str(scored[0][1].get("start_time") or "")[:5]
    if anchor_time and int(anchor_time[:2]) >= 12:
        hour += 12
    return f"{hour:02d}:{minute:02d}"


def _item_display_text(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("text") or item.get("title") or item.get("label") or "").strip()
    return str(item or "").strip()


def _enumerate_items_for_speech(list_name: str, items: list[Any], *, cap: int = 5) -> str:
    """Build a spoken enumeration, e.g. 'You've got bread, milk and eggs — three things on the Shopping list'."""
    names = [text for text in (_item_display_text(item) for item in items) if text]
    total = len(names)
    surface = (list_name or "list").strip() or "list"
    if total == 0:
        return f"There's nothing on the {surface} list yet."
    word_numbers = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten"}
    count_word = word_numbers.get(total, str(total))
    thing_word = "thing" if total == 1 else "things"
    shown = names[:cap]
    remainder = total - len(shown)
    if remainder > 0:
        listed = ", ".join(shown) + f" ...and {remainder} more"
    elif len(shown) == 1:
        listed = shown[0]
    else:
        listed = ", ".join(shown[:-1]) + " and " + shown[-1]
    return f"You've got {listed} — {count_word} {thing_word} on the {surface} list."


def _clean_action_text(value: str) -> str:
    text = re.sub(r"\s+", " ", value or "").strip(" .")
    return re.sub(r"^(?:my|the|a|an)\s+", "", text, flags=re.IGNORECASE).strip()


# Words that may stand BEFORE a command verb ("Hey Zoe, can you add …", "and add …", "I want you to put …"). A verb that
# follows anything else is NOT a command: "I was asking you to set meetings and add things to the shopping list" is a
# complaint, not an add (live 2026-10-12, it stored the whole sentence as an item).
_COMMAND_LEAD_WORDS = frozenset({
    "hey", "hi", "hello", "okay", "ok", "so", "and", "um", "uh", "uhh", "umm", "er", "erm", "well", "yeah", "yes", "please",
    "zoe", "can", "could", "would", "will", "you", "we", "i", "i'd", "id", "like", "want", "need", "to", "just", "go", "ahead",
    "also", "now", "then", "let's", "lets", "let", "us", "me", "oh", "right", "alright", "great", "thanks", "cool", "maybe",
    "actually", "wanna", "gonna", "going", "you'd", "it", "would", "ask", "for", "the", "a", "my", "jason", "jay",
    "hmm", "mm", "ah", "eh", "kindly", "sorry", "i'll", "ill", "i'm", "im", "gotta", "also",
})
# A write verb that, led like a command, means the turn is a WRITE - so a loose read fallback may not claim it as "show".
_WRITE_VERB_RE = r"(?:add|put|create|set\s*up|remind|append|stick)"   # not schedule/book: also nouns ("my schedule")


def _command_shaped(message: str, verbs: str) -> re.Match | None:
    """The match of ``verbs`` (a regex alternation) when it opens a COMMAND - only lead-in words before it - else None."""
    m = re.search(rf"\b(?:{verbs})\b", message or "", re.IGNORECASE)
    if not m:
        return None
    prefix = re.findall(r"[a-z']+", (message[:m.start()] or "").lower())
    if len(prefix) > 8 or any(w not in _COMMAND_LEAD_WORDS for w in prefix):
        return None
    return m


_LIST_NAME_RE = (
    r"(?P<list>shopping list|grocery list|groceries|work list|personal list|bucket list|task list|to[- ]?do list|todo list"
    r"|to[- ]?dos|todos|tasks|list)"
)


def _list_add_from_text(message: str) -> tuple[str, str] | None:
    """"add X to (the|my) <list>" / "put X on (the|my) <list>" - X cleaned (stutter, fillers, punctuation) and only ever a
    plausible ITEM: a sentence is not an item (see ``list_service.is_plausible_item``)."""
    head = _command_shaped(message, "add|put")
    if head is None:
        return None
    verb = head.group(0).lower()
    glue = r"to|into" if verb == "add" else r"on|onto|in|into|to"
    match = re.search(
        rf"\b{verb}\s+(?P<item>.+?)\s+(?:{glue})\s+(?:the\s+|my\s+|our\s+)?{_LIST_NAME_RE}\b",
        message[head.start():],
        re.IGNORECASE,
    )
    if not match:
        return None
    from list_service import clean_item_text, is_plausible_item

    item = clean_item_text(_clean_action_text(match.group("item")))
    if not is_plausible_item(item):
        return None
    list_phrase = f" {re.sub(r'[- ]+', ' ', match.group('list').lower())} "
    list_type = _list_type_from_text(list_phrase) or ("shopping" if "grocery" in list_phrase else "")
    return item, list_type or "shopping"


# A list named by the user ("my chores list"): one to three words, none of them glue, then "list". Tried AFTER the
# known list names, so "shopping list" stays the shopping type.
_LIST_GLUE = (
    r"(?:the|my|our|your|their|his|her|a|an|this|that|these|those|other|new|current|same|whole|entire|any|every"
    r"|from|off|on|in|of|to|into|onto|out)"
)
# An item that only POINTS ("complete it on the list"): a pronoun is not an item - a substring match would tick off
# whatever happens to contain it.
_POINTING_ITEMS = frozenset({"it", "that", "this", "them", "those", "these", "one", "something", "everything", "all"})
_NAMED_LIST_RE = rf"(?P<lname>(?!{_LIST_GLUE}\b)[a-z0-9']+(?:\s+(?!{_LIST_GLUE}\b)[a-z0-9']+){{0,2}})\s+list"
_KNOWN_LIST_RE = (
    r"(?P<list>shopping list|grocery list|groceries|work list|personal list|list|task list|todo list|to[- ]do list"
    r"|tasks|todos)"
)
_LIST_TARGET_RE = rf"(?:the\s+|my\s+|our\s+|this\s+|that\s+)?(?:{_KNOWN_LIST_RE}|{_NAMED_LIST_RE})\b"


def _list_target(match: "re.Match[str]") -> tuple[str, str]:
    """(list_type, list_name) from a ``_LIST_TARGET_RE`` match: a known list gives its type, a named list ("chores")
    gives its name, unless the name IS a known type ("todo list")."""
    groups = match.groupdict()
    name = (groups.get("lname") or "").strip().lower()
    if name:
        # "the grocery shopping list" / "my todo list" is a TYPED list, not one named "grocery shopping".
        list_type = LIST_TYPE_ALIASES.get(name, "") or _list_type_from_text(f" {name} ")
        return (list_type, "") if list_type else ("", name)
    phrase = f" {re.sub(r'[- ]+', ' ', (groups.get('list') or '').lower())} "
    return (_list_type_from_text(phrase) or ("shopping" if "grocery" in phrase else "")), ""


def _list_remove_from_text(message: str) -> tuple[str, str, str] | None:
    """(item, list_type, list_name) for "take/remove/delete X off|from|out of <list>", and - ending the turn - for
    "delete X in <list>" / "remove X on <list>" (HA's phrasings; they used to be answered with a list READ). A question
    or statement ABOUT removing ("can I remove milk from my list?", "I was going to remove ...") deletes nothing."""
    if command_shapes.asks_or_mentions(message, destructive=True):
        return None
    match = re.search(
        rf"\b(?:take|remove|delete|drop)\s+(?P<item>.+?)\s+(?:off|from|out of)\s+{_LIST_TARGET_RE}",
        message,
        re.IGNORECASE,
    ) or re.search(
        rf"\b(?:take|remove|delete|drop)\s+(?P<item>.+?)\s+(?:on|in)\s+{_LIST_TARGET_RE}\s*[.!?]*\s*$",
        message,
        re.IGNORECASE,
    )
    if not match:
        return None
    item = _clean_action_text(match.group("item"))
    if not item or item.lower() in _POINTING_ITEMS or not command_shapes.plain_list_item(item):
        return None
    list_type, list_name = _list_target(match)
    return item, (list_type or ("" if list_name else "shopping")), list_name


# "is milk on the shopping list" / "do we have eggs on the list": a YES/NO question about one item, not a list read.
@lru_cache(maxsize=1)
def _list_contains_res() -> tuple["re.Pattern[str]", ...]:
    # Built on first use: _SHAPE_LEAD is defined further down, with the people shapes.
    # A tag after the list ("…on the list, right?", "…on the list yet", "…please") is not part of the question.
    tag = r"(?:[\s,]+(?:right|yet|as\s+well|too|please|again|now|then|zoe|already|still))*\s*$"
    return (
        re.compile(_SHAPE_LEAD + r"(?:is|are)\s+(?:there\s+)?(?:still\s+)?(?:any\s+|some\s+)?(?P<item>.+?)\s+(?:on|in)\s+"
                   + _LIST_TARGET_RE + tag),
        re.compile(_SHAPE_LEAD + r"(?:do|did|have)\s+(?:we|i)\s+(?:(?:already|still)\s+)?(?:got|have|put|add|added)\s+"
                   r"(?:any\s+|some\s+)?(?P<item>.+?)\s+(?:on|in|to)\s+" + _LIST_TARGET_RE + tag),
        # "milk's on the list, right?" / "milk is on the list, isn't it"
        re.compile(_SHAPE_LEAD + r"(?P<item>[a-z0-9' -]+?)(?:'s|\s+is|\s+are)\s+(?:still\s+|already\s+)?(?:on|in)\s+"
                   + _LIST_TARGET_RE + r"(?:[\s,]+(?:right|isn't\s+it|aren't\s+they|yeah|yes))+\s*$"),
    )


def _list_contains_from_text(text: str) -> tuple[str, str, str] | None:
    """(item, list_type, list_name) for a membership question; ``text`` is the classifier's normalised text."""
    t = text.strip().rstrip("?").strip()
    for pattern in _list_contains_res():
        match = pattern.search(t)
        if not match:
            continue
        from list_service import clean_item_text, is_plausible_item

        # "is milk STILL / ALREADY on the list": the adverb is about the question, not part of the item.
        raw_item = re.sub(r"(?:\s+(?:still|already|even|actually|definitely|really|also|left|remaining|anymore|any\s+more))+$", "",
                          match.group("item").strip())
        raw_item = re.sub(r"^(?:still|already|even|actually|definitely|really|also)\s+", "", raw_item)
        item = clean_item_text(_clean_action_text(raw_item))
        if not item or not is_plausible_item(item) or not command_shapes.plain_list_item(item):
            return None
        list_type, list_name = _list_target(match)
        return item, list_type, list_name
    return None


def _list_complete_from_text(message: str) -> tuple[str, str, bool, str] | None:
    """Parse a check-off / un-check command (from a tapped row or voice).

    Returns (item, list_type, completed, list_name) where completed=True means "tick it off".
    Tap rows emit "check off X on the shopping list" / "uncheck X on the shopping
    list"; this also catches natural forms ("tick off bread", "mark eggs as done",
    "cross milk off the list", "complete X on my chores list", "check X off from my list").
    Direction is explicit so a tap never guesses wrong.
    """
    if command_shapes.asks_or_mentions(message, destructive=True):
        return None  # "did you check milk off my list?" / "would you like to complete ..." tick nothing
    on_tail = r"(?:\s+(?:on|from|in)\s+" + _LIST_TARGET_RE + r")?\s*[.!]*\s*$"
    off_tail = r"\s+off(?:\s+(?:from\s+|of\s+)?" + _LIST_TARGET_RE + r")?\s*[.!]*\s*$"

    def _finish(match: "re.Match[str]", completed: bool) -> tuple[str, str, bool, str] | None:
        item = _clean_action_text(match.group("item"))
        if not item or item.lower() in _POINTING_ITEMS or not command_shapes.plain_list_item(item):
            return None
        list_type, list_name = _list_target(match)
        return item, (list_type or ("" if list_name else "shopping")), completed, list_name

    # Restore / un-check FIRST so "uncheck X" is never read as "check X".
    match = re.search(r"\b(?:uncheck|un-?tick|unmark|undo|put back)\s+(?P<item>.+?)" + on_tail, message, re.IGNORECASE)
    if match:
        result = _finish(match, False)
        if result:
            return result
    # "mark X (as) done/complete".
    match = re.search(r"\bmark\s+(?P<item>.+?)\s+(?:as\s+)?(?:done|complete|completed)\b" + on_tail, message, re.IGNORECASE)
    if match:
        result = _finish(match, True)
        if result:
            return result
    # "check/tick/cross off X [on the LIST]".
    match = re.search(r"\b(?:check|tick|cross)\s+off\s+(?P<item>.+?)" + on_tail, message, re.IGNORECASE)
    if match:
        result = _finish(match, True)
        if result:
            return result
    # "check/tick/cross X off [the LIST]".
    match = re.search(r"\b(?:check|tick|cross)\s+(?P<item>.+?)" + off_tail, message, re.IGNORECASE)
    if match:
        result = _finish(match, True)
        if result:
            return result
    # "complete X on my chores list" - the list is REQUIRED here ("complete the form" is not a list command).
    match = _command_shaped(message, "complete|finish")
    if match:
        match = re.search(r"\b(?:complete|finish)\s+(?P<item>.+?)\s+(?:on|from|in)\s+" + _LIST_TARGET_RE
                          + r"\s*[.!]*\s*$", message[match.start():], re.IGNORECASE)
        if match:
            result = _finish(match, True)
            if result:
                return result
    return None


def _contextual_list_remove_from_text(message: str, context: dict[str, Any] | None) -> tuple[str, str] | None:
    if _context_domain(context) != "lists":
        return None
    match = re.search(r"\b(?:take|remove|delete|drop)\s+(?P<item>.+?)\s*$", message, re.IGNORECASE)
    if not match:
        return None
    if re.search(r"\b(?:off|from|out of)\s+", message, re.IGNORECASE):
        return None
    item = _clean_action_text(match.group("item"))
    if not item or item.lower() in {"item", "something", "this", "that", "it"} or not command_shapes.plain_list_item(item):
        return None
    _list_id, list_type = _context_list_hint(context)
    return item, list_type or "shopping"


def _contextual_list_add_from_text(message: str, context: dict[str, Any] | None) -> tuple[str, str] | None:
    if _context_domain(context) != "lists":
        return None
    match = re.search(r"\badd\s+(?P<item>.+?)\s*$", message, re.IGNORECASE)
    if not match:
        return None
    if re.search(r"\b(?:at|for)\s+\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)?\b", message, re.IGNORECASE):
        return None
    if re.search(r"\bto\s+", message, re.IGNORECASE):
        return None
    # The SAME item validation as _list_add_from_text: a context follow-up must not store a whole sentence or a vague noun
    # just because a list card is open (review, #2004).
    from list_service import clean_item_text, is_plausible_item

    item = clean_item_text(_clean_action_text(match.group("item")))
    if not item or item.lower() in {"item", "something", "this", "that"} or not is_plausible_item(item):
        return None
    _list_id, list_type = _context_list_hint(context)
    return item, list_type or "shopping"


def _calendar_delete_from_text(message: str, context: dict[str, Any] | None) -> str | None:
    """Parse 'delete/remove/cancel <event> from my calendar' (or contextual).

    The target is GREEDY (``.+``) so the delimiter binds to the LAST
    'from/off/out of … calendar/schedule/agenda' — a title that itself contains
    that phrase (e.g. 'Away from schedule') stays intact instead of being
    truncated to 'Away'. The tap-generated query always appends the anchor last,
    so greedy resolves to the whole real target.
    """
    match = re.search(
        r"\b(?:delete|remove|cancel)\s+(?P<target>.+)\s+(?:from|off|out of)\s+(?:the\s+|my\s+)?(?:calendar|schedule|agenda)\b",
        message,
        re.IGNORECASE,
    )
    if match:
        target = _clean_action_text(match.group("target"))
        return target or None
    if _context_domain(context) != "calendar":
        return None
    ctx_match = re.search(r"\b(?:delete|remove|cancel)\s+(?P<target>.+?)\s*$", message, re.IGNORECASE)
    if not ctx_match:
        return None
    target = _clean_action_text(ctx_match.group("target"))
    return target or None


def _list_edit_from_text(message: str, context: dict[str, Any] | None) -> tuple[str, str] | None:
    """Parse 'edit <item> on/from my <list>' (or contextual 'edit <item>') tap-to-edit utterances."""
    match = re.search(
        r"\bedit\s+(?P<item>.+?)\s+(?:on|in|from)\s+(?:the\s+|my\s+)?(?P<list>shopping list|grocery list|groceries|work list|personal list|list|task list|todo list|tasks|todos)\b",
        message,
        re.IGNORECASE,
    )
    if match:
        item = _clean_action_text(match.group("item"))
        list_phrase = f" {match.group('list').lower()} "
        list_type = _list_type_from_text(list_phrase) or ("shopping" if "grocery" in list_phrase else "")
        if item:
            return item, list_type or "shopping"
        return None
    if _context_domain(context) != "lists":
        return None
    ctx_match = re.search(r"\bedit\s+(?P<item>.+?)\s*$", message, re.IGNORECASE)
    if not ctx_match:
        return None
    item = _clean_action_text(ctx_match.group("item"))
    if not item or item.lower() in {"item", "this", "that", "it"}:
        return None
    _list_id, list_type = _context_list_hint(context)
    return item, list_type or "shopping"


def _calendar_edit_from_text(message: str, context: dict[str, Any] | None) -> str | None:
    """Parse 'edit <event title or time>' tap-to-edit utterances for calendar events."""
    match = re.search(r"\bedit\s+(?P<target>.+?)\s*$", message, re.IGNORECASE)
    if not match:
        return None
    if _context_domain(context) != "calendar" and not re.search(r"\b(calendar|schedule|event|appointment|meeting)\b", message, re.IGNORECASE):
        return None
    target = _clean_action_text(match.group("target"))
    return target or None


_HOUR_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
               "ten": 10, "eleven": 11, "twelve": 12}
_HOUR_WORD_RE = "|".join(_HOUR_WORDS)
# A time as people SAY it: "3pm", "9:30", "twelve o'clock", "nine thirty am", "noon". A bare number (no am/pm/o'clock) is
# only accepted by _parse_spoken_time when it is unambiguous (12 or 24-hour).
_TIME_ATOM = (
    r"(?:\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?|o'?\s?clock)?"
    rf"|(?:{_HOUR_WORD_RE})(?:\s+(?:thirty|fifteen|forty[- ]?five))?\s*(?:o'?\s?clock|a\.?m\.?|p\.?m\.?)"
    r"|noon|midday)"
)
_EVENT_NOUNS = r"(?:meeting|appointment|event|call|catch[- ]?up|lunch|dinner|session|interview|class|lesson|check[- ]?up|booking)"
_DAY_PHRASE_RE = re.compile(
    r"\s*\b(?:(?:on|for)\s+)?(?:(?:next|this|coming)\s+)?(?:" + "|".join(_WEEKDAYS) + r"|today|tonight|tomorrow)\b",
    re.IGNORECASE,
)
_CREATE_VERBS = r"add|schedule|book|put|set\s*up|create|make"


def _parse_spoken_time(raw: str) -> str:
    """HH:MM for a spoken/numeric time, or "" when it is ambiguous ("at 9" with no am/pm). "three o'clock" with no meridiem
    is read the way appointments go: 1-6 -> afternoon, 7-11 -> morning, 12 -> noon."""
    s = re.sub(r"\s+", " ", (raw or "").strip().lower().replace(".", ""))
    if s in ("noon", "midday"):
        return "12:00"
    minute_words = {"thirty": 30, "fifteen": 15, "forty five": 45, "fortyfive": 45}
    m = re.match(rf"^(?P<h>{_HOUR_WORD_RE})(?: (?P<mw>thirty|fifteen|forty[- ]?five))? ?(?P<suffix>o'? ?clock|am|pm)$", s)
    if m:
        hour, minute, suffix = _HOUR_WORDS[m.group("h")], minute_words.get((m.group("mw") or "").replace("-", " "), 0), m.group("suffix")
    else:
        m = re.match(r"^(?P<h>\d{1,2})(?::(?P<m>\d{2}))? ?(?P<suffix>am|pm|o'? ?clock)?$", s)
        if not m:
            return ""
        hour, minute, suffix = int(m.group("h")), int(m.group("m") or 0), m.group("suffix") or ""
        if not suffix and 1 <= hour <= 11 and m.group("m") is None:
            return ""                          # "at 9": am or pm? ask, never guess (same rule as _parse_time)
    if hour > 23 or minute > 59:
        return ""
    if suffix == "pm" and hour < 12:
        hour += 12
    elif suffix == "am" and hour == 12:
        hour = 0
    elif suffix and suffix.startswith("o") and 1 <= hour <= 6:
        hour += 12
    return f"{hour:02d}:{minute:02d}"


def _event_title(raw: str, tail: str = "") -> str:
    """The event's title from the words around it: the day phrase and the "to my calendar" destination dropped, the article
    stripped, and a noun-derived default ("a doctor's appointment") given a capital - "Doctor's appointment"."""
    title = _clean_action_text(raw)
    title = _DAY_PHRASE_RE.sub("", title)
    title = re.sub(r"\s+(?:to|on|in|into)\s+(?:my\s+|the\s+)?(?:calendar|schedule|diary|agenda)\s*$", "", title, flags=re.IGNORECASE)
    tail = re.sub(r"^[\s.,;:!?]+|[\s.,;:!?]+$", "", tail or "")
    if re.match(r"(?:for|with|at|to|on|about|in)\b", tail, re.IGNORECASE):
        tail = tail[:1].lower() + tail[1:]          # "… 9 a.m. For work." -> "… for work" (the capital was STT's sentence break)
    if tail:
        title = f"{title} {tail}"
    title = re.sub(r"\s+(?:on|at|for|the|a|an|my)\s*$", "", re.sub(r"\s+", " ", title).strip(), flags=re.IGNORECASE)
    title = _clean_action_text(title)
    if title and re.search(rf"\b{_EVENT_NOUNS}\b", title, re.IGNORECASE) and title[0].islower():
        title = title[0].upper() + title[1:]
    return title


def _calendar_create_from_text(message: str, context: dict[str, Any] | None) -> tuple[str, str, int] | None:
    """"add/schedule/book X (on <day>) at <time>" and "what about a meeting <day> at <time>" -> (title, HH:MM, day_offset). The
    day is resolved by the caller from the same text, plus ``day_offset`` (1 for the small hours of "2 tonight"). A time that cannot be read, or no event cue at all, claims nothing."""
    text = re.sub(r"\s+", " ", message or "").strip()
    cue = re.search(r"\b(calendar|schedule|event|appointment|meeting)\b", text, re.IGNORECASE)
    if _context_domain(context) != "calendar" and not cue:
        return None
    head = _command_shaped(text, _CREATE_VERBS)
    tail = ""
    if head is not None:
        match = re.search(
            rf"\b(?:{_CREATE_VERBS})\s+(?P<title>.+?)\s+(?:at|for)\s+(?P<time>{_TIME_ATOM})\b",
            text[head.start():], re.IGNORECASE,
        )
    else:
        # "What about a meeting tomorrow at 9 a.m. for work" - a suggestion shaped like a request, with an event noun.
        match = re.match(
            rf"^(?:\W*(?:uh|um|okay|ok|so|and|well)\b\W*)*(?:what|how) about\s+(?:uh\s+|um\s+)?(?:at\s+)?"
            rf"(?P<title>.+?\b{_EVENT_NOUNS}\b.*?)\s+(?:at|for)\s+(?P<time>{_TIME_ATOM})\b(?P<tail>.*)$",
            text, re.IGNORECASE,
        )
        if match:
            tail = match.group("tail") or ""
    if not match:
        return None
    title = _event_title(match.group("title"), tail)
    # A timer / alarm / reminder is not a calendar event, whatever verb it follows ("schedule a timer for 10 tonight").
    if _NOT_A_CALENDAR_EVENT_RE.search(text):
        return None
    raw_time = match.group("time")
    resolved = _resolve_daypart_time(raw_time, _parse_spoken_time(raw_time), text)
    if resolved is None:
        return None
    target_time, day_offset = resolved
    return (title, target_time, day_offset) if title and target_time else None


_NOT_A_CALENDAR_EVENT_RE = re.compile(r"\b(?:timers?|alarms?|stopwatch|remind(?:ers?|s)?|remind\s+me)\b", re.IGNORECASE)
_NIGHT_RE = re.compile(r"\b(?:tonight|night)\b", re.IGNORECASE)
_EVENING_RE = re.compile(r"\bevening\b", re.IGNORECASE)
_MORNING_RE = re.compile(r"\bmorning\b", re.IGNORECASE)


def _raw_hour_minute(raw: str) -> tuple[int, int] | None:
    """The hour and minute AS SPOKEN ("two o'clock" -> 2, never the afternoon reading), or None when it is not a plain hour."""
    s = re.sub(r"\s+", " ", (raw or "").strip().lower().replace(".", ""))
    m = re.match(rf"^(?P<h>{_HOUR_WORD_RE})(?: (?P<mw>thirty|fifteen|forty[- ]?five))? ?(?:o'? ?clock)?$", s)
    if m:
        minutes = {"thirty": 30, "fifteen": 15, "forty five": 45, "fortyfive": 45}
        return _HOUR_WORDS[m.group("h")], minutes.get((m.group("mw") or "").replace("-", " "), 0)
    m = re.match(r"^(\d{1,2})(?::(\d{2}))? ?(?:o'? ?clock)?$", s)
    if m and int(m.group(2) or 0) < 60:
        return int(m.group(1)), int(m.group(2) or 0)
    return None


def _resolve_daypart_time(raw_time: str, parsed: str, text: str) -> tuple[str, int] | None:
    """``(HH:MM, day_offset)`` for a time the words place in a part of the day, ``(parsed, 0)`` when they do not, ``None`` when
    the time cannot be placed (the turn is not claimed as a create).

    No am/pm and no noon/midday in the time itself, then the day-part word decides the 12-hour reading:
    * tonight / night: 6-11 -> that hour in the evening, TODAY ("11 tonight" 23:00); 12 and 1-5 are the small hours - they
      are the NEXT date's 00:00-05:00 ("2 o'clock tonight" is not 14:00 today), offset +1.
    * evening: 1-11 -> pm ("5 this evening" 17:00); 12 is not an evening time (None).
    * morning: 1-11 -> am AS SPOKEN ("3 o'clock in the morning" 03:00, not the afternoon reading a bare o'clock gets); 12 None.
    Anything else (explicit am/pm, noon, midday, 24-hour, no day-part word) is left to ``_parse_spoken_time``."""
    if re.search(r"[ap]\.?m\b|noon|midday", raw_time, re.IGNORECASE):
        return parsed, 0
    night, evening, morning = bool(_NIGHT_RE.search(text)), bool(_EVENING_RE.search(text)), bool(_MORNING_RE.search(text))
    if not (night or evening or morning):
        return parsed, 0
    if sum((night, evening, morning)) > 1:
        return None                                            # "tomorrow morning ... tonight": contradictory, ask
    hm = _raw_hour_minute(raw_time)
    if hm is None:
        return (parsed, 0) if parsed else None                # unplaceable
    hour, minute = hm
    if not 1 <= hour <= 12:
        return (f"{hour:02d}:{minute:02d}", 0) if hour <= 23 else None      # 24-hour ("18:30 tonight")
    if night:
        if 6 <= hour <= 11:
            return f"{hour + 12:02d}:{minute:02d}", 0
        return f"{hour % 12:02d}:{minute:02d}", 1
    if evening:
        return (f"{hour + 12:02d}:{minute:02d}", 0) if hour < 12 else None
    return (f"{hour:02d}:{minute:02d}", 0) if hour < 12 else None


def _calendar_create_notime_from_text(message: str, context: dict[str, Any] | None) -> str | None:
    """A calendar add with NO time → an all-day event. Extract the title WITHOUT the
    trailing "to my/the calendar" so "add work to my calendar" is "work", not the
    whole phrase (the bug Jason hit). Requires a calendar cue to avoid hijacking."""
    if _context_domain(context) != "calendar" and not re.search(r"\bcalendar\b", message, re.IGNORECASE):
        return None
    match = re.search(
        r"\b(?:add|put|schedule|create)\s+(?P<title>.+?)\s+(?:to|on|in|into)\s+(?:my\s+|the\s+)?calendar\b",
        message,
        re.IGNORECASE,
    )
    if not match:
        match = re.search(
            r"\b(?:add|create|new)\s+(?:a\s+)?(?:calendar\s+)?(?:event|appointment|meeting)\s+(?:called|named|titled|for)\s+(?P<title>.+?)\s*$",
            message,
            re.IGNORECASE,
        )
    if not match:
        return None
    title = _clean_action_text(match.group("title"))
    # belt-and-suspenders: strip any leftover "to/on my calendar" tail
    title = re.sub(r"\s+(?:to|on|in|into)\s+(?:my\s+|the\s+)?calendar\s*$", "", title, flags=re.IGNORECASE).strip()
    return title or None


def _calendar_update_from_text(message: str, context: dict[str, Any] | None) -> tuple[str, str] | None:
    match = re.search(
        r"\b(?:change|move|reschedule)\s+(?P<target>.+?)\s+to\s+(?P<time>\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)?)\b",
        message,
        re.IGNORECASE,
    )
    if not match:
        return None
    if _context_domain(context) != "calendar" and not re.search(r"\b(calendar|schedule|event|appointment|meeting)\b", message, re.IGNORECASE):
        return None
    target = _clean_action_text(match.group("target"))
    target_time = _parse_contextual_time(match.group("time"), target, context)
    return (target, target_time) if target_time else None


def _birthday_from_text(text: str) -> str:
    match = re.search(
        rf"\b(?:birthday\s+is|birthday\s+on|born\s+on)\s+(?:the\s+)?(?P<day>\d{{1,2}})(?:st|nd|rd|th)?(?:\s+of)?\s+(?P<month>{MONTH_PATTERN})\b",
        text,
        re.IGNORECASE,
    )
    if not match:
        return ""
    day = int(match.group("day"))
    month_name = match.group("month").lower()
    month = MONTHS.get(month_name)
    if not month:
        return ""
    try:
        target = date(2000, month, day)
    except ValueError:
        return ""
    return f"{target.day} {target.strftime('%B')}"


def _people_fact_from_text(message: str) -> tuple[str, str, str] | None:
    match = re.search(
        r"\bremember\s+(?:that\s+)?(?P<name>[a-z][a-z]+(?:\s+[a-z][a-z]+)?)\s+(?P<fact>(?:likes|loves|hates|prefers|birthday|born|has|is)\b.+)$",
        message.strip(),
        re.IGNORECASE,
    )
    if not match:
        return None
    raw_name = match.group("name").strip()
    first_word = raw_name.split()[0].lower()
    if first_word in {"a", "an", "the", "my", "our", "your", "his", "her", "their", "this", "that"}:
        return None
    name = raw_name.title()
    fact = match.group("fact").strip(" .")
    birthday = _birthday_from_text(fact)
    fact = re.sub(
        rf"\s*(?:and\s+)?(?:her|his|their)?\s*birthday\s+is\s+(?:the\s+)?\d{{1,2}}(?:st|nd|rd|th)?(?:\s+of)?\s+(?:{MONTH_PATTERN})\s*",
        " ",
        fact,
        flags=re.IGNORECASE,
    )
    fact = _clean_action_text(fact)
    return name, fact, birthday


def _people_filters_from_text(text: str) -> tuple[str, str, str]:
    context = next((value for value in PEOPLE_CONTEXTS if f" {value} " in text), "")
    circle = next((value for value in PEOPLE_CIRCLES if f" {value} " in text), "")
    query = ""
    match = re.search(r"\b(?:find|show|search for|look up)\s+(?:my\s+)?(?P<name>[a-z][a-z .'-]{1,80})\b", text)
    if match:
        candidate = match.group("name").strip()
        stop_words = {
            "people",
            "contact",
            "contacts",
            "person",
            "profile",
            "family",
            "friends",
            "work contacts",
            "personal contacts",
            "my contacts",
            "settings",
            "dashboard",
            "calendar",
            "weather",
            "list",
            "lists",
            "shopping list",
            "tasks",
        }
        if candidate not in stop_words and not candidate.endswith(" contacts"):
            query = candidate
    return query, context, circle


# Self-identity questions the panel should answer with an identity card rather
# than let fall through to the brain's fuzzy settings match. Kept deliberately
# tight (self-directed only): "who is <name>" and other people SEARCHES must not
# match here.
_IDENTITY_QUERY_RE = re.compile(
    r"\bwho\s+am\s+i\b"
    r"|\bwho(?:'s|\s+is)\s+(?:currently\s+)?(?:signed|logged)\s+in\b"
    r"|\b(?:what(?:'s|\s+is)|whats)\s+my\s+name\b"
    r"|\bam\s+i\s+(?:signed|logged)\s+in\b"
    r"|\bwho\s+do\s+you\s+think\s+i\s+am\b"
    r"|\bwho\s+are\s+you\s+talking\s+to\b"
)

# "what's my name" asks for the NAME, not for the sign-in state: the identity ask answers it as "Your name is <name>."
_IDENTITY_NAME_RE = re.compile(r"\b(?:what(?:'s|\s+is)|whats)\s+my\s+name\b")

# Surface the pending "add this known person as a contact" offers as tappable
# person_confirm cards (as opposed to a people SEARCH). Two shapes:
#   • noun phrases ("contact suggestions") — a distinctive ask, matched anywhere.
#   • verb phrases ("contacts to add") — ambiguous prefixes of directory queries
#     ("show contacts to add as friends" / "…to add to my family"), so they must
#     END the utterance to count. `text` is space-padded, hence \s*[?.!]*\s*$.
# This keeps a plain "show my contacts" (and qualified directory asks) on the
# directory branch below.
_PENDING_OFFERS_RE = re.compile(
    r"\b(?:contact suggestions|suggested contacts|pending contacts)\b"
    r"|\b(?:(?:new\s+)?contacts?\s+to\s+add|people\s+to\s+add|anyone\s+to\s+add"
    r"|who\s+(?:can|should)\s+i\s+add)\s*[?.!]*\s*$",
    re.IGNORECASE,
)

# People CREATE from a spoken/tapped command — the person_confirm card's Add button
# emits exactly these forms, so skybridge handles the create deterministically
# (rather than depending on the brain fallback). Matched against the ORIGINAL-case
# message so the contact name keeps its capitalisation. Name/relationship are
# captured up to the "as my" / "to contacts" delimiter (not a letter whitelist) so
# accented names ("José"), apostrophes ("mom's friend"), etc. all match.
_PEOPLE_CREATE_AS_RE = re.compile(
    r"^\s*(?:add|save|create)\s+(?P<name>.+?)\s+as\s+my\s+(?P<rel>.+?)\s*$",
    re.IGNORECASE,
)
_PEOPLE_CREATE_TO_RE = re.compile(
    r"^\s*(?:add|save|create)\s+(?P<name>.+?)(?:\s+as\s+my\s+(?P<rel>.+?))?"
    r"\s+to\s+(?:my\s+)?contacts\s*$",
    re.IGNORECASE,
)


# Zoe's speaking voice (the "Zoe's voice" settings card). SET is matched against
# the ORIGINAL-case message and anchored to the end so "set your voice to ember"
# captures just the name; SHOW is a deliberately tight vocabulary ("zoe's voice",
# "voice settings", "change your voice") so ordinary sentences containing the
# word "voice" fall through to the brain.
_VOICE_SET_RE = re.compile(
    r"\b(?:set|change|switch)\s+(?:zoe'?s?|your|the)\s+voice\s+to\s+"
    r"(?P<name>[A-Za-z0-9_ -]{2,40}?)\s*[.!?]*\s*$",
    re.IGNORECASE,
)
_VOICE_SHOW_RE = re.compile(
    r"\bzoe'?s?\s+voice\b"
    r"|\bvoice\s+settings?\b"
    r"|\b(?:change|choose|pick|select)\s+(?:zoe'?s?|your)\s+voice\b",
    re.IGNORECASE,
)


# ── Command SHAPE for the people fast path ──────────────────────────────────
# The people branches used to claim a turn from a BARE KEYWORD anywhere in the
# utterance (" family", " friends", " person", " contact" …). Live 2026-09-28 the
# panel heard "I go down and I spend the weekends with him and just get updates
# from the family." and answered "I found 0 contacts." — a statement about
# Jason's life, answered as a directory query. Same class as #1150 (the fast
# path greedily claiming "add a journal entry"). These words are everyday
# vocabulary, so the people fast path now claims ONLY a command or a question:
# an imperative/interrogative anchored at the START of the utterance (after
# wake-word / filler / politeness), with the people noun as the verb's OBJECT.
# A declarative containing the word falls through to the brain.
#
# `_SHAPE_LEAD` eats what Moonshine actually puts in front of a command on the
# panel ("Hey, Zoe.", "Yeah.", "Sorry.", "Thank you. Hey, Zoe."). `text` in the
# classifier is lower-cased and space-padded with trailing punctuation stripped;
# internal punctuation survives, hence the `[\s,.!?]*` separators.
_SHAPE_LEAD = (
    r"^\s*(?:(?:hey|hi|hello|ok|okay|so|and|um|uh|oh|well|yeah|yes|sorry|please"
    r"|thanks|thank\s+you|bye|zoe|zoey)\b[\s,.!?]*)*"
)
_SHAPE_POLITE = (
    r"(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?"
    r"|please\s+"
    r"|(?:can|could|may)\s+i\s+"
    r"|i\s+(?:want|would\s+like)\s+to\s+|i'd\s+like\s+to\s+|i\s+wanna\s+"
    r"|let\s+me\s+|let's\s+)?"
)
# The people nouns, as WHOLE words: `(?!')` keeps "my family's place" out, and
# the trailing \b keeps "personal"/"persona"/"contacted" out (" person" used to
# match "personal" as a substring).
_PEOPLE_NOUN = (
    r"(?:people|contacts?|contact\s+list|directory|address\s+book"
    r"|family(?:\s+members)?|friends|person|profile)\b(?!')"
)
_PEOPLE_QUALIFIERS = r"(?:(?:work|personal|close|inner|best|old|new|other|family)\s+){0,2}"
_PEOPLE_DETERMINER = r"(?:(?:all\s+(?:of\s+)?)?(?:my|our|the|your|zoe's)\s+|all\s+)?"
# "see"/"get"/"go to"/"look at" are deliberately NOT here: "I want to see my
# family this weekend" and "I need to go to my family's" are life statements.
_PEOPLE_VERB = (
    r"(?:show|showing|list|open|find|search(?:\s+for)?|look\s+up|pull\s+up"
    r"|bring\s+up|display|view|browse|check|tell\s+me\s+about)"
)
_PEOPLE_DIRECTORY_ASK_RE = re.compile(
    "|".join(
        (
            # imperative: "show my family", "open people", "can you tell me about the contacts"
            _SHAPE_LEAD + _SHAPE_POLITE + _PEOPLE_VERB + r"\s+(?:(?:me|us)\s+)?"
            + _PEOPLE_DETERMINER + _PEOPLE_QUALIFIERS + _PEOPLE_NOUN,
            # bare noun phrase as the whole turn: "contacts", "my family", "people"
            _SHAPE_LEAD + r"(?:my\s+|our\s+|the\s+)?" + _PEOPLE_QUALIFIERS + _PEOPLE_NOUN + r"\s*$",
            # "who's in my family" / "who are my friends" / "who is in my contacts"
            _SHAPE_LEAD + r"who(?:'s|\s+is|\s+are|\s+do\s+i\s+have)\s+(?:in\s+)?(?:my|our)\s+"
            + _PEOPLE_QUALIFIERS + _PEOPLE_NOUN,
            # "what contacts do I have" / "how many people are in my contacts"
            _SHAPE_LEAD + r"(?:what|which|how\s+many)\s+" + _PEOPLE_QUALIFIERS + _PEOPLE_NOUN
            + r"\s+(?:do|did|have|are|is)\b",
            # "do I have any contacts"
            _SHAPE_LEAD + r"(?:do|have)\s+(?:i|we)\s+(?:have|got)\s+(?:any\s+)?" + _PEOPLE_QUALIFIERS + _PEOPLE_NOUN,
            # "what's in my contacts" / "what are my contacts" — noun must END the turn,
            # so "what's my family doing this weekend" stays a conversation.
            _SHAPE_LEAD + r"(?:what's|whats|what\s+is|what\s+are)\s+(?:in\s+|on\s+)?(?:my|our|the)\s+"
            + _PEOPLE_QUALIFIERS + _PEOPLE_NOUN + r"\s*$",
            # "what's my name in contacts" / "is sarah in my contacts" — a question
            # that ENDS on the directory.
            _SHAPE_LEAD + r"(?:what|what's|whats|who|who's|is|are|does|do|how)\b.*\bin\s+(?:my\s+|the\s+)?"
            r"(?:contacts|contact\s+list|address\s+book|directory)\s*$",
        )
    )
)
# Person SEARCH by name ("find Sarah", "can you look up the plumber"): the verb
# must OPEN the turn, and "show" with a pronoun object ("show me the kitchen",
# "show you Tanika") is Zoe being shown something, not a contact search.
_PEOPLE_NAME_SEARCH_RE = re.compile(
    _SHAPE_LEAD + _SHAPE_POLITE
    + r"(?:find|search\s+for|look\s+up|show)\s+(?!(?:me|us|you|them|him|her|it)\b)(?:my\s+)?[a-z][a-z .'-]{1,80}\b"
)
# "remember that Sarah likes flowers" WRITES a fact, so it gets the same shape
# rule: a request to Zoe ("remember…", "can you remember…", "please remember…"),
# never a first-person memory of the speaker's own ("I remember that Sam is…",
# "I don't remember that Sarah has…").
_PEOPLE_FACT_SHAPE_RE = re.compile(
    _SHAPE_LEAD + r"(?:" + _SHAPE_POLITE + r"|i\s+(?:want|need)\s+you\s+to\s+)" + r"remember\b"
)


# The loose read fallbacks (kept loose on purpose: Moonshine drops leading wh-words, and the router veto covers a
# statement) match WHOLE WORDS, never a substring: " list" used to claim "are you always LISTening?" and read out a list.
_LIST_KEYWORD_RE = re.compile(r"\b(?:lists?|shopping|groceries|grocery|tasks|todos|to-dos)\b")
_CALENDAR_KEYWORD_RE = re.compile(
    r"\b(?:calendars?|schedules?|scheduled|events|appointments|agenda|happening)\b|\bwhat'?s\s+on\b"
)


def _is_people_directory_ask(text: str) -> bool:
    """True when ``text`` (classifier-normalised) is a command/question for the
    people directory, not a sentence that merely contains a people word."""
    return bool(_PEOPLE_DIRECTORY_ASK_RE.search(text))


def classify_skybridge_intent(
    message: str, context: dict[str, Any] | None = None, *, _filler_retry: bool = True
) -> SkybridgeIntent | None:
    """Classify only domains that Skybridge can resolve to real data cards."""
    raw_message = message or ""
    # Strip TRAILING sentence punctuation before matching. Moonshine punctuates
    # its transcripts — the live logs carry "Turn the light off." and "Please,
    # Zoe. Turn off the light." — and 22 patterns below anchor on end-of-string,
    # so a full stop silently defeats them. "turn the light off" classified as a
    # command while "turn the light off." fell through to a fallback that POSTed
    # to a non-existent entity and still said "Lights off."
    #
    # Only the trailing run is removed, so internal punctuation that patterns
    # genuinely rely on ("40%", "5:30", "dr. smith") is untouched. Two patterns
    # already carried a local `[.!]*` workaround for this — fixing it at the
    # source is what makes the other twenty safe.
    text = f" {re.sub(r'[.!?,;:]+$', '', raw_message.lower().strip())} "
    person_fact = _people_fact_from_text(raw_message) if _PEOPLE_FACT_SHAPE_RE.search(text) else None
    if person_fact:
        name, fact, birthday = person_fact
        return SkybridgeIntent(domain="people", action="remember_fact", person_name=name, fact_text=fact, birthday=birthday)
    # Timers claim the phrase early so "set a 5 minute timer" isn't misread as a
    # calendar event ("set ... at a time").
    if re.search(r"\btimers?\b|\bcountdowns?\b", text):
        status = SkybridgeIntent(domain="timer", action="status")
        # A question or statement ABOUT a timer ("should I add 5 minutes to the timer?", "why did you cancel the
        # timer?") is answered with the timers' status - never a write.
        if command_shapes.asks_or_mentions(text):
            return status
        changing = command_shapes.asks_or_mentions(text, destructive=True)
        # DESTRUCTIVE timer commands act only on an ALLOW-LISTED shape (command_shapes.allowed_timer_*). Anything
        # change-like or cancel-like that is not on the list - an ordinal, "other", a subset, a qualified quantifier,
        # a negation, "except", a conjunction, an unknown word - is a status read, never a write. Before this, every
        # phrasing nobody had listed fell through to act (a new timer for "add 5 minutes to the timer", every timer
        # cancelled for "cancel every other timer").
        if command_shapes.timer_adjust(text) is not None or re.search(rf"\b{command_shapes.CHANGE_CUE}\b", text):
            adjust = None if changing else command_shapes.allowed_timer_adjust(text)
            if adjust is None:
                return status
            return SkybridgeIntent(domain="timer", action=adjust.direction, duration_seconds=adjust.seconds,
                                   title=adjust.target.label.title(), start_seconds=adjust.target.start_seconds)
        pause = command_shapes.timer_pause_resume(text)
        if pause is not None:  # never a write: answered honestly that Zoe can't pause
            action, target = pause
            return SkybridgeIntent(domain="timer", action=action, title=target.label.title(),
                                   start_seconds=target.start_seconds)
        if re.search(rf"\b{command_shapes.CANCEL_CUE}\b", text):
            allowed = None if changing else command_shapes.allowed_timer_cancel(text)
            if allowed is None:
                return status
            scope, target = allowed
            return SkybridgeIntent(domain="timer", action="cancel", query="" if scope == "one" else scope,
                                   title=target.label.title(), start_seconds=target.start_seconds)
        if re.search(r"\b(how (?:long|much)|left|remaining|status|check on|still going)\b", text):
            return status
        # "show/list my timers" is a status ask too. Without this, the query fell
        # through to the people fallback ("show my X" -> contact search) and the
        # dashboard Timers tile rendered an empty people directory (glass-verified).
        if re.search(r"\b(show|list|view|see|display|what)\b", text):
            return status
        duration = _parse_timer_duration(text)
        # Only CREATE for an explicit ask — a passing mention ("it's not the timer",
        # "the timer went off") must not spin one up. A duration or a start verb is
        # the cue; "set a timer" with no duration still defaults to 5 minutes.
        if duration or re.search(r"\b(set|start|make|create|run|new|put|begin)\b", text):
            return SkybridgeIntent(domain="timer", action="create", title=_parse_timer_label(text), duration_seconds=duration or 300)
    music_intent = _classify_music(text)
    if music_intent is not None:
        return _unless_delayed(music_intent, text, strict=True)
    # Zoe's speaking voice — matched early (the phrases are distinctive) so
    # "set your voice to ember" can't be misread as a calendar/list write.
    voice_set = _VOICE_SET_RE.search(raw_message)
    if voice_set:
        return SkybridgeIntent(domain="voice", action="set", query=voice_set.group("name").strip())
    if _VOICE_SHOW_RE.search(text):
        return SkybridgeIntent(domain="voice", action="show")
    calendar_update = _calendar_update_from_text(raw_message, context)
    if calendar_update:
        target, target_time = calendar_update
        return SkybridgeIntent(domain="calendar", action="update_time", target_text=target, target_time=target_time)
    list_add = _list_add_from_text(raw_message)
    if list_add:
        item, list_type = list_add
        return SkybridgeIntent(domain="lists", action="add_item", item_text=item, list_type=list_type)
    list_complete = _list_complete_from_text(raw_message)
    if list_complete:
        item, list_type, completed, list_name = list_complete
        return SkybridgeIntent(domain="lists", action="complete_item", item_text=item, list_type=list_type,
                               completed=completed, list_name=list_name)
    list_remove = _list_remove_from_text(raw_message)
    if list_remove:
        item, list_type, list_name = list_remove
        return SkybridgeIntent(domain="lists", action="remove_item", item_text=item, list_type=list_type,
                               list_name=list_name)
    list_contains = _list_contains_from_text(text)
    if list_contains:
        item, list_type, list_name = list_contains
        return SkybridgeIntent(domain="lists", action="contains", item_text=item, list_type=list_type,
                               list_name=list_name)
    calendar_delete = _calendar_delete_from_text(raw_message, context)
    if calendar_delete:
        return SkybridgeIntent(domain="calendar", action="delete_event", target_text=calendar_delete)
    list_create = _list_create_from_text(raw_message, context)
    if list_create:
        list_name, list_type = list_create
        return SkybridgeIntent(domain="lists", action="create_list", list_name=list_name, list_type=list_type)
    contextual_list_add = _contextual_list_add_from_text(raw_message, context)
    if contextual_list_add:
        item, list_type = contextual_list_add
        return SkybridgeIntent(domain="lists", action="add_item", item_text=item, list_type=list_type)
    contextual_list_remove = _contextual_list_remove_from_text(raw_message, context)
    if contextual_list_remove:
        item, list_type = contextual_list_remove
        return SkybridgeIntent(domain="lists", action="remove_item", item_text=item, list_type=list_type)
    list_edit = _list_edit_from_text(raw_message, context)
    if list_edit:
        item, list_type = list_edit
        return SkybridgeIntent(domain="lists", action="edit_item", item_text=item, list_type=list_type)
    calendar_edit = _calendar_edit_from_text(raw_message, context)
    if calendar_edit:
        return SkybridgeIntent(domain="calendar", action="edit_event", target_text=calendar_edit)
    calendar_create = _calendar_create_from_text(raw_message, context)
    if calendar_create:
        title, target_time, day_offset = calendar_create
        parsed_range = _calendar_date_from_text(text, _today())
        start = (parsed_range[1] if parsed_range else _context_calendar_date(context)) + timedelta(days=day_offset)
        return SkybridgeIntent(domain="calendar", action="create_event", title=title, target_time=target_time, range_label=start.isoformat(), start_date=start, end_date=start)
    calendar_create_notime = _calendar_create_notime_from_text(raw_message, context)
    if calendar_create_notime:
        parsed_range = _calendar_date_from_text(text, _today())
        start = parsed_range[1] if parsed_range else _context_calendar_date(context)
        return SkybridgeIntent(domain="calendar", action="create_event", title=calendar_create_notime, target_time="", all_day=True, range_label=start.isoformat(), start_date=start, end_date=start)
    # smart_home runs AFTER list/calendar parsing (so "add lights to the shopping
    # list" stays a list write) but BEFORE the generic clock/weather/people
    # fallbacks. Household-shared like clock/music — no identity gate.
    calendar_followup = _calendar_followup_intent(raw_message, context)
    if calendar_followup is not None:
        return calendar_followup
    smart_home_intent = _smart_home_followup_intent(raw_message, context) or _classify_home(raw_message, text)
    if smart_home_intent is not None:
        return _unless_delayed(smart_home_intent, text)
    if (
        " clock " in text
        or re.search(r"\bwhat(?:'s| is)?\s+the\s+time\b", text)
        or re.search(r"\bwhat\s+time\s+is\s+it\b", text)
        or re.search(r"\bshow\s+(?:me\s+)?(?:the\s+)?time\b", text)
        or re.search(r"\bcurrent\s+time\b", text)
    ):
        return SkybridgeIntent(domain="clock", action="show")
    # A command-shaped WRITE ("add fix the gate to my to-do list", "remind me to check the weather") that no parser above
    # claimed is never answered with a weather / list READ (live 2026-10-12: "Lists has 2 items." and nothing added): it
    # falls through to the brain instead.
    write_led = _command_shaped(raw_message, _WRITE_VERB_RE) is not None
    weather_ask = None if write_led else classify_weather_ask(text)
    if weather_ask is not None:
        # query = what was asked for (high/low/rain/outlook/current), range_label = which day.
        return SkybridgeIntent(domain="weather", action=weather_ask.action, query=weather_ask.focus,
                               range_label=weather_ask.day)
    if not write_led and _LIST_KEYWORD_RE.search(text):
        list_type = _list_type_from_text(text)
        action = "overview" if re.search(r"\blists\b", text) else "show"
        return SkybridgeIntent(domain="lists", action=action, list_type=list_type)
    # A command-shaped CREATE ("schedule a meeting at midnight", "book a lunch at half past twelve tonight") whose time the
    # parser above could not place is not a calendar READ either: the bare "schedule" keyword used to answer it with today's
    # events. It goes on to the brain, which can ask.
    create_led = _command_shaped(raw_message, _CREATE_VERBS) is not None
    if not write_led and not create_led and _CALENDAR_KEYWORD_RE.search(text):
        today = _today()
        parsed_range = _calendar_date_from_text(text, today)
        if parsed_range is not None:
            label, start, end = parsed_range
            return SkybridgeIntent("calendar", "show", label, start, end)
        return SkybridgeIntent("calendar", "show", "today", today, today)
    if _IDENTITY_QUERY_RE.search(text) and not any(
        term in text for term in (" contact", " people", " directory", " profile")
    ):
        # "who am I" / "who's signed in" / "what's my name" — a self-directed
        # identity ask. Surfaces WHO Zoe thinks is speaking (an identity card),
        # not a fuzzy "AI Training" settings page. A people SEARCH ("who is
        # Sarah") does NOT match this pattern, a fact write ("my name is Sam")
        # is already claimed earlier by _people_fact_from_text, and an explicit
        # directory ask ("what's my name in contacts") yields to the people
        # branch below rather than being stolen by the leading identity phrase.
        return SkybridgeIntent(domain="people", action="identity",
                               query="name" if _IDENTITY_NAME_RE.search(text) else "")
    _create_match = _PEOPLE_CREATE_AS_RE.match(raw_message) or _PEOPLE_CREATE_TO_RE.match(raw_message)
    if _create_match:
        # "add Daniel as my brother" / "add Sarah to my contacts" — the
        # person_confirm card's Add button lands here, so the create is handled
        # deterministically server-side (not left to the brain fallback).
        _cname = " ".join(_create_match.group("name").split()).strip()
        _crel = (_create_match.groupdict().get("rel") or "").strip()
        return SkybridgeIntent(domain="people", action="create", person_name=_cname, relationship=_crel)
    if _PENDING_OFFERS_RE.search(text):
        # "any contacts to add?" / "show contact suggestions" — surface the
        # pending person_create offers as tappable Add cards, not a directory.
        return SkybridgeIntent(domain="people", action="pending_offers")
    if _is_people_directory_ask(text):
        # Command/question SHAPE only — never a bare keyword inside a statement
        # (see _PEOPLE_DIRECTORY_ASK_RE; live over-claim 2026-09-28).
        query, people_ctx, circle = _people_filters_from_text(text)
        if " family " in text and not query:
            query = "family"
        if " friends " in text and not query:
            query = "friend"
        return SkybridgeIntent(domain="people", action="show", query=query, context=people_ctx, circle=circle)
    if _PEOPLE_NAME_SEARCH_RE.search(text):
        query, people_ctx, circle = _people_filters_from_text(text)
        if query:
            return SkybridgeIntent(domain="people", action="show", query=query, context=people_ctx, circle=circle)
    if _filler_retry:
        # Nothing matched the words as said: try once more without politeness / wake words ("could you please ...
        # for me"), like HA's skip words. Only ever ADDS a claim - a phrase that already matched is untouched.
        bare = conversation_cues.strip_command_filler(raw_message)
        if bare != raw_message.strip():
            return classify_skybridge_intent(bare, context, _filler_retry=False)
    return None


# ── Router veto: the fast path defers to the two-stage router ───────────────
# The skybridge classifier is regex; the two-stage router (router_two_stage,
# ZOE_ROUTER_HEAD=active) is a trained domain head + grammar-constrained decoder.
# When both ran for a turn and the router did NOT pick a domain compatible with
# what Skybridge claimed — above all when it said `chat` — Skybridge must not
# answer deterministically: the turn goes on to the brain. Live 2026-09-28 the
# router said chat (head_conf 0.92, not gated) and the fast path still answered
# "I found 0 contacts." to a statement about Jason's family.
#
# Sentinels for ``resolve_skybridge_request(router_decision=...)``:
#   UNGATED          (default) the caller has no router turn — a touch tap, a card
#                    button re-issuing a command, the brain-tool card builder. No
#                    gate, no log. Behaviour unchanged.
#   None             a conversational turn whose router was off/failed — allow,
#                    logged as router_unavailable (today's behaviour).
#   dict             ``semantic_router.route()`` output for this turn.
UNGATED = object()

# Skybridge domain → the two-stage router domains that agree with it
# (router_two_stage.DOMAIN_TOOLS vocabulary). A Skybridge domain the router has
# no class for (``voice``) can never be agreed with, so it is never vetoed.
_ROUTER_AGREEING_DOMAINS: dict[str, frozenset[str]] = {
    "people": frozenset({"people"}),
    "calendar": frozenset({"calendar", "reminders"}),
    "lists": frozenset({"lists"}),
    "timer": frozenset({"timers"}),
    "clock": frozenset({"time"}),
    "weather": frozenset({"weather"}),
    "music": frozenset({"music"}),
    "smart_home": frozenset({"smart_home"}),
}


def router_veto_enabled() -> bool:
    """ZOE_SKYBRIDGE_ROUTER_VETO — default ON; ``false``/``0``/``off``/``no``
    restores the pre-2026-09-28 behaviour (Skybridge ignores the router)."""
    raw = (os.environ.get("ZOE_SKYBRIDGE_ROUTER_VETO", "1") or "1").strip().lower()
    return raw not in ("0", "false", "off", "no")


def _router_agrees(intent: SkybridgeIntent, router_domain: str, router_tool: str | None = None) -> bool | None:
    """True/False when the router's verdict agrees with the Skybridge intent;
    None when the router has no class for this Skybridge domain."""
    allowed = _ROUTER_AGREEING_DOMAINS.get(intent.domain)
    if allowed is None:
        return None
    if intent.domain == "people" and intent.action == "remember_fact":
        # "remember that Sarah likes flowers" is the router's remember_fact tool.
        allowed = allowed | {"memory"}
    if intent.domain == "people" and intent.action == "identity" and router_tool == "recall_memory":
        # "what's my name" / "who am I" is a stored-fact READ, and the router files it under memory/recall_memory: a
        # compatible verdict, not a disagreement. Vetoing it sent the question to the brain (6-7 s on voice) while typed chat
        # answered it from the account in ~5 ms (live 2026-10-12). Only the READ tool: a memory/remember_fact verdict on the
        # same words is a write and still disagrees.
        allowed = allowed | {"memory"}
    return router_domain in allowed


# A reply to the new-list naming prompt is a NAME unless it OPENS with a
# hesitation/refusal marker (after optional filler). Deliberately narrow: "Things
# to do" and "Books I want to read" are real list names, so length, pronouns and
# verbs prove nothing — only a leading "actually…/wait/not now/never mind/…" does.
_NAMING_REFUSAL_RE = re.compile(
    r"^(?:(?:um|uh|hmm|well|oh|so|er)\b[\s,.!?]*)*"
    r"(?:actually|wait|hold\s+on|no|nope|never\s*mind|not\s+now|not\s+yet|cancel"
    r"|forget\s+it|let'?s\s+not|let'?s\s+wait|i\s+think\s+we\s+should\s+wait"
    r"|i'?ll\s+do\s+(?:it|that)\s+later|maybe\s+later|later|skip)\b",
    re.IGNORECASE,
)


def _is_naming_prompt_reply(intent: SkybridgeIntent, context: dict[str, Any] | None) -> bool:
    return (
        intent.domain == "lists"
        and intent.action == "create_list"
        and _context_domain(context) == "lists"
        and _context_action(context) == "create_list"
    )


def _looks_like_list_name(message: str) -> bool:
    """False only for a reply that opens with a hesitation/refusal marker
    (``_NAMING_REFUSAL_RE``); any other reply answers the naming prompt."""
    return not _NAMING_REFUSAL_RE.match((message or "").strip())


_READ_ACTIONS = frozenset({"show", "overview", "forecast", "current", "today"})
_WRITE_ACTIONS = frozenset({"add_item", "remove_item", "complete_item", "edit_item", "create_list", "create_event",
                            "update_time", "edit_event", "delete_event", "remember_fact", "create"})
# Domains holding the household's personal data: the only ones the unsure floor applies to (music / smart-home / timers
# keep the garbled-STT recovery - their worst case is a wrong song, not a wrong record).
_UNSURE_FLOOR_DOMAINS = frozenset({"calendar", "lists", "people"})


def _write_like_utterance(message: str, intent: SkybridgeIntent) -> bool:
    """True when the turn is a WRITE or reads like a garbled one: a write action, a write verb in the words, or a calendar
    turn carrying an "at <time>" clause ("Keynes pollutants in my calendar are at 12 o'clock" is a mis-heard "put an
    appointment in my calendar at 12 o'clock", whatever the keyword fallback made of it)."""
    if intent.action in _WRITE_ACTIONS:
        return True
    text = message or ""
    if re.search(rf"\b(?:{_WRITE_VERB_RE}|schedule|book)\b", text, re.IGNORECASE) and intent.action not in _READ_ACTIONS:
        return True
    return intent.domain == "calendar" and re.search(rf"\bat\s+{_TIME_ATOM}", text, re.IGNORECASE) is not None


def _unsure_floor(intent: SkybridgeIntent, message: str = "") -> float | None:
    """The router confidence an UNSURE (below the 0.5 chat gate) verdict must still reach for a WRITE-like turn on a personal-
    data domain to proceed, or None when no floor applies (a plain read keeps the garbled-STT recovery: "shammy my lists").
    ``ZOE_SKYBRIDGE_UNSURE_FLOOR_WRITE``, default 0.45; 0 turns the floor off."""
    if intent.domain not in _UNSURE_FLOOR_DOMAINS or not _write_like_utterance(message, intent):
        return None
    try:
        value = float(os.environ.get("ZOE_SKYBRIDGE_UNSURE_FLOOR_WRITE", 0.45))
    except ValueError:
        value = 0.45
    return value if value > 0 else None


_CALENDAR_NOUN_RE = re.compile(
    rf"\b(?:calendar|events?|appointments?|bookings?|meetings?|calls?|{_EVENT_NOUNS[3:-1]}|(?:my|the)\s+schedule)\b", re.IGNORECASE)


def _has_calendar_noun(message: str) -> bool:
    """A calendar NOUN in the words (meeting / appointment / event / booking / lunch / dinner / call ... / "my schedule"), not
    merely the verb "schedule": "schedule a timer for 10 tonight" has none. Timer / alarm / reminder phrasing never counts."""
    text = message or ""
    return bool(_CALENDAR_NOUN_RE.search(text)) and not _NOT_A_CALENDAR_EVENT_RE.search(text)


def _head_names_calendar(decision: dict[str, Any]) -> bool:
    """The router head's top class (or its non-chat shortlist) is calendar: a low-confidence verdict for some OTHER domain
    (timers, music, people ...) is not "unsure about the calendar"."""
    shortlist = decision.get("shortlist") if isinstance(decision.get("shortlist"), (list, tuple)) else ()
    return str(decision.get("head_top") or "") == "calendar" or "calendar" in {str(c) for c in shortlist}


def skybridge_router_gate(
    message: str,
    intent: SkybridgeIntent,
    router_decision: dict[str, Any] | None,
    *,
    context: dict[str, Any] | None = None,
) -> tuple[str, str, str, float | None]:
    """Decide whether a classified Skybridge intent may answer this turn.

    Returns ``(decision, reason, router_domain, conf)`` with decision ``allow``
    or ``veto``. Pure — no I/O; the caller logs. Only an ACTIVE two-stage
    decision (``router_decision["two_stage"]``) can veto: the similarity router
    has no music/smart_home/notes examples and routes "play some jazz" to
    ``time``, so vetoing on it would switch those domains off.
    """
    if not router_veto_enabled():
        return "allow", "flag_off", "-", None
    if (
        intent.domain in ("lists", "calendar")
        and intent.action in _READ_ACTIONS
        and _command_shaped(message, _WRITE_VERB_RE) is not None
    ):
        # The WORDS say "add/put/create …" and the parse says "show": the parse is the one that is wrong (live 2026-10-12:
        # "Can you add fix zoe to my to-do list" -> "Lists has 2 items." and nothing added). Whatever the router thinks.
        return "veto", "verb_contradiction", "-", None
    decision = (router_decision or {}).get("two_stage") if isinstance(router_decision, dict) else None
    if not isinstance(decision, dict):
        return "allow", "router_unavailable", "-", None
    router_domain = str(decision.get("domain") or "chat")
    conf_raw = decision.get("head_conf")
    conf = float(conf_raw) if isinstance(conf_raw, (int, float)) else None
    if context:
        # A follow-up that only classifies BECAUSE of the card on screen ("add
        # eggs", "move it to 5") — the router sees the bare utterance and cannot
        # know that context, so its verdict carries no information here.
        bare = classify_skybridge_intent(message, None)
        if bare is None or (bare.domain, bare.action) != (intent.domain, intent.action):
            # EXCEPT the new-list naming prompt: it takes the WHOLE reply as the
            # name, so "Actually I think we should wait" would become a list. A
            # reply that opens with a hesitation/refusal marker gets the router's
            # verdict like any other turn; any other reply is a name (Greptile, #1757).
            if not _is_naming_prompt_reply(intent, context) or _looks_like_list_name(message):
                return "allow", "context_followup", router_domain, conf
    agrees = _router_agrees(intent, router_domain, str(decision.get("tool") or "") or None)
    if agrees is None:
        return "allow", "no_router_class", router_domain, conf
    if agrees:
        return "allow", "router_agrees", router_domain, conf
    if (
        router_domain == "chat"
        and decision.get("gated")
        and str(decision.get("head_top") or "") not in ("", "chat")
        # Only the 0.5 stage-1 chat gate (`below_gate`; a record without a
        # reason predates router_two_stage.gate_reason and can only be that)
        # means "no opinion". `low_conf` (ZOE_ROUTER_HEAD_MIN_CONF) is a
        # DELIBERATE send-to-the-brain verdict: allowing here would hand the
        # Samantha-bar S1 shape ("who is flying in Thursday…" with a calendar
        # cue, head people @ 0.54) straight back to a deterministic calendar
        # reply (Greptile, #1763). `chat_top` cannot reach here (head_top is
        # non-chat) and is a chat verdict anyway.
        and (decision.get("reason") or "below_gate") == "below_gate"
    ):
        # Stage 1 fell under its confidence gate on a non-chat domain: the router
        # is UNSURE, not saying "chat". Typically garbled STT ("shammy my lists")
        # where the keyword fallback is the useful recovery — no opinion, no veto.
        # EXCEPT personal-data domains below a confidence FLOOR: "Keynes pollutants in my calendar are at 12 o'clock"
        # (router 0.39) is not a calendar request, and a write on that little evidence is worse than asking.
        floor = _unsure_floor(intent, message)
        if floor is not None and (conf is None or conf < floor):
            return "veto", "router_unsure_low_conf", router_domain, conf
        return "allow", "router_unsure", router_domain, conf
    if (
        router_domain == "chat"
        and decision.get("gated")
        and str(decision.get("reason") or "") == "low_conf"
        and intent.domain == "calendar"
        and intent.action == "create_event"
        and intent.target_time
        and not intent.all_day
        and _head_names_calendar(decision)
        and _has_calendar_noun(message)
    ):
        # "schedule a meeting for 10 o'clock tonight" (live 2026-10-14): the parser read a create verb, an event noun AND a
        # clock time, and the router fell under its low-confidence floor (chat @ 0.65) - it did not say "this is chat", it said
        # "not sure". The structured parse is the stronger evidence for a WRITE it fully understood, and the veto cost 9.4 s on
        # the brain for the same event. Only `low_conf` (a CONFIDENT chat head top / `chat_top` still vetoes, and so does a
        # confident other domain, which `router_disagrees` covers above); only a create with a parsed time (no time = an
        # all-day guess, which keeps the veto); the unsure floor still applies to a verdict this weak.
        floor = _unsure_floor(intent, message)
        if floor is None or (conf is not None and conf >= floor):
            return "allow", "write_parsed_time", router_domain, conf
    return "veto", ("router_chat" if router_domain == "chat" else "router_disagrees"), router_domain, conf


def _log_router_gate(intent: SkybridgeIntent, decision: str, reason: str, router_domain: str, conf: float | None) -> None:
    logger.info(
        "SKYBRIDGE_GATE router=%s conf=%s skybridge=%s action=%s decision=%s reason=%s",
        router_domain,
        "-" if conf is None else f"{conf:.4f}",
        intent.domain,
        intent.action,
        decision,
        reason,
    )


async def _room_eids_for_context(
    context: dict[str, Any] | None, db: Any | None
) -> set[str]:
    """Entity ids of the room the speaking panel is in, or an empty set.

    Every failure mode collapses to EMPTY on purpose: no panel_id (a phone or
    the web chat has no room), no db handle, the panel is in no room, or the
    lookup raised. The resolver treats empty as "no room context" and decides
    exactly as it did before rooms existed, so a bad lookup can never act on the
    wrong room — it can only decline to help.
    """
    panel_id = str((context or {}).get("panel_id") or "").strip()
    if not panel_id:
        return set()
    try:
        from routers.rooms import room_entity_ids_for_panel

        if db is not None:
            return await room_entity_ids_for_panel(db, panel_id)
        async with get_db_ctx() as ctx_db:
            return await room_entity_ids_for_panel(ctx_db, panel_id)
    except Exception:  # noqa: BLE001 — never break a turn over room context
        logger.warning("room context lookup failed for panel %s", panel_id, exc_info=True)
        return set()


async def resolve_skybridge_request(
    message: str,
    user_id: str,
    *,
    context: dict[str, Any] | None = None,
    db: Any | None = None,
    router_decision: Any = UNGATED,
) -> dict[str, Any]:
    """Resolve a typed or spoken Skybridge request into real card contracts.

    ``router_decision`` is the turn's router verdict for the router veto (see
    ``UNGATED`` above). A vetoed turn returns
    ``handled: False`` + ``vetoed: True`` BEFORE any auth challenge or side
    effect, so the caller falls through to its brain lane exactly as for an
    unclassified utterance.
    """
    intent = classify_skybridge_intent(message, context)
    if intent is None:
        return {
            "handled": False,
            "intent": None,
            "spoken_summary": "",
            "cards": [],
            "skybridge_context": context or {},
        }

    if router_decision is not UNGATED:
        decision, reason, router_domain, conf = skybridge_router_gate(
            message, intent, router_decision, context=context
        )
        _log_router_gate(intent, decision, reason, router_domain, conf)
        try:  # the struggle detector: what Skybridge recognised and whether the router let it (an O(1) append, never raises)
            import struggle_detector

            struggle_detector.note_skybridge(message, intent.domain, intent.action, decision)
        except Exception:  # noqa: BLE001
            pass
        if decision == "veto":
            return {
                "handled": False,
                "vetoed": True,
                "intent": None,
                "spoken_summary": "",
                "cards": [],
                "skybridge_context": context or {},
                "gate": {
                    "router": router_domain,
                    "skybridge": intent.domain,
                    "action": intent.action,
                    "reason": reason,
                },
            }

    try:  # failure capture (ZOE_STRUGGLE_OUTCOMES, default off): Skybridge takes this user's turn - a deque append, never raises
        import struggle_detector

        struggle_detector.note_handler("", user_id, "skybridge", f"{intent.domain}.{intent.action}")
    except Exception:  # noqa: BLE001
        pass
    if skybridge_intent_requires_identity(intent) and _is_guest_user(user_id):
        if (intent.domain == "lists" and intent.action in _GUEST_HOUSEHOLD_LIST_ACTIONS
                and panel_principal.in_panel_turn()):
            # Anyone AT THE PANEL may read, add to, tick off and remove from the HOUSEHOLD list (a fridge list). The
            # resolvers below see the household viewer: it owns nothing, so only family-visible lists can match.
            user_id = panel_principal.HOUSEHOLD_VIEWER
        else:
            return _attach_skybridge_context(_auth_required_result(intent))

    # B1.1 phase 2: a speculative voice turn (verdict pending) must not run a
    # Skybridge WRITE (list add, calendar create, music, smart-home, timers) on a
    # transcript prefix — hold it for the verdict; reads render at once. No-op
    # whenever no speculative turn is bound.
    import voice_speculation as _vs
    if (_vs.bound_gate() is not None
            and not _vs.skybridge_intent_is_speculation_safe(intent.domain, intent.action)):
        await _vs.await_commit(f"skybridge:{intent.domain}:{intent.action}")

    if intent.action == "delayed":
        # Nothing is scheduled and nothing runs now: no scheduler for device commands exists yet.
        return _attach_skybridge_context({
            "handled": True, "intent": _intent_dict(intent), "spoken_summary": DELAYED_COMMAND_REPLY,
            "cards": [_status_card("Not scheduled", "Zoe can't run a command later yet. Nothing was changed.",
                                   status="Not scheduled")],
            "actions": [],
        })

    if intent.domain == "clock":
        return _attach_skybridge_context(_resolve_clock(intent))

    if intent.domain == "timer":
        return _attach_skybridge_context(_resolve_timer(intent, user_id))

    if intent.domain == "music":
        from music_service import resolve_music
        # user_id threads the acting user into the listening journal (guest
        # fallback happens at music_history.resolve_music_user, not here).
        return _attach_skybridge_context(await resolve_music(intent, user_id=user_id))

    if intent.domain == "voice":
        return _attach_skybridge_context(await _resolve_voice_settings(intent))

    if intent.domain == "smart_home":
        from smart_home_service import resolve_smart_home
        # "the light" said on a panel means the light in THAT panel's room. The
        # panel already sends context.panel_id; resolve it to the room's device
        # ids so the resolver can break a tie it would otherwise have to ask
        # about. Empty set = no room context = unchanged behaviour, which is
        # every install that has not created a room.
        room_eids = await _room_eids_for_context(context, db)
        out = _attach_skybridge_context(await resolve_smart_home(intent, room_eids))
        _remember_smart_home_target(intent, out)
        return out

    if db is not None:
        out = _attach_skybridge_context(await _resolve_with_db(intent, user_id, db, context=context))
    else:
        async with get_db_ctx() as ctx_db:
            out = _attach_skybridge_context(await _resolve_with_db(intent, user_id, ctx_db, context=context))
    _remember_calendar_read(intent, out)
    return out


def _card_as_component(card: Any) -> dict[str, Any]:
    """Normalize a producer's card to the canonical component shape so it can be
    validated. Mirrors the client-side normalizeCard: a `{component, props}` card
    passes through; a card_service envelope (`{card_type, content, ...}`) maps to
    `{component: card_type, props: content}`."""
    if not isinstance(card, dict):
        raise CardContractError("card must be an object")
    if card.get("component"):
        return {"component": card["component"], "props": card.get("props") or {}}
    if card.get("card_type") and isinstance(card.get("content"), dict):
        return {"component": card["card_type"], "props": card["content"]}
    raise CardContractError(f"card has neither 'component' nor 'card_type' (keys={sorted(card)[:6]})")


def _validate_cards_for_convergence(cards: Any) -> None:
    """Consolidation increment 2 — NON-FATAL measurement gate. Validate every card
    leaving the resolver against the canonical component contract and log any that
    don't conform, so the 4 producers can be migrated one at a time. NEVER mutates
    or drops a card — pure measurement, zero panel risk."""
    for card in cards or []:
        try:
            validate_component(_card_as_component(card))
        except CardContractError as exc:
            comp = card.get("component") or card.get("card_type") if isinstance(card, dict) else "?"
            logger.info("skybridge card non-conforming [convergence]: %s | component=%s", exc, comp)
        except Exception:  # measurement must never break a turn — but stay auditable
            logger.warning("convergence gate hit an unexpected error (non-fatal)", exc_info=True)


def _attach_skybridge_context(result: dict[str, Any]) -> dict[str, Any]:
    if result.get("handled"):
        _validate_cards_for_convergence(result.get("cards"))
        result["skybridge_context"] = {
            "intent": result.get("intent") or {},
            "cards": result.get("cards") or [],
        }
    return result


def _is_guest_user(user_id: str | None) -> bool:
    return (user_id or "").strip() in {"", "guest", "voice-guest"}


def _pick_list_row(rows: Any, name: str = "") -> dict[str, Any]:
    """Which of the lists of a TYPE a type-only add/remove lands on: the household (family-visible) list by the ONE shared
    rule (``list_service.pick_household_list``: fullest, name only a tie-break, then oldest); with none, the caller's own list."""
    from list_service import pick_household_list

    dicts = [dict(r) for r in rows]
    family = [r for r in dicts if r.get("visibility", "family") == "family"]
    return pick_household_list(family, name) or dicts[0]


def _audit_list_item(op: str, user_id: str, list_id: str, item_id: str) -> None:
    """Who touched a list item (no column holds it): the member, or ``panel`` when nobody at the panel was identified."""
    actor = panel_principal.PANEL_ACTOR if user_id == panel_principal.HOUSEHOLD_VIEWER else user_id
    logger.info("list_item_audit op=%s actor=%s list=%s item=%s", op, actor, list_id, item_id)


# What a panel guest may do to the household list; everything else on a list (edit text, create a list) needs a member.
_GUEST_HOUSEHOLD_LIST_ACTIONS = frozenset({"show", "overview", "contains", "add_item", "remove_item", "complete_item"})


def skybridge_intent_requires_identity(intent: SkybridgeIntent | None) -> bool:
    """Return whether a Skybridge intent reads or mutates personal user data."""
    if intent is None:
        return False
    # A self-identity ask ("who am I") answers for guests too — it reports the
    # session's own identity, not another person's private data — so it must not
    # be forced behind the sign-in wall.
    if intent.domain == "people" and intent.action == "identity":
        return False
    return intent.domain in {"calendar", "lists", "people"}


# Music context anchors: a word clearly about audio playback. "play <x>" is only
# treated as music when paired with one of these OR a clear play verb, so
# "play a game" / "play along" never steal the music domain.
_MUSIC_CTX = re.compile(r"\b(music|song|songs|track|tune|tunes|album|playlist|artist|radio|spotify|now\s+playing)\b")
_MUSIC_STOPWORDS = re.compile(r"\b(play\s+(?:a\s+)?(?:game|along|nice|fair|pretend|dead|catch))\b")


# Actions that CHANGE a device or playback right now. A command for later ("turn off the lights in 5 minutes", "stop
# the music in half an hour") used to run immediately - 14 of 14 of HA's delayed-command sentences did (2026-10-14).
_ACTUATING_ACTIONS = frozenset({
    "turn_on", "turn_off", "set_brightness", "activate_scene",
    "play", "pause", "resume", "next", "previous", "stop", "volume_up", "volume_down", "transfer", "dont_stop",
})
DELAYED_COMMAND_REPLY = command_shapes.DELAYED_COMMAND_REPLY


def _unless_delayed(intent: "SkybridgeIntent", text: str, *, strict: bool = False) -> "SkybridgeIntent":
    """An actuation for LATER becomes ``<domain>/delayed``: answered honestly, never run now. A tile tap (exact
    entity id) is always now. ``strict`` (music PLAY only) counts only an explicit amount or clock time, because a
    title may hold "tonight" or "tomorrow"; a transport verb ("stop the music tomorrow") has no title to protect."""
    strict = strict and intent.action in ("play", "transfer", "dont_stop")
    if (intent.action in _ACTUATING_ACTIONS and not intent.entity_id and not intent.entity_ids
            and command_shapes.delayed_command(text, strict=strict)):
        return SkybridgeIntent(domain=intent.domain, action="delayed", query=intent.query)
    return intent


def _classify_music(text: str) -> "SkybridgeIntent | None":
    """Classify a music command for the Music Assistant bridge. `text` is the
    lowercased message padded with surrounding spaces."""
    if _MUSIC_STOPWORDS.search(text):
        return None
    # "play corner light off": a DEVICE whose name has "play" in it, switched on/off - smart home, not music.
    if _HOME_DEVICE_NOUN.search(text) and re.search(r"\b(?:on|off)\s*$", text):
        return None
    # Setup: "add music" / "add/connect <service>" / "music setup".
    m = re.search(r"\b(?:add|connect|set ?up|link)\s+(?:my\s+|a\s+|the\s+)?(spotify|youtube music|ytmusic|tidal|qobuz|deezer|radio|music service|music)\b", text)
    if m:
        svc = m.group(1).strip()
        prov = {"youtube music": "ytmusic", "radio": "radiobrowser",
                "music service": "", "music": ""}.get(svc, svc)
        return SkybridgeIntent(domain="music", action="setup", query=prov)
    # Speaker switch: "move/switch/send music to the kitchen", "play it in the bedroom".
    # The verb+"music/it/this"+to/in guard keeps "play jazz in the kitchen" (a play
    # target, not a transfer) from matching here.
    m = re.search(r"\b(?:move|switch|send|transfer|cast|play)\s+(?:the\s+|my\s+)?(?:music|song|playback|it|this)\s+(?:over\s+)?(?:to|in|on)\s+(?:the\s+)?(.+?)\s*$", text)
    if m:
        return SkybridgeIntent(domain="music", action="transfer", query=m.group(1).strip())
    # "Don't stop the music": MA auto-continues the queue with similar tracks.
    # Checked BEFORE transport so "don't stop the music" isn't read as a stop.
    # Only clearly-continuation phrasings — everything else stays untouched.
    if re.search(r"\b(?:keep\s+(?:the\s+)?music\s+(?:going|playing|on)"
                 r"|don'?t\s+stop\s+the\s+music"
                 r"|play\s+(?:something|songs?|more|music)\s+like\s+(?:this|that)"
                 r"|(?:play\s+)?more\s+(?:songs?|music|tracks?)\s+like\s+(?:this|that))\b", text):
        return SkybridgeIntent(domain="music", action="dont_stop")
    has_ctx = bool(_MUSIC_CTX.search(text))
    # Transport / volume — only with clear music context so bare "pause"/"next"
    # (which could be timers, reading, etc.) don't get hijacked.
    if has_ctx or re.search(r"\bplaying\b", text):
        if re.search(r"\b(pause|hold)\b", text):
            return SkybridgeIntent(domain="music", action="pause")
        if re.search(r"\b(resume|unpause|keep playing|carry on)\b", text):
            return SkybridgeIntent(domain="music", action="resume")
        # "next" as a TIME ("play relaxing music for the next hour") is not a skip.
        if re.search(r"\b(?:next(?!\s+(?:hours?|minutes?|weeks?|days?|time|years?|months?|morning|night|few))|skip|forward)\b",
                     text):
            return SkybridgeIntent(domain="music", action="next")
        if re.search(r"\b(previous|back|last)\s+(?:song|track|tune)\b|\bgo back\b", text):
            return SkybridgeIntent(domain="music", action="previous")
        if re.search(r"\b(stop|turn off)\b", text):
            return SkybridgeIntent(domain="music", action="stop")
        if re.search(r"\b(up|louder|higher|turn it up)\b", text):
            return SkybridgeIntent(domain="music", action="volume_up")
        if re.search(r"\b(down|quieter|lower|softer|turn it down)\b", text):
            return SkybridgeIntent(domain="music", action="volume_down")
    # Radio mode: "play <artist/track> radio" / "start <x> radio" → MA seeds an
    # endless station of similar tracks from the seed. Bare "play radio" /
    # "play the radio" (no seed) falls through to the normal play/search path.
    m = re.search(r"\b(?:play|put on|start)\s+(?:some\s+|the\s+|a\s+)?(.+?)\s+radio\s*$", text)
    if m:
        seed = m.group(1).strip()
        if seed and seed not in ("the", "some", "a", "my"):
            return SkybridgeIntent(domain="music", action="play", query=seed, radio_mode=True)
    # Play X — "play some jazz", "put on the beatles", "play the news".
    m = re.search(r"\b(?:play|put on|start playing|listen to)\s+(?:some\s+|the\s+|a\s+)?(.+?)\s*$", text)
    # The play verb must OPEN the command: "turn on the kitchen play corner lights" names a DEVICE with "play" in it
    # (HA's corpus: 28 such device commands started music instead).
    if m and _command_shaped(text, r"play|put\s+on|start\s+playing|listen\s+to") is not None and (
            has_ctx or re.search(r"\b(play|put on|listen to)\b", text)):
        query = m.group(1).strip()
        # "play music" / "play some music" with no real target is still a PLAY
        # (resume the queue or start the house default) — routing it to status
        # produced the maddening loop "Nothing's playing. Ask me to put
        # something on." in response to being asked to put something on.
        if query in ("music", "something", "a song", "songs", "tunes", "some tunes",
                     "some music", "anything", ""):
            return SkybridgeIntent(domain="music", action="play", query="")
        return SkybridgeIntent(domain="music", action="play", query=query)
    # Status: "what's playing", "now playing", "show music", bare "music".
    if (has_ctx or re.search(r"\bplaying\b", text)) and re.search(r"\b(what|show|see|playing|song|music)\b", text):
        return SkybridgeIntent(domain="music", action="status")
    return None


# "Turn it back on again" right after "Turn the light off" (live 2026-10-12: intent_miss, then the brain: "It sounds like you're ready
# to get back to chatting"). The last on/off command's TARGET rides in the panel's Skybridge context for a short window, and a bare
# pronoun command (the words are the lexicon's, ``conversation_cues.smart_home_followup``) resolves to it.
_SMART_HOME_TARGET_KEY = "smart_home_target"


def _remember_smart_home_target(intent: "SkybridgeIntent", out: dict[str, Any]) -> None:
    """Record the on/off command this result carried in ``out["skybridge_context"]`` (only a handled on/off turn that CONTROLLED
    something: a status read, a "which one?" question or a brightness change is not something "it ... back on" can mean). The
    target is what the resolver actually SELECTED (``out["controlled_entities"]`` - the room-disambiguated light, the fuzzy match),
    never the words of the command: a follow-up must switch the very device that was switched."""
    ctx = out.get("skybridge_context")
    entities = [str(e) for e in out.get("controlled_entities") or [] if e]
    if out.get("handled") and isinstance(ctx, dict) and intent.action in ("turn_on", "turn_off") and entities:
        ctx[_SMART_HOME_TARGET_KEY] = {
            "action": intent.action, "query": intent.query, "entity_ids": entities, "ts": time.time(),
        }


def _smart_home_followup_intent(raw_message: str, context: dict[str, Any] | None) -> "SkybridgeIntent | None":
    """A bare pronoun command ("turn it back on again", "switch them off", "turn it back") about the device just controlled."""
    mem = context.get(_SMART_HOME_TARGET_KEY) if isinstance(context, dict) else None
    if not isinstance(mem, dict) or mem.get("action") not in ("turn_on", "turn_off"):
        return None
    from conversation_cues import smart_home_followup, smart_home_followup_ttl_s

    if time.time() - float(mem.get("ts") or 0.0) > smart_home_followup_ttl_s():
        return None
    verdict = smart_home_followup(raw_message)
    if verdict is None:
        return None
    action = ("turn_on" if verdict == "on" else "turn_off" if verdict == "off"
              else "turn_off" if mem["action"] == "turn_on" else "turn_on")
    ids = tuple(str(e) for e in mem.get("entity_ids") or [] if e)
    if not ids:
        return None
    return SkybridgeIntent(domain="smart_home", action=action, query=str(mem.get("query") or ""),
                           entity_id=ids[0] if len(ids) == 1 else "", entity_ids=ids)


# "What is it?" right after "You have one event today." (live 2026-10-14: the first two went to the brain with no context). The
# range the last calendar READ covered rides in the panel's Skybridge context for a short window, and a short question about
# "it / them / the one event" (the words are the lexicon's, ``conversation_cues.calendar_followup``) re-runs that read. It is the
# SAME member-scoped read as the original ask: a guest's follow-up is the PIN challenge, never the events.
_CALENDAR_READ_KEY = "calendar_read"


def _remember_calendar_read(intent: "SkybridgeIntent", out: dict[str, Any]) -> None:
    """Record the range a handled calendar READ covered in ``out["skybridge_context"]`` (not a challenge, not a write)."""
    ctx = out.get("skybridge_context")
    if out.get("handled") and not out.get("auth_required") and isinstance(ctx, dict) and (
        intent.domain == "calendar" and intent.action == "show" and intent.start_date
    ):
        ctx[_CALENDAR_READ_KEY] = {
            "label": intent.range_label, "start": intent.start_date.isoformat(),
            "end": (intent.end_date or intent.start_date).isoformat(), "ts": time.time(),
        }


def _calendar_followup_intent(raw_message: str, context: dict[str, Any] | None) -> "SkybridgeIntent | None":
    """A short question about the calendar answer just given ("what is it", "what are they", "which one") -> the same read."""
    mem = context.get(_CALENDAR_READ_KEY) if isinstance(context, dict) else None
    if not isinstance(mem, dict):
        return None
    from conversation_cues import calendar_followup, calendar_followup_ttl_s

    if time.time() - float(mem.get("ts") or 0.0) > calendar_followup_ttl_s() or not calendar_followup(raw_message):
        return None
    try:
        start = date.fromisoformat(str(mem.get("start")))
        end = date.fromisoformat(str(mem.get("end") or mem.get("start")))
    except ValueError:
        return None
    return SkybridgeIntent("calendar", "show", str(mem.get("label") or "today"), start, end)


# Smart-home device anchors: a word clearly about a controllable household
# device. A control verb ("turn on") is only treated as smart_home when paired
# with one of these, so "turn on charm" / "turn off the news" never steal it.
_HOME_DEVICE_NOUN = re.compile(r"\b(lights?|lamps?|switch(?:es)?|plugs?|outlets?|fans?)\b")
_NOT_BRIGHTNESS_RE = re.compile(r"\b(?:speed|position|volume|temperature|temp|colou?r|humidity|mode)\b")


def _home_text(raw_message: str, text: str) -> str:
    """The smart-home matcher's input: ``text`` with the polite / wake / modal frame off ("can you turn the light on?" ->
    " turn the light on "). Uses the shared ``conversation_cues.strip_command_filler`` (lexicon section
    ``command_filler``) so there is ONE stripper; falls back to ``text`` unchanged.

    An explicit entity marker (``@light.zoe``, sent by a panel tap) is an exact selection, not natural language: the text goes
    through untouched so the stripper can never eat part of the id (``.zoe`` looks like a trailing wake word)."""
    if re.search(r"@[a-z_]+\.[a-z0-9_]+", text):
        return text
    try:
        from conversation_cues import strip_command_filler

        bare = strip_command_filler(re.sub(r"[.!?,;:]+$", "", raw_message.strip()))
        bare = re.sub(r"[.!?,;:]+$", "", bare.lower().strip())
        return f" {bare} " if bare else text
    except Exception:  # noqa: BLE001 - never break a turn over filler stripping
        return text


_FRAME_LEAD_WORDS = frozenset({"you", "can", "could", "would", "will", "please", "kindly", "hey", "zoe", "go", "just"})
_FRAME_TAILS = ("please", "for me", "thanks", "thank you")


def _original_parse_stands(intent: "SkybridgeIntent") -> bool:
    """Existing-claim preservation: a parse of the UNstripped text is kept unless its device query visibly carries the polite
    frame ("can you light", "lamp for me"). A scene activation or an exact entity is an explicit name - never re-parsed, so a
    scene called "Thank You" or "Time for Me" keeps its whole name."""
    if intent.action == "activate_scene" or intent.entity_id:
        return True
    q = (intent.query or "").strip().lower()
    if not q:
        return True
    return q.split()[0] not in _FRAME_LEAD_WORDS and not any(q == t or q.endswith(" " + t) for t in _FRAME_TAILS)


def _classify_home(raw_message: str, text: str) -> "SkybridgeIntent | None":
    """The smart-home parse: the original text first (so every command that already worked keeps its exact result), and the
    filler-stripped text only when the original parse failed or kept the polite frame in its device query."""
    original = _classify_smart_home(text)
    if original is not None and _original_parse_stands(original):
        return original
    stripped = _home_text(raw_message, text)
    if stripped == text:
        return original
    return _classify_smart_home(stripped) or original


def _classify_smart_home(text: str) -> "SkybridgeIntent | None":
    """Classify a smart-home command for the Home Assistant bridge. `text` is the
    lowercased message padded with surrounding spaces. Device control is
    household-shared (no identity gate), like clock and music."""
    # Onboarding — the owner's key ask: a way to GROW the home from the panel.
    # "add a device", "set up a new light", "connect a plug", "pair a sensor".
    # Placed first so the "device" keyword here can't be swallowed by the generic
    # overview rule below. (Smart-home runs AFTER list parsing upstream, so
    # "add milk to the list" never reaches here.)
    if re.search(
        r"\b(?:add|set\s*up|connect|pair|onboard|install)\s+"
        r"(?:(?:a|an|another|new|my|some|smart)\s+)*"
        r"(?:device|light|lamp|bulb|plug|switch|outlet|speaker|sensor|gadget|thing)s?\b",
        text,
    ):
        return SkybridgeIntent(domain="smart_home", action="add_device")
    # Tile taps carry the device's EXACT HA entity id as an "@domain.entity" marker
    # so a tap controls exactly its device (never a fuzzy name match). Voice never
    # includes it. This is the authoritative, unambiguous control path.
    ent_match = re.search(r"@([a-z_]+\.[a-z0-9_]+)", text)
    if ent_match:
        entity_id = ent_match.group(1)
        rest = (text[: ent_match.start()] + " " + text[ent_match.end():])
        if entity_id.startswith(("scene.", "script.")):
            return SkybridgeIntent(domain="smart_home", action="activate_scene", entity_id=entity_id)
        pct_match = re.search(r"to\s+(\d{1,3})\s*(?:%|percent)", rest)
        if pct_match:
            pct = max(0, min(100, int(pct_match.group(1))))
            return SkybridgeIntent(domain="smart_home", action="set_brightness", entity_id=entity_id, duration_seconds=pct)
        # Read the LEADING control verb, not any "off" that might sit in the device
        # name (e.g. "turn on Off Switch @…" is a turn_on).
        verb = re.search(r"\b(?:turn|switch|flip|put)\s+(on|off)\b", rest)
        off = (verb.group(1) == "off") if verb else bool(re.search(r"\boff\b", rest))
        return SkybridgeIntent(domain="smart_home", action="turn_off" if off else "turn_on", entity_id=entity_id)
    # Scene / routine activation: "activate the movie time scene", "run good night".
    m = re.search(r"\b(?:activate|run|start|trigger|enable|set)\s+(?:the\s+)?(.+?)\s+scene\b", text)
    if m:
        return SkybridgeIntent(domain="smart_home", action="activate_scene", query=m.group(1).strip())
    m = re.search(r"\bscene\s+(?:called\s+)?(.+?)\s*$", text)
    if m:
        return SkybridgeIntent(domain="smart_home", action="activate_scene", query=m.group(1).strip())
    has_device = bool(_HOME_DEVICE_NOUN.search(text))
    # Brightness: "dim the kitchen lamp to 40%", "set the lights to 80 percent".
    # (No trailing \b after % — a "%" before a space is not a word boundary.)
    m = re.search(r"\b(?:dim|set|turn|put)\s+(?:the\s+)?(.+?)\s+to\s+(\d{1,3})\s*(?:%|percent)", text)
    # A percentage of something that is NOT brightness ("fan speed to 50%", "blind position to 30%") is not a dim.
    if m and has_device and not _NOT_BRIGHTNESS_RE.search(m.group(1)):
        pct = max(0, min(100, int(m.group(2))))
        return SkybridgeIntent(domain="smart_home", action="set_brightness", query=_clean_device(m.group(1)), duration_seconds=pct)
    if has_device and re.search(r"\bdim(?:mer)?\b", text):
        return SkybridgeIntent(domain="smart_home", action="set_brightness", query=_clean_device(text), duration_seconds=25)
    if has_device and re.search(r"\bbrighten\b", text):
        return SkybridgeIntent(domain="smart_home", action="set_brightness", query=_clean_device(text), duration_seconds=100)
    # Explicit command: "turn on the lamp", "switch off the fan".
    m = re.search(r"\b(?:turn|switch|flip|put)\s+(on|off)\s+(?:the\s+)?(.*?)\s*$", text)
    if m and has_device:
        action = "turn_on" if m.group(1) == "on" else "turn_off"
        return SkybridgeIntent(domain="smart_home", action=action, query=_clean_device(m.group(2)))
    # State query BEFORE the bare "X on/off" toggle so "is the light on" / "what
    # lights are on" read as a question, not a command.
    if has_device and re.search(r"\b(?:is|are|what|which|how)\b.*\b(?:on|off|status)\b", text):
        return SkybridgeIntent(domain="smart_home", action="status", query=_clean_device(text))
    # Bare toggle: "lights on", "fan off".
    m = re.search(r"\b(.+?)\s+(on|off)\s*$", text)
    if m and has_device:
        action = "turn_on" if m.group(2) == "on" else "turn_off"
        return SkybridgeIntent(domain="smart_home", action=action, query=_clean_device(m.group(1)))
    # Overview: "smart home", "show me the devices", "control the lights".
    if re.search(r"\bsmart\s*home\b|\bdevices?\b", text):
        return SkybridgeIntent(domain="smart_home", action="status")
    if has_device and re.search(r"\b(show|see|control|manage|list of)\b", text):
        return SkybridgeIntent(domain="smart_home", action="status", query=_clean_device(text))
    return None


def _clean_device(value: str) -> str:
    """Strip control VERBS/grammar so the device words survive for matching. The
    device-class noun (light/lamp/switch/…) and any "all"/room words are KEPT —
    the resolver needs them to tell a singular target from an all-class sweep."""
    words = re.split(r"\s+", (value or "").strip().lower())
    drop = {"turn", "flip", "put", "set", "dim", "dimmer", "brighten",
            "on", "off", "the", "a", "an", "my", "our", "to", "please",
            "show", "me", "see", "control", "manage", "is", "are", "what",
            "which", "how", "status"}
    kept = [w for w in words if w and w not in drop]
    return " ".join(kept).strip()


async def _resolve_with_db(intent: SkybridgeIntent, user_id: str, db: Any, *, context: dict[str, Any] | None = None) -> dict[str, Any]:
    if intent.domain == "clock":
        return _resolve_clock(intent)
    if intent.domain == "calendar":
        if intent.action == "create_event":
            return await _resolve_calendar_create(intent, user_id, db)
        if intent.action == "update_time":
            return await _resolve_calendar_update_time(intent, user_id, db, context)
        if intent.action == "edit_event":
            return await _resolve_calendar_edit_event(intent, user_id, db, context)
        if intent.action == "delete_event":
            return await _resolve_calendar_delete_event(intent, user_id, db, context)
        return await _resolve_calendar(intent, user_id, db)
    if intent.domain == "weather":
        return await _resolve_weather(intent, user_id, db)
    if intent.domain == "lists":
        if intent.action == "add_item":
            return await _resolve_list_add_item(intent, user_id, db, context)
        if intent.action == "remove_item":
            return await _resolve_list_remove_item(intent, user_id, db, context)
        if intent.action == "complete_item":
            return await _resolve_list_complete_item(intent, user_id, db, context)
        if intent.action == "edit_item":
            return await _resolve_list_edit_item(intent, user_id, db, context)
        if intent.action == "create_list":
            return await _resolve_list_create(intent, user_id, db)
        return await _resolve_lists(intent, user_id, db)
    if intent.domain == "people":
        if intent.action == "identity":
            return await _resolve_self_identity(user_id, db, kind=intent.query)
        if intent.action == "remember_fact":
            return await _resolve_people_remember_fact(intent, user_id, db)
        if intent.action == "pending_offers":
            return await _resolve_people_pending_offers(user_id, db)
        if intent.action == "create":
            return await _resolve_people_create(intent, user_id, db)
        return await _resolve_people(intent, user_id, db)
    return {"handled": False, "intent": None, "spoken_summary": "", "cards": []}


def _resolve_clock(intent: SkybridgeIntent) -> dict[str, Any]:
    now, timezone_name = _clock_now()
    hour_str = str(now.hour % 12 or 12)
    spoken = f"It is {hour_str}:{now.strftime('%M %p')}."
    return {
        "handled": True,
        "intent": {"domain": "clock", "action": intent.action, "timezone": timezone_name},
        "spoken_summary": spoken,
        "cards": [{
            "component": "status",
            "props": {
                "source": "clock_show",
                "title": "Clock",
                "summary": spoken,
                "timezone": timezone_name,
                "iso": now.isoformat(),
                "hour": hour_str,
                "minute": now.strftime("%M"),
                "meridiem": now.strftime("%p"),
                "weekday": now.strftime("%A"),
                "date_label": now.strftime("%d %B"),
            },
        }],
    }


async def _resolve_voice_settings(intent: SkybridgeIntent) -> dict[str, Any]:
    """The "Zoe's voice" settings card: pick from the voices bin catalogue.

    Household-shared like clock/music — no identity gate (the kiosk panel is the
    primary surface). SET persists via voice_settings and the confirmation is
    naturally spoken in the NEW voice (the TTS path re-reads the preference).
    Preview is tap-only: the card's Preview buttons call /api/voice/preview from
    the panel (the typed/tap resolver path carries no audio).
    """
    import voice_settings

    spoken = "Here are the voices I can speak with."
    if intent.action == "set":
        matched = voice_settings.match_voice(intent.query)
        if matched is None:
            spoken = f"I don't have a voice called {intent.query}. Here's what I can use."
        else:
            try:
                await voice_settings.set_tts_voice(matched)
                spoken = "Done. This is my voice from now on."
            except ValueError:
                spoken = f"I don't have a voice called {intent.query}. Here's what I can use."
            except Exception:  # DB down etc. — honest failure, never a false "done"
                logger.warning("voice preference write failed", exc_info=True)
                spoken = "Sorry, I couldn't save that voice right now."

    current = await voice_settings.get_tts_voice()
    names = voice_settings.list_kokoro_voices()
    summary = (
        "Tap a voice to make it mine, or preview it first."
        if names
        else "The voice catalogue isn't available right now."
    )
    card = {
        "component": "voice_settings",
        "props": {
            "source": "voice_settings",
            "title": "Zoe's voice",
            "summary": summary,
            "current": current,
            "default": voice_settings.env_default_voice(),
            "sample_text": voice_settings.PREVIEW_TEXT,
            "voices": [
                {
                    "id": name,
                    "label": voice_settings.voice_label(name),
                    "zoe": name.startswith("zoe_"),
                }
                for name in names
            ],
        },
    }
    return {
        "handled": True,
        "intent": {"domain": "voice", "action": intent.action},
        "spoken_summary": spoken,
        "cards": [card],
    }


async def _maybe_commit(db: Any) -> None:
    commit = getattr(db, "commit", None)
    if callable(commit):
        await commit()


def _affected_rows(result: Any) -> int | None:
    if result is None:
        return None
    if isinstance(result, int):
        return result
    text = str(result)
    insert_match = re.search(r"\bINSERT\s+\d+\s+(\d+)\b", text)
    if insert_match:
        return int(insert_match.group(1))
    match = re.search(r"\b(?:UPDATE|DELETE)\s+(\d+)\b", text)
    return int(match.group(1)) if match else None


def _status_card(title: str, body: str, *, status: str = "Needs context") -> dict[str, Any]:
    return {
        "component": "status",
        "props": {
            "title": title,
            "body": body,
            "status": status,
            "tone": "warn",
            "wide": True,
        },
    }


def _person_confirm_card(name: str, relationship: str | None, *, suggestion_id: str | None = None) -> dict[str, Any]:
    """A tappable "Add {name} as your {relationship}?" confirm card for the panel.

    The Add button re-issues a natural-language people_create command (the panel's
    only wired dispatch is `query` → /api/skybridge/resolve), so the contact is
    ALWAYS created server-side under the authenticated panel user — never trusted
    from the client. "Not now" is a client-only `dismiss` (clears the card, no
    round-trip). Shape mirrors `_status_card`: a `{component, props}` card the
    Skybridge renderer draws via `renderPersonConfirm`.
    """
    name = str(name or "").strip()
    rel = str(relationship or "").strip()
    add_query = f"add {name} as my {rel}" if rel else f"add {name} to my contacts"
    props: dict[str, Any] = {
        "title": f"Add {name} as your {rel}?" if rel else f"Add {name}?",
        "name": name,
        "relationship": rel,
        "icon": (name[:1] or "+").upper(),
        "status": "Suggested",
        "actions": [
            {"type": "query", "label": f"Add {name}", "query": add_query, "kind": "primary"},
            {"type": "dismiss", "label": "Not now", "kind": "normal"},
        ],
    }
    if suggestion_id:
        props["suggestion_id"] = str(suggestion_id)
    return {"component": "person_confirm", "props": props}


async def _resolve_people_pending_offers(user_id: str, db: Any) -> dict[str, Any]:
    """Surface pending person_create offers as tappable person_confirm cards.

    Reads the user-scoped, session-agnostic pending list (so backfill offers under
    the static 'backfill' session surface too) and turns each into an Add card.
    """
    from pending_suggestions import list_pending_contacts

    intent_dict = {"domain": "people", "action": "pending_offers"}
    offers = await list_pending_contacts(user_id, limit=6)
    cards: list[dict[str, Any]] = []
    for offer in offers:
        name = str(offer.get("name") or "").strip()
        if not name:
            continue
        cards.append(_person_confirm_card(name, offer.get("relationship"), suggestion_id=offer.get("id")))
    if not cards:
        return {
            "handled": True,
            "intent": intent_dict,
            "spoken_summary": "You have no new contacts to add right now.",
            "cards": [_status_card(
                "No contact suggestions",
                "When someone new comes up in conversation, I'll offer to add them here.",
                status="People",
            )],
        }
    spoken = f"You have {len(cards)} " + ("person" if len(cards) == 1 else "people") + " I can add to your contacts."
    return {
        "handled": True,
        "intent": intent_dict,
        "spoken_summary": spoken,
        "cards": cards,
    }


async def _resolve_people_create(intent: SkybridgeIntent, user_id: str, db: Any) -> dict[str, Any]:
    """Create a contact from "add {name} as my {relationship}" — the Add button's
    command, handled deterministically here rather than via the brain fallback.

    Reuses the shared `_execute_people_create_direct` path (private-by-default,
    circle='circle', deduped), so a panel tap and a spoken command create the same
    full, editable contact.
    """
    from intent_router import Intent, _execute_people_create_direct

    name = (intent.person_name or "").strip()
    rel = (intent.relationship or "").strip()
    intent_dict = {"domain": "people", "action": "create", "query": name}
    if not name:
        return {
            "handled": True,
            "intent": intent_dict,
            "spoken_summary": "I didn't catch who to add.",
            "cards": [_status_card("Who should I add?", "Say e.g. “add Daniel as my brother”.", status="People")],
        }
    result = await _execute_people_create_direct(
        Intent("people_create", {"name": name, "relationship": rel or None}), user_id
    )
    if not result:
        return {
            "handled": True,
            "intent": intent_dict,
            "spoken_summary": f"I couldn't add {name} just now.",
            "cards": [_status_card("Couldn't add contact", f"Something went wrong adding {name}. Try again in a moment.", status="People")],
        }
    # Clear any pending offer(s) for this person so the Add card doesn't re-surface
    # for a contact that now exists (the NL query can't carry the suggestion id).
    try:
        from pending_suggestions import resolve_person_offers_by_name
        await resolve_person_offers_by_name(user_id, name)
    except Exception as exc:  # non-fatal — the contact was still created
        logger.debug("skybridge people/create: offer-resolve failed: %s", exc)
    spoken = f"Added {name}" + (f" as your {rel}." if rel else " to your contacts.")
    card = {
        "component": "status",
        "props": {
            "title": f"Added {name}",
            "body": spoken,
            "status": "Contacts",
            "tone": "hero",
            "wide": True,
        },
    }
    return {"handled": True, "intent": intent_dict, "spoken_summary": spoken, "cards": [card]}


def _intent_dict(intent: SkybridgeIntent) -> dict[str, Any]:
    return {
        "domain": intent.domain,
        "action": intent.action,
        "list_type": intent.list_type,
        "list_name": intent.list_name,
        "target_time": intent.target_time,
    }


def _auth_required_result(intent: SkybridgeIntent) -> dict[str, Any]:
    domain_labels = {
        "calendar": "calendar",
        "lists": "lists",
        "people": "people",
    }
    domain = domain_labels.get(intent.domain, "this data")
    title = f"Sign in to view {domain}"
    if intent.action in {"create_event", "update_time", "add_item", "complete_item", "remember_fact"}:
        title = f"Sign in to change {domain}"
    body = "Zoe needs to know who is speaking before showing or changing personal data."
    return {
        "handled": True,
        "auth_required": True,
        "intent": _intent_dict(intent),
        "spoken_summary": "Please authenticate on the touch panel to continue.",
        "cards": [{
            "component": "auth_challenge",
            "props": {
                "title": title,
                "body": body,
                "domain": domain,
                "action": intent.action,
            },
        }],
        "actions": [{"type": "auth_required", "domain": intent.domain, "action": intent.action}],
    }


async def _resolve_calendar_create(intent: SkybridgeIntent, user_id: str, db: Any) -> dict[str, Any]:
    if user_id in {"guest", "voice-guest"}:
        return {
            "handled": True,
            "intent": _intent_dict(intent),
            "spoken_summary": "Calendar changes are not available for guest sessions.",
            "cards": [_status_card("Sign in to change calendar", "Zoe needs to know who is speaking before creating calendar events.")],
            "actions": [],
        }
    start = intent.start_date or _today()
    event_id = str(uuid.uuid4())
    await db.execute(
        """
        INSERT INTO events (
            id, user_id, title, start_date, start_time, end_date, end_time,
            duration, category, location, all_day, recurring, metadata,
            visibility, deleted
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, 0)
        """,
        event_id,
        user_id,
        intent.title,
        start.isoformat(),
        None if intent.all_day else intent.target_time,
        start.isoformat(),
        None,
        None,
        "general",
        None,
        1 if intent.all_day else 0,
        None,
        None,
        "family",
    )
    await _maybe_commit(db)
    write_receipts.record(user_id, "calendar", event_id, "skybridge:create_event")
    refreshed = await _resolve_calendar(SkybridgeIntent("calendar", "show", start.isoformat(), start, start), user_id, db)
    refreshed["intent"] = {"domain": "calendar", "action": "create_event", "event_id": event_id}
    # The read-back names the DAY and the TIME it was booked for, so a mis-heard day is caught by the listener at once.
    when = _spoken_when(start)
    refreshed["spoken_summary"] = (
        f"Added {intent.title} to your calendar {when}." if intent.all_day
        else f"Added {intent.title} {when} at {_spoken_clock(intent.target_time)}."
    )
    refreshed["actions"] = [{"type": "created", "domain": "calendar", "id": event_id}]
    return refreshed


def _spoken_when(day: date) -> str:
    """"today" / "tomorrow" / "on Friday" (within a week) / "on Friday 16 October" for a booked day."""
    today = _today()
    if day == today:
        return "today"
    if day == today + timedelta(days=1):
        return "tomorrow"
    if 0 < (day - today).days < 7:
        return f"on {day.strftime('%A')}"
    return f"on {day.strftime('%A')} {day.day} {day.strftime('%B')}"


def _spoken_clock(hhmm: str) -> str:
    """"12:00" -> "12 PM", "09:30" -> "9:30 AM" (how it is said, not how it is stored)."""
    try:
        hour, minute = int(hhmm[:2]), int(hhmm[3:5])
    except (ValueError, TypeError):
        return hhmm or ""
    suffix = "PM" if hour >= 12 else "AM"
    hour12 = hour % 12 or 12
    return f"{hour12}{f':{minute:02d}' if minute else ''} {suffix}"


def _score_event_for_target(event: dict[str, Any], target: str) -> int:
    score = 0
    # Explicit ISO date match — the disambiguator for ALL-DAY events, which carry no
    # time. It is parsed and then STRIPPED before the clock/token passes so its
    # digits can't be misread as a time (e.g. "2026-12-09" → a spurious 12:00) or
    # leak in as title tokens.
    remainder = target
    date_match = re.search(r"\b\d{4}-\d{2}-\d{2}\b", target)
    if date_match:
        if str(event.get("start_date") or "")[:10] == date_match.group(0):
            score += 4
        remainder = target[:date_match.start()] + " " + target[date_match.end():]
    # "all day" shape — lets a tapped all-day row win a same-DATE tie against a
    # same-title timed row. Stripped so it can't leak in as title tokens.
    if re.search(r"\ball[ -]?day\b", remainder, re.IGNORECASE):
        remainder = re.sub(r"\ball[ -]?day\b", " ", remainder, flags=re.IGNORECASE)
        if event.get("all_day"):
            score += 2
    target_time = _parse_time(remainder)
    if target_time and str(event.get("start_time") or "")[:5] == target_time:
        score += 4
    # Query-syntax words from the tap templates ("edit/delete X AT <time> ON <date>
    # FROM/ON MY CALENDAR") — not event words. They must be filtered because the
    # token test below is a SUBSTRING match, so a stray "at" would match "Bath",
    # "Saturday", etc. and give a spurious point.
    target_tokens = {token for token in re.split(r"\W+", remainder.lower()) if token and token not in {"my", "the", "at", "on", "from", "calendar", "appointment", "event"}}
    haystack = " ".join(str(event.get(key) or "") for key in ("title", "category", "location")).lower()
    if not target_tokens:
        return score or 1
    return score + sum(1 for token in target_tokens if token in haystack)


async def _resolve_calendar_update_time(intent: SkybridgeIntent, user_id: str, db: Any, context: dict[str, Any] | None) -> dict[str, Any]:
    if user_id in {"guest", "voice-guest"}:
        return {
            "handled": True,
            "intent": _intent_dict(intent),
            "spoken_summary": "Calendar changes are not available for guest sessions.",
            "cards": [_status_card("Sign in to change calendar", "Zoe needs to know who is speaking before moving appointments.")],
            "actions": [],
        }
    candidates = _context_events(context)
    if not candidates:
        start = _context_calendar_date(context)
        rows = await db.fetch(
            """
            SELECT *
            FROM events
            WHERE (visibility = 'family' OR user_id = $1)
              AND deleted = 0
              AND start_date = $2
            ORDER BY start_time
            """,
            user_id,
            start.isoformat(),
        )
        candidates = [row_to_event(row) for row in rows]
    if not candidates:
        return {
            "handled": True,
            "intent": _intent_dict(intent),
            "spoken_summary": "I could not find a calendar event to move.",
            "cards": [_status_card("Which appointment?", "I could not find a visible calendar event to move. Show the calendar first or name the event.")],
            "actions": [],
        }
    scored = sorted(((event, _score_event_for_target(event, intent.target_text)) for event in candidates), key=lambda pair: pair[1], reverse=True)
    top_score = scored[0][1]
    matches = [event for event, score in scored if score == top_score and score > 0]
    if len(matches) != 1:
        return {
            "handled": True,
            "intent": _intent_dict(intent),
            "spoken_summary": "I need to know which event to move.",
            "cards": [_status_card("Which event should move?", "There is more than one possible calendar event. Say the event title and the new time.")],
            "actions": [],
        }
    event = matches[0]
    event_id = event.get("id")
    update_result = await db.execute(
        # Giving an all-day event a time makes it a timed event — clear all_day so
        # the row can't be both (renderers/spoken summaries would disagree).
        "UPDATE events SET start_time = $1, all_day = 0, updated_at = NOW() WHERE id = $2 AND user_id = $3 AND deleted = 0",
        intent.target_time,
        event_id,
        user_id,
    )
    if _affected_rows(update_result) == 0:
        return {
            "handled": True,
            "intent": _intent_dict(intent),
            "spoken_summary": "I can see that event, but I cannot move it from this account.",
            "cards": [_status_card("I could not move that event", "The event is visible to the family, but this account does not own it. Ask the owner to move it or create a new event.")],
            "actions": [],
        }
    await _maybe_commit(db)
    start = date.fromisoformat(str(event.get("start_date") or _context_calendar_date(context).isoformat())[:10])
    refreshed = await _resolve_calendar(SkybridgeIntent("calendar", "show", start.isoformat(), start, start), user_id, db)
    refreshed["intent"] = {"domain": "calendar", "action": "update_time", "event_id": event_id}
    refreshed["spoken_summary"] = f"Moved {event.get('title') or 'the event'} to {intent.target_time}."
    refreshed["actions"] = [{"type": "updated", "domain": "calendar", "id": event_id}]
    return refreshed


async def _resolve_calendar_edit_event(intent: SkybridgeIntent, user_id: str, db: Any, context: dict[str, Any] | None) -> dict[str, Any]:
    """Open the existing calendar event editor card for the tapped/named event (read, not a mutation)."""
    if user_id in {"guest", "voice-guest"}:
        return {
            "handled": True,
            "intent": _intent_dict(intent),
            "spoken_summary": "Calendar changes are not available for guest sessions.",
            "cards": [_status_card("Sign in to change calendar", "Zoe needs to know who is speaking before editing calendar events.")],
            "actions": [],
        }
    candidates = _context_events(context)
    start = _context_calendar_date(context)
    if not candidates:
        rows = await db.fetch(
            """
            SELECT *
            FROM events
            WHERE (visibility = 'family' OR user_id = $1)
              AND deleted = 0
              AND start_date = $2
            ORDER BY start_time
            """,
            user_id,
            start.isoformat(),
        )
        candidates = [row_to_event(row) for row in rows]
    if not candidates:
        return {
            "handled": True,
            "intent": _intent_dict(intent),
            "spoken_summary": "I could not find a calendar event to edit.",
            "cards": [_status_card("Which appointment?", "I could not find a visible calendar event to edit. Show the calendar first or name the event.")],
            "actions": [],
        }
    scored = sorted(((event, _score_event_for_target(event, intent.target_text)) for event in candidates), key=lambda pair: pair[1], reverse=True)
    top_score = scored[0][1]
    matches = [event for event, score in scored if score == top_score and score > 0]
    if len(matches) != 1:
        return {
            "handled": True,
            "intent": _intent_dict(intent),
            "spoken_summary": "I need to know which event to edit.",
            "cards": [_status_card("Which event should I edit?", "There is more than one possible calendar event. Say the event title to edit it.")],
            "actions": [],
        }
    event = matches[0]
    title = str(event.get("title") or "Event")
    event_time = str(event.get("start_time") or "")[:5]
    event_date = str(event.get("start_date") or start.isoformat())[:10]
    actions = [
        {"type": "query", "label": "Move to 9am", "query": f"move {title} to 9am"},
        {"type": "query", "label": "Delete event", "query": f"delete {title} from my calendar", "kind": "warn"},
        {"type": "query", "label": "Back to calendar", "query": "show my calendar"},
    ]
    card = card_service.build_calendar_event_editor_card(
        {
            "title": title,
            "date": event_date,
            "time": event_time,
            "location": str(event.get("location") or ""),
            "event_id": str(event.get("id") or ""),
            "actions": actions,
        }
    )
    return {
        "handled": True,
        "intent": {"domain": "calendar", "action": "edit_event", "event_id": event.get("id")},
        "spoken_summary": f"Editing {title}. You can move it or delete it.",
        "cards": [card_service.convert_emit(card, target_major=1)],
        "actions": [],
    }


async def _resolve_calendar_delete_event(intent: SkybridgeIntent, user_id: str, db: Any, context: dict[str, Any] | None) -> dict[str, Any]:
    if user_id in {"guest", "voice-guest"}:
        return {
            "handled": True,
            "intent": _intent_dict(intent),
            "spoken_summary": "Calendar changes are not available for guest sessions.",
            "cards": [_status_card("Sign in to change calendar", "Zoe needs to know who is speaking before deleting calendar events.")],
            "actions": [],
        }
    candidates = _context_events(context)
    start = _context_calendar_date(context)
    if not candidates:
        rows = await db.fetch(
            """
            SELECT *
            FROM events
            WHERE (visibility = 'family' OR user_id = $1)
              AND deleted = 0
              AND start_date = $2
            ORDER BY start_time
            """,
            user_id,
            start.isoformat(),
        )
        candidates = [row_to_event(row) for row in rows]
    if not candidates:
        return {
            "handled": True,
            "intent": _intent_dict(intent),
            "spoken_summary": "I could not find a calendar event to delete.",
            "cards": [_status_card("Which appointment?", "I could not find a visible calendar event to delete. Show the calendar first or name the event.")],
            "actions": [],
        }
    scored = sorted(((event, _score_event_for_target(event, intent.target_text)) for event in candidates), key=lambda pair: pair[1], reverse=True)
    top_score = scored[0][1]
    matches = [event for event, score in scored if score == top_score and score > 0]
    if len(matches) != 1:
        return {
            "handled": True,
            "intent": _intent_dict(intent),
            "spoken_summary": "I need to know which event to delete.",
            "cards": [_status_card("Which event should I delete?", "There is more than one possible calendar event. Say the event title to delete it.")],
            "actions": [],
        }
    event = matches[0]
    event_id = event.get("id")
    delete_result = await db.execute(
        "UPDATE events SET deleted = 1, updated_at = NOW() WHERE id = $1 AND user_id = $2 AND deleted = 0",
        event_id,
        user_id,
    )
    if _affected_rows(delete_result) == 0:
        return {
            "handled": True,
            "intent": _intent_dict(intent),
            "spoken_summary": "I can see that event, but I cannot delete it from this account.",
            "cards": [_status_card("I could not delete that event", "The event is visible to the family, but this account does not own it. Ask the owner to delete it.")],
            "actions": [],
        }
    await _maybe_commit(db)
    start = date.fromisoformat(str(event.get("start_date") or start.isoformat())[:10])
    refreshed = await _resolve_calendar(SkybridgeIntent("calendar", "show", start.isoformat(), start, start), user_id, db)
    refreshed["intent"] = {"domain": "calendar", "action": "delete_event", "event_id": event_id}
    refreshed["spoken_summary"] = f"Deleted {event.get('title') or 'the event'}."
    refreshed["actions"] = [{"type": "deleted", "domain": "calendar", "id": event_id}]
    return refreshed


async def _resolve_calendar(intent: SkybridgeIntent, user_id: str, db: Any) -> dict[str, Any]:
    start = intent.start_date or today_for_zoe_tz()
    end = intent.end_date or start
    events = []
    if not _is_guest_user(user_id):
        rows = await db.fetch(
            """
            SELECT *
            FROM events
            WHERE (visibility = 'family' OR user_id = $1)
              AND deleted = 0
              AND start_date >= $2
              AND start_date <= $3
            ORDER BY start_date, start_time
            """,
            user_id,
            start.isoformat(),
            end.isoformat(),
        )
        events = [row_to_event(row) for row in rows]
    qualifier = intent.range_label or start.isoformat()
    event_word = "event" if len(events) == 1 else "events"
    count_line = f"You have {len(events)} {event_word} {qualifier}."
    # The identified member HEARS the events (the asker is the member: this resolver only reads rows for a real user_id, and
    # the voice lane passes one only for a fresh PIN sign-in, an open voice session or the member's own session); the card
    # carries all of them. No identity: no rows, no count - only how to unlock (docs/knowledge/voice-verified-session.md).
    spoken = count_line
    try:
        import voice_cards

        if _is_guest_user(user_id):
            spoken = voice_cards.spoken_private() or "That's private. Unlock with your PIN."
        else:
            spoken = voice_cards.spoken_events(
                events, qualifier, start_date=start.isoformat(), end_date=end.isoformat()) or count_line
    except Exception:  # noqa: BLE001 - the count is the floor; speech never fails over a lexicon
        logger.warning("calendar read-back fell back to the count", exc_info=True)
    card = card_service.convert_emit(
        card_service.build_calendar_timeline_card(
            {
                "qualifier": qualifier,
                "date": start.isoformat(),
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "events": events,
                "summary": count_line,
            }
        ),
        target_major=1,
    )
    return {
        "handled": True,
        "intent": {"domain": "calendar", "action": intent.action, "range": qualifier},
        "spoken_summary": spoken,
        "cards": [card],
    }


async def _resolve_weather(intent: SkybridgeIntent, user_id: str, db: Any) -> dict[str, Any]:
    row = await db.fetchrow("SELECT * FROM weather_preferences WHERE user_id = $1", user_id)
    prefs = _row_to_prefs(row)
    fallback = await _get_system_default_location(db)
    lat, lon, city, country = _resolve_location(prefs, fallback=fallback)
    # Voice replies must feel instant: _get_current/_get_forecast short-circuit on
    # a fresh keyed-cache hit for THESE coords (kept warm by the panel's periodic
    # /weather/current + /forecast polls), so the warm path is a dict lookup and
    # only a cold location pays the ~1s live API. The cache is keyed by (kind,
    # lat, lon) — another user's/city's refresh can never feed this one, so the
    # old cached-city mismatch guard is structurally unnecessary.
    current = await _get_current(lat, lon, city, country)
    current = {k: v for k, v in current.items() if not str(k).startswith("_")}
    forecast = await _get_forecast(lat, lon)

    ask = WeatherAsk(intent.query or "current", intent.range_label,
                     wide=intent.action == "forecast" and intent.range_label != "tomorrow")
    slots = {"current": current, "forecast": forecast, "location": {"city": city, "country": country}}
    # Forecast card for tomorrow-and-later; current card for current and today's outlook
    # (it carries the forecast block too, so the panel shows the day's range).
    if intent.action == "forecast":
        card = card_service.build_weather_forecast_card(slots)
    else:
        card = card_service.build_weather_current_card(slots)
    # Whole degrees, the asked-for fact first (weather_speech) — keeps the plain
    # current reply inside voice_stitch's stitchable shape.
    spoken = speak_weather(ask, current, forecast, city)
    return {
        "handled": True,
        "intent": {"domain": "weather", "action": intent.action},
        "spoken_summary": spoken,
        "cards": [card_service.convert_emit(card, target_major=1)],
    }


async def _resolve_lists(intent: SkybridgeIntent, user_id: str, db: Any) -> dict[str, Any]:
    if user_id in {"guest", "voice-guest"}:
        card = card_service.build_shopping_list_card(
            {
                "list_name": "Lists",
                "list_type": intent.list_type or "all",
                "lists": [],
                "items": [],
                "summary": "No list data is available for guest sessions.",
            }
        )
        return {
            "handled": True,
            "intent": {"domain": "lists", "action": intent.action, "list_type": intent.list_type},
            "spoken_summary": "No list data is available for guest sessions.",
            "cards": [card_service.convert_emit(card, target_major=1)],
        }

    await _ensure_default_lists(user_id, db)
    lists = await _fetch_list_catalog(user_id, db)
    list_ids = [list_row["id"] for list_row in lists]
    items_by_list: dict[Any, list[dict[str, Any]]] = {list_id: [] for list_id in list_ids}
    if list_ids:
        item_rows = await db.fetch(
            """
            SELECT id, list_id, text, completed, priority, category, quantity, sort_order,
                   parent_id, assigned_to, created_at, updated_at
            FROM list_items
            WHERE list_id = ANY($1) AND deleted = 0
            ORDER BY list_id, completed ASC, sort_order ASC, created_at ASC
            """,
            list_ids,
        )
        for item_row in item_rows:
            item = dict(item_row)
            bucket = items_by_list.setdefault(item.get("list_id"), [])
            bucket.append(item)

    for list_row in lists:
        items = items_by_list.get(list_row["id"], [])
        list_row["items"] = items[:24]
        list_row["item_count"] = len(items)
        list_row["open_count"] = sum(1 for item in items if not item.get("completed"))
        list_row["completed_count"] = len(items) - list_row["open_count"]

    list_type = intent.list_type
    selected = None if intent.action == "overview" else _select_list(lists, list_type, intent.list_name)
    contains_answer = ""
    if intent.action == "contains":
        contains_answer, found_in = _list_contains_answer(intent, lists, items_by_list)
        selected = found_in or selected
    items = selected.get("items", []) if selected else []
    list_name = selected.get("name") if selected else (list_type.title() if list_type else "Lists")
    summary_count = len(items) if selected else sum(item.get("item_count", 0) for item in lists)
    open_count = selected.get("open_count", 0) if selected else sum(item.get("open_count", 0) for item in lists)
    completed_count = selected.get("completed_count", 0) if selected else sum(item.get("completed_count", 0) for item in lists)
    if contains_answer:
        spoken = contains_answer
    elif selected and items:
        # Readback: enumerate the items so the voice half names them, not just a count.
        spoken = _enumerate_items_for_speech(list_name, items)
    else:
        spoken = f"{list_name} has {summary_count} item{'s' if summary_count != 1 else ''}."
    card = card_service.build_shopping_list_card(
        {
            "list_id": selected.get("id") if selected else "lists-overview",
            "list_name": list_name,
            "list_type": selected.get("list_type") if selected else (list_type or "all"),
            "lists": lists,
            "items": items,
            "item_count": summary_count,
            "open_count": open_count,
            "completed_count": completed_count,
            "summary": spoken,
            "actions": _list_card_actions(lists, selected),
        }
    )
    return {
        "handled": True,
        "intent": {"domain": "lists", "action": intent.action, "list_type": list_type},
        "spoken_summary": spoken,
        "cards": [card_service.convert_emit(card, target_major=1)],
    }


def _list_contains_answer(
    intent: SkybridgeIntent, lists: list[dict[str, Any]], items_by_list: dict[Any, list[dict[str, Any]]]
) -> tuple[str, dict[str, Any] | None]:
    """"Is milk on the list?" answered YES/NO first, naming the list (and the item as stored), never a list read-out.
    A named or typed list is the only one searched; a plain "the list" searches every list the user can see."""
    wanted = intent.item_text
    if intent.list_name:
        # A NAMED list is that list or none (``_select_list`` would fall back to the only list).
        list_key = intent.list_name.strip().lower()
        scope = [lst for lst in lists if str(lst.get("name") or "").strip().lower() in (list_key, f"{list_key} list")]
        if not scope:
            return f"I couldn't find a list called {intent.list_name}.", None
    elif intent.list_type:
        scope = [lst for lst in lists if lst.get("list_type") == intent.list_type][:1]
        if not scope:
            return "I couldn't find that list.", None
    else:
        scope = lists
    for lst in scope:
        hits = _match_list_items_by_text(items_by_list.get(lst["id"], []), wanted)
        if not hits:
            continue
        open_hits = [h for h in hits if not h.get("completed")]
        name = str(lst.get("name") or "")
        if open_hits:
            return f"Yes, {open_hits[0].get('text') or wanted} is on your {name} list.", lst
        text = str(hits[0].get("text") or wanted)
        return f"{text[:1].upper()}{text[1:]} is on your {name} list, but it's already ticked off.", lst
    if len(scope) == 1:
        return f"No, {wanted} isn't on your {scope[0].get('name')} list.", scope[0]
    return f"No, {wanted} isn't on any of your lists.", None


async def _ensure_default_lists(user_id: str, db: Any) -> None:
    if user_id == panel_principal.HOUSEHOLD_VIEWER:
        return  # a panel guest owns nothing and seeds nothing
    rows = await db.fetch(
        """
        SELECT list_type, name, user_id, visibility
        FROM lists
        WHERE deleted = 0 AND (user_id = $1 OR visibility = 'family')
        """,
        user_id,
    )
    existing_rows = [dict(row) for row in rows]
    existing_types = {str(row.get("list_type") or "").strip().lower()
                      for row in existing_rows if row.get("user_id", user_id) == user_id}
    # The household's shared list (shopping) is created ONCE for the house: a family-visible list of that type that anyone
    # owns satisfies every member. Seeding one per member is how the panel came to show two shopping lists.
    family_types = {str(row.get("list_type") or "").strip().lower()
                    for row in existing_rows if row.get("visibility") == "family"}
    for list_type, name, description, visibility in DEFAULT_USER_LISTS:
        if list_type in existing_types or (visibility == "family" and list_type in family_types):
            continue
        await db.execute(
            """
            INSERT INTO lists (id, user_id, name, list_type, description, visibility)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (user_id, lower(name)) WHERE deleted = 0 DO NOTHING
            """,
            str(uuid.uuid4()),
            user_id,
            name,
            list_type,
            description,
            visibility,
        )
    await _maybe_commit(db)


async def _fetch_list_catalog(user_id: str, db: Any) -> list[dict[str, Any]]:
    rows = await db.fetch(
        """
        SELECT id, user_id, name, list_type, description, visibility, created_at, updated_at,
               (SELECT COUNT(*) FROM list_items i WHERE i.list_id = lists.id AND i.deleted = 0) AS n_items
        FROM lists
        WHERE deleted = 0
          AND (user_id = $1 OR visibility = 'family')
        ORDER BY
          CASE WHEN user_id = $1 THEN 0 ELSE 1 END,
          CASE list_type WHEN 'shopping' THEN 0 WHEN 'work' THEN 1 WHEN 'personal' THEN 2 WHEN 'tasks' THEN 3 ELSE 4 END,
          updated_at DESC
        LIMIT 18
        """,
        user_id,
    )
    household = user_id == panel_principal.HOUSEHOLD_VIEWER  # a panel guest: every family-visible list, nothing else
    catalog: list[dict[str, Any]] = []
    seen: dict[tuple[str, str], int] = {}
    for row in rows:
        item = dict(row)
        if household and item.get("visibility") != "family":
            continue  # belt and braces: the query already filters, a panel guest must never be handed a private list
        if item.get("user_id") != user_id and item.get("list_type") != "shopping" and not household:
            continue
        key = (str(item.get("list_type") or ""), str(item.get("name") or "").strip().lower())
        if key in seen:
            # ONE list per name: a fuller duplicate replaces an emptier one already kept (never the reverse), so the
            # panel/voice never lands on a stray empty twin.
            kept = catalog[seen[key]]
            if int(item.get("n_items") or 0) > int(kept.get("n_items") or 0):
                catalog[seen[key]] = item
            continue
        seen[key] = len(catalog)
        catalog.append(item)
    for item in catalog:
        item.pop("n_items", None)  # a tiebreak for the dedupe above, not part of the list contract
    return catalog


def _select_list(lists: list[dict[str, Any]], list_type: str = "", list_name: str = "") -> dict[str, Any] | None:
    if list_name:
        wanted = list_name.strip().lower()
        for item in lists:
            if str(item.get("name") or "").strip().lower() == wanted:
                return item
    if list_type:
        for item in lists:
            if item.get("list_type") == list_type:
                return item
    if len(lists) == 1:
        return lists[0]
    return None


def _list_card_actions(lists: list[dict[str, Any]], selected: dict[str, Any] | None) -> list[dict[str, str]]:
    actions = []
    for item in lists[:5]:
        name = str(item.get("name") or _default_list_name(str(item.get("list_type") or "list")))
        actions.append({"type": "query", "label": name, "query": f"show my {name} list"})
    actions.append({"type": "query", "label": "New list", "query": "new list"})
    return actions


def _new_list_prompt_card(list_type: str) -> dict[str, Any]:
    label = _default_list_name(list_type or "personal")
    return {
        "component": "action_form",
        "props": {
            "title": "New list",
            "summary": "What should I name it?",
            "source": "list_create",
            "form_id": "list_create",
            "fields": [
                {"label": "List type", "name": "list_type", "value": label},
                {"label": "Name", "name": "name", "value": "Say or type the list name"},
            ],
            "actions": [],
        },
    }


async def _resolve_list_create(intent: SkybridgeIntent, user_id: str, db: Any) -> dict[str, Any]:
    if not intent.list_name:
        return {
            "handled": True,
            "intent": _intent_dict(intent),
            "spoken_summary": "What should I name the new list?",
            "cards": [_new_list_prompt_card(intent.list_type or "personal")],
            "actions": [],
        }
    await _ensure_default_lists(user_id, db)
    list_type = intent.list_type or _list_type_for_name(intent.list_name)
    existing = await db.fetch(
        """
        SELECT id, name, list_type
        FROM lists
        WHERE user_id = $1 AND deleted = 0 AND lower(name) = lower($2)
        LIMIT 1
        """,
        user_id,
        intent.list_name,
    )
    created = False
    if not existing:
        status = await db.execute(
            """
            INSERT INTO lists (id, user_id, name, list_type, description, visibility)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (user_id, lower(name)) WHERE deleted = 0 DO NOTHING
            """,
            str(uuid.uuid4()),
            user_id,
            intent.list_name,
            list_type,
            "",
            "personal" if list_type in {"work", "personal", "tasks"} else "family",
        )
        created = _affected_rows(status) == 1
        await _maybe_commit(db)
    result = await _resolve_lists(SkybridgeIntent(domain="lists", action="show", list_type=list_type, list_name=intent.list_name), user_id, db)
    result["intent"] = {"domain": "lists", "action": "create_list", "list_type": list_type, "list_name": intent.list_name}
    result["spoken_summary"] = f"Created {intent.list_name}." if created else f"You already have a {intent.list_name} list."
    result["actions"] = [{"type": "created" if created else "existing", "domain": "lists", "list_name": intent.list_name, "list_type": list_type}]
    return result


async def _find_or_create_list(intent: SkybridgeIntent, user_id: str, db: Any, context: dict[str, Any] | None) -> tuple[str, str] | None:
    hinted_id, hinted_type = _context_list_hint(context)
    list_type = intent.list_type or hinted_type or "shopping"
    if hinted_id:
        rows = await db.fetch(
            """
            SELECT id, name, list_type
            FROM lists
            WHERE id = $1 AND deleted = 0
              AND (visibility = 'family' OR user_id = $2)
            LIMIT 1
            """,
            hinted_id,
            user_id,
        )
        if not rows:
            return None
        row = dict(rows[0])
        return str(row["id"]), str(row.get("list_type") or list_type)
    rows = await db.fetch(
        """
        SELECT id, name, list_type, visibility, created_at,
               (SELECT COUNT(*) FROM list_items i WHERE i.list_id = lists.id AND i.deleted = 0) AS n
        FROM lists
        WHERE list_type = $2 AND deleted = 0
          AND (visibility = 'family' OR user_id = $1)
        ORDER BY
          (SELECT COUNT(*) FROM list_items i WHERE i.list_id = lists.id AND i.deleted = 0) DESC,
          CASE WHEN user_id = $1 THEN 0 ELSE 1 END,
          created_at ASC, id ASC
        """,
        user_id,
        list_type,
    )
    if rows:
        row = _pick_list_row(rows, intent.list_name)
        return str(row["id"]), str(row.get("list_type") or list_type)
    household = user_id == panel_principal.HOUSEHOLD_VIEWER
    if household and list_type != "shopping":
        return None  # a panel guest adds to a household list that exists; it only ever starts the shopping list
    if household:
        user_id = panel_principal.HOUSEHOLD_OWNER_ID  # the household owns what a guest starts (a guest has no users row)
    list_id = str(uuid.uuid4())
    list_name = "Shopping" if list_type == "shopping" else list_type.title()
    await db.execute(
        """
        INSERT INTO lists (id, user_id, name, list_type, description, visibility)
        VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT (user_id, lower(name)) WHERE deleted = 0 DO NOTHING
        """,
        list_id,
        user_id,
        list_name,
        list_type,
        "",
        "family" if list_type == "shopping" else "personal",
    )
    await _maybe_commit(db)
    rows = await db.fetch(
        """
        SELECT id, name, list_type
        FROM lists
        WHERE user_id = $1 AND deleted = 0 AND lower(name) = lower($2)
        LIMIT 1
        """,
        user_id,
        list_name,
    )
    if rows:
        row = dict(rows[0])
        return str(row["id"]), str(row.get("list_type") or list_type)
    return list_id, list_type


async def _resolve_list_add_item(intent: SkybridgeIntent, user_id: str, db: Any, context: dict[str, Any] | None) -> dict[str, Any]:
    if user_id in {"guest", "voice-guest"}:
        return {
            "handled": True,
            "intent": _intent_dict(intent),
            "spoken_summary": "List changes are not available for guest sessions.",
            "cards": [_status_card("Sign in to change lists", "Zoe needs to know who is speaking before editing list items.")],
            "actions": [],
        }
    if not intent.item_text:
        return {
            "handled": True,
            "intent": _intent_dict(intent),
            "spoken_summary": "I did not catch the list item.",
            "cards": [_status_card("What should I add?", "Say the item and the list, for example: add bread to the shopping list.")],
            "actions": [],
        }
    await _ensure_default_lists(user_id, db)
    list_target = await _find_or_create_list(intent, user_id, db, context)
    if not list_target:
        return {
            "handled": True,
            "intent": _intent_dict(intent),
            "spoken_summary": "I could not find that list anymore.",
            "cards": [_status_card("Which list should I use?", "The list from the visible card is no longer available. Show your lists again or name the list to update.")],
            "actions": [],
        }
    list_id, list_type = list_target
    # Retry idempotency: the voice daemon (and any HTTP retry layer) can
    # re-submit a turn the server already processed — live 2026-07-07 every
    # barge-aborted voice add landed twice ~1.5-2.5s apart. An identical
    # item added to the same list moments ago is a replay, not a new intent;
    # skip the insert and answer as if it just succeeded (it did).
    # created_at is a TEXT column — cast before the timestamp comparison, or
    # `created_at > now() - interval` throws `operator does not exist:
    # text > timestamp` on Postgres and breaks the skybridge fast path.
    recent_dup = await db.fetchrow(
        """
        SELECT id FROM list_items
        WHERE list_id = $1 AND lower(text) = lower($2) AND deleted = 0
          AND created_at::timestamptz > now() - interval '10 seconds'
        """,
        list_id,
        intent.item_text,
    )
    if recent_dup is not None:
        # Answer as if the add just succeeded (it did) and point the card's
        # "recent" highlight at the original row.
        item_id = str(recent_dup["id"])
        logger.info("skybridge list add: duplicate %r within 10s on list %s — replay skipped", intent.item_text, list_id)
    else:
        item_id = str(uuid.uuid4())
        await db.execute(
            """
            INSERT INTO list_items (id, list_id, text, priority, category, quantity, parent_id, assigned_to)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            """,
            item_id,
            list_id,
            intent.item_text,
            "normal",
            "",
            "",
            None,
            None,
        )
        await _maybe_commit(db)
    # The item is on the list now (inserted, or already there from a replayed add): the receipt the write-claim guard reads.
    write_receipts.record("guest" if user_id == panel_principal.HOUSEHOLD_VIEWER else user_id, "list", item_id, "skybridge:add_item")
    _audit_list_item("add", user_id, list_id, item_id)
    refreshed = await _resolve_lists(SkybridgeIntent(domain="lists", action="show", list_type=list_type), user_id, db)
    card_content = refreshed.get("cards", [{}])[0].get("content", {}) if refreshed.get("cards") else {}
    items = card_content.get("items")
    if isinstance(items, list):
        found_recent = False
        for item in items:
            if isinstance(item, dict) and str(item.get("id") or "") == item_id:
                item["recent"] = True
                found_recent = True
        if not found_recent:
            items.insert(
                0,
                {
                    "id": item_id,
                    "list_id": list_id,
                    "text": intent.item_text,
                    "completed": False,
                    "priority": "normal",
                    "category": "",
                    "quantity": "",
                    "recent": True,
                },
            )
        items.sort(key=lambda row: 0 if isinstance(row, dict) and str(row.get("id") or "") == item_id else 1)
        card_content["recent_item_id"] = item_id
    refreshed["intent"] = {"domain": "lists", "action": "add_item", "list_type": list_type, "item_id": item_id}
    refreshed["spoken_summary"] = f"Added {intent.item_text} to the {list_type} list."
    refreshed["actions"] = [{"type": "created", "domain": "lists", "id": item_id, "list_id": list_id}]
    return refreshed


def _match_list_items_by_text(items: list[dict[str, Any]], wanted: str) -> list[dict[str, Any]]:
    """Find list items whose text matches `wanted`, preferring exact over substring."""
    target = wanted.strip().lower()
    if not target:
        return []
    exact = [item for item in items if str(item.get("text") or "").strip().lower() == target]
    if exact:
        return exact
    if re.search(r"\b(?:and|or)\b|&", target):
        # "milk and eggs" is ONE item only if an item is literally called that; a substring guess would act on one of
        # two things the user named.
        return []
    return [item for item in items if target in str(item.get("text") or "").strip().lower()]


async def _find_list_for_remove(intent: SkybridgeIntent, user_id: str, db: Any, context: dict[str, Any] | None) -> tuple[str, str] | None:
    """Locate an existing list (never create) for a removal, mirroring _find_or_create_list's lookups."""
    hinted_id, hinted_type = _context_list_hint(context)
    list_type = intent.list_type or hinted_type or "shopping"
    if hinted_id and not intent.list_name:
        rows = await db.fetch(
            """
            SELECT id, name, list_type
            FROM lists
            WHERE id = $1 AND deleted = 0
              AND (visibility = 'family' OR user_id = $2)
            LIMIT 1
            """,
            hinted_id,
            user_id,
        )
        if rows:
            row = dict(rows[0])
            return str(row["id"]), str(row.get("list_type") or list_type)
    if intent.list_name:
        # A list the user NAMED ("my chores list"): that list or none - never the default shopping list instead.
        rows = await db.fetch(
            """
            SELECT id, name, list_type
            FROM lists
            WHERE deleted = 0 AND (lower(name) = lower($2) OR lower(name) = lower($2) || ' list')
              AND (visibility = 'family' OR user_id = $1)
            ORDER BY
              CASE WHEN user_id = $1 THEN 0 ELSE 1 END,
              updated_at DESC
            LIMIT 1
            """,
            user_id,
            intent.list_name.strip(),
        )
        if not rows:
            return None
        row = dict(rows[0])
        return str(row["id"]), str(row.get("list_type") or list_type)
    rows = await db.fetch(
        """
        SELECT id, name, list_type, visibility, created_at,
               (SELECT COUNT(*) FROM list_items i WHERE i.list_id = lists.id AND i.deleted = 0) AS n
        FROM lists
        WHERE list_type = $2 AND deleted = 0
          AND (visibility = 'family' OR user_id = $1)
        ORDER BY
          (SELECT COUNT(*) FROM list_items i WHERE i.list_id = lists.id AND i.deleted = 0) DESC,
          CASE WHEN user_id = $1 THEN 0 ELSE 1 END,
          created_at ASC, id ASC
        """,
        user_id,
        list_type,
    )
    if rows:
        row = _pick_list_row(rows, intent.list_name)
        return str(row["id"]), str(row.get("list_type") or list_type)
    return None


async def _resolve_list_remove_item(intent: SkybridgeIntent, user_id: str, db: Any, context: dict[str, Any] | None) -> dict[str, Any]:
    if user_id in {"guest", "voice-guest"}:
        return {
            "handled": True,
            "intent": _intent_dict(intent),
            "spoken_summary": "List changes are not available for guest sessions.",
            "cards": [_status_card("Sign in to change lists", "Zoe needs to know who is speaking before editing list items.")],
            "actions": [],
        }
    if not intent.item_text:
        return {
            "handled": True,
            "intent": _intent_dict(intent),
            "spoken_summary": "I did not catch which item to remove.",
            "cards": [_status_card("What should I remove?", "Say the item and the list, for example: take milk off the shopping list.")],
            "actions": [],
        }
    await _ensure_default_lists(user_id, db)
    list_target = await _find_list_for_remove(intent, user_id, db, context)
    if not list_target:
        return {
            "handled": True,
            "intent": _intent_dict(intent),
            "spoken_summary": "I could not find that list.",
            "cards": [_status_card("Which list should I use?", "I could not find that list. Show your lists again or name the list to update.")],
            "actions": [],
        }
    list_id, list_type = list_target
    item_rows = await db.fetch(
        """
        SELECT id, list_id, text, completed
        FROM list_items
        WHERE list_id = $1 AND deleted = 0
        """,
        list_id,
    )
    items = [dict(row) for row in item_rows]
    matches = _match_list_items_by_text(items, intent.item_text)
    if len(matches) != 1:
        # Ambiguity rule: never delete the wrong item. Ask for clarification.
        refreshed = await _resolve_lists(SkybridgeIntent(domain="lists", action="show", list_type=list_type, list_name=intent.list_name), user_id, db)
        if not matches:
            body = f"I could not find \"{intent.item_text}\" on that list. Say the exact item to remove."
            spoken = f"I could not find {intent.item_text} on that list."
            status = "not_found"
        else:
            options = ", ".join(str(match.get("text") or "") for match in matches[:5])
            body = f"More than one item matches \"{intent.item_text}\" ({options}). Say the exact item to remove."
            spoken = f"More than one item matches {intent.item_text}. Which one should I remove?"
            status = "ambiguous"
        refreshed["intent"] = {"domain": "lists", "action": "remove_item", "list_type": list_type, "status": status}
        refreshed["spoken_summary"] = spoken
        refreshed["cards"] = [_status_card("Which item should I remove?", body)] + list(refreshed.get("cards") or [])
        refreshed["actions"] = []
        return refreshed
    target_item = matches[0]
    item_id = str(target_item.get("id") or "")
    remove_result = await db.execute(
        "UPDATE list_items SET deleted = 1, updated_at = NOW() WHERE id = $1 AND list_id = $2 AND deleted = 0",
        item_id,
        list_id,
    )
    if _affected_rows(remove_result) == 0:
        refreshed = await _resolve_lists(SkybridgeIntent(domain="lists", action="show", list_type=list_type, list_name=intent.list_name), user_id, db)
        refreshed["intent"] = {"domain": "lists", "action": "remove_item", "list_type": list_type}
        refreshed["spoken_summary"] = "I could not remove that item."
        refreshed["cards"] = [_status_card("I could not remove that item", "The list item is no longer available. Show your lists again to confirm.")] + list(refreshed.get("cards") or [])
        refreshed["actions"] = []
        return refreshed
    await _maybe_commit(db)
    item_text = str(target_item.get("text") or intent.item_text)
    refreshed = await _resolve_lists(SkybridgeIntent(domain="lists", action="show", list_type=list_type, list_name=intent.list_name), user_id, db)
    _audit_list_item("remove", user_id, list_id, item_id)
    refreshed["intent"] = {"domain": "lists", "action": "remove_item", "list_type": list_type, "item_id": item_id}
    refreshed["spoken_summary"] = f"Removed {item_text} from the {list_type} list."
    refreshed["actions"] = [{"type": "deleted", "domain": "lists", "id": item_id, "list_id": list_id}]
    return refreshed


async def _resolve_list_complete_item(intent: SkybridgeIntent, user_id: str, db: Any, context: dict[str, Any] | None) -> dict[str, Any]:
    """Tick an item off (or restore it) — the core list gesture. Mutate, then
    authoritative re-read → refreshed card, mirroring the add/remove loop."""
    if user_id in {"guest", "voice-guest"}:
        return {
            "handled": True,
            "intent": _intent_dict(intent),
            "spoken_summary": "List changes are not available for guest sessions.",
            "cards": [_status_card("Sign in to change lists", "Zoe needs to know who is speaking before editing list items.")],
            "actions": [],
        }
    if not intent.item_text:
        return {
            "handled": True,
            "intent": _intent_dict(intent),
            "spoken_summary": "I did not catch which item to check off.",
            "cards": [_status_card("Which item?", "Say the item, for example: check off milk on the shopping list.")],
            "actions": [],
        }
    await _ensure_default_lists(user_id, db)
    list_target = await _find_list_for_remove(intent, user_id, db, context)
    if not list_target:
        return {
            "handled": True,
            "intent": _intent_dict(intent),
            "spoken_summary": "I could not find that list.",
            "cards": [_status_card("Which list should I use?", "I could not find that list. Show your lists again or name the list to update.")],
            "actions": [],
        }
    list_id, list_type = list_target
    item_rows = await db.fetch(
        """
        SELECT id, list_id, text, completed
        FROM list_items
        WHERE list_id = $1 AND deleted = 0
        """,
        list_id,
    )
    items = [dict(row) for row in item_rows]
    matches = _match_list_items_by_text(items, intent.item_text)
    if len(matches) != 1:
        # Ambiguity rule: never tick the wrong item. Ask instead of guessing.
        refreshed = await _resolve_lists(SkybridgeIntent(domain="lists", action="show", list_type=list_type, list_name=intent.list_name), user_id, db)
        if not matches:
            body = f"I could not find \"{intent.item_text}\" on that list. Say the exact item."
            spoken = f"I could not find {intent.item_text} on that list."
            status = "not_found"
        else:
            options = ", ".join(str(match.get("text") or "") for match in matches[:5])
            body = f"More than one item matches \"{intent.item_text}\" ({options}). Say the exact item."
            spoken = f"More than one item matches {intent.item_text}. Which one?"
            status = "ambiguous"
        refreshed["intent"] = {"domain": "lists", "action": "complete_item", "list_type": list_type, "status": status}
        refreshed["spoken_summary"] = spoken
        refreshed["cards"] = [_status_card("Which item?", body)] + list(refreshed.get("cards") or [])
        refreshed["actions"] = []
        return refreshed
    target_item = matches[0]
    item_id = str(target_item.get("id") or "")
    # Explicit direction from the parser; fall back to a toggle if unset.
    desired = intent.completed
    if desired is None:
        desired = not bool(target_item.get("completed"))
    completed_val = 1 if desired else 0
    update_result = await db.execute(
        "UPDATE list_items SET completed = $1, updated_at = NOW() WHERE id = $2 AND list_id = $3 AND deleted = 0",
        completed_val,
        item_id,
        list_id,
    )
    if _affected_rows(update_result) == 0:
        refreshed = await _resolve_lists(SkybridgeIntent(domain="lists", action="show", list_type=list_type, list_name=intent.list_name), user_id, db)
        refreshed["intent"] = {"domain": "lists", "action": "complete_item", "list_type": list_type}
        refreshed["spoken_summary"] = "I could not update that item."
        refreshed["cards"] = [_status_card("I could not update that item", "The list item is no longer available. Show your lists again to confirm.")] + list(refreshed.get("cards") or [])
        refreshed["actions"] = []
        return refreshed
    await _maybe_commit(db)
    item_text = str(target_item.get("text") or intent.item_text)
    refreshed = await _resolve_lists(SkybridgeIntent(domain="lists", action="show", list_type=list_type, list_name=intent.list_name), user_id, db)
    _audit_list_item("complete" if desired else "restore", user_id, list_id, item_id)
    refreshed["intent"] = {"domain": "lists", "action": "complete_item", "list_type": list_type, "item_id": item_id, "completed": desired}
    refreshed["spoken_summary"] = (
        f"Ticked off {item_text} on the {list_type} list." if desired
        else f"Put {item_text} back on the {list_type} list."
    )
    refreshed["actions"] = [{"type": "updated", "domain": "lists", "id": item_id, "list_id": list_id, "completed": desired}]
    return refreshed


async def _resolve_list_edit_item(intent: SkybridgeIntent, user_id: str, db: Any, context: dict[str, Any] | None) -> dict[str, Any]:
    """Open the existing shopping item editor card for the tapped/named item (read, not a mutation)."""
    if user_id in {"guest", "voice-guest"}:
        return {
            "handled": True,
            "intent": _intent_dict(intent),
            "spoken_summary": "List changes are not available for guest sessions.",
            "cards": [_status_card("Sign in to change lists", "Zoe needs to know who is speaking before editing list items.")],
            "actions": [],
        }
    await _ensure_default_lists(user_id, db)
    list_target = await _find_list_for_remove(intent, user_id, db, context)
    if not list_target:
        return {
            "handled": True,
            "intent": _intent_dict(intent),
            "spoken_summary": "I could not find that list.",
            "cards": [_status_card("Which list should I use?", "I could not find that list. Show your lists again or name the list to edit.")],
            "actions": [],
        }
    list_id, list_type = list_target
    item_rows = await db.fetch(
        """
        SELECT id, list_id, text, completed, quantity, category
        FROM list_items
        WHERE list_id = $1 AND deleted = 0
        """,
        list_id,
    )
    items = [dict(row) for row in item_rows]
    matches = _match_list_items_by_text(items, intent.item_text)
    if len(matches) != 1:
        if not matches:
            body = f"I could not find \"{intent.item_text}\" on that list. Say the exact item to edit."
            spoken = f"I could not find {intent.item_text} on that list."
        else:
            options = ", ".join(str(match.get("text") or "") for match in matches[:5])
            body = f"More than one item matches \"{intent.item_text}\" ({options}). Say the exact item to edit."
            spoken = f"More than one item matches {intent.item_text}. Which one should I edit?"
        return {
            "handled": True,
            "intent": {"domain": "lists", "action": "edit_item", "list_type": list_type, "status": "ambiguous"},
            "spoken_summary": spoken,
            "cards": [_status_card("Which item should I edit?", body)],
            "actions": [],
        }
    item = matches[0]
    item_text = str(item.get("text") or intent.item_text)
    list_label = _default_list_name(list_type)
    actions = [
        {"type": "query", "label": "Remove from list", "query": f"take {item_text} off the {list_type} list", "kind": "warn"},
        {"type": "query", "label": "Back to list", "query": f"show my {list_label} list"},
    ]
    card = card_service.build_shopping_item_editor_card(
        {
            "items": [item_text],
            "list_name": list_label,
            "list_type": list_type,
            "quantity": str(item.get("quantity") or ""),
            "item_id": str(item.get("id") or ""),
            "actions": actions,
        }
    )
    return {
        "handled": True,
        "intent": {"domain": "lists", "action": "edit_item", "list_type": list_type, "item_id": item.get("id")},
        "spoken_summary": f"Editing {item_text}. You can remove it or go back to the list.",
        "cards": [card_service.convert_emit(card, target_major=1)],
        "actions": [],
    }


def _humanize_user_id(user_id: str | None) -> str:
    """Best-effort display name from a bare user id when the users table has none."""
    raw = str(user_id or "").strip()
    if not raw or raw in {"guest", "voice-guest"}:
        return "Guest"
    cleaned = re.sub(r"[._-]+", " ", raw).strip()
    # A UUID/opaque id is not a name — fall back to a neutral second-person label.
    if re.fullmatch(r"[0-9a-f]{8,}", cleaned.replace(" ", "")):
        return "You"
    return cleaned.title() if cleaned.islower() else cleaned


_IDENTITY_ROLE_LABELS = {
    "admin": "Administrator",
    "family-admin": "Administrator",
    "member": "Member",
    "user": "Member",
}


GUEST_NAME_REPLY = "I don't know who you are yet. Sign in with your PIN on the panel and I'll know you."


async def _self_display_name(user_id: str, db: Any) -> tuple[str, str]:
    """``(display name, role)`` of the signed-in account. The name is what Zoe calls the person (identity_facts: the preferred name,
    else the account's own, capitalised) - NEVER the raw lowercase user id, which is a key, not a name."""
    name = role = ""
    try:
        import identity_facts as _idf

        ident = await _idf.resolve_identity(user_id)  # its own pooled connection: never the request `db`
        if ident is not None and ident.name:
            name = ident.name
    except Exception:
        pass  # identity unavailable - the users row below, then the humanised id
    try:
        rows = await db.fetch("SELECT name, role FROM users WHERE id = $1 LIMIT 1", user_id)
        if rows:
            role = str(rows[0]["role"] or "").strip()
            if not name:
                raw = str(rows[0]["name"] or "").strip()
                name = raw.title() if raw.islower() else raw
    except Exception:
        # Users table unavailable / schema mismatch - still answer with the id we have.
        pass
    return (name or _humanize_user_id(user_id)), role


async def _resolve_self_identity(user_id: str, db: Any, kind: str = "") -> dict[str, Any]:
    """Answer a self-identity ask ("who am I", "what's my name") with the panel's identity card.

    Reuses the person-profile card so "who am I" surfaces WHO Zoe thinks is
    speaking - name + role - instead of falling through to a generic settings
    page. A guest gets an informative "not signed in" card rather than the
    sign-in wall, so the question is always answered. ``kind="name"`` ("what's my
    name") is a NAME question: it is answered "Your name is <name>." and never as a
    sign-in statement.
    """
    if _is_guest_user(user_id):
        asked_name = kind == "name"
        person = {
            "name": "Guest",
            "circle": "self",
            "relationship": "Not signed in",
            "notes": "You're using Zoe as a guest. Sign in to get your calendar, lists and people.",
            "health_score": 100,
        }
        summary = GUEST_NAME_REPLY if asked_name else "You're signed in as a guest."
        card = card_service.build_person_profile_card({"person": person, "summary": summary})
        return {
            "handled": True,
            "intent": {"domain": "people", "action": "identity"},
            "spoken_summary": summary,
            "cards": [card_service.convert_emit(card, target_major=1)],
        }

    display, role = await _self_display_name(user_id, db)
    role_label = _IDENTITY_ROLE_LABELS.get(role.lower(), role.title() if role else "Signed in")
    person = {
        "name": display,
        "circle": "self",
        "relationship": "You",
        "context": role_label,
        "notes": f"You're signed in on this panel as {display}.",
        "health_score": 100,
    }
    summary = f"Your name is {display}." if kind == "name" else f"You're signed in as {display}."
    card = card_service.build_person_profile_card({"person": person, "summary": summary})
    return {
        "handled": True,
        "intent": {"domain": "people", "action": "identity"},
        "spoken_summary": summary,
        "cards": [card_service.convert_emit(card, target_major=1)],
    }


async def _resolve_people(intent: SkybridgeIntent, user_id: str, db: Any) -> dict[str, Any]:
    if user_id in {"guest", "voice-guest"}:
        card = card_service.build_people_directory_card(
            {
                "title": "People",
                "people": [],
                "count": 0,
                "summary": "No people data is available for guest sessions.",
            }
        )
        return {
            "handled": True,
            "intent": {"domain": "people", "action": intent.action, "query": intent.query},
            "spoken_summary": "No people data is available for guest sessions.",
            "cards": [card_service.convert_emit(card, target_major=1)],
        }

    filters = ["deleted = 0", "(visibility = 'family' OR user_id = $1)", "(is_partial = 0 OR is_partial IS NULL)"]
    params: list[Any] = [user_id]
    if intent.context:
        params.append(intent.context)
        filters.append(f"context = ${len(params)}")
    if intent.circle:
        params.append(intent.circle)
        filters.append(f"circle = ${len(params)}")
    if intent.query:
        params.append(f"%{intent.query}%")
        query_index = len(params)
        filters.append(
            f"(name ILIKE ${query_index} OR relationship ILIKE ${query_index} OR email ILIKE ${query_index} OR phone ILIKE ${query_index})"
        )
    where = " AND ".join(filters)
    rows = await db.fetch(
        f"""
        SELECT *
        FROM people
        WHERE {where}
        ORDER BY name
        LIMIT 12
        """,
        *params,
    )
    people = [row_to_person(row) for row in rows]
    exact = None
    if intent.query:
        normalized_query = intent.query.strip().lower()
        exact = next((person for person in people if str(person.get("name") or "").strip().lower() == normalized_query), None)
    if exact or (intent.query and len(people) == 1):
        person = exact or people[0]
        summary = f"{person.get('name')} is in your people."
        card = card_service.build_person_profile_card({"person": person, "summary": summary})
        return {
            "handled": True,
            "intent": {"domain": "people", "action": intent.action, "query": intent.query},
            "spoken_summary": summary,
            "cards": [card_service.convert_emit(card, target_major=1)],
        }

    title = "People"
    if intent.context:
        title = f"{intent.context.title()} Contacts"
    if intent.query:
        title = f"People matching {intent.query}"
    count = len(people)
    spoken = f"I found {count} contact{'s' if count != 1 else ''}."
    card = card_service.build_people_directory_card(
        {
            "title": title,
            "people": people,
            "count": count,
            "query": intent.query,
            "context": intent.context,
            "circle": intent.circle,
            "summary": spoken,
        }
    )
    return {
        "handled": True,
        "intent": {"domain": "people", "action": intent.action, "query": intent.query, "context": intent.context},
        "spoken_summary": spoken,
        "cards": [card_service.convert_emit(card, target_major=1)],
    }


async def _store_skybridge_memory_fact(
    fact: str,
    *,
    user_id: str,
    person_id: str | None = None,
    person_name: str = "",
) -> bool:
    if not fact.strip():
        return False
    try:
        from memory_service import MemoryServiceError, get_memory_service

        try:
            await get_memory_service().ingest(
                fact,
                user_id=user_id,
                source="skybridge_action",
                memory_type="person" if person_id else "fact",
                confidence=0.86,
                status="approved",
                tags=["skybridge", "person"] if person_id else ["skybridge"],
                entity_type="person" if person_id else None,
                entity_id=person_id,
                source_excerpt=person_name or fact[:80],
            )
            return True
        except MemoryServiceError:
            return False
    except Exception:
        return False


async def _find_or_create_person(user_id: str, name: str, db: Any) -> dict[str, Any]:
    rows = await db.fetch(
        """
        SELECT *
        FROM people
        WHERE deleted = 0
          AND (visibility = 'family' OR user_id = $1)
          AND lower(name) = lower($2)
        ORDER BY is_partial ASC, updated_at DESC
        LIMIT 1
        """,
        user_id,
        name,
    )
    if rows:
        return row_to_person(rows[0])
    person_id = str(uuid.uuid4())
    await db.execute(
        """
        INSERT INTO people (id, user_id, name, relationship, circle, context, visibility, is_partial)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        """,
        person_id,
        user_id,
        name,
        None,
        "circle",
        "personal",
        "family",
        0,
    )
    await _maybe_commit(db)
    return {
        "id": person_id,
        "user_id": user_id,
        "name": name,
        "relationship": None,
        "circle": "circle",
        "context": "personal",
        "visibility": "family",
        "is_partial": False,
    }


def _append_note(existing: str | None, addition: str) -> str:
    existing_text = str(existing or "").strip()
    addition_text = addition.strip().rstrip(".")
    if not addition_text:
        return existing_text
    sentence = addition_text[:1].upper() + addition_text[1:] + "."
    if sentence.lower() in existing_text.lower():
        return existing_text
    return f"{existing_text} {sentence}".strip()


async def _resolve_people_remember_fact(intent: SkybridgeIntent, user_id: str, db: Any) -> dict[str, Any]:
    if user_id in {"guest", "voice-guest"}:
        return {
            "handled": True,
            "intent": _intent_dict(intent),
            "spoken_summary": "People memory changes are not available for guest sessions.",
            "cards": [_status_card("Sign in to remember this", "Zoe needs to know who is speaking before saving people facts.")],
            "actions": [],
        }
    if not intent.person_name:
        return {
            "handled": True,
            "intent": _intent_dict(intent),
            "spoken_summary": "I did not catch who this is about.",
            "cards": [_status_card("Who is this about?", "Say the person's name and the fact you want Zoe to remember.")],
            "actions": [],
        }
    person = await _find_or_create_person(user_id, intent.person_name, db)
    if person.get("user_id") and person.get("user_id") != user_id:
        return {
            "handled": True,
            "intent": _intent_dict(intent),
            "spoken_summary": "I can see that person, but I cannot update their profile from this account.",
            "cards": [_status_card("I could not update that profile", "That person is visible to the family, but this account does not own the profile. Ask the owner to update it or create a new profile.")],
            "actions": [],
        }
    memory_bits = []
    if intent.fact_text:
        memory_bits.append(f"{intent.person_name} {intent.fact_text}")
    if intent.birthday:
        memory_bits.append(f"{intent.person_name}'s birthday is {intent.birthday}")
    if memory_bits:
        memory_stored = await _store_skybridge_memory_fact(
            ". ".join(memory_bits),
            user_id=user_id,
            person_id=str(person.get("id") or ""),
            person_name=intent.person_name,
        )
        if not memory_stored:
            return {
                "handled": True,
                "intent": _intent_dict(intent),
                "spoken_summary": "I could not save that memory.",
                "cards": [_status_card("Memory was not saved", "Zoe could not store that people fact through the memory service, so the profile card was left unchanged.")],
                "actions": [],
            }
    updates = []
    params: list[Any] = []
    note_fact = intent.fact_text
    if note_fact:
        updates.append("notes = $" + str(len(params) + 1))
        params.append(_append_note(person.get("notes"), note_fact))
    if intent.birthday:
        updates.append("birthday = $" + str(len(params) + 1))
        params.append(intent.birthday)
    if updates:
        updates.append("updated_at = NOW()")
        params.extend([person["id"], user_id])
        # MemoryService is the write gate for the fact; this table update is the
        # people-card projection so the visible profile reflects the saved memory.
        await db.execute(
            f"UPDATE people SET {', '.join(updates)} WHERE id = ${len(params)-1} AND user_id = ${len(params)}",
            *params,
        )
        await _maybe_commit(db)
    refreshed = await _resolve_people(
        SkybridgeIntent(domain="people", action="show", query=intent.person_name),
        user_id,
        db,
    )
    refreshed["intent"] = {"domain": "people", "action": "remember_fact", "query": intent.person_name}
    refreshed["spoken_summary"] = f"Remembered that for {intent.person_name}."
    refreshed["actions"] = [{"type": "updated", "domain": "people", "id": person.get("id")}]
    return refreshed
