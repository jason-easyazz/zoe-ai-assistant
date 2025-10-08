"""
Weather Service
Provides weather data for the frontend
"""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, Dict, Any
import requests
import os
from datetime import datetime

router = APIRouter(prefix="/api/weather", tags=["weather"])

# Weather API configuration
OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY", "")
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

def get_user_location(user_id: str = "default"):
    """Get user's location settings from database or environment"""
    try:
        import sqlite3
        DB_PATH = os.getenv("DATABASE_PATH", "/app/data/zoe.db")
        conn = sqlite3.connect(DB_PATH)
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
                "city": row[2],
                "country": row[3]
            }
    except:
        pass
    
    # Fallback to environment variables or defaults
    return {
        "latitude": float(os.getenv("WEATHER_LAT", DEFAULT_LAT)),
        "longitude": float(os.getenv("WEATHER_LON", DEFAULT_LON)),
        "city": os.getenv("WEATHER_CITY", DEFAULT_CITY),
        "country": os.getenv("WEATHER_COUNTRY", DEFAULT_COUNTRY)
    }

@router.get("/")
async def get_weather(user_id: str = Query("default", description="User ID")):
    """Get current weather data for user's location"""
    location = get_user_location(user_id)
    
    if not OPENWEATHER_API_KEY:
        # Return mock weather data if no API key
        return {
            "temperature": 23.0,
            "condition": "sunny",
            "description": "Clear sky",
            "humidity": 65,
            "wind_speed": 3.2,
            "pressure": 1013,
            "icon": "01d",
            "location": f"{location['city']}, {location['country']}"
        }
    
    try:
        # Call OpenWeatherMap API
        url = f"http://api.openweathermap.org/data/2.5/weather"
        params = {
            "lat": location["latitude"],
            "lon": location["longitude"],
            "appid": OPENWEATHER_API_KEY,
            "units": "metric"
        }
        
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        
        data = response.json()
        
        return {
            "temperature": data["main"]["temp"],
            "condition": data["weather"][0]["main"].lower(),
            "description": data["weather"][0]["description"],
            "humidity": data["main"]["humidity"],
            "wind_speed": data["wind"]["speed"],
            "pressure": data["main"]["pressure"],
            "icon": data["weather"][0]["icon"],
            "location": f"{location['city']}, {location['country']}"
        }
        
    except Exception as e:
        # Fallback to mock data on error
        return {
            "temperature": 23.0,
            "condition": "sunny",
            "description": "Clear sky",
            "humidity": 65,
            "wind_speed": 3.2,
            "pressure": 1013,
            "icon": "01d",
            "location": f"{location['city']}, {location['country']}"
        }

@router.get("/forecast")
async def get_forecast(days: int = Query(5, description="Number of days for forecast")):
    """Get weather forecast"""
    if not OPENWEATHER_API_KEY:
        # Return mock forecast data
        mock_forecast = []
        for i in range(min(days, 5)):
            mock_forecast.append({
                "date": f"2024-01-{i+1:02d}",
                "temperature": 20 + i * 2,
                "condition": ["sunny", "cloudy", "rainy", "clear", "partly-cloudy"][i % 5],
                "description": ["Clear sky", "Cloudy", "Light rain", "Clear", "Partly cloudy"][i % 5],
                "humidity": 60 + i * 5,
                "wind_speed": 2.0 + i * 0.5
            })
        return {"forecast": mock_forecast}
    
    try:
        # Call OpenWeatherMap 5-day forecast API
        url = f"http://api.openweathermap.org/data/2.5/forecast"
        params = {
            "lat": WEATHER_LAT,
            "lon": WEATHER_LON,
            "appid": OPENWEATHER_API_KEY,
            "units": "metric"
        }
        
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        
        data = response.json()
        
        # Process forecast data (group by day)
        forecast = []
        daily_data = {}
        
        for item in data["list"]:
            date = item["dt_txt"].split(" ")[0]
            if date not in daily_data:
                daily_data[date] = []
            daily_data[date].append(item)
        
        # Get daily averages
        for date, items in list(daily_data.items())[:days]:
            temps = [item["main"]["temp"] for item in items]
            conditions = [item["weather"][0]["main"].lower() for item in items]
            humidities = [item["main"]["humidity"] for item in items]
            wind_speeds = [item["wind"]["speed"] for item in items]
            
            # Get most common condition for the day
            most_common_condition = max(set(conditions), key=conditions.count)
            
            forecast.append({
                "date": date,
                "temperature": round(sum(temps) / len(temps), 1),
                "condition": most_common_condition,
                "description": items[0]["weather"][0]["description"],
                "humidity": round(sum(humidities) / len(humidities)),
                "wind_speed": round(sum(wind_speeds) / len(wind_speeds), 1)
            })
        
        return {"forecast": forecast}
        
    except Exception as e:
        # Fallback to mock data on error
        mock_forecast = []
        for i in range(min(days, 5)):
            mock_forecast.append({
                "date": f"2024-01-{i+1:02d}",
                "temperature": 20 + i * 2,
                "condition": ["sunny", "cloudy", "rainy", "clear", "partly-cloudy"][i % 5],
                "description": ["Clear sky", "Cloudy", "Light rain", "Clear", "Partly cloudy"][i % 5],
                "humidity": 60 + i * 5,
                "wind_speed": 2.0 + i * 0.5
            })
        return {"forecast": mock_forecast}

