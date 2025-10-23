"""
Touch Panel API Endpoints
Optimized endpoints for touch panel quick authentication and offline support
"""

from fastapi import APIRouter, HTTPException, Depends, Query, Request
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
import logging

from touch_panel.quick_auth import get_quick_auth_manager, QuickAuthResult, TouchPanelConfig
from touch_panel.cache import cache_manager
from api.dependencies import get_current_session, optional_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/touch-panel", tags=["touch-panel"])

# Pydantic models
class TouchPanelAuthRequest(BaseModel):
    """Touch panel authentication request"""
    username: str = Field(..., min_length=1, max_length=50)
    passcode: str = Field(..., min_length=4, max_length=8, pattern=r'^\d+$')
    device_id: str = Field(..., min_length=1, max_length=100)
    location: str = Field(default="unknown")

class QuickSwitchRequest(BaseModel):
    """Quick user switch request"""
    current_session_id: Optional[str] = None
    new_username: str = Field(..., min_length=1, max_length=50)
    new_passcode: str = Field(..., min_length=4, max_length=8, pattern=r'^\d+$')
    device_id: str = Field(..., min_length=1, max_length=100)

class SessionValidationRequest(BaseModel):
    """Session validation request"""
    session_id: str
    device_id: str

class TouchPanelResponse(BaseModel):
    """Touch panel response model"""
    success: bool
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    username: Optional[str] = None
    permissions: List[str] = []
    expires_at: Optional[str] = None
    offline_mode: bool = False
    error_message: Optional[str] = None
    device_status: Optional[Dict[str, Any]] = None

# Touch Panel Authentication Endpoints
@router.post("/auth", response_model=TouchPanelResponse)
async def touch_panel_auth(request: TouchPanelAuthRequest, http_request: Request):
    """
    Fast passcode authentication for touch panels
    
    Args:
        request: Authentication request with device info
        http_request: FastAPI request for IP extraction
        
    Returns:
        TouchPanelResponse with authentication result
    """
    try:
        # Get or create quick auth manager for device
        quick_auth = get_quick_auth_manager(request.device_id, request.location)
        
        # Authenticate with passcode
        result = await quick_auth.authenticate_passcode(
            username=request.username,
            passcode=request.passcode,
            device_info={
                "type": "touch_panel",
                "device_id": request.device_id,
                "location": request.location,
                "ip_address": http_request.client.host if http_request.client else None,
                "user_agent": http_request.headers.get("user-agent")
            }
        )

        return TouchPanelResponse(
            success=result.success,
            user_id=result.user_id,
            session_id=result.session_id,
            username=result.username,
            permissions=result.permissions or [],
            expires_at=result.session_expires.isoformat() if result.session_expires else None,
            offline_mode=result.offline_mode,
            error_message=result.error_message
        )

    except Exception as e:
        logger.error(f"Touch panel auth error: {e}")
        raise HTTPException(status_code=500, detail="Authentication failed")

@router.post("/quick-switch", response_model=TouchPanelResponse)
async def quick_user_switch(request: QuickSwitchRequest, http_request: Request):
    """
    Quick user switching for touch panels
    
    Args:
        request: Quick switch request
        http_request: FastAPI request for IP extraction
        
    Returns:
        TouchPanelResponse with new user session
    """
    try:
        # Get quick auth manager for device
        quick_auth = get_quick_auth_manager(request.device_id)
        
        # Perform quick switch
        result = await quick_auth.quick_user_switch(
            current_session_id=request.current_session_id,
            new_username=request.new_username,
            new_passcode=request.new_passcode
        )

        return TouchPanelResponse(
            success=result.success,
            user_id=result.user_id,
            session_id=result.session_id,
            username=result.username,
            permissions=result.permissions or [],
            expires_at=result.session_expires.isoformat() if result.session_expires else None,
            offline_mode=result.offline_mode,
            error_message=result.error_message
        )

    except Exception as e:
        logger.error(f"Quick switch error: {e}")
        raise HTTPException(status_code=500, detail="Quick switch failed")

