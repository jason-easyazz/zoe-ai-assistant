"""
Weather Service
Provides weather data using Open-Meteo free API
"""
from fastapi import APIRouter, HTTPException, Query, Body, Depends
from pydantic import BaseModel
from typing import Optional, Dict, Any
import requests
import os
from datetime import datetime
import logging
import sqlite3
from auth_integration import validate_session, AuthenticatedSession

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/weather", tags=["weather"])

# Default location (can be overridden by user settings)
DEFAULT_LAT = "37.7749"  # San Francisco
DEFAULT_LON = "-122.4194"
DEFAULT_CITY = "San Francisco"
DEFAULT_COUNTRY = "US"

class WeatherResponse(BaseModel):
    temperature: float
    condition: str
    description: str
    humidity: Optional[int] = None
    wind_speed: Optional[float] = None
    pressure: Optional[int] = None
    icon: Optional[str] = None
    location: Optional[str] = None
    temperature_unit: Optional[str] = "celsius"

def convert_temperature(temp_celsius: float, unit: str = "celsius") -> float:
    """Convert temperature from Celsius to requested unit"""
    if unit.lower() == "fahrenheit":
        return round((temp_celsius * 9/5) + 32, 1)
    return round(temp_celsius, 1)

def map_weather_code_to_condition(code: int) -> tuple:
    """
    Map Open-Meteo weather codes to condition, description, and icon
    Reference: https://open-meteo.com/en/docs
    """
    weather_mapping = {
        0: ("clear", "Clear sky", "01d"),
        1: ("clear", "Mainly clear", "01d"),
        2: ("partly-cloudy", "Partly cloudy", "02d"),
        3: ("cloudy", "Overcast", "03d"),
        45: ("fog", "Foggy", "50d"),
        48: ("fog", "Depositing rime fog", "50d"),
        51: ("drizzle", "Light drizzle", "09d"),
        53: ("drizzle", "Moderate drizzle", "09d"),
        55: ("drizzle", "Dense drizzle", "09d"),
        61: ("rain", "Slight rain", "10d"),
        63: ("rain", "Moderate rain", "10d"),
        65: ("rain", "Heavy rain", "10d"),
        71: ("snow", "Slight snow", "13d"),
        73: ("snow", "Moderate snow", "13d"),
        75: ("snow", "Heavy snow", "13d"),
        77: ("snow", "Snow grains", "13d"),
        80: ("rain", "Slight rain showers", "09d"),
        81: ("rain", "Moderate rain showers", "09d"),
        82: ("rain", "Violent rain showers", "09d"),
        85: ("snow", "Slight snow showers", "13d"),
        86: ("snow", "Heavy snow showers", "13d"),
        95: ("thunderstorm", "Thunderstorm", "11d"),
        96: ("thunderstorm", "Thunderstorm with slight hail", "11d"),
        99: ("thunderstorm", "Thunderstorm with heavy hail", "11d")
    }
    return weather_mapping.get(code, ("unknown", "Unknown conditions", "01d"))

def get_db_connection():
    """Get database connection"""
    DB_PATH = os.getenv("DATABASE_PATH", "/app/data/zoe.db")
    return sqlite3.connect(DB_PATH)

def ensure_user_settings_table():
    """Ensure user_settings table exists with all required columns"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Create table if it doesn't exist
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                setting_key TEXT NOT NULL,
                setting_value TEXT,
                latitude REAL,
                longitude REAL,
                city TEXT,
                country TEXT,
                temperature_unit TEXT DEFAULT 'celsius',
                use_current_location INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, setting_key)
            )
        """)
        
        # Check if columns exist (for existing tables)
        cursor.execute("PRAGMA table_info(user_settings)")
        columns = [row[1] for row in cursor.fetchall()]
        
        if 'temperature_unit' not in columns:
            cursor.execute("ALTER TABLE user_settings ADD COLUMN temperature_unit TEXT DEFAULT 'celsius'")
            logger.info("Added temperature_unit column to user_settings table")
        
        if 'use_current_location' not in columns:
            cursor.execute("ALTER TABLE user_settings ADD COLUMN use_current_location INTEGER DEFAULT 0")
            logger.info("Added use_current_location column to user_settings table")
        
        conn.commit()
        conn.close()
        logger.info("User settings table initialized successfully")
        
    except Exception as e:
        logger.error(f"Error ensuring user_settings table: {e}")

