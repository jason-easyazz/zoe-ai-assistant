"""Server-side resolver for Skybridge data cards."""

from __future__ import annotations

import re
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


def classify_skybridge_intent(message: str) -> SkybridgeIntent | None:
    """Classify only domains that Skybridge can resolve to real data cards."""
    text = f" {(message or '').lower()} "
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
        query, context, circle = _people_filters_from_text(text)
        if " family " in text and not query:
            query = "family"
        if " friends " in text and not query:
            query = "friend"
        return SkybridgeIntent(domain="people", action="show", query=query, context=context, circle=circle)
    if re.search(r"\b(?:find|search for|look up|show)\s+(?:my\s+)?[a-z][a-z .'-]{1,80}\b", text):
        query, context, circle = _people_filters_from_text(text)
        if query:
            return SkybridgeIntent(domain="people", action="show", query=query, context=context, circle=circle)
    return None


async def resolve_skybridge_request(
    message: str,
    user_id: str,
    *,
    db: Any | None = None,
) -> dict[str, Any]:
    """Resolve a typed or spoken Skybridge request into real card contracts."""
    intent = classify_skybridge_intent(message)
    if intent is None:
        return {
            "handled": False,
            "intent": None,
            "spoken_summary": "",
            "cards": [],
        }

    if db is not None:
        return await _resolve_with_db(intent, user_id, db)

    async with get_db_ctx() as ctx_db:
        return await _resolve_with_db(intent, user_id, ctx_db)


async def _resolve_with_db(intent: SkybridgeIntent, user_id: str, db: Any) -> dict[str, Any]:
    if intent.domain == "calendar":
        return await _resolve_calendar(intent, user_id, db)
    if intent.domain == "weather":
        return await _resolve_weather(intent, user_id, db)
    if intent.domain == "lists":
        return await _resolve_lists(intent, user_id, db)
    if intent.domain == "people":
        return await _resolve_people(intent, user_id, db)
    return {"handled": False, "intent": None, "spoken_summary": "", "cards": []}


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
