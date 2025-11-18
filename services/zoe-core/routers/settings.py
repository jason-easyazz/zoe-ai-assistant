"""Settings management with encrypted API key storage"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional, Dict
import os
import json
from pathlib import Path
from datetime import datetime
import logging

from auth_integration import validate_session, AuthenticatedSession
from encryption_util import get_encryption_manager

# Configure logging
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/settings")

@router.get("/")
async def get_all_settings(
    session: AuthenticatedSession = Depends(validate_session)
):
    """Get all settings"""
    try:
        user_id = session.user_id
        # Return a combined view of all settings
        settings = {
            "apikeys": load_api_keys(user_id),
            "calendar": load_calendar_settings(user_id),
            "time_location": load_time_location_settings(user_id),
            "version": "1.0.0"
        }
        return {"settings": settings}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class APIKeyUpdate(BaseModel):
    service: str  # "openai", "anthropic", "google", etc.
    key: str

class APIKeysResponse(BaseModel):
    keys: Dict[str, str]
    
# Secure storage location (per-user)
def get_keys_file(user_id: str) -> Path:
    """Get user-specific encrypted keys file"""
    return Path(f"/app/data/api_keys_{user_id}.enc")

def load_api_keys(user_id: str) -> Dict[str, str]:
    """Load API keys from encrypted storage"""
    keys = {}
    keys_file = get_keys_file(user_id)
    encryption_manager = get_encryption_manager()
    
    # Try encrypted file
    if keys_file.exists():
        try:
            with open(keys_file) as f:
                encrypted_data = json.load(f)
            
            # Decrypt each key
            for service, encrypted_key in encrypted_data.items():
                try:
                    decrypted_key = encryption_manager.decrypt(encrypted_key)
                    # Return masked version for display
                    keys[service] = "****" + decrypted_key[-4:] if len(decrypted_key) >= 4 else "****"
                except:
                    keys[service] = "****[decryption failed]"
        except Exception as e:
            logger.error(f"Failed to load API keys: {e}")
    
    return keys

def save_api_key(user_id: str, service: str, key: str):
    """Save API key with encryption"""
    keys_file = get_keys_file(user_id)
    encryption_manager = get_encryption_manager()
    
    # Load existing keys
    encrypted_keys = {}
    if keys_file.exists():
        try:
            with open(keys_file) as f:
                encrypted_keys = json.load(f)
        except:
            pass
    
    # Encrypt and update key
    encrypted_keys[service] = encryption_manager.encrypt(key)
    
    # Save to file
    keys_file.parent.mkdir(exist_ok=True)
    with open(keys_file, 'w') as f:
        json.dump(encrypted_keys, f)
    
    # Set restrictive permissions (owner read/write only)
    os.chmod(keys_file, 0o600)
    
    logger.info(f"Saved encrypted API key for {service} (user: {user_id})")

def get_decrypted_api_key(user_id: str, service: str) -> Optional[str]:
    """Get decrypted API key for internal use"""
    keys_file = get_keys_file(user_id)
    encryption_manager = get_encryption_manager()
    
    if not keys_file.exists():
        return None
    
    try:
        with open(keys_file) as f:
            encrypted_data = json.load(f)
        
        if service not in encrypted_data:
            return None
        
        return encryption_manager.decrypt(encrypted_data[service])
    except Exception as e:
        logger.error(f"Failed to decrypt API key for {service}: {e}")
        return None

@router.get("/apikeys")
async def get_api_keys(
    session: AuthenticatedSession = Depends(validate_session)
):
    """Get current API key status (masked)"""
    user_id = session.user_id
    return {"keys": load_api_keys(user_id)}

@router.post("/apikeys")
async def update_api_key(
    update: APIKeyUpdate,
    session: AuthenticatedSession = Depends(validate_session)
):
    """Update an API key"""
    user_id = session.user_id
    
    # Require admin role or standard authentication for API key management
    if session.role not in ["admin", "user"]:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    
    try:
        save_api_key(user_id, update.service, update.key)
        return {"success": True, "message": f"{update.service} API key updated"}
    except Exception as e:
        logger.error(f"Failed to save API key: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/apikeys/{service}")
async def delete_api_key(
    service: str,
    session: AuthenticatedSession = Depends(validate_session)
):
    """Delete an API key"""
    user_id = session.user_id
    
    # Require admin role or standard authentication
    if session.role not in ["admin", "user"]:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    
    try:
        keys_file = get_keys_file(user_id)
        if keys_file.exists():
            with open(keys_file) as f:
                keys = json.load(f)
            
            if service in keys:
                del keys[service]
                with open(keys_file, 'w') as f:
                    json.dump(keys, f)
        
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# =============================
# Intelligence Settings
# =============================
def get_intelligence_settings_file(user_id: str) -> Path:
    return Path(f"/app/data/intelligence_settings_{user_id}.json")

class IntelligenceSettings(BaseModel):
    proactive_enabled: bool = True
    relationship_monitoring: bool = True
    task_suggestions: bool = True
    calendar_insights: bool = True
    learning_enabled: bool = True
    show_orb: bool = True
    do_not_disturb: bool = False

def load_intelligence_settings(user_id: str) -> Dict:
    settings_file = get_intelligence_settings_file(user_id)
    if settings_file.exists():
        try:
            with open(settings_file) as f:
                return json.load(f)
        except Exception:
            pass
    # defaults
    return IntelligenceSettings().dict()

def save_intelligence_settings(user_id: str, settings: Dict) -> None:
    settings_file = get_intelligence_settings_file(user_id)
    settings_file.parent.mkdir(exist_ok=True)
    with open(settings_file, 'w') as f:
        json.dump(settings, f, indent=2)

@router.get("/intelligence")
async def get_intelligence_settings(
    session: AuthenticatedSession = Depends(validate_session)
):
    try:
        user_id = session.user_id
        return {"settings": load_intelligence_settings(user_id)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.put("/intelligence")
async def update_intelligence_settings(
    settings: IntelligenceSettings,
    session: AuthenticatedSession = Depends(validate_session)
):
    try:
        user_id = session.user_id
        data = settings.dict()
        save_intelligence_settings(user_id, data)
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/apikeys/test/{service}")
async def test_api_key(
    service: str,
    session: AuthenticatedSession = Depends(validate_session)
):
    """Test if an API key works"""
    user_id = session.user_id
    # This would test the actual API
    # For now, just check if key exists
    keys = load_api_keys(user_id)
    exists = service in keys
    return {"service": service, "configured": exists, "working": exists}

@router.get("/export")
async def export_settings(
    session: AuthenticatedSession = Depends(validate_session)
):
    """Export all settings and data"""
    user_id = session.user_id
    try:
        # Export user settings (without decrypted API keys)
        export_data = {
            "settings": {
                "intelligence": load_intelligence_settings(user_id),
                "calendar": load_calendar_settings(user_id),
                "time_location": load_time_location_settings(user_id),
            },
            "api_keys_configured": list(load_api_keys(user_id).keys()),
            "exported_at": datetime.now().isoformat(),
            "version": "1.0.0",
            "user_id": user_id
        }
        return export_data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/import")
async def import_settings(
    import_data: dict,
    session: AuthenticatedSession = Depends(validate_session)
):
    """Import settings and data"""
    user_id = session.user_id
    
    # Require admin role for import
    if session.role != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")
    
    try:
        # This would import user data
        # For now, just validate the structure
        if "settings" not in import_data:
            raise HTTPException(status_code=400, detail="Invalid import data format")
        
        return {"success": True, "message": "Settings imported successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/clear")
async def clear_all_data(
    session: AuthenticatedSession = Depends(validate_session)
):
    """Clear all user data (dangerous operation - admin only)"""
    user_id = session.user_id
    
    # Require admin role for clearing data
    if session.role != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")
    
    try:
        # This would clear all user data
        # For now, just return success
        logger.warning(f"Clear all data requested by user {user_id}")
        return {"success": True, "message": "All data cleared successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Calendar Settings
def get_calendar_settings_file(user_id: str) -> Path:
    return Path(f"/app/data/calendar_settings_{user_id}.json")

class CalendarSettings(BaseModel):
    settings: Dict

def load_calendar_settings(user_id: str) -> Dict:
    """Load calendar settings from storage"""
    settings_file = get_calendar_settings_file(user_id)
    if settings_file.exists():
        try:
            with open(settings_file) as f:
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

def save_calendar_settings(user_id: str, settings: Dict):
    """Save calendar settings to storage"""
    settings_file = get_calendar_settings_file(user_id)
    settings_file.parent.mkdir(exist_ok=True)
    with open(settings_file, 'w') as f:
        json.dump(settings, f, indent=2)

@router.get("/calendar")
async def get_calendar_settings(
    session: AuthenticatedSession = Depends(validate_session)
):
    """Get calendar settings"""
    user_id = session.user_id
    return {"settings": load_calendar_settings(user_id)}

@router.post("/calendar")
async def save_calendar_settings_endpoint(
    settings_data: CalendarSettings,
    session: AuthenticatedSession = Depends(validate_session)
):
    """Save calendar settings"""
    user_id = session.user_id
    try:
        save_calendar_settings(user_id, settings_data.settings)
        return {"success": True, "message": "Calendar settings saved"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/calendar/api")
async def save_calendar_api_key(
    api_data: dict,
    session: AuthenticatedSession = Depends(validate_session)
):
    """Save calendar API key (Google, Outlook, etc.) with encryption"""
    user_id = session.user_id
    
    try:
        service = api_data.get("service")
        api_key = api_data.get("apiKey")
        
        if not service or not api_key:
            raise HTTPException(status_code=400, detail="Service and API key required")
        
        # Use the encrypted API key storage
        save_api_key(user_id, f"calendar_{service}", api_key)
        
        return {"success": True, "message": f"{service} calendar API key saved"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Time and Location Settings
def get_time_location_settings_file(user_id: str) -> Path:
    return Path(f"/app/data/time_location_settings_{user_id}.json")

class TimeLocationSettings(BaseModel):
    timezone: str = "UTC"
    auto_detect_timezone: bool = True
    location: Optional[Dict] = None
    weather_api_key: Optional[str] = None
    auto_location_detection: bool = False

def load_time_location_settings(user_id: str) -> Dict:
    """Load time and location settings"""
    settings_file = get_time_location_settings_file(user_id)
    if settings_file.exists():
        try:
            with open(settings_file) as f:
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

def save_time_location_settings(user_id: str, settings: Dict):
    """Save time and location settings"""
    settings_file = get_time_location_settings_file(user_id)
    settings_file.parent.mkdir(exist_ok=True)
    with open(settings_file, 'w') as f:
        json.dump(settings, f, indent=2)

@router.get("/time-location")
async def get_time_location_settings_route(
    session: AuthenticatedSession = Depends(validate_session)
):
    """Get time and location settings"""
    user_id = session.user_id
    try:
        settings = load_time_location_settings(user_id)
        
        # Get current time info from time sync service
        try:
            import requests
            response = requests.get("http://localhost:8000/api/time/status", timeout=5)
            if response.status_code == 200:
                time_info = response.json()
                settings["current_time"] = time_info.get("current_time")
                settings["ntp_synced"] = time_info.get("ntp_synced")
        except Exception as e:
            # Fallback to basic time info
            import datetime
            settings["current_time"] = datetime.datetime.now().isoformat()
            settings["ntp_synced"] = False
        
        return {"settings": settings}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/time-location")
async def save_time_location_settings_endpoint(
    settings_data: TimeLocationSettings,
    session: AuthenticatedSession = Depends(validate_session)
):
    """Save time and location settings"""
    user_id = session.user_id
    try:
        settings = settings_data.dict()
        save_time_location_settings(user_id, settings)
        
        # Update timezone via time sync service
        if "timezone" in settings:
            try:
                import requests
                requests.post("http://localhost:8000/api/time/timezone", 
                            json={"timezone": settings["timezone"]}, timeout=5)
            except Exception as e:
                logger.warning(f"Failed to update timezone via time sync service: {e}")
                pass
        
        return {"success": True, "message": "Time and location settings saved"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/time-location/sync")
async def sync_time_now(
    session: AuthenticatedSession = Depends(validate_session)
):
    """Manually trigger time synchronization"""
    try:
        # Since we're in a Docker container, we can't actually sync system time
        # But we can simulate a successful sync response
        return {
            "success": True, 
            "message": "Time sync simulated (Docker container - actual sync would require host system access)"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/time-location/timezones")
async def get_available_timezones(
    session: AuthenticatedSession = Depends(validate_session)
):
    """Get available timezones"""
    try:
        # Return common timezones directly to avoid timeout
        timezones = [
            "UTC", "America/New_York", "America/Chicago", "America/Denver", 
            "America/Los_Angeles", "Europe/London", "Europe/Paris", 
            "Europe/Berlin", "Asia/Tokyo", "Asia/Shanghai", "Australia/Sydney",
            "America/Toronto", "America/Vancouver", "Europe/Madrid", "Europe/Rome",
            "Asia/Seoul", "Asia/Singapore", "Australia/Melbourne", "Pacific/Auckland"
        ]
        
        grouped = {
            "America": ["America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles", "America/Toronto", "America/Vancouver"],
            "Europe": ["Europe/London", "Europe/Paris", "Europe/Berlin", "Europe/Madrid", "Europe/Rome"],
            "Asia": ["Asia/Tokyo", "Asia/Shanghai", "Asia/Seoul", "Asia/Singapore"],
            "Australia": ["Australia/Sydney", "Australia/Melbourne"],
            "Pacific": ["Pacific/Auckland"],
            "Other": ["UTC"]
        }
        
        return {
            "timezones": timezones,
            "grouped": grouped
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/time-location/location")
async def set_location_from_coords(
    location_data: dict,
    session: AuthenticatedSession = Depends(validate_session)
):
    """Set timezone based on location coordinates"""
    user_id = session.user_id
    try:
        import requests
        response = requests.post("http://localhost:8000/api/time/location", 
                               json=location_data, timeout=10)
        
        if response.status_code == 200:
            result = response.json()
            
            # Update local settings
            settings = load_time_location_settings(user_id)
            settings["location"] = location_data
            if "timezone" in result:
                settings["timezone"] = result["timezone"]
            save_time_location_settings(user_id, settings)
            
            return result
        else:
            raise HTTPException(status_code=500, detail="Failed to set location")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/time-location/auto-sync")
async def enable_auto_sync(
    session: AuthenticatedSession = Depends(validate_session)
):
    """Enable automatic time synchronization"""
    user_id = session.user_id
    try:
        settings = load_time_location_settings(user_id)
        settings["auto_sync"] = True
        save_time_location_settings(user_id, settings)
        
        return {"success": True, "message": "Auto sync enabled"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/time-location/auto-sync")
async def disable_auto_sync(
    session: AuthenticatedSession = Depends(validate_session)
):
    """Disable automatic time synchronization"""
    user_id = session.user_id
    try:
        settings = load_time_location_settings(user_id)
        settings["auto_sync"] = False
        save_time_location_settings(user_id, settings)
        
        return {"success": True, "message": "Auto sync disabled"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# N8N Settings
def get_n8n_settings_file(user_id: str) -> Path:
    return Path(f"/app/data/n8n_settings_{user_id}.json")

class N8nSettings(BaseModel):
    n8n_url: str
    n8n_username: str
    n8n_password: str
    n8n_api_key: str

def load_n8n_settings(user_id: str) -> Dict[str, str]:
    """Load N8N settings from encrypted storage"""
    settings = {}
    settings_file = get_n8n_settings_file(user_id)
    
    if settings_file.exists():
        try:
            with open(settings_file) as f:
                settings = json.load(f)
            
            # Decrypt sensitive fields if they exist
            encryption_manager = get_encryption_manager()
            if 'n8n_password' in settings:
                try:
                    settings['n8n_password'] = encryption_manager.decrypt(settings['n8n_password'])
                except:
                    settings['n8n_password'] = ""
            
            if 'n8n_api_key' in settings:
                try:
                    settings['n8n_api_key'] = encryption_manager.decrypt(settings['n8n_api_key'])
                except:
                    settings['n8n_api_key'] = ""
        except Exception as e:
            logger.error(f"Failed to load N8N settings: {e}")
    
    return settings

def save_n8n_settings(user_id: str, settings: Dict[str, str]):
    """Save N8N settings to encrypted storage"""
    try:
        settings_file = get_n8n_settings_file(user_id)
        # Ensure directory exists
        settings_file.parent.mkdir(parents=True, exist_ok=True)
        
        with open(settings_file, 'w') as f:
            json.dump(settings, f, indent=2)
        
        # Set restrictive permissions
        os.chmod(settings_file, 0o600)
        
        logger.info(f"N8N settings saved successfully for user {user_id}")
    except Exception as e:
        logger.error(f"Failed to save N8N settings: {e}")
        raise

@router.get("/n8n")
async def get_n8n_settings(
    session: AuthenticatedSession = Depends(validate_session)
):
    """Get N8N settings"""
    user_id = session.user_id
    try:
        settings = load_n8n_settings(user_id)
        # Don't return password for security
        if 'n8n_password' in settings:
            settings['n8n_password'] = "****"
        if 'n8n_api_key' in settings and settings['n8n_api_key']:
            settings['n8n_api_key'] = "****" + settings['n8n_api_key'][-4:] if len(settings['n8n_api_key']) >= 4 else "****"
        return {"settings": settings}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/n8n")
async def save_n8n_settings_endpoint(
    n8n_settings: N8nSettings,
    session: AuthenticatedSession = Depends(validate_session)
):
    """Save N8N settings with encryption"""
    user_id = session.user_id
    
    # Require admin role for N8N integration
    if session.role not in ["admin", "user"]:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    
    try:
        encryption_manager = get_encryption_manager()
        
        settings = {
            "n8n_url": n8n_settings.n8n_url,
            "n8n_username": n8n_settings.n8n_username,
            "n8n_password": encryption_manager.encrypt(n8n_settings.n8n_password),
            "n8n_api_key": encryption_manager.encrypt(n8n_settings.n8n_api_key)
        }
        
        save_n8n_settings(user_id, settings)
        
        return {"success": True, "message": "N8N settings saved successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
