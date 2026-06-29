import os
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from routers import weather


@pytest.mark.asyncio
async def test_openmeteo_forecast_filters_hourly_and_today_in_provider_timezone(monkeypatch):
    perth = ZoneInfo("Australia/Perth")
    monkeypatch.setattr(weather, "_weather_now", lambda tz: datetime(2026, 6, 29, 7, 0, tzinfo=perth))
    weather._weather_cache.clear()
    weather._weather_cache["current"] = {
        "_hourly_raw": {
            "timezone": "Australia/Perth",
            "times": [
                "2026-06-29T06:00",
                "2026-06-29T07:00",
                "2026-06-29T08:00",
            ],
            "temps": [11, 12, 13],
            "codes": [0, 1, 2],
        },
        "_daily_raw": {
            "time": ["2026-06-29", "2026-06-30", "2026-07-01"],
            "temperature_2m_max": [21, 22, 23],
            "temperature_2m_min": [10, 11, 12],
            "weathercode": [0, 1, 2],
        },
    }

    forecast = await weather._fetch_openmeteo_forecast(-31.95, 115.86)

    assert [hour["time"] for hour in forecast["hourly"]] == ["2026-06-29T07:00", "2026-06-29T08:00"]
    assert [day["day"] for day in forecast["daily"]] == ["2026-06-30", "2026-07-01"]


@pytest.mark.asyncio
async def test_openweather_forecast_filters_by_remote_location_timezone(monkeypatch):
    perth = ZoneInfo("Australia/Perth")
    monkeypatch.setattr(weather, "_weather_now", lambda tz: datetime(2026, 6, 29, 7, 0, tzinfo=perth))

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "city": {"timezone": 8 * 3600},
                "list": [
                    {
                        "dt": 1782685200,  # 2026-06-29 06:20 Australia/Perth
                        "main": {"temp": 11},
                        "weather": [{"description": "past", "icon": "01d"}],
                    },
                    {
                        "dt": 1782688800,  # 2026-06-29 07:20 Australia/Perth
                        "main": {"temp": 12},
                        "weather": [{"description": "current", "icon": "01d"}],
                    },
                    {
                        "dt": 1782689100,  # 2026-06-29 07:25 Australia/Perth
                        "main": None,
                        "weather": None,
                    },
                    {
                        "dt_txt": "not-a-timestamp",
                        "main": {"temp": 99},
                        "weather": [{"description": "bad timestamp", "icon": "50d"}],
                    },
                    {
                        "dt": 1782789600,  # 2026-06-30 11:20 Australia/Perth
                        "main": {"temp": 22},
                        "weather": {"description": "tomorrow", "icon": "02d"},
                    },
                ],
            }

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr(weather.httpx, "AsyncClient", FakeClient)

    forecast = await weather._fetch_owm_forecast(-31.95, 115.86)

    assert [hour["description"] for hour in forecast["hourly"]] == ["current", None, "tomorrow"]
    assert [day["day"] for day in forecast["daily"]] == ["2026-06-30"]


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_timezone", ["UTC", 999999999])
async def test_openweather_forecast_bad_provider_timezone_keeps_valid_rows(monkeypatch, provider_timezone):
    monkeypatch.setattr(weather, "_weather_now", lambda tz: datetime(2026, 6, 28, 23, 0, tzinfo=timezone.utc))

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "city": {"timezone": provider_timezone},
                "list": [
                    {
                        "dt": 1782691200,  # 2026-06-29 00:00 UTC
                        "main": {"temp": 12},
                        "weather": [{"description": "valid", "icon": "01d"}],
                    }
                ],
            }

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr(weather.httpx, "AsyncClient", FakeClient)

    forecast = await weather._fetch_owm_forecast(-31.95, 115.86)

    assert [hour["description"] for hour in forecast["hourly"]] == ["valid"]
