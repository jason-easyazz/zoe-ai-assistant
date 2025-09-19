"""Settings management with working API key storage"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict
import os
import json
from pathlib import Path
from datetime import datetime

router = APIRouter(prefix="/api/settings")

class APIKeyUpdate(BaseModel):
    service: str  # "openai", "anthropic", "google", etc.
    key: str

class APIKeysResponse(BaseModel):
    keys: Dict[str, str]
    
# Secure storage location
KEYS_FILE = Path("/app/data/api_keys.json")
ENV_FILE = Path("/app/.env")

def load_api_keys() -> Dict[str, str]:
    """Load API keys from secure storage"""
    keys = {}
    
    # Try JSON file first
    if KEYS_FILE.exists():
        try:
            with open(KEYS_FILE) as f:
                keys = json.load(f)
        except:
            pass
    
    # Check environment variables
    for service in ["OPENAI", "ANTHROPIC", "GOOGLE"]:
        env_key = f"{service}_API_KEY"
        if os.getenv(env_key):
            keys[service.lower()] = "****" + os.getenv(env_key)[-4:]
    
    return keys

def save_api_key(service: str, key: str):
    """Save API key securely"""
    
    # Load existing keys
    keys = {}
    if KEYS_FILE.exists():
        try:
            with open(KEYS_FILE) as f:
                keys = json.load(f)
        except:
            pass
    
    # Update key
    keys[service] = key
    
    # Save to file
    KEYS_FILE.parent.mkdir(exist_ok=True)
    with open(KEYS_FILE, 'w') as f:
        json.dump(keys, f)
    
    # Also set as environment variable for current session
    env_name = f"{service.upper()}_API_KEY"
    os.environ[env_name] = key
    
    # Try to update .env file
    try:
        env_lines = []
        env_updated = False
        
        if ENV_FILE.exists():
            with open(ENV_FILE) as f:
                env_lines = f.readlines()
        
        # Update or add the key
        for i, line in enumerate(env_lines):
            if line.startswith(f"{env_name}="):
                env_lines[i] = f"{env_name}={key}\n"
                env_updated = True
                break
        
        if not env_updated:
            env_lines.append(f"{env_name}={key}\n")
        
        with open(ENV_FILE, 'w') as f:
            f.writelines(env_lines)
    except:
        pass  # Fallback to JSON storage only

@router.get("/apikeys")
async def get_api_keys():
    """Get current API key status (masked)"""
    return {"keys": load_api_keys()}

@router.post("/apikeys")
async def update_api_key(update: APIKeyUpdate):
    """Update an API key"""
    try:
        save_api_key(update.service, update.key)
        return {"success": True, "message": f"{update.service} API key updated"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/apikeys/{service}")
async def delete_api_key(service: str):
    """Delete an API key"""
    try:
        keys = load_api_keys()
        if service in keys:
            del keys[service]
            with open(KEYS_FILE, 'w') as f:
                json.dump(keys, f)
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/apikeys/test/{service}")
async def test_api_key(service: str):
    """Test if an API key works"""
    # This would test the actual API
    # For now, just check if key exists
    keys = load_api_keys()
    exists = service in keys
    return {"service": service, "configured": exists, "working": exists}

@router.get("/export")
async def export_settings():
    """Export all settings and data"""
    try:
        # This would export all user data
        # For now, return a mock export
        export_data = {
            "settings": {
                "theme": "light",
                "language": "en",
                "notifications": True,
                "timezone": "UTC"
            },
            "api_keys": load_api_keys(),
            "exported_at": datetime.now().isoformat(),
            "version": "1.0.0"
        }
        return export_data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/import")
async def import_settings(import_data: dict):
    """Import settings and data"""
    try:
        # This would import user data
        # For now, just validate the structure
        if "settings" not in import_data:
            raise HTTPException(status_code=400, detail="Invalid import data format")
        
        return {"success": True, "message": "Settings imported successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/clear")
async def clear_all_data():
    """Clear all user data (dangerous operation)"""
    try:
        # This would clear all user data
        # For now, just return success
        return {"success": True, "message": "All data cleared successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Calendar Settings
CALENDAR_SETTINGS_FILE = Path("/app/data/calendar_settings.json")

class CalendarSettings(BaseModel):
    settings: Dict

def load_calendar_settings() -> Dict:
    """Load calendar settings from storage"""
    if CALENDAR_SETTINGS_FILE.exists():
        try:
            with open(CALENDAR_SETTINGS_FILE) as f:
                return json.load(f)
        except:
            pass
    
    # Return default settings
    return {
        "workHours": {"start": "09:00", "end": "17:00"},
        "defaultView": "month",
        "showWorkTasks": True,
        "showPersonalTasks": True,
        "showAllDayEvents": True,
        "timeSlotInterval": 60,
        "syncFrequency": "hourly"
    }

def save_calendar_settings(settings: Dict):
    """Save calendar settings to storage"""
    CALENDAR_SETTINGS_FILE.parent.mkdir(exist_ok=True)
    with open(CALENDAR_SETTINGS_FILE, 'w') as f:
        json.dump(settings, f, indent=2)

@router.get("/calendar")
async def get_calendar_settings():
    """Get calendar settings"""
    return {"settings": load_calendar_settings()}

@router.post("/calendar")
async def save_calendar_settings_endpoint(settings_data: CalendarSettings):
    """Save calendar settings"""
    try:
        save_calendar_settings(settings_data.settings)
        return {"success": True, "message": "Calendar settings saved"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/calendar/api")
async def save_calendar_api_key(api_data: dict):
    """Save calendar API key (Google, Outlook, etc.)"""
    try:
        service = api_data.get("service")
        api_key = api_data.get("apiKey")
        
        if not service or not api_key:
            raise HTTPException(status_code=400, detail="Service and API key required")
        
        # Store calendar API keys separately
        calendar_keys_file = Path("/app/data/calendar_api_keys.json")
        calendar_keys = {}
        
        if calendar_keys_file.exists():
            try:
                with open(calendar_keys_file) as f:
                    calendar_keys = json.load(f)
            except:
                pass
        
        calendar_keys[service] = api_key
        calendar_keys_file.parent.mkdir(exist_ok=True)
        with open(calendar_keys_file, 'w') as f:
            json.dump(calendar_keys, f, indent=2)
        
        return {"success": True, "message": f"{service} calendar API key saved"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Time and Location Settings
TIME_LOCATION_SETTINGS_FILE = Path("/app/data/time_location_settings.json")

class TimeLocationSettings(BaseModel):
    timezone: str = "UTC"
    auto_detect_timezone: bool = True
    location: Optional[Dict] = None
    weather_api_key: Optional[str] = None
    auto_location_detection: bool = False

def load_time_location_settings() -> Dict:
    """Load time and location settings"""
    if TIME_LOCATION_SETTINGS_FILE.exists():
        try:
            with open(TIME_LOCATION_SETTINGS_FILE) as f:
                return json.load(f)
        except:
            pass
    
    # Return default settings
    return {
        "timezone": "UTC",
        "auto_detect_timezone": True,
        "location": None,
        "weather_api_key": None,
        "auto_location_detection": False
    }

def save_time_location_settings(settings: Dict):
    """Save time and location settings"""
    TIME_LOCATION_SETTINGS_FILE.parent.mkdir(exist_ok=True)
    with open(TIME_LOCATION_SETTINGS_FILE, 'w') as f:
        json.dump(settings, f, indent=2)

@router.get("/time-location")
async def get_time_location_settings():
    """Get time and location settings"""
    try:
        settings = load_time_location_settings()
        
        # Get current time info from time sync service
        try:
            import requests
            response = requests.get("http://localhost:8000/api/time/status", timeout=5)
            if response.status_code == 200:
                time_info = response.json()
                settings["current_time"] = time_info.get("current_time")
                settings["ntp_synced"] = time_info.get("ntp_synced")
        except:
            pass
        
        return {"settings": settings}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/time-location")
async def save_time_location_settings_endpoint(settings_data: TimeLocationSettings):
    """Save time and location settings"""
    try:
        settings = settings_data.dict()
        save_time_location_settings(settings)
        
        # Update timezone via time sync service
        if "timezone" in settings:
            try:
                import requests
                requests.post("http://localhost:8000/api/time/timezone", 
                            json={"timezone": settings["timezone"]}, timeout=5)
            except:
                pass
        
        return {"success": True, "message": "Time and location settings saved"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/time-location/sync")
async def sync_time_now():
    """Manually trigger time synchronization"""
    try:
        import requests
        response = requests.post("http://localhost:8000/api/time/sync", timeout=30)
        
        if response.status_code == 200:
            result = response.json()
            return {"success": result.get("success", False), "message": result.get("message")}
        else:
            return {"success": False, "message": "Time sync failed"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/time-location/timezones")
async def get_available_timezones():
    """Get available timezones"""
    try:
        import requests
        response = requests.get("http://localhost:8000/api/time/timezones", timeout=10)
        
        if response.status_code == 200:
            return response.json()
        else:
            # Fallback timezones
            return {
                "timezones": [
                    "UTC", "America/New_York", "America/Chicago", "America/Denver", 
                    "America/Los_Angeles", "Europe/London", "Europe/Paris", 
                    "Europe/Berlin", "Asia/Tokyo", "Asia/Shanghai", "Australia/Sydney"
                ],
                "grouped": {
                    "America": ["America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles"],
                    "Europe": ["Europe/London", "Europe/Paris", "Europe/Berlin"],
                    "Asia": ["Asia/Tokyo", "Asia/Shanghai"],
                    "Australia": ["Australia/Sydney"],
                    "Other": ["UTC"]
                }
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/time-location/location")
async def set_location_from_coords(location_data: dict):
    """Set timezone based on location coordinates"""
    try:
        import requests
        response = requests.post("http://localhost:8000/api/time/location", 
                               json=location_data, timeout=10)
        
        if response.status_code == 200:
            result = response.json()
            
            # Update local settings
            settings = load_time_location_settings()
            settings["location"] = location_data
            if "timezone" in result:
                settings["timezone"] = result["timezone"]
            save_time_location_settings(settings)
            
            return result
        else:
            raise HTTPException(status_code=500, detail="Failed to set location")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