@router.post("/validate-session", response_model=TouchPanelResponse)
async def validate_touch_session(request: SessionValidationRequest):
    """
    Validate touch panel session (with offline support)
    
    Args:
        request: Session validation request
        
    Returns:
        TouchPanelResponse with validation result
    """
    try:
        # Get quick auth manager for device
        quick_auth = get_quick_auth_manager(request.device_id)
        
        # Validate session
        result = await quick_auth.validate_session(request.session_id)

        return TouchPanelResponse(
            success=result.success,
            user_id=result.user_id,
            session_id=result.session_id,
            username=result.username,
            permissions=result.permissions or [],
            expires_at=result.session_expires.isoformat() if result.session_expires else None,
            offline_mode=result.offline_mode,
            error_message=result.error_message
        )

    except Exception as e:
        logger.error(f"Session validation error: {e}")
        raise HTTPException(status_code=500, detail="Session validation failed")

@router.post("/logout")
async def touch_panel_logout(
    session_id: str = Query(..., description="Session ID to logout"),
    device_id: str = Query(..., description="Touch panel device ID")
):
    """
    Logout from touch panel session
    
    Args:
        session_id: Session ID to invalidate
        device_id: Touch panel device ID
        
    Returns:
        Success message
    """
    try:
        # Get quick auth manager for device
        quick_auth = get_quick_auth_manager(device_id)
        
        # Invalidate session
        await quick_auth._invalidate_session(session_id)
        
        return {"message": "Logged out successfully"}

    except Exception as e:
        logger.error(f"Touch panel logout error: {e}")
        raise HTTPException(status_code=500, detail="Logout failed")

# Touch Panel Management Endpoints
@router.get("/users")
async def get_cached_users(
    device_id: str = Query(..., description="Touch panel device ID")
):
    """
    Get cached users for touch panel (offline display)
    
    Args:
        device_id: Touch panel device ID
        
    Returns:
        List of cached users
    """
    try:
        # Get quick auth manager for device
        quick_auth = get_quick_auth_manager(device_id)
        
        # Get cached users
        cached_users = await quick_auth.get_cached_users()
        
        return {"users": cached_users, "device_id": device_id}

    except Exception as e:
        logger.error(f"Get cached users error: {e}")
        raise HTTPException(status_code=500, detail="Failed to get cached users")

@router.get("/status")
async def get_device_status(
    device_id: str = Query(..., description="Touch panel device ID")
):
    """
    Get touch panel device status
    
    Args:
        device_id: Touch panel device ID
        
    Returns:
        Device status and statistics
    """
    try:
        # Get quick auth manager for device
        quick_auth = get_quick_auth_manager(device_id)
        
        # Get device status
        status = await quick_auth.get_device_status()
        
        return status

    except Exception as e:
        logger.error(f"Get device status error: {e}")
        raise HTTPException(status_code=500, detail="Failed to get device status")

@router.post("/sync")
async def sync_device_cache(
    device_id: str = Query(..., description="Touch panel device ID")
):
    """
    Manually trigger cache sync for touch panel
    
    Args:
        device_id: Touch panel device ID
        
    Returns:
        Sync result
    """
    try:
        # Get quick auth manager for device
        quick_auth = get_quick_auth_manager(device_id)
        
        # Trigger sync
        success = await quick_auth.sync_with_server()
        
        if success:
            return {"message": "Cache synced successfully", "device_id": device_id}
        else:
            return {"message": "Sync failed - server unreachable", "device_id": device_id}

    except Exception as e:
        logger.error(f"Device sync error: {e}")
        raise HTTPException(status_code=500, detail="Sync failed")

@router.delete("/cache")
async def clear_device_cache(
    device_id: str = Query(..., description="Touch panel device ID")
):
    """
    Clear cache for touch panel device
    
    Args:
        device_id: Touch panel device ID
        
    Returns:
        Success message
    """
    try:
        # Get cache for device
        cache = cache_manager.get_cache(device_id)
        
        # Clear cache
        success = cache.clear_cache()
        
        if success:
            return {"message": "Cache cleared successfully", "device_id": device_id}
        else:
            raise HTTPException(status_code=500, detail="Failed to clear cache")

    except Exception as e:
        logger.error(f"Clear cache error: {e}")
        raise HTTPException(status_code=500, detail="Failed to clear cache")

