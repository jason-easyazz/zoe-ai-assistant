"""
SSO API Endpoints
Integration endpoints for Home Assistant, n8n, Matrix, and other services
"""

from fastapi import APIRouter, HTTPException, Depends, Request, Query
from pydantic import BaseModel, EmailStr
from typing import Optional, List, Dict, Any
import logging

from ..sso.homeassistant import ha_integration
from ..sso.n8n import n8n_integration
from ..sso.matrix import matrix_integration
from .dependencies import require_permission, get_current_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sso", tags=["sso"])

# Pydantic models
class SSOAuthRequest(BaseModel):
    """SSO authentication request"""
    username: str
    password: str
    service: str
    device_info: Dict[str, Any] = {}

class SSOSyncRequest(BaseModel):
    """SSO sync request"""
    user_id: str
    service: str

class ServiceConfigResponse(BaseModel):
    """Service configuration response"""
    service: str
    enabled: bool
    config: Dict[str, Any]

# Home Assistant SSO Endpoints
@router.post("/homeassistant/auth")
async def homeassistant_auth(request: SSOAuthRequest):
    """
    Home Assistant authentication endpoint
    
    Args:
        request: Authentication request
        
    Returns:
        Authentication result for HA
    """
    try:
        if not ha_integration:
            raise HTTPException(status_code=503, detail="Home Assistant integration not configured")

        success, user_info = await ha_integration.authenticate_ha_user(
            request.username, 
            request.password
        )

        if success:
            return {
                "success": True,
                "user_info": user_info
            }
        else:
            return {
                "success": False,
                "error": "Invalid credentials"
            }

    except Exception as e:
        logger.error(f"HA auth error: {e}")
        raise HTTPException(status_code=500, detail="Authentication failed")

@router.post("/homeassistant/sync-user")
async def sync_user_to_homeassistant(
    request: SSOSyncRequest,
    current_session = Depends(require_permission("users.sync"))
):
    """
    Sync user to Home Assistant
    
    Args:
        request: Sync request
        current_session: Current admin session
        
    Returns:
        Sync result
    """
    try:
        if not ha_integration:
            raise HTTPException(status_code=503, detail="Home Assistant integration not configured")

        success = await ha_integration.sync_user_to_ha(request.user_id)
        
        return {
            "success": success,
            "message": "User synced to Home Assistant" if success else "Sync failed"
        }

    except Exception as e:
        logger.error(f"HA sync error: {e}")
        raise HTTPException(status_code=500, detail="Sync failed")

@router.get("/homeassistant/areas")
async def get_homeassistant_areas(
    current_session = Depends(require_permission("homeassistant.read"))
):
    """
    Get Home Assistant areas
    
    Args:
        current_session: Current authenticated session
        
    Returns:
        List of HA areas
    """
    try:
        if not ha_integration:
            raise HTTPException(status_code=503, detail="Home Assistant integration not configured")

        areas = await ha_integration.get_ha_areas()
        return {"areas": areas}

    except Exception as e:
        logger.error(f"HA areas error: {e}")
        raise HTTPException(status_code=500, detail="Failed to get areas")

# n8n SSO Endpoints
@router.post("/n8n/auth")
async def n8n_auth(request: SSOAuthRequest):
    """
    n8n authentication endpoint
    
    Args:
        request: Authentication request
        
    Returns:
        Authentication result for n8n
    """
    try:
        if not n8n_integration:
            raise HTTPException(status_code=503, detail="n8n integration not configured")

        success, user_data = await n8n_integration.authenticate_n8n_user(
            request.username,  # email for n8n
            request.password
        )

        if success:
            return {
                "success": True,
                "user": user_data
            }
        else:
            return {
                "success": False,
                "error": "Invalid credentials"
            }

    except Exception as e:
        logger.error(f"n8n auth error: {e}")
        raise HTTPException(status_code=500, detail="Authentication failed")

@router.get("/n8n/user/{email}")
async def get_n8n_user_info(email: str):
    """
    Get n8n user information
    
    Args:
        email: User email
        
    Returns:
        User information for n8n
    """
    try:
        if not n8n_integration:
            raise HTTPException(status_code=503, detail="n8n integration not configured")

        # Find user by email
        from ..models.database import auth_db
        with auth_db.get_connection() as conn:
            cursor = conn.execute(
                "SELECT user_id FROM users WHERE email = ? AND is_active = 1",
                (email,)
            )
            row = cursor.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="User not found")

        user_id = row[0]
        from ..core.auth import auth_manager
        from ..core.rbac import rbac_manager
        
        user_info = auth_manager.get_user_info(user_id)
        user_permissions = rbac_manager.list_user_permissions(user_id)
        
        n8n_user = n8n_integration._map_user_to_n8n(user_info, user_permissions)
        
        return n8n_user

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"n8n user info error: {e}")
        raise HTTPException(status_code=500, detail="Failed to get user info")

