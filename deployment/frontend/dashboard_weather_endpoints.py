# Add these dashboard and weather endpoints to your services/zoe-core/main.py

import requests
from datetime import timedelta

# Weather and Dashboard Models
class WeatherSettings(BaseModel):
    api_key: Optional[str] = None
    location: str = Field(default="Perth, Australia")
    units: str = Field(default="metric")  # metric, imperial, kelvin

class DashboardResponse(BaseModel):
    current_time: str
    weather: Dict[str, Any]
    task_stats: Dict[str, int]
    journal_stats: Dict[str, Any]
    upcoming_events: List[Dict[str, Any]]
    ai_greeting: str
    integration_status: Dict[str, str]

# Weather Endpoints

@app.get("/api/weather")
async def get_weather():
    """Get current weather for dashboard"""
    try:
        # Get user weather settings
        api_key = await get_setting("weather", "api_key", "")
        location = await get_setting("weather", "location", "Perth, Australia")
        units = await get_setting("weather", "units", "metric")
        
        if not api_key:
            # Return mock weather data for demo
            return {
                "location": location,
                "temperature": 22,
                "condition": "Partly Cloudy",
                "icon": "partly-cloudy",
                "humidity": 65,
                "wind_speed": 12,
                "description": "Pleasant conditions",
                "demo_mode": True
            }
        
        # Call OpenWeatherMap API
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"https://api.openweathermap.org/data/2.5/weather",
                params={
                    "q": location,
                    "appid": api_key,
                    "units": units
                }
            )
            
            if response.status_code == 200:
                data = response.json()
                return {
                    "location": data["name"],
                    "temperature": round(data["main"]["temp"]),
                    "condition": data["weather"][0]["main"],
                    "description": data["weather"][0]["description"],
                    "icon": data["weather"][0]["icon"],
                    "humidity": data["main"]["humidity"],
                    "wind_speed": round(data["wind"]["speed"]),
                    "feels_like": round(data["main"]["feels_like"]),
                    "demo_mode": False
                }
            else:
                # Fallback to demo data
                return {
                    "location": location,
                    "temperature": 22,
                    "condition": "Partly Cloudy", 
                    "description": "Weather service unavailable",
                    "demo_mode": True,
                    "error": "API error"
                }
                
    except Exception as e:
        logger.error(f"Weather error: {e}")
        return {
            "location": "Perth, Australia",
            "temperature": 22,
            "condition": "Unknown",
            "description": "Weather unavailable",
            "demo_mode": True,
            "error": str(e)
        }

@app.post("/api/weather/settings")
async def update_weather_settings(settings: WeatherSettings):
    """Update weather API settings"""
    try:
        if settings.api_key:
            await set_setting("weather", "api_key", settings.api_key)
        await set_setting("weather", "location", settings.location)
        await set_setting("weather", "units", settings.units)
        
        return {"success": True, "message": "Weather settings updated"}
    except Exception as e:
        logger.error(f"Weather settings error: {e}")
        raise HTTPException(status_code=500, detail="Failed to update weather settings")

# Dashboard Endpoints

@app.get("/api/dashboard")
async def get_dashboard():
    """Get complete dashboard data"""
    try:
        # Get current time
        current_time = datetime.now().strftime("%H:%M")
        current_date = datetime.now().strftime("%A, %B %d")
        
        # Get weather
        weather = await get_weather()
        
        # Get task statistics
        task_stats = await get_task_statistics()
        
        # Get journal statistics
        journal_stats = await get_journal_statistics()
        
        # Get upcoming events
        upcoming_events = await get_upcoming_events()
        
        # Generate AI greeting
        ai_greeting = await generate_ai_greeting()
        
        # Get integration status
        integration_status = await get_integration_status()
        
        return {
            "current_time": current_time,
            "current_date": current_date,
            "weather": weather,
            "task_stats": task_stats,
            "journal_stats": journal_stats,
            "upcoming_events": upcoming_events,
            "ai_greeting": ai_greeting,
            "integration_status": integration_status,
            "timestamp": datetime.now().isoformat()
        }
        
    except Exception as e:
        logger.error(f"Dashboard error: {e}")
        raise HTTPException(status_code=500, detail=f"Dashboard data failed: {str(e)}")

