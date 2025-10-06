"""Time synchronization and timezone management system for Zoe"""
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional, Dict, List
import subprocess
import json
import time
import datetime
import socket
from pathlib import Path
import threading
import logging

# Try to import optional dependencies
try:
    import pytz
    HAS_PYTZ = True
except ImportError:
    HAS_PYTZ = False

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

router = APIRouter(prefix="/api/time")

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Storage paths
TIME_SETTINGS_FILE = Path("/app/data/time_settings.json")
TIMEZONE_DB_FILE = Path("/app/data/timezones.json")

class TimeSettings(BaseModel):
    timezone: str = "UTC"
    ntp_servers: List[str] = ["pool.ntp.org", "time.nist.gov", "time.google.com"]
    sync_interval: int = 3600  # seconds
    auto_sync: bool = True
    manual_time: Optional[str] = None
    location: Optional[Dict] = None

class TimeSyncResponse(BaseModel):
    success: bool
    message: str
    current_time: str
    timezone: str
    sync_source: str

class LocationData(BaseModel):
    latitude: float
    longitude: float
    city: str
    country: str
    timezone: str

def load_time_settings() -> Dict:
    """Load time settings from storage"""
    if TIME_SETTINGS_FILE.exists():
        try:
            with open(TIME_SETTINGS_FILE) as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load time settings: {e}")
    
    # Return default settings
    return {
        "timezone": "UTC",
        "ntp_servers": ["pool.ntp.org", "time.nist.gov", "time.google.com"],
        "sync_interval": 3600,
        "auto_sync": True,
        "manual_time": None,
        "location": None
    }

def save_time_settings(settings: Dict):
    """Save time settings to storage"""
    TIME_SETTINGS_FILE.parent.mkdir(exist_ok=True)
    with open(TIME_SETTINGS_FILE, 'w') as f:
        json.dump(settings, f, indent=2)

def get_timezone_from_location(lat: float, lon: float) -> str:
    """Get timezone from coordinates - simplified version"""
    try:
        if HAS_REQUESTS:
            # Simple timezone estimation based on longitude
            # This is a basic approximation - in production you'd use a proper timezone API
            if -180 <= lon <= -67.5:
                return "America/New_York"
            elif -67.5 < lon <= -52.5:
                return "America/Chicago" 
            elif -52.5 < lon <= -37.5:
                return "America/Denver"
            elif -37.5 < lon <= -22.5:
                return "America/Los_Angeles"
            elif -22.5 < lon <= -7.5:
                return "Atlantic/Azores"
            elif -7.5 < lon <= 7.5:
                return "Europe/London"
            elif 7.5 < lon <= 22.5:
                return "Europe/Paris"
            elif 22.5 < lon <= 37.5:
                return "Europe/Berlin"
            elif 37.5 < lon <= 52.5:
                return "Europe/Moscow"
            elif 52.5 < lon <= 67.5:
                return "Asia/Karachi"
            elif 67.5 < lon <= 82.5:
                return "Asia/Kolkata"
            elif 82.5 < lon <= 97.5:
                return "Asia/Bangkok"
            elif 97.5 < lon <= 112.5:
                return "Asia/Shanghai"
            elif 112.5 < lon <= 127.5:
                return "Asia/Tokyo"
            elif 127.5 < lon <= 142.5:
                return "Australia/Sydney"
            else:
                return "Pacific/Auckland"
        else:
            # Fallback to UTC if requests not available
            return "UTC"
    except Exception as e:
        logger.error(f"Failed to get timezone from location: {e}")
        return "UTC"

def sync_with_ntp(ntp_server: str = "pool.ntp.org") -> bool:
    """Synchronize system time with NTP server"""
    try:
        # Use ntpdate or timedatectl for time sync
        result = subprocess.run([
            "sudo", "timedatectl", "set-ntp", "true"
        ], capture_output=True, text=True, timeout=30)
        
        if result.returncode == 0:
            logger.info(f"Successfully enabled NTP sync")
            return True
        else:
            logger.error(f"NTP sync failed: {result.stderr}")
            
            # Fallback: try ntpdate
            result = subprocess.run([
                "sudo", "ntpdate", "-s", ntp_server
            ], capture_output=True, text=True, timeout=30)
            
            return result.returncode == 0
            
    except subprocess.TimeoutExpired:
        logger.error("NTP sync timed out")
        return False
    except Exception as e:
        logger.error(f"NTP sync error: {e}")
        return False

def set_timezone(timezone: str) -> bool:
    """Set system timezone"""
    try:
        result = subprocess.run([
            "sudo", "timedatectl", "set-timezone", timezone
        ], capture_output=True, text=True, timeout=10)
        
        if result.returncode == 0:
            logger.info(f"Successfully set timezone to {timezone}")
            return True
        else:
            logger.error(f"Failed to set timezone: {result.stderr}")
            return False
            
    except Exception as e:
        logger.error(f"Timezone setting error: {e}")
        return False