@router.get("/location")
async def get_location():
    """Get current location coordinates"""
    return {
        "latitude": float(DEFAULT_LAT),
        "longitude": float(DEFAULT_LON),
        "city": DEFAULT_CITY,
        "country": DEFAULT_COUNTRY
    }

@router.post("/location")
async def update_location(
    latitude: float, 
    longitude: float, 
    city: Optional[str] = None,
    country: Optional[str] = None,
    user_id: str = Query("default", description="User ID")
):
    """Update user's location coordinates"""
    try:
        import sqlite3
        DB_PATH = os.getenv("DATABASE_PATH", "/app/data/zoe.db")
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Create user_settings table if it doesn't exist
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
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, setting_key)
            )
        """)
        
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
        raise HTTPException(status_code=500, detail=f"Failed to update location: {str(e)}")

@router.get("/location/search")
async def search_location(
    query: str = Query(..., description="Location search query"),
    limit: int = Query(5, description="Number of results to return")
):
    """Search for locations using OpenWeatherMap Geocoding API"""
    if not OPENWEATHER_API_KEY:
        # Return mock search results
        mock_results = [
            {"name": "San Francisco, US", "lat": 37.7749, "lon": -122.4194, "country": "US"},
            {"name": "New York, US", "lat": 40.7128, "lon": -74.0060, "country": "US"},
            {"name": "London, GB", "lat": 51.5074, "lon": -0.1278, "country": "GB"},
            {"name": "Tokyo, JP", "lat": 35.6762, "lon": 139.6503, "country": "JP"},
            {"name": "Paris, FR", "lat": 48.8566, "lon": 2.3522, "country": "FR"}
        ]
        return {"results": mock_results[:limit]}
    
    try:
        # Call OpenWeatherMap Geocoding API
        url = f"http://api.openweathermap.org/geo/1.0/direct"
        params = {
            "q": query,
            "limit": limit,
            "appid": OPENWEATHER_API_KEY
        }
        
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        
        data = response.json()
        
        results = []
        for item in data:
            results.append({
                "name": f"{item['name']}, {item['country']}",
                "lat": item['lat'],
                "lon": item['lon'],
                "country": item['country'],
                "state": item.get('state', '')
            })
        
        return {"results": results}
        
    except Exception as e:
        # Fallback to mock results on error
        mock_results = [
            {"name": "San Francisco, US", "lat": 37.7749, "lon": -122.4194, "country": "US"},
            {"name": "New York, US", "lat": 40.7128, "lon": -74.0060, "country": "US"},
            {"name": "London, GB", "lat": 51.5074, "lon": -0.1278, "country": "GB"}
        ]
        return {"results": mock_results[:limit]}
