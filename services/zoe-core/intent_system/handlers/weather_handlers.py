"""
Weather Intent Handlers
=======================

Handles weather-related intents:
- WeatherCurrent: Get current weather conditions
- WeatherForecast: Get weather forecast for upcoming days
"""

import logging
import httpx
from datetime import datetime
from typing import Dict, Any

from intent_system.classifiers import ZoeIntent

logger = logging.getLogger(__name__)


async def handle_weather_current(intent: ZoeIntent, user_id: str, context: Dict) -> Dict[str, Any]:
    """
    Handle WeatherCurrent intent - get current weather conditions.
    
    Args:
        intent: Parsed intent with slots
        user_id: User identifier
        context: Conversation context
        
    Returns:
        Dict with success, message, and data
    """
    try:
        async with httpx.AsyncClient() as client:
            # Call the weather API
            response = await client.get(
                "http://localhost:8000/api/weather/",
                headers={"X-Auth-Token": "internal", "X-Session-ID": "weather-query"},
                timeout=10.0
            )
            
            if response.status_code == 200:
                data = response.json()
                temp = data.get("temperature", "N/A")
                unit = data.get("temperature_unit", "celsius")
                unit_symbol = "°F" if unit.lower() == "fahrenheit" else "°C"
                condition = data.get("description", data.get("condition", "Unknown"))
                location = data.get("location", "your area")
                humidity = data.get("humidity")
                wind = data.get("wind_speed")
                
                # Build a friendly response
                response_text = f"🌡️ **Current Weather in {location}**\n\n"
                response_text += f"**Temperature:** {temp}{unit_symbol}\n"
                response_text += f"**Conditions:** {condition}\n"
                
                if humidity:
                    response_text += f"**Humidity:** {humidity}%\n"
                if wind:
                    response_text += f"**Wind:** {wind} km/h\n"
                
                # Add a friendly tip based on conditions
                condition_lower = condition.lower()
                if "rain" in condition_lower or "drizzle" in condition_lower:
                    response_text += "\n☔ Don't forget your umbrella!"
                elif "clear" in condition_lower or "sunny" in condition_lower:
                    response_text += "\n☀️ Great day to be outside!"
                elif "cloud" in condition_lower:
                    response_text += "\n⛅ Might want to keep an eye on the sky."
                elif "snow" in condition_lower:
                    response_text += "\n❄️ Bundle up and stay warm!"
                
                return {
                    "success": True,
                    "message": response_text,
                    "data": data
                }
            else:
                logger.warning(f"Weather API returned {response.status_code}")
                return {
                    "success": False,
                    "message": "Sorry, I couldn't fetch the weather right now. Please try again later."
                }
                
    except Exception as e:
        logger.error(f"Weather fetch failed: {e}", exc_info=True)
        return {
            "success": False,
            "message": "Sorry, I couldn't get the weather information right now."
        }


async def handle_weather_forecast(intent: ZoeIntent, user_id: str, context: Dict) -> Dict[str, Any]:
    """
    Handle WeatherForecast intent - get weather forecast for upcoming days.
    
    Args:
        intent: Parsed intent with slots
        user_id: User identifier
        context: Conversation context
        
    Returns:
        Dict with success, message, and data
    """
    try:
        # Get number of days from slots (default 3)
        days = intent.slots.get("days", 3)
        if isinstance(days, str):
            try:
                days = int(days)
            except ValueError:
                days = 3
        days = min(max(days, 1), 7)  # Clamp to 1-7 days
        
        async with httpx.AsyncClient() as client:
            # Call the weather forecast API
            response = await client.get(
                f"http://localhost:8000/api/weather/forecast?days={days}",
                headers={"X-Auth-Token": "internal", "X-Session-ID": "weather-query"},
                timeout=10.0
            )
            
            if response.status_code == 200:
                data = response.json()
                forecast = data.get("forecast", [])
                location = data.get("location", "your area")
                unit = data.get("temperature_unit", "celsius")
                unit_symbol = "°F" if unit.lower() == "fahrenheit" else "°C"
                
                if not forecast:
                    return {
                        "success": False,
                        "message": "Sorry, I couldn't get the forecast data."
                    }
                
                # Build forecast message
                response_text = f"📅 **{days}-Day Forecast for {location}**\n\n"
                
                for day in forecast[:days]:
                    date_str = day.get("date", "")
                    if date_str:
                        try:
                            date_obj = datetime.strptime(date_str, "%Y-%m-%d")
                            day_name = date_obj.strftime("%A")  # e.g., "Monday"
                            date_formatted = date_obj.strftime("%b %-d")  # e.g., "Dec 22"
                        except ValueError:
                            day_name = date_str
                            date_formatted = ""
                    else:
                        day_name = "Unknown"
                        date_formatted = ""
                    
                    temp_max = day.get("temperature_max", day.get("temperature", "N/A"))
                    temp_min = day.get("temperature_min", "")
                    condition = day.get("description", day.get("condition", "Unknown"))
                    
                    # Format temperature
                    if temp_min:
                        temp_str = f"{temp_min}{unit_symbol} - {temp_max}{unit_symbol}"
                    else:
                        temp_str = f"{temp_max}{unit_symbol}"
                    
                    # Get weather icon
                    condition_lower = condition.lower()
                    if "rain" in condition_lower or "drizzle" in condition_lower:
                        icon = "🌧️"
                    elif "snow" in condition_lower:
                        icon = "❄️"
                    elif "cloud" in condition_lower or "overcast" in condition_lower:
                        icon = "☁️"
                    elif "clear" in condition_lower or "sunny" in condition_lower:
                        icon = "☀️"
                    elif "thunder" in condition_lower or "storm" in condition_lower:
                        icon = "⛈️"
                    elif "fog" in condition_lower:
                        icon = "🌫️"
                    else:
                        icon = "🌤️"
                    
                    response_text += f"**{day_name}** ({date_formatted})\n"
                    response_text += f"  {icon} {condition}, {temp_str}\n\n"
                
                return {
                    "success": True,
                    "message": response_text.strip(),
                    "data": data
                }
            else:
                logger.warning(f"Weather forecast API returned {response.status_code}")
                return {
                    "success": False,
                    "message": "Sorry, I couldn't fetch the forecast right now. Please try again later."
                }
                
    except Exception as e:
        logger.error(f"Weather forecast failed: {e}", exc_info=True)
        return {
            "success": False,
            "message": "Sorry, I couldn't get the forecast right now."
        }

