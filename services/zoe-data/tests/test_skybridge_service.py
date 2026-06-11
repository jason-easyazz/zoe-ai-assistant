"""Tests for Skybridge real data card resolution."""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import skybridge_service  # noqa: E402
from skybridge_service import classify_skybridge_intent, resolve_skybridge_request  # noqa: E402


class Cursor:
    def __init__(self, row=None):
        self.row = row

    async def fetchone(self):
        return self.row


class FakeDb:
    def __init__(self, *, events=None, prefs=None):
        self.events = events or []
        self.prefs = prefs
        self.fetch_args = None

    async def fetch(self, *args):
        self.fetch_args = args
        return self.events

    async def fetchrow(self, *_args):
        return self.prefs

    async def execute(self, *_args):
        raise AssertionError("Skybridge service must use asyncpg fetch/fetchrow APIs")


class GuardedGuestDb(FakeDb):
    async def fetch(self, *_args):
        raise AssertionError("guest calendar requests must not fetch family events")


def test_classify_calendar_and_weather_requests():
    assert classify_skybridge_intent("show my calendar").domain == "calendar"
    assert classify_skybridge_intent("show me the weather").domain == "weather"
    assert classify_skybridge_intent("what is happening this week").domain == "calendar"
    assert classify_skybridge_intent("open settings") is None


def test_classify_calendar_date_and_range_requests(monkeypatch):
    class FrozenDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 6, 11)

    monkeypatch.setattr(skybridge_service, "date", FrozenDate)

    dated = classify_skybridge_intent("show me my calendar on the 17th of June")
    assert dated.domain == "calendar"
    assert dated.start_date == date(2026, 6, 17)
    assert dated.end_date == date(2026, 6, 17)
    assert dated.range_label == "17 June 2026"

    week = classify_skybridge_intent("what is happening this week")
    assert week.domain == "calendar"
    assert week.start_date == date(2026, 6, 11)
    assert week.end_date == date(2026, 6, 18)

    tomorrow = classify_skybridge_intent("show me the weather for tomorrow")
    assert tomorrow.domain == "weather"
    assert tomorrow.action == "forecast"

    next_week = classify_skybridge_intent("show my schedule next week")
    assert next_week.domain == "calendar"
    assert next_week.start_date == date(2026, 6, 15)
    assert next_week.end_date == date(2026, 6, 21)
    assert next_week.range_label == "next week"

    iso = classify_skybridge_intent("show my calendar on 2026-06-17")
    assert iso.domain == "calendar"
    assert iso.start_date == date(2026, 6, 17)
    assert iso.end_date == date(2026, 6, 17)
    assert iso.range_label == "2026-06-17"

    iso_after_count = classify_skybridge_intent("show my 10 events on 2026-06-17")
    assert iso_after_count.domain == "calendar"
    assert iso_after_count.start_date == date(2026, 6, 17)
    assert iso_after_count.end_date == date(2026, 6, 17)
    assert iso_after_count.range_label == "2026-06-17"

    weekend = classify_skybridge_intent("what is happening this weekend")
    assert weekend.domain == "calendar"
    assert weekend.range_label == "today"
    assert weekend.start_date == date(2026, 6, 11)
    assert weekend.end_date == date(2026, 6, 11)


@pytest.mark.asyncio
async def test_calendar_request_returns_real_event_card():
    event = {
        "id": "event-1",
        "user_id": "family-admin",
        "title": "Dentist",
        "start_date": date.today().isoformat(),
        "start_time": "09:00",
        "end_time": "09:30",
        "location": "Clinic",
        "category": "health",
        "visibility": "family",
        "deleted": False,
    }

    result = await resolve_skybridge_request("show my calendar", "family-admin", db=FakeDb(events=[event]))

    assert result["handled"] is True
    assert result["intent"]["domain"] == "calendar"
    card = result["cards"][0]
    assert card["producer"] == "zoe-calendar"
    assert card["content"]["source"] == "calendar_show"
    assert card["content"]["events"][0]["title"] == "Dentist"
    assert "Surface" not in str(card)