def get_user_location(user_id: str = "default") -> Dict[str, Any]:
    """Get user's location settings from database or environment"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Check if user has location settings
        cursor.execute("""
            SELECT latitude, longitude, city, country 
            FROM user_settings 
            WHERE user_id = ? AND setting_key = 'location'
        """, (user_id,))
        
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return {
                "latitude": float(row[0]),
                "longitude": float(row[1]),
                "city": row[2] or DEFAULT_CITY,
                "country": row[3] or DEFAULT_COUNTRY
            }
    except Exception as e:
        logger.error(f"Error fetching user location: {e}")
    
    # Fallback to environment variables or defaults
    return {
        "latitude": float(os.getenv("WEATHER_LAT", DEFAULT_LAT)),
        "longitude": float(os.getenv("WEATHER_LON", DEFAULT_LON)),
        "city": os.getenv("WEATHER_CITY", DEFAULT_CITY),
        "country": os.getenv("WEATHER_COUNTRY", DEFAULT_COUNTRY)
    }

def get_user_temperature_unit(user_id: str = "default") -> str:
    """Get user's preferred temperature unit"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT temperature_unit 
            FROM user_settings 
            WHERE user_id = ? AND setting_key = 'weather_preferences'
        """, (user_id,))
        
        row = cursor.fetchone()
        conn.close()
        
        if row and row[0]:
            return row[0]
    except Exception as e:
        logger.error(f"Error fetching temperature unit: {e}")
    
    return "celsius"

def get_user_use_current_location(user_id: str = "default") -> bool:
    """Check if user wants to use current device location"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT use_current_location 
            FROM user_settings 
            WHERE user_id = ? AND setting_key = 'weather_preferences'
        """, (user_id,))
        
        row = cursor.fetchone()
        conn.close()
        
        if row and row[0]:
            return bool(row[0])
    except Exception as e:
        logger.error(f"Error fetching use_current_location: {e}")
    
    return False

def get_user_preferences(user_id: str = "default") -> Dict[str, Any]:
    """Get user's weather preferences (location and temperature unit)"""
    location = get_user_location(user_id)
    temperature_unit = get_user_temperature_unit(user_id)
    use_current_location = get_user_use_current_location(user_id)
    
    return {
        "location": location,
        "temperature_unit": temperature_unit,
        "use_current_location": use_current_location
    }

@router.get("/")
async def get_weather(
    session: AuthenticatedSession = Depends(validate_session),
    lat: Optional[float] = Query(None, description="Override latitude (for device location)"),
    lon: Optional[float] = Query(None, description="Override longitude (for device location)")
):
    """Get current weather data using Open-Meteo API"""
    ensure_user_settings_table()  # Ensure table exists before querying
    user_id = session.user_id
    
    # Use override coordinates if provided (for device location)
    if lat is not None and lon is not None:
        location = {
            "latitude": lat,
            "longitude": lon,
            "city": "Current Location",
            "country": ""
        }
    else:
        # Use saved user location
        location = get_user_location(user_id)
    
    temperature_unit = get_user_temperature_unit(user_id)
    
    try:
        # Call Open-Meteo API (no API key needed!)
        url = "https://api.open-meteo.com/v1/forecast"
        params = {
            "latitude": location["latitude"],
            "longitude": location["longitude"],
            "current": "temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m,surface_pressure",
            "timezone": "auto"
        }
        
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        
        data = response.json()
        current = data["current"]
        
        # Map weather code to condition
        weather_code = current["weather_code"]
        condition, description, icon = map_weather_code_to_condition(weather_code)
        
        # Convert temperature to user's preferred unit
        temp_celsius = current["temperature_2m"]
        temperature = convert_temperature(temp_celsius, temperature_unit)
        
        return {
            "temperature": temperature,
            "condition": condition,
            "description": description,
            "humidity": round(current["relative_humidity_2m"]),
            "wind_speed": round(current["wind_speed_10m"], 1),
            "pressure": round(current["surface_pressure"]),
            "icon": icon,
            "location": f"{location['city']}, {location['country']}",
            "temperature_unit": temperature_unit
        }
        
    except Exception as e:
        logger.error(f"Weather API error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch weather data: {str(e)}")

@router.get("/current")
async def get_current_weather(
    session: AuthenticatedSession = Depends(validate_session),
    lat: Optional[float] = Query(None, description="Override latitude (for device location)"),
    lon: Optional[float] = Query(None, description="Override longitude (for device location)")
):
    """Get current weather data (alias for /)"""
    ensure_user_settings_table()  # Ensure table exists before querying
    user_id = session.user_id
    return await get_weather(session, lat, lon)