async def get_task_statistics() -> Dict[str, int]:
    """Get task statistics for dashboard"""
    try:
        async with aiosqlite.connect(CONFIG["database_path"]) as db:
            # Total tasks
            cursor = await db.execute("SELECT COUNT(*) FROM tasks WHERE archived = 0")
            total = (await cursor.fetchone())[0]
            
            # Completed tasks
            cursor = await db.execute("SELECT COUNT(*) FROM tasks WHERE completed = 1 AND archived = 0")
            completed = (await cursor.fetchone())[0]
            
            # Overdue tasks
            cursor = await db.execute("""
                SELECT COUNT(*) FROM tasks 
                WHERE due_date < ? AND completed = 0 AND archived = 0
            """, (datetime.now(),))
            overdue = (await cursor.fetchone())[0]
            
            # Today's tasks
            today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            today_end = today_start + timedelta(days=1)
            cursor = await db.execute("""
                SELECT COUNT(*) FROM tasks 
                WHERE due_date BETWEEN ? AND ? AND archived = 0
            """, (today_start, today_end))
            today = (await cursor.fetchone())[0]
            
            return {
                "total": total,
                "completed": completed,
                "pending": total - completed,
                "overdue": overdue,
                "today": today
            }
    except Exception as e:
        logger.error(f"Task stats error: {e}")
        return {"total": 0, "completed": 0, "pending": 0, "overdue": 0, "today": 0}

async def get_journal_statistics() -> Dict[str, Any]:
    """Get journal statistics for dashboard"""
    try:
        async with aiosqlite.connect(CONFIG["database_path"]) as db:
            # Total entries
            cursor = await db.execute("SELECT COUNT(*) FROM journal_entries")
            total_entries = (await cursor.fetchone())[0]
            
            # This week's entries
            week_start = datetime.now() - timedelta(days=7)
            cursor = await db.execute("""
                SELECT COUNT(*) FROM journal_entries 
                WHERE created_at >= ?
            """, (week_start,))
            week_entries = (await cursor.fetchone())[0]
            
            # Recent entries
            cursor = await db.execute("""
                SELECT title, created_at FROM journal_entries 
                ORDER BY created_at DESC 
                LIMIT 3
            """)
            recent_entries = []
            for title, created_at in await cursor.fetchall():
                recent_entries.append({
                    "title": title or "Untitled Entry",
                    "date": created_at
                })
            
            # Current streak
            streak = await calculate_journal_streak()
            
            return {
                "total_entries": total_entries,
                "week_entries": week_entries,
                "recent_entries": recent_entries,
                "current_streak": streak
            }
    except Exception as e:
        logger.error(f"Journal stats error: {e}")
        return {
            "total_entries": 0,
            "week_entries": 0,
            "recent_entries": [],
            "current_streak": 0
        }

async def get_upcoming_events() -> List[Dict[str, Any]]:
    """Get upcoming events for dashboard"""
    try:
        async with aiosqlite.connect(CONFIG["database_path"]) as db:
            # Get events in next 7 days
            week_ahead = datetime.now() + timedelta(days=7)
            cursor = await db.execute("""
                SELECT title, start_time, location, description 
                FROM events 
                WHERE start_time BETWEEN ? AND ?
                ORDER BY start_time ASC
                LIMIT 5
            """, (datetime.now(), week_ahead))
            
            events = []
            for title, start_time, location, description in await cursor.fetchall():
                events.append({
                    "title": title,
                    "start_time": start_time,
                    "location": location or "",
                    "description": description or "",
                    "relative_time": get_relative_time(start_time)
                })
            
            return events
    except Exception as e:
        logger.error(f"Events error: {e}")
        return []