@pytest.mark.asyncio
async def test_calendar_empty_state_still_returns_data_card():
    result = await resolve_skybridge_request("show my schedule today", "family-admin", db=FakeDb(events=[]))

    assert result["handled"] is True
    card = result["cards"][0]
    assert card["content"]["source"] == "calendar_show"
    assert card["content"]["events"] == []
    assert "0 events" in result["spoken_summary"]


@pytest.mark.asyncio
async def test_calendar_explicit_date_queries_requested_day(monkeypatch):
    class FrozenDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 6, 11)

    monkeypatch.setattr(skybridge_service, "date", FrozenDate)

    db = FakeDb(events=[])
    result = await resolve_skybridge_request("show my calendar on the 17th of June", "family-admin", db=db)

    assert result["handled"] is True
    assert result["intent"]["range"] == "17 June 2026"
    assert db.fetch_args[2] == "2026-06-17"
    assert db.fetch_args[3] == "2026-06-17"
    assert result["cards"][0]["content"]["qualifier"] == "17 June 2026"


@pytest.mark.asyncio
async def test_calendar_happening_this_week_queries_range(monkeypatch):
    class FrozenDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 6, 11)

    monkeypatch.setattr(skybridge_service, "date", FrozenDate)

    db = FakeDb(events=[])
    result = await resolve_skybridge_request("what is happening this week", "family-admin", db=db)

    assert result["handled"] is True
    assert result["intent"]["range"] == "this week"
    assert db.fetch_args[2] == "2026-06-11"
    assert db.fetch_args[3] == (date(2026, 6, 11) + timedelta(days=7)).isoformat()


@pytest.mark.asyncio
async def test_guest_calendar_request_does_not_fetch_family_events():
    result = await resolve_skybridge_request("show my calendar", "guest", db=GuardedGuestDb())

    assert result["handled"] is True
    assert result["cards"][0]["content"]["events"] == []


@pytest.mark.asyncio
async def test_weather_current_request_returns_current_card(monkeypatch):
    async def fake_default(_db):
        return {"latitude": -28.7, "longitude": 114.6, "city": "Geraldton", "country": "AU"}

    async def fake_current(_lat, _lon, city, country):
        return {
            "temp": 23.4,
            "feels_like": 22.9,
            "humidity": 55,
            "wind_speed": 4.2,
            "description": "clear sky",
            "city": city,
            "country": country,
        }

    async def fake_forecast(_lat, _lon):
        return {"daily": [{"day": "2026-06-11", "high": 24, "low": 14, "description": "clear"}]}

    monkeypatch.setattr(skybridge_service, "_get_system_default_location", fake_default)
    monkeypatch.setattr(skybridge_service, "_get_current", fake_current)
    monkeypatch.setattr(skybridge_service, "_get_forecast", fake_forecast)

    result = await resolve_skybridge_request("show me the weather", "family-admin", db=FakeDb())

    assert result["handled"] is True
    assert result["intent"] == {"domain": "weather", "action": "current"}
    card = result["cards"][0]
    assert card["producer"] == "zoe-weather"
    assert card["content"]["source"] == "weather_current"
    assert card["content"]["current"]["temp"] == 23.4
    assert card["content"]["forecast"]["daily"][0]["high"] == 24


@pytest.mark.asyncio
async def test_weather_forecast_request_returns_forecast_card(monkeypatch):
    async def fake_default(_db):
        return {"latitude": -28.7, "longitude": 114.6, "city": "Geraldton", "country": "AU"}

    async def fake_current(_lat, _lon, city, country):
        return {"temp": 20, "description": "cloudy", "city": city, "country": country}

    async def fake_forecast(_lat, _lon):
        return {"daily": [{"day": "2026-06-11", "high": 21, "low": 12, "description": "cloudy"}]}

    monkeypatch.setattr(skybridge_service, "_get_system_default_location", fake_default)
    monkeypatch.setattr(skybridge_service, "_get_current", fake_current)
    monkeypatch.setattr(skybridge_service, "_get_forecast", fake_forecast)

    result = await resolve_skybridge_request("show weather forecast", "family-admin", db=FakeDb())

    card = result["cards"][0]
    assert result["intent"] == {"domain": "weather", "action": "forecast"}
    assert card["content"]["source"] == "weather_forecast"
    assert card["content"]["forecast"]["daily"][0]["description"] == "cloudy"
