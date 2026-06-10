"""Tests for Skybridge real data card resolution."""

from __future__ import annotations

import os
import sys
from datetime import date

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

    async def fetch(self, *_args):
        return self.events

    async def execute(self, *_args):
        return Cursor(self.prefs)


class GuardedGuestDb(FakeDb):
    async def fetch(self, *_args):
        raise AssertionError("guest calendar requests must not fetch family events")


def test_classify_calendar_and_weather_requests():
    assert classify_skybridge_intent("show my calendar").domain == "calendar"
    assert classify_skybridge_intent("show me the weather").domain == "weather"
    assert classify_skybridge_intent("open settings") is None


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