def get_current_time_info() -> Dict:
    """Get current time information"""
    try:
        # Get system time
        now = datetime.datetime.now()
        
        # Get timezone info
        result = subprocess.run([
            "timedatectl", "show", "--property=Timezone"
        ], capture_output=True, text=True, timeout=5)
        
        timezone = "UTC"
        if result.returncode == 0:
            timezone = result.stdout.strip().split("=")[1]
        
        # Get NTP status
        result = subprocess.run([
            "timedatectl", "show", "--property=NTPSynchronized"
        ], capture_output=True, text=True, timeout=5)
        
        ntp_synced = False
        if result.returncode == 0:
            ntp_synced = result.stdout.strip().split("=")[1] == "yes"
        
        return {
            "current_time": now.isoformat(),
            "timezone": timezone,
            "ntp_synced": ntp_synced,
            "unix_timestamp": int(now.timestamp())
        }
    except Exception as e:
        logger.error(f"Failed to get time info: {e}")
        return {
            "current_time": datetime.datetime.now().isoformat(),
            "timezone": "UTC",
            "ntp_synced": False,
            "unix_timestamp": int(time.time())
        }

def get_available_timezones() -> List[str]:
    """Get list of available timezones"""
    try:
        # Get timezone list from system
        result = subprocess.run([
            "timedatectl", "list-timezones"
        ], capture_output=True, text=True, timeout=10)
        
        if result.returncode == 0:
            return result.stdout.strip().split('\n')
        else:
            # Fallback to pytz timezones if available
            if HAS_PYTZ:
                return list(pytz.all_timezones)
            else:
                # Return common timezones as fallback
                return [
                    "UTC", "America/New_York", "America/Chicago", "America/Denver", 
                    "America/Los_Angeles", "Europe/London", "Europe/Paris", 
                    "Europe/Berlin", "Asia/Tokyo", "Asia/Shanghai", "Australia/Sydney"
                ]
            
    except Exception as e:
        logger.error(f"Failed to get timezones: {e}")
        # Return common timezones as fallback
        return [
            "UTC", "America/New_York", "America/Chicago", "America/Denver", 
            "America/Los_Angeles", "Europe/London", "Europe/Paris", 
            "Europe/Berlin", "Asia/Tokyo", "Asia/Shanghai", "Australia/Sydney"
        ]