@router.get("/n8n/workflows")
async def get_user_n8n_workflows(
    current_session = Depends(get_current_session)
):
    """
    Get workflows accessible to current user
    
    Args:
        current_session: Current authenticated session
        
    Returns:
        List of accessible workflows
    """
    try:
        if not n8n_integration:
            raise HTTPException(status_code=503, detail="n8n integration not configured")

        workflows = await n8n_integration.get_user_workflows(current_session.user_id)
        return {"workflows": workflows}

    except Exception as e:
        logger.error(f"n8n workflows error: {e}")
        raise HTTPException(status_code=500, detail="Failed to get workflows")

# Matrix SSO Endpoints
@router.post("/matrix/auth")
async def matrix_auth(request: SSOAuthRequest):
    """
    Matrix authentication endpoint
    
    Args:
        request: Authentication request
        
    Returns:
        Authentication result for Matrix
    """
    try:
        if not matrix_integration:
            raise HTTPException(status_code=503, detail="Matrix integration not configured")

        success, user_data = await matrix_integration.authenticate_matrix_user(
            request.username,
            request.password
        )

        if success:
            return {
                "success": True,
                "user": user_data
            }
        else:
            return {
                "success": False,
                "error": "Invalid credentials"
            }

    except Exception as e:
        logger.error(f"Matrix auth error: {e}")
        raise HTTPException(status_code=500, detail="Authentication failed")

@router.post("/matrix/sync-user")
async def sync_user_to_matrix(
    request: SSOSyncRequest,
    current_session = Depends(require_permission("users.sync"))
):
    """
    Sync user to Matrix homeserver
    
    Args:
        request: Sync request
        current_session: Current admin session
        
    Returns:
        Sync result
    """
    try:
        if not matrix_integration:
            raise HTTPException(status_code=503, detail="Matrix integration not configured")

        success = await matrix_integration.sync_user_to_matrix(request.user_id)
        
        return {
            "success": success,
            "message": "User synced to Matrix" if success else "Sync failed"
        }

    except Exception as e:
        logger.error(f"Matrix sync error: {e}")
        raise HTTPException(status_code=500, detail="Sync failed")

@router.get("/matrix/rooms")
async def get_user_matrix_rooms(
    current_session = Depends(get_current_session)
):
    """
    Get Matrix rooms for current user
    
    Args:
        current_session: Current authenticated session
        
    Returns:
        List of user's Matrix rooms
    """
    try:
        if not matrix_integration:
            raise HTTPException(status_code=503, detail="Matrix integration not configured")

        rooms = await matrix_integration.get_user_rooms(current_session.user_id)
        return {"rooms": rooms}

    except Exception as e:
        logger.error(f"Matrix rooms error: {e}")
        raise HTTPException(status_code=500, detail="Failed to get rooms")

# Generic SSO Management Endpoints
@router.get("/services")
async def list_sso_services(
    current_session = Depends(require_permission("system.monitor"))
):
    """
    List configured SSO services
    
    Args:
        current_session: Current admin session
        
    Returns:
        List of SSO services and their status
    """
    try:
        services = []
        
        # Home Assistant
        services.append({
            "name": "Home Assistant",
            "id": "homeassistant",
            "enabled": ha_integration is not None,
            "description": "Smart home automation platform",
            "auth_endpoint": "/api/sso/homeassistant/auth"
        })
        
        # n8n
        services.append({
            "name": "n8n",
            "id": "n8n", 
            "enabled": n8n_integration is not None,
            "description": "Workflow automation platform",
            "auth_endpoint": "/api/sso/n8n/auth"
        })
        
        # Matrix
        services.append({
            "name": "Matrix",
            "id": "matrix",
            "enabled": matrix_integration is not None,
            "description": "Decentralized chat platform",
            "auth_endpoint": "/api/sso/matrix/auth"
        })

        return {"services": services}

    except Exception as e:
        logger.error(f"List services error: {e}")
        raise HTTPException(status_code=500, detail="Failed to list services")

