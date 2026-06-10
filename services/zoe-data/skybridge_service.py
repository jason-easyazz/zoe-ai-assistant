"""Server-side resolver for Skybridge data cards."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from card_service import card_service
from database import get_db_ctx
from routers.calendar import _row_to_event
from routers.weather import (
    _get_current,
    _get_forecast,
    _get_system_default_location,
    _resolve_location,
    _row_to_prefs,
)


@dataclass(frozen=True)
class SkybridgeIntent:
    domain: str
    action: str
    range_label: str = ""
    start_date: date | None = None
    end_date: date | None = None


def classify_skybridge_intent(message: str) -> SkybridgeIntent | None:
    """Classify only domains that Skybridge can resolve to real data cards."""
    text = f" {(message or '').lower()} "
    if any(term in text for term in (" weather", " forecast", " temperature", " rain", " windy", " wind ")):
        action = "forecast" if any(term in text for term in ("forecast", "tomorrow", "week", "next few days")) else "current"
        return SkybridgeIntent(domain="weather", action=action)
    if any(term in text for term in (" calendar", " schedule", " events", " appointments", " agenda")):
        today = date.today()
        if "tomorrow" in text:
            target = today + timedelta(days=1)
            return SkybridgeIntent("calendar", "show", "tomorrow", target, target)
        if any(term in text for term in ("this week", "week", "upcoming", "next few days")):
            return SkybridgeIntent("calendar", "show", "this week", today, today + timedelta(days=7))
        return SkybridgeIntent("calendar", "show", "today", today, today)
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
        events = [_row_to_event(row) for row in rows]
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
    cursor = await db.execute("SELECT * FROM weather_preferences WHERE user_id = ?", [user_id])
    prefs = _row_to_prefs(await cursor.fetchone())
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