@router.get("/status")
async def get_time_status():
    """Get current time synchronization status"""
    try:
        time_info = get_current_time_info()
        settings = load_time_settings()
        
        return {
            "current_time": time_info["current_time"],
            "timezone": time_info["timezone"],
            "ntp_synced": time_info["ntp_synced"],
            "unix_timestamp": time_info["unix_timestamp"],
            "settings": settings,
            "last_sync": settings.get("last_sync"),
            "sync_attempts": settings.get("sync_attempts", 0)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/sync")
async def sync_time(background_tasks: BackgroundTasks):
    """Manually trigger time synchronization"""
    try:
        settings = load_time_settings()
        ntp_servers = settings.get("ntp_servers", ["pool.ntp.org"])
        
        # Try each NTP server
        sync_success = False
        sync_source = "unknown"
        
        for server in ntp_servers:
            if sync_with_ntp(server):
                sync_success = True
                sync_source = server
                break
        
        # Update settings with sync info
        settings["last_sync"] = datetime.datetime.now().isoformat()
        settings["sync_attempts"] = settings.get("sync_attempts", 0) + 1
        save_time_settings(settings)
        
        time_info = get_current_time_info()
        
        return TimeSyncResponse(
            success=sync_success,
            message="Time synchronized successfully" if sync_success else "Time sync failed",
            current_time=time_info["current_time"],
            timezone=time_info["timezone"],
            sync_source=sync_source
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/timezone")
async def set_timezone_endpoint(timezone_data: dict):
    """Set system timezone"""
    try:
        timezone = timezone_data.get("timezone")
        if not timezone:
            raise HTTPException(status_code=400, detail="Timezone is required")
        
        # Validate timezone
        if HAS_PYTZ:
            try:
                pytz.timezone(timezone)
            except pytz.UnknownTimeZoneError:
                raise HTTPException(status_code=400, detail=f"Unknown timezone: {timezone}")
        else:
            # Basic validation without pytz
            valid_timezones = ["UTC", "America/New_York", "America/Chicago", "America/Denver", 
                             "America/Los_Angeles", "Europe/London", "Europe/Paris", 
                             "Europe/Berlin", "Asia/Tokyo", "Asia/Shanghai", "Australia/Sydney"]
            if timezone not in valid_timezones:
                raise HTTPException(status_code=400, detail=f"Unknown timezone: {timezone}")
        
        # Set system timezone
        if set_timezone(timezone):
            # Update settings
            settings = load_time_settings()
            settings["timezone"] = timezone
            save_time_settings(settings)
            
            return {"success": True, "message": f"Timezone set to {timezone}"}
        else:
            raise HTTPException(status_code=500, detail="Failed to set timezone")
            
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/timezones")
async def get_timezones():
    """Get available timezones"""
    try:
        timezones = get_available_timezones()
        
        # Group by region for better UX
        grouped = {}
        for tz in timezones:
            region = tz.split('/')[0] if '/' in tz else 'Other'
            if region not in grouped:
                grouped[region] = []
            grouped[region].append(tz)
        
        return {"timezones": timezones, "grouped": grouped}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/location")
async def set_location_from_coordinates(location_data: LocationData):
    """Set timezone based on location coordinates"""
    try:
        # Get timezone from coordinates
        detected_timezone = get_timezone_from_location(
            location_data.latitude, 
            location_data.longitude
        )
        
        # Set timezone
        if set_timezone(detected_timezone):
            # Update settings
            settings = load_time_settings()
            settings["timezone"] = detected_timezone
            settings["location"] = {
                "latitude": location_data.latitude,
                "longitude": location_data.longitude,
                "city": location_data.city,
                "country": location_data.country
            }
            save_time_settings(settings)
            
            return {
                "success": True,
                "message": f"Timezone set to {detected_timezone} based on location",
                "timezone": detected_timezone,
                "location": location_data.dict()
            }
        else:
            raise HTTPException(status_code=500, detail="Failed to set timezone")
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/settings")
async def get_time_settings():
    """Get time synchronization settings"""
    try:
        settings = load_time_settings()
        time_info = get_current_time_info()
        
        return {
            "settings": settings,
            "current_status": time_info
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/settings")
async def update_time_settings(settings_data: TimeSettings):
    """Update time synchronization settings"""
    try:
        settings = settings_data.dict()
        save_time_settings(settings)
        
        # Apply timezone if changed
        if "timezone" in settings:
            set_timezone(settings["timezone"])
        
        # Enable/disable auto sync
        if "auto_sync" in settings:
            if settings["auto_sync"]:
                sync_with_ntp()
        
        return {"success": True, "message": "Time settings updated"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/auto-sync")
async def enable_auto_sync(background_tasks: BackgroundTasks):
    """Enable automatic time synchronization"""
    try:
        settings = load_time_settings()
        settings["auto_sync"] = True
        save_time_settings(settings)
        
        # Enable NTP sync
        sync_with_ntp()
        
        # Schedule periodic sync (this would be handled by a background task)
        background_tasks.add_task(periodic_sync)
        
        return {"success": True, "message": "Auto sync enabled"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/auto-sync")
async def disable_auto_sync():
    """Disable automatic time synchronization"""
    try:
        settings = load_time_settings()
        settings["auto_sync"] = False
        save_time_settings(settings)
        
        # Disable NTP sync
        subprocess.run(["sudo", "timedatectl", "set-ntp", "false"], 
                      capture_output=True, timeout=10)
        
        return {"success": True, "message": "Auto sync disabled"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

def periodic_sync():
    """Background task for periodic time synchronization"""
    while True:
        try:
            settings = load_time_settings()
            if settings.get("auto_sync", False):
                sync_with_ntp()
                logger.info("Periodic time sync completed")
            
            # Sleep for sync interval
            time.sleep(settings.get("sync_interval", 3600))
        except Exception as e:
            logger.error(f"Periodic sync error: {e}")
            time.sleep(60)  # Wait 1 minute before retrying

@router.get("/test-ntp")
async def test_ntp_servers():
    """Test connectivity to NTP servers"""
    try:
        settings = load_time_settings()
        ntp_servers = settings.get("ntp_servers", ["pool.ntp.org"])
        
        results = []
        for server in ntp_servers:
            try:
                # Test DNS resolution
                socket.gethostbyname(server)
                results.append({
                    "server": server,
                    "dns_resolved": True,
                    "reachable": True  # Simplified for now
                })
            except socket.gaierror:
                results.append({
                    "server": server,
                    "dns_resolved": False,
                    "reachable": False
                })
        
        return {"ntp_tests": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Initialize time sync on startup
@router.on_event("startup")
async def startup_time_sync():
    """Initialize time synchronization on startup"""
    try:
        settings = load_time_settings()
        
        # Enable NTP sync if auto_sync is enabled
        if settings.get("auto_sync", True):
            sync_with_ntp()
            logger.info("Time sync initialized on startup")
    except Exception as e:
        logger.error(f"Startup time sync failed: {e}")



