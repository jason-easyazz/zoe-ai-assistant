"""Server-side resolver for Skybridge data cards."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from calendar_utils import row_to_event
from card_service import card_service
from database import get_db_ctx
from people_utils import row_to_person


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
    target_time: str = ""
    title: str = ""
    target_text: str = ""
    person_name: str = ""
    fact_text: str = ""
    birthday: str = ""


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


def _today() -> date:
    return date.today()


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


def _context_domain(context: dict[str, Any] | None) -> str:
    if not isinstance(context, dict):
        return ""
    intent = context.get("intent") if isinstance(context.get("intent"), dict) else {}
    return str(intent.get("domain") or context.get("domain") or "").strip().lower()


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
        hour += 12
    return f"{hour:02d}:{minute:02d}"


def _clean_action_text(value: str) -> str:
    text = re.sub(r"\s+", " ", value or "").strip(" .")
    return re.sub(r"^(?:my|the|a|an)\s+", "", text, flags=re.IGNORECASE).strip()


def _list_add_from_text(message: str) -> tuple[str, str] | None:
    match = re.search(
        r"\badd\s+(?P<item>.+?)\s+to\s+(?:the\s+|my\s+)?(?P<list>shopping list|grocery list|groceries|list|task list|todo list|tasks|todos)\b",
        message,
        re.IGNORECASE,
    )
    if not match:
        return None
    item = _clean_action_text(match.group("item"))
    list_phrase = f" {match.group('list').lower()} "
    list_type = _list_type_from_text(list_phrase) or ("shopping" if "grocery" in list_phrase else "")
    return item, list_type or "shopping"


def _calendar_create_from_text(message: str, context: dict[str, Any] | None) -> tuple[str, str] | None:
    match = re.search(r"\badd\s+(?P<title>.+?)\s+(?:at|for)\s+(?P<time>\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)?)\b", message, re.IGNORECASE)
    if not match:
        return None
    if _context_domain(context) != "calendar" and not re.search(r"\b(calendar|schedule|event|appointment|meeting)\b", message, re.IGNORECASE):
        return None
    title = _clean_action_text(match.group("title"))
    target_time = _parse_time(match.group("time"))
    return (title, target_time) if title and target_time else None


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
    target_time = _parse_time(match.group("time"))
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
    name = match.group("name").strip().title()
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
    calendar_update = _calendar_update_from_text(raw_message, context)
    if calendar_update:
        target, target_time = calendar_update
        return SkybridgeIntent(domain="calendar", action="update_time", target_text=target, target_time=target_time)
    list_add = _list_add_from_text(raw_message)
    if list_add:
        item, list_type = list_add
        return SkybridgeIntent(domain="lists", action="add_item", item_text=item, list_type=list_type)
    calendar_create = _calendar_create_from_text(raw_message, context)
    if calendar_create:
        title, target_time = calendar_create
        parsed_range = _calendar_date_from_text(text, _today())
        start = parsed_range[1] if parsed_range else _context_calendar_date(context)
        return SkybridgeIntent(domain="calendar", action="create_event", title=title, target_time=target_time, range_label=start.isoformat(), start_date=start, end_date=start)
    if any(term in text for term in (" weather", " forecast", " temperature", " rain", " windy", " wind ")):
        action = "forecast" if any(term in text for term in ("forecast", "tomorrow", "week", "next few days")) else "current"
        return SkybridgeIntent(domain="weather", action=action)
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
    if any(term in text for term in (" list", " lists", " shopping", " groceries", " grocery", " tasks", " todos")):
        list_type = _list_type_from_text(text)
        return SkybridgeIntent(domain="lists", action="show", list_type=list_type)
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

    if db is not None:
        return _attach_skybridge_context(await _resolve_with_db(intent, user_id, db, context=context))

    async with get_db_ctx() as ctx_db:
        return _attach_skybridge_context(await _resolve_with_db(intent, user_id, ctx_db, context=context))


def _attach_skybridge_context(result: dict[str, Any]) -> dict[str, Any]:
    if result.get("handled"):
        result["skybridge_context"] = {
            "intent": result.get("intent") or {},
            "cards": result.get("cards") or [],
        }
    return result


async def _resolve_with_db(intent: SkybridgeIntent, user_id: str, db: Any, *, context: dict[str, Any] | None = None) -> dict[str, Any]:
    if intent.domain == "calendar":
        if intent.action == "create_event":
            return await _resolve_calendar_create(intent, user_id, db)
        if intent.action == "update_time":
            return await _resolve_calendar_update_time(intent, user_id, db, context)
        return await _resolve_calendar(intent, user_id, db)
    if intent.domain == "weather":
        return await _resolve_weather(intent, user_id, db)
    if intent.domain == "lists":
        if intent.action == "add_item":
            return await _resolve_list_add_item(intent, user_id, db, context)
        return await _resolve_lists(intent, user_id, db)
    if intent.domain == "people":
        if intent.action == "remember_fact":
            return await _resolve_people_remember_fact(intent, user_id, db)
        return await _resolve_people(intent, user_id, db)
    return {"handled": False, "intent": None, "spoken_summary": "", "cards": []}


async def _maybe_commit(db: Any) -> None:
    commit = getattr(db, "commit", None)
    if callable(commit):
        await commit()


def _affected_rows(result: Any) -> int | None:
    if result is None:
        return None
    if isinstance(result, int):
        return result
    match = re.search(r"\b(?:INSERT|UPDATE|DELETE)\s+(\d+)\b", str(result))
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
        "target_time": intent.target_time,
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
        intent.target_time,
        start.isoformat(),
        "",
        None,
        "general",
        None,
        0,
        None,
        None,
        "family",
    )
    await _maybe_commit(db)
    refreshed = await _resolve_calendar(SkybridgeIntent("calendar", "show", start.isoformat(), start, start), user_id, db)
    refreshed["intent"] = {"domain": "calendar", "action": "create_event", "event_id": event_id}
    refreshed["spoken_summary"] = f"Added {intent.title} at {intent.target_time}."
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
        "UPDATE events SET start_time = $1, updated_at = NOW() WHERE id = $2 AND user_id = $3 AND deleted = 0",
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


async def _resolve_calendar(intent: SkybridgeIntent, user_id: str, db: Any) -> dict[str, Any]:
    start = intent.start_date or date.today()
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
    current = await _get_current(lat, lon, city, country)
    current = {k: v for k, v in current.items() if not str(k).startswith("_")}
    forecast = {}
    if intent.action == "forecast":
        forecast = await _get_forecast(lat, lon)
        card = card_service.build_weather_forecast_card(
            {"current": current, "forecast": forecast, "location": {"city": city, "country": country}}
        )
        spoken = f"Here is the forecast for {city}."
    else:
        forecast = await _get_forecast(lat, lon)
        card = card_service.build_weather_current_card(
            {"current": current, "forecast": forecast, "location": {"city": city, "country": country}}
        )
        temp = current.get("temp")
        desc = current.get("description") or "current conditions"
        spoken = f"It is {temp} degrees in {city} with {desc}." if temp is not None else f"Here is the weather for {city}."
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

    list_type = intent.list_type
    if list_type:
        rows = await db.fetch(
            """
            SELECT id, user_id, name, list_type, description, visibility, created_at, updated_at
            FROM lists
            WHERE list_type = $2 AND deleted = 0
              AND (visibility = 'family' OR user_id = $1)
            ORDER BY updated_at DESC
            LIMIT 8
            """,
            user_id,
            list_type,
        )
    else:
        rows = await db.fetch(
            """
            SELECT id, user_id, name, list_type, description, visibility, created_at, updated_at
            FROM lists
            WHERE deleted = 0
              AND (visibility = 'family' OR user_id = $1)
            ORDER BY updated_at DESC
            LIMIT 8
            """,
            user_id,
        )
    lists = [dict(row) for row in rows]
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
            if len(bucket) < 24:
                bucket.append(item)

    selected = lists[0] if len(lists) == 1 else None
    for list_row in lists:
        items = items_by_list.get(list_row["id"], [])
        list_row["items"] = items if selected else []
        list_row["item_count"] = len(items)
        list_row["open_count"] = sum(1 for item in items if not item.get("completed"))
        list_row["completed_count"] = len(items) - list_row["open_count"]

    items = selected.get("items", []) if selected else []
    list_name = selected.get("name") if selected else (list_type.title() if list_type else "Lists")
    summary_count = len(items) if selected else sum(item.get("item_count", 0) for item in lists)
    spoken = f"{list_name} has {summary_count} item{'s' if summary_count != 1 else ''}."
    card = card_service.build_shopping_list_card(
        {
            "list_id": selected.get("id") if selected else "lists-overview",
            "list_name": list_name,
            "list_type": selected.get("list_type") if selected else (list_type or "all"),
            "lists": lists,
            "items": items,
            "summary": spoken,
        }
    )
    return {
        "handled": True,
        "intent": {"domain": "lists", "action": intent.action, "list_type": list_type},
        "spoken_summary": spoken,
        "cards": [card_service.convert_emit(card, target_major=1)],
    }


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
        ORDER BY updated_at DESC
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
        """,
        list_id,
        user_id,
        list_name,
        list_type,
        "",
        "family",
    )
    await _maybe_commit(db)
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
    refreshed["intent"] = {"domain": "lists", "action": "add_item", "list_type": list_type, "item_id": item_id}
    refreshed["spoken_summary"] = f"Added {intent.item_text} to the {list_type} list."
    refreshed["actions"] = [{"type": "created", "domain": "lists", "id": item_id, "list_id": list_id}]
    return refreshed


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