@router.get("/forecast")
async def get_forecast(
    days: int = Query(7, description="Number of days for forecast (1-16)"),
    session: AuthenticatedSession = Depends(validate_session),
    lat: Optional[float] = Query(None, description="Override latitude (for device location)"),
    lon: Optional[float] = Query(None, description="Override longitude (for device location)")
):
    """Get weather forecast using Open-Meteo API"""
    ensure_user_settings_table()  # Ensure table exists before querying
    user_id = session.user_id
    
    # Use override coordinates if provided (for device location)
    if lat is not None and lon is not None:
        location = {
            "latitude": lat,
            "longitude": lon,
            "city": "Current Location",
            "country": ""
        }
    else:
        # Use saved user location
        location = get_user_location(user_id)
    
    temperature_unit = get_user_temperature_unit(user_id)
    
    # Open-Meteo supports up to 16 days
    days = min(max(days, 1), 16)
    
    try:
        # Call Open-Meteo API
        url = "https://api.open-meteo.com/v1/forecast"
        params = {
            "latitude": location["latitude"],
            "longitude": location["longitude"],
            "daily": "temperature_2m_max,temperature_2m_min,weather_code,precipitation_sum,wind_speed_10m_max",
            "timezone": "auto",
            "forecast_days": days
        }
        
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        
        data = response.json()
        daily = data["daily"]
        
        forecast = []
        for i in range(len(daily["time"])):
            weather_code = daily["weather_code"][i]
            condition, description, icon = map_weather_code_to_condition(weather_code)
            
            # Convert temperatures
            temp_max = convert_temperature(daily["temperature_2m_max"][i], temperature_unit)
            temp_min = convert_temperature(daily["temperature_2m_min"][i], temperature_unit)
            avg_temp = convert_temperature((daily["temperature_2m_max"][i] + daily["temperature_2m_min"][i]) / 2, temperature_unit)
            
            forecast.append({
                "date": daily["time"][i],
                "temperature": avg_temp,
                "temperature_max": temp_max,
                "temperature_min": temp_min,
                "condition": condition,
                "description": description,
                "precipitation": round(daily["precipitation_sum"][i], 1),
                "wind_speed": round(daily["wind_speed_10m_max"][i], 1),
                "icon": icon
            })
        
        return {
            "forecast": forecast, 
            "location": f"{location['city']}, {location['country']}",
            "temperature_unit": temperature_unit
        }
        
    except Exception as e:
        logger.error(f"Forecast API error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch forecast data: {str(e)}")

@router.get("/hourly")
async def get_hourly_forecast(
    hours: int = Query(24, description="Number of hours (1-168)"),
    session: AuthenticatedSession = Depends(validate_session)
):
    """Get hourly weather forecast"""
    ensure_user_settings_table()  # Ensure table exists before querying
    user_id = session.user_id
    location = get_user_location(user_id)
    temperature_unit = get_user_temperature_unit(user_id)
    hours = min(max(hours, 1), 168)  # Max 7 days (168 hours)
    
    try:
        url = "https://api.open-meteo.com/v1/forecast"
        params = {
            "latitude": location["latitude"],
            "longitude": location["longitude"],
            "hourly": "temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m,precipitation",
            "timezone": "auto",
            "forecast_hours": hours
        }
        
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        
        data = response.json()
        hourly = data["hourly"]
        
        forecast = []
        for i in range(min(hours, len(hourly["time"]))):
            weather_code = hourly["weather_code"][i]
            condition, description, icon = map_weather_code_to_condition(weather_code)
            
            temperature = convert_temperature(hourly["temperature_2m"][i], temperature_unit)
            
            forecast.append({
                "time": hourly["time"][i],
                "temperature": temperature,
                "humidity": round(hourly["relative_humidity_2m"][i]),
                "condition": condition,
                "description": description,
                "wind_speed": round(hourly["wind_speed_10m"][i], 1),
                "precipitation": round(hourly["precipitation"][i], 1),
                "icon": icon
            })
        
        return {
            "hourly": forecast, 
            "location": f"{location['city']}, {location['country']}",
            "temperature_unit": temperature_unit
        }
        
    except Exception as e:
        logger.error(f"Hourly forecast error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch hourly forecast: {str(e)}")

