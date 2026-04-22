"""
FastAPI router for weather.
Mounted at prefix="/api/weather" with tag "weather".

Data sources (priority order):
  1. OpenWeatherMap — if OPENWEATHERMAP_API_KEY is set
  2. Open-Meteo     — free, no API key required (default)

Geocoding (location search):
  1. OpenWeatherMap Geocoding — if API key set
  2. Open-Meteo Geocoding API — free fallback
"""
import os
import json
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException

from auth import get_current_user, require_admin
from database import get_db
from models import WeatherPreferences
from push import broadcaster

router = APIRouter(prefix="/api/weather", tags=["weather"])

OPENWEATHERMAP_API_KEY = os.environ.get("OPENWEATHERMAP_API_KEY", "")
_weather_cache: dict = {}

# Default location: Geraldton, Western Australia
DEFAULT_CITY = "Geraldton"
DEFAULT_COUNTRY = "AU"
DEFAULT_LAT = -28.7774
DEFAULT_LON = 114.6158
SYSTEM_WEATHER_DEFAULT_KEY = "weather_default_location"

# WMO weather code → (description, OWM-style icon code)
_WMO_MAP = {
    0:  ("clear sky",          "01d"),
    1:  ("mainly clear",       "01d"),
    2:  ("partly cloudy",      "02d"),
    3:  ("overcast",           "04d"),
    45: ("fog",                "50d"),
    48: ("icy fog",            "50d"),
    51: ("light drizzle",      "09d"),
    53: ("moderate drizzle",   "09d"),
    55: ("heavy drizzle",      "09d"),
    61: ("light rain",         "10d"),
    63: ("moderate rain",      "10d"),
    65: ("heavy rain",         "10d"),
    71: ("light snow",         "13d"),
    73: ("moderate snow",      "13d"),
    75: ("heavy snow",         "13d"),
    77: ("snow grains",        "13d"),
    80: ("light showers",      "09d"),
    81: ("moderate showers",   "09d"),
    82: ("heavy showers",      "09d"),
    85: ("light snow showers", "13d"),
    86: ("heavy snow showers", "13d"),
    95: ("thunderstorm",       "11d"),
    96: ("thunderstorm + hail","11d"),
    99: ("thunderstorm + hail","11d"),
}


def _wmo_info(code, is_night: bool = False):
    desc, icon = _WMO_MAP.get(code, ("unknown", "01d"))
    if is_night and icon.endswith("d"):
        icon = icon[:-1] + "n"
    return desc, icon


def _row_to_prefs(row) -> Optional[dict]:
    if row is None:
        return None
    d = dict(row)
    if "use_current_location" in d and d["use_current_location"] is not None:
        d["use_current_location"] = bool(d["use_current_location"])
    return d


def _resolve_location(prefs: Optional[dict], fallback: Optional[dict] = None) -> tuple:
    """Return (lat, lon, city, country) with optional system-level fallback."""
    if prefs:
        lat = prefs.get("latitude")
        lon = prefs.get("longitude")
        city = prefs.get("city") or (fallback or {}).get("city") or DEFAULT_CITY
        country = prefs.get("country") or (fallback or {}).get("country") or DEFAULT_COUNTRY
        if lat is not None and lon is not None:
            return lat, lon, city, country
    if fallback:
        lat = fallback.get("latitude")
        lon = fallback.get("longitude")
        city = fallback.get("city") or DEFAULT_CITY
        country = fallback.get("country") or DEFAULT_COUNTRY
        if lat is not None and lon is not None:
            return lat, lon, city, country
    return DEFAULT_LAT, DEFAULT_LON, DEFAULT_CITY, DEFAULT_COUNTRY


