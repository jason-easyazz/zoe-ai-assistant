"""
FastAPI router for weather.
Mounted at prefix="/api/weather" with tag "weather".
"""
import os
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException

from auth import get_current_user
from database import get_db
from models import WeatherPreferences
from push import broadcaster

router = APIRouter(prefix="/api/weather", tags=["weather"])

OPENWEATHERMAP_API_KEY = os.environ.get("OPENWEATHERMAP_API_KEY", "")
_weather_cache: dict = {}
_cache_ttl_seconds = 600  # 10 minutes


def _row_to_prefs(row) -> Optional[dict]:
    """Convert weather_preferences row to dict."""
    if row is None:
        return None
    d = dict(row)
    if "use_current_location" in d and d["use_current_location"] is not None:
        d["use_current_location"] = bool(d["use_current_location"])
    return d


async def _fetch_current_weather(lat: Optional[float], lon: Optional[float], city: Optional[str]) -> dict:
    """Fetch current weather from OpenWeatherMap or return cached/empty."""
    if not OPENWEATHERMAP_API_KEY:
        return _weather_cache.get("current", {"cached": False, "error": "No API key configured"})

    params = {"appid": OPENWEATHERMAP_API_KEY, "units": "metric"}
    if lat is not None and lon is not None:
        params["lat"] = lat
        params["lon"] = lon
    elif city:
        params["q"] = city
    else:
        return _weather_cache.get("current", {"cached": False, "error": "No location configured"})

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                "https://api.openweathermap.org/data/2.5/weather",
                params=params,
            )
            r.raise_for_status()
            data = r.json()
            result = {
                "temp": data.get("main", {}).get("temp"),
                "feels_like": data.get("main", {}).get("feels_like"),
                "humidity": data.get("main", {}).get("humidity"),
                "description": data.get("weather", [{}])[0].get("description") if data.get("weather") else None,
                "icon": data.get("weather", [{}])[0].get("icon") if data.get("weather") else None,
                "city": data.get("name"),
                "country": data.get("sys", {}).get("country"),
            }
            _weather_cache["current"] = result
            return result
    except Exception as e:
        return _weather_cache.get("current", {"cached": False, "error": str(e)})


async def _fetch_forecast(lat: Optional[float], lon: Optional[float], city: Optional[str]) -> dict:
    """Fetch forecast from OpenWeatherMap or return cached/empty."""
    if not OPENWEATHERMAP_API_KEY:
        return _weather_cache.get("forecast", {"cached": False, "error": "No API key configured"})

    params = {"appid": OPENWEATHERMAP_API_KEY, "units": "metric"}
    if lat is not None and lon is not None:
        params["lat"] = lat
        params["lon"] = lon
    elif city:
        params["q"] = city
    else:
        return _weather_cache.get("forecast", {"cached": False, "error": "No location configured"})

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                "https://api.openweathermap.org/data/2.5/forecast",
                params=params,
            )
            r.raise_for_status()
            data = r.json()
            list_data = data.get("list", [])[:5]
            result = {
                "list": [
                    {
                        "dt": item.get("dt"),
                        "temp": item.get("main", {}).get("temp"),
                        "description": item.get("weather", [{}])[0].get("description") if item.get("weather") else None,
                        "icon": item.get("weather", [{}])[0].get("icon") if item.get("weather") else None,
                    }
                    for item in list_data
                ],
            }
            _weather_cache["forecast"] = result
            return result
    except Exception as e:
        return _weather_cache.get("forecast", {"cached": False, "error": str(e)})


@router.get("/", response_model=dict)
@router.get("/current", response_model=dict)
async def get_current_weather(
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Get current weather. Uses OpenWeatherMap or returns cached."""
    user_id = user["user_id"]
    cursor = await db.execute(
        "SELECT * FROM weather_preferences WHERE user_id = ?",
        [user_id],
    )
    row = await cursor.fetchone()
    prefs = _row_to_prefs(row) if row else {}
    lat = prefs.get("latitude") if prefs else None
    lon = prefs.get("longitude") if prefs else None
    city = prefs.get("city") if prefs else None
    return await _fetch_current_weather(lat, lon, city)


@router.get("/forecast", response_model=dict)
async def get_forecast(
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Get weather forecast."""
    user_id = user["user_id"]
    cursor = await db.execute(
        "SELECT * FROM weather_preferences WHERE user_id = ?",
        [user_id],
    )
    row = await cursor.fetchone()
    prefs = _row_to_prefs(row) if row else {}
    lat = prefs.get("latitude") if prefs else None
    lon = prefs.get("longitude") if prefs else None
    city = prefs.get("city") if prefs else None
    return await _fetch_forecast(lat, lon, city)


@router.get("/preferences", response_model=dict)
async def get_preferences(
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Get user weather preferences from weather_preferences table."""
    user_id = user["user_id"]
    cursor = await db.execute(
        "SELECT * FROM weather_preferences WHERE user_id = ?",
        [user_id],
    )
    row = await cursor.fetchone()
    if row is None:
        return {}
    return _row_to_prefs(row)


@router.post("/preferences", response_model=dict)
async def update_preferences(
    payload: WeatherPreferences,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Update weather preferences (upsert)."""
    user_id = user["user_id"]
    await db.execute(
        """INSERT INTO weather_preferences (
            user_id, latitude, longitude, city, country,
            temperature_unit, use_current_location, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(user_id) DO UPDATE SET
            latitude = excluded.latitude,
            longitude = excluded.longitude,
            city = excluded.city,
            country = excluded.country,
            temperature_unit = excluded.temperature_unit,
            use_current_location = excluded.use_current_location,
            updated_at = datetime('now')
        """,
        (
            user_id,
            payload.latitude,
            payload.longitude,
            payload.city,
            payload.country,
            payload.temperature_unit,
            1 if payload.use_current_location else 0,
        ),
    )
    await db.commit()

    cursor = await db.execute(
        "SELECT * FROM weather_preferences WHERE user_id = ?",
        [user_id],
    )
    row = await cursor.fetchone()
    prefs = _row_to_prefs(row)

    await broadcaster.broadcast("weather", "preferences_updated", prefs)
    return prefs


@router.get("/location/search", response_model=dict)
async def search_locations(
    user: dict = Depends(get_current_user),
):
    """Search locations. Stub returning empty list."""
    return {"results": []}
