"""Server-side resolver for Skybridge data cards."""

from __future__ import annotations

import logging
import os
import re
import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from calendar_utils import row_to_event
from card_contract import CardContractError, validate_component
from card_service import card_service
from database import get_db_ctx
from people_utils import row_to_person
from time_utils import today_for_zoe_tz

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
    birthday: str = ""
    duration_seconds: int = 0
    completed: bool | None = None


# ── Timers ───────────────────────────────────────────────────────────────────
# A REAL countdown timer (not the old speak-only stub). The store is the
# authoritative source for spoken "how long left" / "cancel" queries; the panel
# ticks and rings from the absolute expires_at so firing is immediate and
# survives a reload. In-memory is intentional: timers are short-lived and the
# panel persists its own copy, so a service restart losing one is acceptable.

_WORD_NUMBERS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40,
    "fifty": 50, "sixty": 60, "ninety": 90,
}

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
            "created_at": now,
            "expires_at": now + int(seconds),
        }
        self._by_owner.setdefault(owner, []).append(timer)
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

    def cancel_by_id(self, owner: str, timer_id: str) -> dict[str, Any] | None:
        self._prune(owner)
        timers = self._by_owner.get(owner, [])
        target = next((t for t in timers if t["timer_id"] == timer_id), None)
        if target is not None:
            timers.remove(target)
            self._prune(owner)
        return target


_timers = _TimerStore()


def _unit_to_seconds(unit: str) -> int:
    u = unit.lower()
    if u.startswith("h"):
        return 3600
    if u.startswith("m"):
        return 60
    return 1


def _parse_timer_duration(text: str) -> int:
    """Total seconds from a timer phrase (digits or number-words), else 0."""
    num = r"\d+|" + "|".join(re.escape(w) for w in _WORD_NUMBERS)
    pattern = re.compile(r"\b(" + num + r")\s*(hours?|hrs?|minutes?|mins?|seconds?|secs?)\b")
    total = 0
    found = False
    for value, unit in pattern.findall(text):
        n = int(value) if value.isdigit() else _WORD_NUMBERS.get(value, 0)
        total += n * _unit_to_seconds(unit)
        found = True
    if found:
        return total
    short = re.search(r"\b(\d+)\s*([hms])\b", text)
    if short:
        return int(short.group(1)) * _unit_to_seconds(short.group(2))
    return 0


def _parse_timer_label(text: str) -> str:
    """Extract an optional timer name ('for the eggs', 'called pasta'); never a
    duration ('for 5 minutes')."""
    m = re.search(r"\b(?:called|named|for)\s+(?:the\s+)?(.+?)\s*$", text.strip())
    if not m:
        return ""
    cand = m.group(1).strip().strip(".!?")
    if not cand or re.search(r"\d", cand):
        return ""
    if re.search(r"\b(hours?|hrs?|minutes?|mins?|seconds?|secs?|timer)\b", cand):
        return ""
    if cand in _WORD_NUMBERS:
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


def _resolve_timer(intent: SkybridgeIntent, user_id: str) -> dict[str, Any]:
    owner = (user_id or "guest")

    if intent.action == "cancel":
        cancelled = _timers.cancel(owner, intent.title or None)
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


def _list_add_from_text(message: str) -> tuple[str, str] | None:
    match = re.search(
        r"\badd\s+(?P<item>.+?)\s+to\s+(?:the\s+|my\s+)?(?P<list>shopping list|grocery list|groceries|work list|personal list|list|task list|todo list|tasks|todos)\b",
        message,
        re.IGNORECASE,
    )
    if not match:
        return None
    item = _clean_action_text(match.group("item"))
    list_phrase = f" {match.group('list').lower()} "
    list_type = _list_type_from_text(list_phrase) or ("shopping" if "grocery" in list_phrase else "")
    return item, list_type or "shopping"