@router.post("/sync-all-users")
async def sync_all_users_to_services(
    services: List[str] = Query([], description="Services to sync to (empty = all)"),
    current_session = Depends(require_permission("users.sync"))
):
    """
    Sync all users to SSO services
    
    Args:
        services: List of service IDs to sync to
        current_session: Current admin session
        
    Returns:
        Sync results for all services
    """
    try:
        results = {}
        
        # If no services specified, sync to all
        if not services:
            services = ["homeassistant", "n8n", "matrix"]
        
        # Home Assistant sync
        if "homeassistant" in services and ha_integration:
            try:
                ha_results = await ha_integration.sync_all_users_to_ha()
                results["homeassistant"] = {
                    "success": True,
                    "user_results": ha_results,
                    "total_users": len(ha_results)
                }
            except Exception as e:
                results["homeassistant"] = {"success": False, "error": str(e)}
        
        # n8n sync
        if "n8n" in services and n8n_integration:
            try:
                # Get all users and sync to n8n
                from ..core.auth import auth_manager
                users = auth_manager.list_users(current_session.user_id)
                n8n_results = {}
                
                for user in users:
                    user_id = user["user_id"]
                    n8n_results[user_id] = await n8n_integration.sync_user_to_n8n(user_id)
                
                results["n8n"] = {
                    "success": True,
                    "user_results": n8n_results,
                    "total_users": len(n8n_results)
                }
            except Exception as e:
                results["n8n"] = {"success": False, "error": str(e)}
        
        # Matrix sync
        if "matrix" in services and matrix_integration:
            try:
                # Get all users and sync to Matrix
                from ..core.auth import auth_manager
                users = auth_manager.list_users(current_session.user_id)
                matrix_results = {}
                
                for user in users:
                    user_id = user["user_id"]
                    matrix_results[user_id] = await matrix_integration.sync_user_to_matrix(user_id)
                
                results["matrix"] = {
                    "success": True,
                    "user_results": matrix_results,
                    "total_users": len(matrix_results)
                }
            except Exception as e:
                results["matrix"] = {"success": False, "error": str(e)}

        return {"sync_results": results}

    except Exception as e:
        logger.error(f"Sync all users error: {e}")
        raise HTTPException(status_code=500, detail="Sync failed")

@router.get("/service-configs")
async def get_service_configurations(
    current_session = Depends(require_permission("system.config"))
):
    """
    Get SSO service configurations
    
    Args:
        current_session: Current admin session
        
    Returns:
        Service configurations
    """
    try:
        configs = {}
        
        # Home Assistant config
        if ha_integration:
            configs["homeassistant"] = {
                "enabled": True,
                "homeserver_url": ha_integration.ha_url,
                "role_mapping": ha_integration.role_mapping,
                "sync_interval": ha_integration.sync_interval
            }
        
        # n8n config
        if n8n_integration:
            configs["n8n"] = {
                "enabled": True,
                "n8n_url": n8n_integration.n8n_url,
                "role_mapping": n8n_integration.role_mapping,
                "permission_mapping": n8n_integration.permission_mapping
            }
        
        # Matrix config  
        if matrix_integration:
            configs["matrix"] = {
                "enabled": True,
                "homeserver_url": matrix_integration.homeserver_url,
                "server_name": matrix_integration.server_name,
                "admin_roles": matrix_integration.admin_roles,
                "auto_join_rooms": matrix_integration.auto_join_rooms
            }

        return {"configurations": configs}

    except Exception as e:
        logger.error(f"Get service configs error: {e}")
        raise HTTPException(status_code=500, detail="Failed to get configurations")

# Webhook endpoints for service notifications
@router.post("/webhook/password-changed")
async def handle_password_change_webhook(
    user_id: str,
    services: List[str] = Query([], description="Services to notify"),
    current_session = Depends(require_permission("users.update"))
):
    """
    Handle password change webhook
    
    Args:
        user_id: User ID whose password changed
        services: Services to notify
        current_session: Current session
        
    Returns:
        Notification results
    """
    try:
        results = {}
        
        if not services:
            services = ["homeassistant", "n8n", "matrix"]
        
        # Notify services of password change
        if "homeassistant" in services and ha_integration:
            results["homeassistant"] = await ha_integration.sync_password_change(user_id, "")
        
        if "n8n" in services and n8n_integration:
            results["n8n"] = await n8n_integration.sync_password_change(user_id)
        
        if "matrix" in services and matrix_integration:
            results["matrix"] = await matrix_integration.sync_password_change(user_id)

        return {"notification_results": results}

    except Exception as e:
        logger.error(f"Password change webhook error: {e}")
        raise HTTPException(status_code=500, detail="Notification failed")

@router.post("/webhook/user-deactivated")
async def handle_user_deactivation_webhook(
    username: str,
    services: List[str] = Query([], description="Services to notify"),
    current_session = Depends(require_permission("users.delete"))
):
    """
    Handle user deactivation webhook
    
    Args:
        username: Username that was deactivated
        services: Services to notify
        current_session: Current session
        
    Returns:
        Deactivation results
    """
    try:
        results = {}
        
        if not services:
            services = ["homeassistant", "n8n", "matrix"]
        
        # Deactivate user in services
        if "homeassistant" in services and ha_integration:
            results["homeassistant"] = await ha_integration.sync_user_deletion(username)
        
        if "matrix" in services and matrix_integration:
            results["matrix"] = await matrix_integration.deactivate_matrix_user(username)

        return {"deactivation_results": results}

    except Exception as e:
        logger.error(f"User deactivation webhook error: {e}")
        raise HTTPException(status_code=500, detail="Deactivation failed")