# Touch Panel Configuration Endpoints
@router.get("/config")
async def get_device_config(
    device_id: str = Query(..., description="Touch panel device ID")
):
    """
    Get touch panel configuration
    
    Args:
        device_id: Touch panel device ID
        
    Returns:
        Device configuration
    """
    try:
        # Get quick auth manager for device
        quick_auth = get_quick_auth_manager(device_id)
        
        config = {
            "device_id": quick_auth.config.device_id,
            "location": quick_auth.config.location,
            "offline_enabled": quick_auth.config.offline_enabled,
            "auto_sync_interval": quick_auth.config.auto_sync_interval,
            "server_timeout": quick_auth.config.server_timeout,
            "max_offline_duration": quick_auth.config.max_offline_duration,
            "allowed_auth_methods": quick_auth.config.allowed_auth_methods or ["passcode"]
        }
        
        return config

    except Exception as e:
        logger.error(f"Get device config error: {e}")
        raise HTTPException(status_code=500, detail="Failed to get device config")

# Touch Panel Health and Diagnostics
@router.get("/health")
async def touch_panel_health(
    device_id: str = Query(..., description="Touch panel device ID")
):
    """
    Health check for touch panel
    
    Args:
        device_id: Touch panel device ID
        
    Returns:
        Health status
    """
    try:
        # Get quick auth manager for device
        quick_auth = get_quick_auth_manager(device_id)
        
        # Get comprehensive status
        status = await quick_auth.get_device_status()
        
        # Add health indicators
        health = {
            "status": "healthy" if not status.get("offline_mode", True) else "degraded",
            "device_id": device_id,
            "timestamp": status.get("last_server_contact"),
            "offline_mode": status.get("offline_mode", True),
            "cache_stats": status.get("cache_stats", {}),
            "sync_needed": status.get("sync_needed", True)
        }
        
        # Determine overall health
        if status.get("offline_mode") and status.get("cache_stats", {}).get("cached_users", 0) == 0:
            health["status"] = "unhealthy"
            health["issues"] = ["Offline mode with no cached users"]
        elif status.get("sync_needed"):
            health["status"] = "degraded"
            health["issues"] = ["Cache sync needed"]
        
        return health

    except Exception as e:
        logger.error(f"Touch panel health error: {e}")
        return {
            "status": "unhealthy",
            "device_id": device_id,
            "error": str(e)
        }

# Bulk Operations for Multiple Touch Panels
@router.get("/devices")
async def list_touch_panel_devices():
    """
    List all registered touch panel devices
    
    Returns:
        List of touch panel devices with status
    """
    try:
        devices = []
        
        # Get all cache managers
        cache_stats = cache_manager.get_all_cache_stats()
        
        for device_id, stats in cache_stats.items():
            device_info = {
                "device_id": device_id,
                "cache_stats": stats,
                "last_sync": stats.get("last_sync"),
                "sync_stale": stats.get("sync_stale", True),
                "cached_users": stats.get("cached_users", 0),
                "cached_sessions": stats.get("cached_sessions", 0)
            }
            devices.append(device_info)
        
        return {"devices": devices, "total": len(devices)}

    except Exception as e:
        logger.error(f"List devices error: {e}")
        raise HTTPException(status_code=500, detail="Failed to list devices")

@router.post("/devices/sync-all")
async def sync_all_devices():
    """
    Trigger sync for all touch panel devices
    
    Returns:
        Sync results for all devices
    """
    try:
        # This would typically be called from admin interface
        # Get sync data from main auth service
        from api.admin import get_sync_data
        
        # Note: This is a simplified version - in production you'd need proper auth
        sync_data = {
            "users": [],  # Would fetch from database
            "sync_time": "now",
            "device_id": "all"
        }
        
        # Sync all caches
        results = cache_manager.sync_all_caches(sync_data)
        
        return {"sync_results": results}

    except Exception as e:
        logger.error(f"Sync all devices error: {e}")
        raise HTTPException(status_code=500, detail="Failed to sync all devices")

@router.delete("/devices/cache-all")
async def clear_all_device_caches():
    """
    Clear cache for all touch panel devices
    
    Returns:
        Clear results for all devices
    """
    try:
        results = {}
        
        for device_id, cache in cache_manager.caches.items():
            results[device_id] = cache.clear_cache()
        
        return {"clear_results": results}

    except Exception as e:
        logger.error(f"Clear all caches error: {e}")
        raise HTTPException(status_code=500, detail="Failed to clear all caches")