async def _get_system_default_location(db) -> dict:
    """Read admin-configured default weather location from DB."""
    try:
        cursor = await db.execute(
            "SELECT value FROM system_preferences WHERE key = ?",
            [SYSTEM_WEATHER_DEFAULT_KEY],
        )
        row = await cursor.fetchone()
        if row and row["value"]:
            parsed = json.loads(row["value"])
            if isinstance(parsed, dict):
                lat = parsed.get("latitude")
                lon = parsed.get("longitude")
                if lat is not None and lon is not None:
                    return {
                        "latitude": float(lat),
                        "longitude": float(lon),
                        "city": parsed.get("city") or DEFAULT_CITY,
                        "country": parsed.get("country") or DEFAULT_COUNTRY,
                        "timezone": parsed.get("timezone") or os.environ.get("ZOE_TIMEZONE", "Australia/Perth"),
                        "source": "db",
                    }
    except Exception:
        pass
    return {
        "latitude": float(os.environ.get("ZOE_LOCATION_LAT", str(DEFAULT_LAT))),
        "longitude": float(os.environ.get("ZOE_LOCATION_LON", str(DEFAULT_LON))),
        "city": os.environ.get("ZOE_LOCATION_CITY", DEFAULT_CITY),
        "country": os.environ.get("ZOE_LOCATION_COUNTRY", DEFAULT_COUNTRY),
        "timezone": os.environ.get("ZOE_TIMEZONE", "Australia/Perth"),
        "source": "env",
    }


# ─── Open-Meteo implementation (no API key) ───────────────────────────────────