def _list_remove_from_text(message: str) -> tuple[str, str] | None:
    match = re.search(
        r"\b(?:take|remove|delete|drop)\s+(?P<item>.+?)\s+(?:off|from|out of)\s+(?:the\s+|my\s+)?(?P<list>shopping list|grocery list|groceries|work list|personal list|list|task list|todo list|tasks|todos)\b",
        message,
        re.IGNORECASE,
    )
    if not match:
        return None
    item = _clean_action_text(match.group("item"))
    list_phrase = f" {match.group('list').lower()} "
    list_type = _list_type_from_text(list_phrase) or ("shopping" if "grocery" in list_phrase else "")
    if not item:
        return None
    return item, list_type or "shopping"


# Item nouns shared by the check-off parser. Kept inline (not a module constant)
# so it sits next to the patterns that use it.
_LIST_NOUNS_RE = (
    r"(?P<list>shopping list|grocery list|groceries|work list|personal list|list"
    r"|task list|todo list|tasks|todos)"
)


def _list_complete_from_text(message: str) -> tuple[str, str, bool] | None:
    """Parse a check-off / un-check command (from a tapped row or voice).

    Returns (item, list_type, completed) where completed=True means "tick it off".
    Tap rows emit "check off X on the shopping list" / "uncheck X on the shopping
    list"; this also catches natural forms ("tick off bread", "mark eggs as done",
    "cross milk off the list"). Direction is explicit so a tap never guesses wrong.
    """
    on_tail = r"(?:\s+(?:on|from|in)\s+(?:the\s+|my\s+)?" + _LIST_NOUNS_RE + r")?\s*[.!]*\s*$"
    off_tail = r"\s+off(?:\s+(?:the\s+|my\s+)?" + _LIST_NOUNS_RE + r")?\s*[.!]*\s*$"

    def _finish(match: "re.Match[str]", completed: bool) -> tuple[str, str, bool] | None:
        item = _clean_action_text(match.group("item"))
        if not item:
            return None
        raw = match.groupdict().get("list")
        if raw:
            phrase = f" {raw.lower()} "
            list_type = _list_type_from_text(phrase) or "shopping"
        else:
            list_type = "shopping"
        return item, list_type, completed

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
    if not item or item.lower() in {"item", "something", "this", "that", "it"}:
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
    item = _clean_action_text(match.group("item"))
    if not item or item.lower() in {"item", "something", "this", "that"}:
        return None
    _list_id, list_type = _context_list_hint(context)
    return item, list_type or "shopping"