@router.get("/location")
async def get_location(session: AuthenticatedSession = Depends(validate_session)):
    """Get current location coordinates"""
    ensure_user_settings_table()  # Ensure table exists before querying
    user_id = session.user_id
    location = get_user_location(user_id)
    return location

@router.get("/preferences")
async def get_preferences(session: AuthenticatedSession = Depends(validate_session)):
    """Get user's weather preferences (location and temperature unit)"""
    ensure_user_settings_table()  # Ensure table exists before querying
    user_id = session.user_id
    return get_user_preferences(user_id)

class WeatherPreferences(BaseModel):
    user_id: str = "default"
    temperature_unit: Optional[str] = None
    use_current_location: Optional[bool] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    city: Optional[str] = None
    country: Optional[str] = None

@router.post("/preferences")
async def update_preferences(preferences: WeatherPreferences):
    """Update user's weather preferences"""
    try:
        ensure_user_settings_table()
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Update location if provided
        if preferences.latitude is not None and preferences.longitude is not None:
            cursor.execute("""
                INSERT OR REPLACE INTO user_settings 
                (user_id, setting_key, setting_value, latitude, longitude, city, country, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (preferences.user_id, "location", f"{preferences.latitude},{preferences.longitude}", 
                  preferences.latitude, preferences.longitude, preferences.city, preferences.country))
        
        # Update weather preferences (temperature unit and use_current_location)
        if preferences.temperature_unit or preferences.use_current_location is not None:
            # Get current values first
            cursor.execute("""
                SELECT temperature_unit, use_current_location 
                FROM user_settings 
                WHERE user_id = ? AND setting_key = 'weather_preferences'
            """, (preferences.user_id,))
            
            current = cursor.fetchone()
            current_temp_unit = current[0] if current else 'celsius'
            current_use_location = current[1] if current else 0
            
            # Use provided values or keep current
            final_temp_unit = preferences.temperature_unit if preferences.temperature_unit else current_temp_unit
            final_use_location = int(preferences.use_current_location) if preferences.use_current_location is not None else current_use_location
            
            cursor.execute("""
                INSERT OR REPLACE INTO user_settings 
                (user_id, setting_key, temperature_unit, use_current_location, updated_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (preferences.user_id, "weather_preferences", final_temp_unit, final_use_location))
        
        conn.commit()
        conn.close()
        
        return {
            "message": "Weather preferences updated successfully",
            "preferences": get_user_preferences(preferences.user_id)
        }
        
    except Exception as e:
        logger.error(f"Failed to update preferences: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to update preferences: {str(e)}")

@router.post("/location")
async def update_location(
    latitude: float = Body(...), 
    longitude: float = Body(...), 
    city: Optional[str] = Body(None),
    country: Optional[str] = Body(None),
    session: AuthenticatedSession = Depends(validate_session)
):
    """Update user's location coordinates"""
    try:
        user_id = session.user_id
        ensure_user_settings_table()
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Insert or update location settings
        cursor.execute("""
            INSERT OR REPLACE INTO user_settings 
            (user_id, setting_key, setting_value, latitude, longitude, city, country, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, (user_id, "location", f"{latitude},{longitude}", latitude, longitude, city, country))
        
        conn.commit()
        conn.close()
        
        return {
            "message": "Location updated successfully",
            "latitude": latitude,
            "longitude": longitude,
            "city": city,
            "country": country
        }
        
    except Exception as e:
        logger.error(f"Failed to update location: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to update location: {str(e)}")

@router.get("/location/search")
async def search_location(
    query: str = Query(..., description="Location search query"),
    limit: int = Query(5, description="Number of results to return")
):
    """Search for locations using Open-Meteo Geocoding API"""
    try:
        # Use Open-Meteo's free geocoding API
        url = "https://geocoding-api.open-meteo.com/v1/search"
        params = {
            "name": query,
            "count": limit,
            "language": "en",
            "format": "json"
        }
        
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        
        data = response.json()
        
        results = []
        if "results" in data:
            for item in data["results"]:
                results.append({
                    "name": f"{item['name']}, {item.get('country', '')}",
                    "lat": item['latitude'],
                    "lon": item['longitude'],
                    "country": item.get('country', ''),
                    "country_code": item.get('country_code', ''),
                    "admin1": item.get('admin1', ''),  # State/region
                    "timezone": item.get('timezone', '')
                })
        
        return {"results": results}
        
    except Exception as e:
        logger.error(f"Location search error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to search location: {str(e)}")

# Table will be created on first API call (moved to avoid blocking startup)