async def _fetch_openmeteo_current(lat: float, lon: float, city: str, country: str) -> dict:
    params = {
        "latitude": lat,
        "longitude": lon,
        "current_weather": "true",
        "hourly": "temperature_2m,weathercode,relativehumidity_2m,apparent_temperature,windspeed_10m",
        "daily": "temperature_2m_max,temperature_2m_min,weathercode,sunrise,sunset",
        "timezone": "auto",
        "forecast_days": 6,
        "wind_speed_unit": "ms",  # m/s like OpenWeatherMap metric
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get("https://api.open-meteo.com/v1/forecast", params=params)
            r.raise_for_status()
            data = r.json()

        cw = data.get("current_weather", {})
        hourly = data.get("hourly", {})
        daily_d = data.get("daily", {})

        wmo_code = int(cw.get("weathercode", 0))
        is_night = cw.get("is_day", 1) == 0
        desc, icon = _wmo_info(wmo_code, is_night)

        # Current hour index for humidity/feels-like
        now_iso = cw.get("time", "")
        h_times = hourly.get("time", [])
        cur_idx = next((i for i, t in enumerate(h_times) if t == now_iso), 0)

        humidity   = hourly.get("relativehumidity_2m", [None])[cur_idx] if hourly.get("relativehumidity_2m") else None
        feels_like = hourly.get("apparent_temperature", [None])[cur_idx] if hourly.get("apparent_temperature") else None
        wind_speed = cw.get("windspeed", 0) / 3.6  # km/h → m/s for consistent unit

        # Sunrise/sunset from daily[0]
        sunrise_str = (daily_d.get("sunrise") or [None])[0]
        sunset_str  = (daily_d.get("sunset")  or [None])[0]
        # Open-Meteo returns local ISO: "2025-01-01T06:30" — add Z-offset for JS Date()
        def _fix_iso(s):
            if s and "T" in s and "+" not in s and "Z" not in s:
                return s + ":00"  # keep as-is, JS will parse in local TZ which is correct
            return s

        result = {
            "temp": cw.get("temperature"),
            "feels_like": feels_like,
            "humidity": humidity,
            "wind_speed": round(wind_speed, 2) if wind_speed else None,
            "description": desc,
            "icon": icon,
            "city": city,
            "country": country,
            "sunrise": _fix_iso(sunrise_str),
            "sunset": _fix_iso(sunset_str),
            "_hourly_raw": {
                "times": h_times,
                "temps": hourly.get("temperature_2m", []),
                "codes": hourly.get("weathercode", []),
            },
            "_daily_raw": daily_d,
        }
        _weather_cache["current"] = result
        return result
    except Exception as e:
        return _weather_cache.get("current", {"cached": False, "error": str(e)})


async def _fetch_openmeteo_forecast(lat: float, lon: float) -> dict:
    """Build hourly+daily forecast from Open-Meteo."""
    cached = _weather_cache.get("current", {})
    hourly_raw = cached.get("_hourly_raw") or {}
    daily_raw  = cached.get("_daily_raw") or {}

    # If cache is fresh enough, use it; otherwise re-fetch
    if not hourly_raw:
        params = {
            "latitude": lat, "longitude": lon,
            "hourly": "temperature_2m,weathercode",
            "daily": "temperature_2m_max,temperature_2m_min,weathercode",
            "timezone": "auto", "forecast_days": 6,
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get("https://api.open-meteo.com/v1/forecast", params=params)
                r.raise_for_status()
                data = r.json()
            hourly_raw = {"times": data["hourly"]["time"], "temps": data["hourly"]["temperature_2m"],
                          "codes": data["hourly"]["weathercode"]}
            daily_raw  = data.get("daily", {})
        except Exception as e:
            return _weather_cache.get("forecast", {"hourly": [], "daily": [], "error": str(e)})

    # Hourly: next 8 slots from now
    now = datetime.now()
    h_times = hourly_raw.get("times", [])
    h_temps = hourly_raw.get("temps", [])
    h_codes = hourly_raw.get("codes", [])
    hourly = []
    for i, t in enumerate(h_times):
        try:
            dt = datetime.fromisoformat(t)
            if dt < now:
                continue
        except Exception:
            continue
        code = int(h_codes[i]) if i < len(h_codes) else 0
        desc, icon = _wmo_info(code, dt.hour < 6 or dt.hour >= 20)
        hourly.append({"time": t, "temp": h_temps[i] if i < len(h_temps) else None, "description": desc, "icon": icon})
        if len(hourly) >= 8:
            break

    # Daily: skip today, next 5 days
    today_str = now.strftime("%Y-%m-%d")
    d_dates = daily_raw.get("time", [])
    d_maxes = daily_raw.get("temperature_2m_max", [])
    d_mins  = daily_raw.get("temperature_2m_min", [])
    d_codes = daily_raw.get("weathercode", [])
    daily = []
    for i, date_str in enumerate(d_dates):
        if date_str == today_str:
            continue
        code = int(d_codes[i]) if i < len(d_codes) else 0
        desc, icon = _wmo_info(code)
        daily.append({
            "day": date_str,
            "high": d_maxes[i] if i < len(d_maxes) else None,
            "low":  d_mins[i]  if i < len(d_mins)  else None,
            "description": desc,
            "icon": icon,
        })
        if len(daily) >= 5:
            break

    result = {"hourly": hourly, "daily": daily}
    _weather_cache["forecast"] = result
    return result


# ─── OpenWeatherMap implementation (when API key is set) ──────────────────────

async def _fetch_owm_current(lat: float, lon: float, city: str, country: str) -> dict:
    params = {"appid": OPENWEATHERMAP_API_KEY, "units": "metric", "lat": lat, "lon": lon}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get("https://api.openweathermap.org/data/2.5/weather", params=params)
            r.raise_for_status()
            data = r.json()

        sys_d = data.get("sys", {})
        wind  = data.get("wind", {})
        sr_ts = sys_d.get("sunrise")
        ss_ts = sys_d.get("sunset")

        result = {
            "temp":        data.get("main", {}).get("temp"),
            "feels_like":  data.get("main", {}).get("feels_like"),
            "humidity":    data.get("main", {}).get("humidity"),
            "wind_speed":  wind.get("speed"),
            "wind_deg":    wind.get("deg"),
            "description": (data.get("weather") or [{}])[0].get("description"),
            "icon":        (data.get("weather") or [{}])[0].get("icon"),
            "city":        data.get("name") or city,
            "country":     sys_d.get("country") or country,
            "sunrise": datetime.fromtimestamp(sr_ts, tz=timezone.utc).isoformat() if sr_ts else None,
            "sunset":  datetime.fromtimestamp(ss_ts, tz=timezone.utc).isoformat() if ss_ts else None,
            "pressure":    data.get("main", {}).get("pressure"),
            "visibility":  data.get("visibility"),
        }
        _weather_cache["current"] = result
        return result
    except Exception as e:
        return _weather_cache.get("current", {"cached": False, "error": str(e)})


async def _fetch_owm_forecast(lat: float, lon: float) -> dict:
    params = {"appid": OPENWEATHERMAP_API_KEY, "units": "metric", "cnt": 40, "lat": lat, "lon": lon}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get("https://api.openweathermap.org/data/2.5/forecast", params=params)
            r.raise_for_status()
            data = r.json()
        items = data.get("list", [])

        hourly = []
        for item in items[:8]:
            wa = item.get("weather", [{}])
            hourly.append({
                "time": item.get("dt_txt") or datetime.fromtimestamp(item["dt"], tz=timezone.utc).isoformat(),
                "temp": item.get("main", {}).get("temp"),
                "description": wa[0].get("description") if wa else None,
                "icon": wa[0].get("icon") if wa else None,
            })

        day_buckets: dict = defaultdict(list)
        for item in items:
            day_buckets[item.get("dt_txt", "")[:10]].append(item)

        daily = []
        today_str = datetime.now().strftime("%Y-%m-%d")
        for date_key in sorted(day_buckets.keys()):
            if date_key == today_str:
                continue
            entries = day_buckets[date_key]
            temps = [e.get("main", {}).get("temp") for e in entries if e.get("main", {}).get("temp") is not None]
            midday = min(entries, key=lambda e: abs(int((e.get("dt_txt") or "00:00:00 00")[11:13]) - 12))
            wa = midday.get("weather", [{}])
            daily.append({
                "day":  date_key,
                "high": max(temps) if temps else None,
                "low":  min(temps) if temps else None,
                "description": wa[0].get("description") if wa else None,
                "icon": wa[0].get("icon") if wa else None,
            })
            if len(daily) >= 5:
                break

        result = {"hourly": hourly, "daily": daily}
        _weather_cache["forecast"] = result
        return result
    except Exception as e:
        return _weather_cache.get("forecast", {"hourly": [], "daily": [], "error": str(e)})


# ─── Public route helpers ─────────────────────────────────────────────────────

async def _get_current(lat, lon, city, country):
    if OPENWEATHERMAP_API_KEY:
        return await _fetch_owm_current(lat, lon, city, country)
    return await _fetch_openmeteo_current(lat, lon, city, country)


async def _get_forecast(lat, lon):
    if OPENWEATHERMAP_API_KEY:
        return await _fetch_owm_forecast(lat, lon)
    return await _fetch_openmeteo_forecast(lat, lon)


# ─── Routes ───────────────────────────────────────────────────────────────────

@router.get("/", response_model=dict)
@router.get("/current", response_model=dict)
async def get_current_weather(user: dict = Depends(get_current_user), db=Depends(get_db)):
    cursor = await db.execute("SELECT * FROM weather_preferences WHERE user_id = ?", [user["user_id"]])
    prefs  = _row_to_prefs(await cursor.fetchone())
    fallback = await _get_system_default_location(db)
    lat, lon, city, country = _resolve_location(prefs, fallback=fallback)
    result = await _get_current(lat, lon, city, country)
    # Strip internal cache keys before returning
    return {k: v for k, v in result.items() if not k.startswith("_")}


@router.get("/forecast", response_model=dict)
async def get_forecast(user: dict = Depends(get_current_user), db=Depends(get_db)):
    cursor = await db.execute("SELECT * FROM weather_preferences WHERE user_id = ?", [user["user_id"]])
    prefs  = _row_to_prefs(await cursor.fetchone())
    fallback = await _get_system_default_location(db)
    lat, lon, city, country = _resolve_location(prefs, fallback=fallback)
    # Warm current cache first (needed for Open-Meteo hourly reuse)
    if not _weather_cache.get("current"):
        await _get_current(lat, lon, city, country)
    return await _get_forecast(lat, lon)


@router.get("/preferences", response_model=dict)
async def get_preferences(user: dict = Depends(get_current_user), db=Depends(get_db)):
    cursor = await db.execute("SELECT * FROM weather_preferences WHERE user_id = ?", [user["user_id"]])
    row = await cursor.fetchone()
    if row is None:
        fallback = await _get_system_default_location(db)
        return {
            "city": fallback.get("city", DEFAULT_CITY), "country": fallback.get("country", DEFAULT_COUNTRY),
            "latitude": fallback.get("latitude", DEFAULT_LAT), "longitude": fallback.get("longitude", DEFAULT_LON),
            "temperature_unit": "celsius", "use_current_location": False,
        }
    return _row_to_prefs(row)


@router.post("/preferences", response_model=dict)
async def update_preferences(payload: WeatherPreferences, user: dict = Depends(get_current_user), db=Depends(get_db)):
    user_id = user["user_id"]
    await db.execute(
        """INSERT INTO weather_preferences (
            user_id, latitude, longitude, city, country,
            temperature_unit, use_current_location, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(user_id) DO UPDATE SET
            latitude=excluded.latitude, longitude=excluded.longitude,
            city=excluded.city, country=excluded.country,
            temperature_unit=excluded.temperature_unit,
            use_current_location=excluded.use_current_location,
            updated_at=datetime('now')
        """,
        (user_id, payload.latitude, payload.longitude, payload.city, payload.country,
         payload.temperature_unit, 1 if payload.use_current_location else 0),
    )
    await db.commit()
    _weather_cache.clear()  # force fresh fetch with new location

    cursor = await db.execute("SELECT * FROM weather_preferences WHERE user_id = ?", [user_id])
    prefs = _row_to_prefs(await cursor.fetchone())
    await broadcaster.broadcast("weather", "preferences_updated", prefs)
    return prefs


@router.get("/default-location", response_model=dict)
async def get_default_location(user: dict = Depends(require_admin), db=Depends(get_db)):
    return await _get_system_default_location(db)


@router.put("/default-location", response_model=dict)
async def update_default_location(payload: dict, user: dict = Depends(require_admin), db=Depends(get_db)):
    city = str((payload or {}).get("city", "")).strip()
    country = str((payload or {}).get("country", DEFAULT_COUNTRY)).strip().upper()[:2] or DEFAULT_COUNTRY
    timezone_value = str((payload or {}).get("timezone", os.environ.get("ZOE_TIMEZONE", "Australia/Perth"))).strip()
    try:
        lat = float((payload or {}).get("latitude"))
        lon = float((payload or {}).get("longitude"))
    except Exception as exc:
        raise HTTPException(status_code=400, detail="latitude/longitude must be numeric") from exc

    if not city:
        raise HTTPException(status_code=400, detail="city is required")
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        raise HTTPException(status_code=400, detail="latitude/longitude out of range")

    value = {
        "latitude": lat,
        "longitude": lon,
        "city": city,
        "country": country,
        "timezone": timezone_value or "Australia/Perth",
    }
    await db.execute(
        """INSERT INTO system_preferences (key, value, updated_by, updated_at)
           VALUES (?, ?, ?, datetime('now'))
           ON CONFLICT(key) DO UPDATE SET
             value=excluded.value,
             updated_by=excluded.updated_by,
             updated_at=datetime('now')
        """,
        [SYSTEM_WEATHER_DEFAULT_KEY, json.dumps(value), user.get("user_id", "admin")],
    )
    await db.commit()
    _weather_cache.clear()
    return {**value, "source": "db"}


@router.get("/location/search", response_model=dict)
async def search_locations(query: str = "", limit: int = 8, user: dict = Depends(get_current_user)):
    """Search locations. Uses OpenWeatherMap geocoding (if key set) or Open-Meteo geocoding."""
    if not query or len(query) < 2:
        return {"results": []}

    if OPENWEATHERMAP_API_KEY:
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                r = await client.get(
                    "http://api.openweathermap.org/geo/1.0/direct",
                    params={"q": query, "limit": min(limit, 10), "appid": OPENWEATHERMAP_API_KEY},
                )
                r.raise_for_status()
                data = r.json()
            results = [{"name": i.get("name",""), "lat": i.get("lat"), "lon": i.get("lon"),
                        "country": i.get("country",""), "admin1": i.get("state","")}
                       for i in data if i.get("lat") is not None]
            return {"results": results}
        except Exception:
            pass  # fall through to Open-Meteo geocoding

    # Open-Meteo geocoding (free, no key)
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={"name": query, "count": min(limit, 10), "language": "en", "format": "json"},
            )
            r.raise_for_status()
            data = r.json()
        results = [
            {"name": i.get("name",""), "lat": i.get("latitude"), "lon": i.get("longitude"),
             "country": i.get("country",""), "admin1": i.get("admin1","")}
            for i in data.get("results", [])
            if i.get("latitude") is not None
        ]
        return {"results": results}
    except Exception as e:
        return {"results": [], "error": str(e)}