def _calendar_delete_from_text(message: str, context: dict[str, Any] | None) -> str | None:
    """Parse 'delete/remove/cancel <event> from my calendar' (or contextual)."""
    match = re.search(
        r"\b(?:delete|remove|cancel)\s+(?P<target>.+?)\s+(?:from|off|out of)\s+(?:the\s+|my\s+)?(?:calendar|schedule|agenda)\b",
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


def _calendar_create_from_text(message: str, context: dict[str, Any] | None) -> tuple[str, str] | None:
    match = re.search(r"\badd\s+(?P<title>.+?)\s+(?:at|for)\s+(?P<time>\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)?)\b", message, re.IGNORECASE)
    if not match:
        return None
    if _context_domain(context) != "calendar" and not re.search(r"\b(calendar|schedule|event|appointment|meeting)\b", message, re.IGNORECASE):
        return None
    title = _clean_action_text(match.group("title"))
    # "add standup to my calendar at 9am" → title is "standup", not "standup to my
    # calendar" — strip the destination tail the lazy match leaves behind.
    title = re.sub(r"\s+(?:to|on|in|into)\s+(?:my\s+|the\s+)?calendar\s*$", "", title, flags=re.IGNORECASE).strip()
    target_time = _parse_time(match.group("time"))
    return (title, target_time) if title and target_time else None


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


def classify_skybridge_intent(message: str, context: dict[str, Any] | None = None) -> SkybridgeIntent | None:
    """Classify only domains that Skybridge can resolve to real data cards."""
    raw_message = message or ""
    text = f" {raw_message.lower()} "
    person_fact = _people_fact_from_text(raw_message)
    if person_fact:
        name, fact, birthday = person_fact
        return SkybridgeIntent(domain="people", action="remember_fact", person_name=name, fact_text=fact, birthday=birthday)
    # Timers claim the phrase early so "set a 5 minute timer" isn't misread as a
    # calendar event ("set ... at a time").
    if re.search(r"\btimers?\b|\bcountdowns?\b", text):
        if re.search(r"\b(cancel|stop|clear|delete|dismiss|reset|turn off)\b", text):
            return SkybridgeIntent(domain="timer", action="cancel", title=_parse_timer_label(text))
        if re.search(r"\b(how (?:long|much)|left|remaining|status|check on|still going)\b", text):
            return SkybridgeIntent(domain="timer", action="status")
        # "show/list my timers" is a status ask too. Without this, the query fell
        # through to the people fallback ("show my X" -> contact search) and the
        # dashboard Timers tile rendered an empty people directory (glass-verified).
        if re.search(r"\b(show|list|view|see|display|what)\b", text):
            return SkybridgeIntent(domain="timer", action="status")
        duration = _parse_timer_duration(text)
        # Only CREATE for an explicit ask — a passing mention ("it's not the timer",
        # "the timer went off") must not spin one up. A duration or a start verb is
        # the cue; "set a timer" with no duration still defaults to 5 minutes.
        if duration or re.search(r"\b(set|start|make|create|run|new|put|begin)\b", text):
            return SkybridgeIntent(domain="timer", action="create", title=_parse_timer_label(text), duration_seconds=duration or 300)
    music_intent = _classify_music(text)
    if music_intent is not None:
        return music_intent
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
        item, list_type, completed = list_complete
        return SkybridgeIntent(domain="lists", action="complete_item", item_text=item, list_type=list_type, completed=completed)
    list_remove = _list_remove_from_text(raw_message)
    if list_remove:
        item, list_type = list_remove
        return SkybridgeIntent(domain="lists", action="remove_item", item_text=item, list_type=list_type)
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
        title, target_time = calendar_create
        parsed_range = _calendar_date_from_text(text, _today())
        start = parsed_range[1] if parsed_range else _context_calendar_date(context)
        return SkybridgeIntent(domain="calendar", action="create_event", title=title, target_time=target_time, range_label=start.isoformat(), start_date=start, end_date=start)
    calendar_create_notime = _calendar_create_notime_from_text(raw_message, context)
    if calendar_create_notime:
        parsed_range = _calendar_date_from_text(text, _today())
        start = parsed_range[1] if parsed_range else _context_calendar_date(context)
        return SkybridgeIntent(domain="calendar", action="create_event", title=calendar_create_notime, target_time="", all_day=True, range_label=start.isoformat(), start_date=start, end_date=start)
    if (
        " clock " in text
        or re.search(r"\bwhat(?:'s| is)?\s+the\s+time\b", text)
        or re.search(r"\bwhat\s+time\s+is\s+it\b", text)
        or re.search(r"\bshow\s+(?:me\s+)?(?:the\s+)?time\b", text)
        or re.search(r"\bcurrent\s+time\b", text)
    ):
        return SkybridgeIntent(domain="clock", action="show")
    if any(term in text for term in (" weather", " forecast", " temperature", " rain", " windy", " wind ")):
        action = "forecast" if any(term in text for term in ("forecast", "tomorrow", "week", "next few days")) else "current"
        return SkybridgeIntent(domain="weather", action=action)
    if any(term in text for term in (" list", " lists", " shopping", " groceries", " grocery", " tasks", " todos")):
        list_type = _list_type_from_text(text)
        action = "overview" if re.search(r"\blists\b", text) else "show"
        return SkybridgeIntent(domain="lists", action=action, list_type=list_type)
    if any(
        term in text
        for term in (
            " calendar",
            " schedule",
            " events",
            " appointments",
            " agenda",
            " happening",
            " what's on",
            " whats on",
        )
    ):
        today = _today()
        parsed_range = _calendar_date_from_text(text, today)
        if parsed_range is not None:
            label, start, end = parsed_range
            return SkybridgeIntent("calendar", "show", label, start, end)
        return SkybridgeIntent("calendar", "show", "today", today, today)
    if any(term in text for term in (" people", " contacts", " contact", " person", " profile", " family", " friends")):
        query, people_ctx, circle = _people_filters_from_text(text)
        if " family " in text and not query:
            query = "family"
        if " friends " in text and not query:
            query = "friend"
        return SkybridgeIntent(domain="people", action="show", query=query, context=people_ctx, circle=circle)
    if re.search(r"\b(?:find|search for|look up|show)\s+(?:my\s+)?[a-z][a-z .'-]{1,80}\b", text):
        query, people_ctx, circle = _people_filters_from_text(text)
        if query:
            return SkybridgeIntent(domain="people", action="show", query=query, context=people_ctx, circle=circle)
    return None


async def resolve_skybridge_request(
    message: str,
    user_id: str,
    *,
    context: dict[str, Any] | None = None,
    db: Any | None = None,
) -> dict[str, Any]:
    """Resolve a typed or spoken Skybridge request into real card contracts."""
    intent = classify_skybridge_intent(message, context)
    if intent is None:
        return {
            "handled": False,
            "intent": None,
            "spoken_summary": "",
            "cards": [],
            "skybridge_context": context or {},
        }

    if skybridge_intent_requires_identity(intent) and _is_guest_user(user_id):
        return _attach_skybridge_context(_auth_required_result(intent))

    if intent.domain == "clock":
        return _attach_skybridge_context(_resolve_clock(intent))

    if intent.domain == "timer":
        return _attach_skybridge_context(_resolve_timer(intent, user_id))

    if intent.domain == "music":
        from music_service import resolve_music
        return _attach_skybridge_context(await resolve_music(intent))

    if db is not None:
        return _attach_skybridge_context(await _resolve_with_db(intent, user_id, db, context=context))

    async with get_db_ctx() as ctx_db:
        return _attach_skybridge_context(await _resolve_with_db(intent, user_id, ctx_db, context=context))


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


def skybridge_intent_requires_identity(intent: SkybridgeIntent | None) -> bool:
    """Return whether a Skybridge intent reads or mutates personal user data."""
    return bool(intent and intent.domain in {"calendar", "lists", "people"})


# Music context anchors: a word clearly about audio playback. "play <x>" is only
# treated as music when paired with one of these OR a clear play verb, so
# "play a game" / "play along" never steal the music domain.
_MUSIC_CTX = re.compile(r"\b(music|song|songs|track|tune|tunes|album|playlist|artist|radio|spotify|now\s+playing)\b")
_MUSIC_STOPWORDS = re.compile(r"\b(play\s+(?:a\s+)?(?:game|along|nice|fair|pretend|dead|catch))\b")


def _classify_music(text: str) -> "SkybridgeIntent | None":
    """Classify a music command for the Music Assistant bridge. `text` is the
    lowercased message padded with surrounding spaces."""
    if _MUSIC_STOPWORDS.search(text):
        return None
    has_ctx = bool(_MUSIC_CTX.search(text))
    # Transport / volume — only with clear music context so bare "pause"/"next"
    # (which could be timers, reading, etc.) don't get hijacked.
    if has_ctx or re.search(r"\bplaying\b", text):
        if re.search(r"\b(pause|hold)\b", text):
            return SkybridgeIntent(domain="music", action="pause")
        if re.search(r"\b(resume|unpause|keep playing|carry on)\b", text):
            return SkybridgeIntent(domain="music", action="resume")
        if re.search(r"\b(next|skip|forward)\b", text):
            return SkybridgeIntent(domain="music", action="next")
        if re.search(r"\b(previous|back|last)\s+(?:song|track|tune)\b|\bgo back\b", text):
            return SkybridgeIntent(domain="music", action="previous")
        if re.search(r"\b(stop|turn off)\b", text):
            return SkybridgeIntent(domain="music", action="stop")
        if re.search(r"\b(up|louder|higher|turn it up)\b", text):
            return SkybridgeIntent(domain="music", action="volume_up")
        if re.search(r"\b(down|quieter|lower|softer|turn it down)\b", text):
            return SkybridgeIntent(domain="music", action="volume_down")
    # Play X — "play some jazz", "put on the beatles", "play the news".
    m = re.search(r"\b(?:play|put on|start playing|listen to)\s+(?:some\s+|the\s+|a\s+)?(.+?)\s*$", text)
    if m and (has_ctx or re.search(r"\b(play|put on|listen to|start playing)\b", text)):
        query = m.group(1).strip()
        # "play music" / "play some music" with no real target → just show/resume.
        if query in ("music", "something", "a song", "songs", "tunes", "some tunes", ""):
            return SkybridgeIntent(domain="music", action="status")
        return SkybridgeIntent(domain="music", action="play", query=query)
    # Status: "what's playing", "now playing", "show music", bare "music".
    if (has_ctx or re.search(r"\bplaying\b", text)) and re.search(r"\b(what|show|see|playing|song|music)\b", text):
        return SkybridgeIntent(domain="music", action="status")
    return None


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
        if intent.action == "remember_fact":
            return await _resolve_people_remember_fact(intent, user_id, db)
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
    refreshed = await _resolve_calendar(SkybridgeIntent("calendar", "show", start.isoformat(), start, start), user_id, db)
    refreshed["intent"] = {"domain": "calendar", "action": "create_event", "event_id": event_id}
    refreshed["spoken_summary"] = (
        f"Added {intent.title} to your calendar." if intent.all_day
        else f"Added {intent.title} at {intent.target_time}."
    )
    refreshed["actions"] = [{"type": "created", "domain": "calendar", "id": event_id}]
    return refreshed


def _score_event_for_target(event: dict[str, Any], target: str) -> int:
    score = 0
    target_time = _parse_time(target)
    if target_time and str(event.get("start_time") or "")[:5] == target_time:
        score += 4
    target_tokens = {token for token in re.split(r"\W+", target.lower()) if token and token not in {"my", "the", "appointment", "event"}}
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
    if user_id not in {"guest", "voice-guest"}:
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
    spoken = f"You have {len(events)} {event_word} {qualifier}."
    card = card_service.convert_emit(
        card_service.build_calendar_timeline_card(
            {
                "qualifier": qualifier,
                "date": start.isoformat(),
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "events": events,
                "summary": spoken,
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

    def _say_num(n) -> str:
        s = str(n)
        return s.replace(".", " point ") if "." in s else s

    if intent.action == "forecast":
        card = card_service.build_weather_forecast_card(
            {"current": current, "forecast": forecast, "location": {"city": city, "country": country}}
        )
        spoken = f"Here is the forecast for {city}."
    else:
        card = card_service.build_weather_current_card(
            {"current": current, "forecast": forecast, "location": {"city": city, "country": country}}
        )
        temp = current.get("temp")
        desc = current.get("description") or "current conditions"
        # Speak naturally: "18.3" → "18 point 3" (bare decimals get mangled to "18 3"),
        # and join the condition with "and" rather than the robotic "with".
        spoken = (
            f"It's {_say_num(temp)} degrees and {desc} in {city}."
            if temp is not None else f"Here is the weather for {city}."
        )
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
    items = selected.get("items", []) if selected else []
    list_name = selected.get("name") if selected else (list_type.title() if list_type else "Lists")
    summary_count = len(items) if selected else sum(item.get("item_count", 0) for item in lists)
    open_count = selected.get("open_count", 0) if selected else sum(item.get("open_count", 0) for item in lists)
    completed_count = selected.get("completed_count", 0) if selected else sum(item.get("completed_count", 0) for item in lists)
    if selected and items:
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


async def _ensure_default_lists(user_id: str, db: Any) -> None:
    rows = await db.fetch(
        """
        SELECT list_type, name
        FROM lists
        WHERE user_id = $1 AND deleted = 0
        """,
        user_id,
    )
    existing_rows = [dict(row) for row in rows]
    existing_types = {str(row.get("list_type") or "").strip().lower() for row in existing_rows}
    for list_type, name, description, visibility in DEFAULT_USER_LISTS:
        if list_type in existing_types:
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
        SELECT id, user_id, name, list_type, description, visibility, created_at, updated_at
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
    catalog: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        item = dict(row)
        if item.get("user_id") != user_id and item.get("list_type") != "shopping":
            continue
        key = (str(item.get("list_type") or ""), str(item.get("name") or "").strip().lower())
        if key in seen:
            continue
        seen.add(key)
        catalog.append(item)
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
        SELECT id, name, list_type
        FROM lists
        WHERE list_type = $2 AND deleted = 0
          AND (visibility = 'family' OR user_id = $1)
        ORDER BY
          CASE WHEN user_id = $1 THEN 0 ELSE 1 END,
          updated_at DESC
        LIMIT 1
        """,
        user_id,
        list_type,
    )
    if rows:
        row = dict(rows[0])
        return str(row["id"]), str(row.get("list_type") or list_type)
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
    return [item for item in items if target in str(item.get("text") or "").strip().lower()]


async def _find_list_for_remove(intent: SkybridgeIntent, user_id: str, db: Any, context: dict[str, Any] | None) -> tuple[str, str] | None:
    """Locate an existing list (never create) for a removal, mirroring _find_or_create_list's lookups."""
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
        if rows:
            row = dict(rows[0])
            return str(row["id"]), str(row.get("list_type") or list_type)
    rows = await db.fetch(
        """
        SELECT id, name, list_type
        FROM lists
        WHERE list_type = $2 AND deleted = 0
          AND (visibility = 'family' OR user_id = $1)
        ORDER BY
          CASE WHEN user_id = $1 THEN 0 ELSE 1 END,
          updated_at DESC
        LIMIT 1
        """,
        user_id,
        list_type,
    )
    if rows:
        row = dict(rows[0])
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
        refreshed = await _resolve_lists(SkybridgeIntent(domain="lists", action="show", list_type=list_type), user_id, db)
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
        refreshed = await _resolve_lists(SkybridgeIntent(domain="lists", action="show", list_type=list_type), user_id, db)
        refreshed["intent"] = {"domain": "lists", "action": "remove_item", "list_type": list_type}
        refreshed["spoken_summary"] = "I could not remove that item."
        refreshed["cards"] = [_status_card("I could not remove that item", "The list item is no longer available. Show your lists again to confirm.")] + list(refreshed.get("cards") or [])
        refreshed["actions"] = []
        return refreshed
    await _maybe_commit(db)
    item_text = str(target_item.get("text") or intent.item_text)
    refreshed = await _resolve_lists(SkybridgeIntent(domain="lists", action="show", list_type=list_type), user_id, db)
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
        refreshed = await _resolve_lists(SkybridgeIntent(domain="lists", action="show", list_type=list_type), user_id, db)
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
        refreshed = await _resolve_lists(SkybridgeIntent(domain="lists", action="show", list_type=list_type), user_id, db)
        refreshed["intent"] = {"domain": "lists", "action": "complete_item", "list_type": list_type}
        refreshed["spoken_summary"] = "I could not update that item."
        refreshed["cards"] = [_status_card("I could not update that item", "The list item is no longer available. Show your lists again to confirm.")] + list(refreshed.get("cards") or [])
        refreshed["actions"] = []
        return refreshed
    await _maybe_commit(db)
    item_text = str(target_item.get("text") or intent.item_text)
    refreshed = await _resolve_lists(SkybridgeIntent(domain="lists", action="show", list_type=list_type), user_id, db)
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