async def generate_ai_greeting() -> str:
    """Generate personalized AI greeting for dashboard"""
    try:
        current_hour = datetime.now().hour
        
        # Time-based greeting
        if current_hour < 12:
            base_greeting = "Good morning"
        elif current_hour < 17:
            base_greeting = "Good afternoon"
        else:
            base_greeting = "Good evening"
        
        # Get recent activity context
        async with aiosqlite.connect(CONFIG["database_path"]) as db:
            # Check for recent tasks
            cursor = await db.execute("""
                SELECT COUNT(*) FROM tasks 
                WHERE due_date = DATE('now') AND completed = 0
            """)
            today_tasks = (await cursor.fetchone())[0]
            
            # Check for recent journal entries
            cursor = await db.execute("""
                SELECT COUNT(*) FROM journal_entries 
                WHERE DATE(created_at) = DATE('now')
            """)
            today_journal = (await cursor.fetchone())[0]
        
        # Personalize greeting based on activity
        if today_tasks > 0:
            greeting = f"{base_greeting}! You have {today_tasks} task{'s' if today_tasks != 1 else ''} for today."
        elif today_journal > 0:
            greeting = f"{base_greeting}! I see you've already been journaling today. How can I help?"
        else:
            greetings = [
                f"{base_greeting}! Ready to make today great?",
                f"{base_greeting}! What's on your mind today?",
                f"{base_greeting}! How can I help you today?",
                f"{base_greeting}! Let's tackle the day together!"
            ]
            greeting = greetings[datetime.now().day % len(greetings)]
        
        return greeting
        
    except Exception as e:
        logger.error(f"Greeting error: {e}")
        return "Hello! How can I help you today?"

async def get_integration_status() -> Dict[str, str]:
    """Check status of all integrations"""
    status = {}
    
    # Check Ollama (AI)
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{CONFIG['ollama_url']}/api/tags")
            status["ollama"] = "connected" if response.status_code == 200 else "error"
    except Exception:
        status["ollama"] = "disconnected"
    
    # Check Whisper (STT)
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{CONFIG['whisper_url']}/health")
            status["whisper"] = "connected" if response.status_code == 200 else "error"
    except Exception:
        status["whisper"] = "disconnected"
    
    # Check TTS
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{CONFIG['tts_url']}/health")
            status["tts"] = "connected" if response.status_code == 200 else "error"
    except Exception:
        status["tts"] = "disconnected"
    
    # Check n8n
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{CONFIG['n8n_url']}/healthz")
            status["n8n"] = "connected" if response.status_code == 200 else "error"
    except Exception:
        status["n8n"] = "disconnected"
    
    # Check Home Assistant
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{CONFIG['ha_url']}/api/")
            status["homeassistant"] = "connected" if response.status_code == 200 else "error"
    except Exception:
        status["homeassistant"] = "disconnected"
    
    return status

@app.get("/api/integrations/status")
async def integration_status():
    """Get detailed integration status"""
    return await get_integration_status()

# Helper Functions

async def calculate_journal_streak() -> int:
    """Calculate current journal writing streak"""
    try:
        async with aiosqlite.connect(CONFIG["database_path"]) as db:
            # Get daily journal counts for last 30 days
            cursor = await db.execute("""
                SELECT DATE(created_at) as entry_date, COUNT(*) as count
                FROM journal_entries
                WHERE created_at >= DATE('now', '-30 days')
                GROUP BY DATE(created_at)
                ORDER BY entry_date DESC
            """)
            
            daily_counts = await cursor.fetchall()
            
            streak = 0
            current_date = datetime.now().date()
            
            for entry_date_str, count in daily_counts:
                entry_date = datetime.strptime(entry_date_str, '%Y-%m-%d').date()
                
                # Check if this is the next expected date in streak
                expected_date = current_date - timedelta(days=streak)
                
                if entry_date == expected_date and count > 0:
                    streak += 1
                else:
                    break
            
            return streak
    except Exception as e:
        logger.error(f"Streak calculation error: {e}")
        return 0

def get_relative_time(event_time_str: str) -> str:
    """Get human-readable relative time"""
    try:
        event_time = datetime.fromisoformat(event_time_str.replace('Z', '+00:00'))
        now = datetime.now()
        
        if event_time.date() == now.date():
            return f"Today at {event_time.strftime('%H:%M')}"
        elif event_time.date() == (now + timedelta(days=1)).date():
            return f"Tomorrow at {event_time.strftime('%H:%M')}"
        else:
            days_diff = (event_time.date() - now.date()).days
            if days_diff <= 7:
                return f"In {days_diff} day{'s' if days_diff != 1 else ''}"
            else:
                return event_time.strftime('%m/%d at %H:%M')
    except Exception:
        return "Soon"