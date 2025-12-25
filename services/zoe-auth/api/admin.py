"""
Admin API Endpoints
User management, role management, and system administration
"""

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, EmailStr, Field
from typing import Optional, List, Dict, Any
from datetime import datetime
import logging
import json

from core.auth import auth_manager
from core.passcode import passcode_manager
from core.sessions import session_manager
from core.rbac import rbac_manager
from models.database import auth_db
from api.dependencies import require_permission, require_admin, get_current_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])

# Pydantic models
class UserCreateRequest(BaseModel):
    """Admin user creation request"""
    username: str = Field(..., min_length=3, max_length=50)
    email: EmailStr
    password: str = Field(..., min_length=8)
    role: str = Field(default="user")

class UserUpdateRequest(BaseModel):
    """Admin user update request"""
    email: Optional[EmailStr] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None
    is_verified: Optional[bool] = None

class PasswordResetRequest(BaseModel):
    """Admin password reset request"""
    user_id: str

class RoleCreateRequest(BaseModel):
    """Role creation request"""
    role_id: str = Field(..., min_length=1, max_length=50)
    name: str = Field(..., min_length=1, max_length=100)
    description: str = Field(default="")
    permissions: List[str] = Field(default_factory=list)
    inherits_from: Optional[str] = None

class PermissionGrantRequest(BaseModel):
    """Permission grant request"""
    user_id: str
    permissions: List[str]

# User Management Endpoints
@router.get("/users")
async def list_users(
    active_only: bool = Query(True, description="Only return active users"),
    role: Optional[str] = Query(None, description="Filter by role"),
    current_session = Depends(require_permission("users.read"))
):
    """
    List all users (admin only)
    
    Args:
        active_only: Filter to active users only
        role: Filter by specific role
        current_session: Current admin session
        
    Returns:
        List of users with details
    """
    try:
        users = auth_manager.list_users(current_session.user_id, active_only)
        
        # Filter by role if specified
        if role:
            users = [user for user in users if user.get("role") == role]
        
        # Add additional info for each user
        for user in users:
            # Add passcode info
            passcode_info = passcode_manager.get_passcode_info(user["user_id"])
            user["has_passcode"] = passcode_info.get("has_passcode", False)
            user["passcode_locked"] = passcode_info.get("is_locked", False)
            
            # Add active sessions count
            user_sessions = session_manager.get_user_sessions(user["user_id"])
            user["active_sessions"] = len(user_sessions)
            
            # Add permissions
            user["permissions"] = rbac_manager.list_user_permissions(user["user_id"])

        return {"users": users, "total": len(users)}

    except Exception as e:
        logger.error(f"List users error: {e}")
        raise HTTPException(status_code=500, detail="Failed to list users")

@router.post("/users")
async def create_user(
    request: UserCreateRequest,
    current_session = Depends(require_permission("users.create"))
):
    """
    Create new user (admin only)
    
    Args:
        request: User creation details
        current_session: Current admin session
        
    Returns:
        Created user information
    """
    try:
        success, result = auth_manager.create_user(
            username=request.username,
            email=request.email,
            password=request.password,
            role=request.role,
            created_by=current_session.user_id
        )

        if success:
            # Get full user info
            user_info = auth_manager.get_user_info(result)
            return {"message": "User created successfully", "user": user_info}
        else:
            raise HTTPException(status_code=400, detail=result)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Create user error: {e}")
        raise HTTPException(status_code=500, detail="Failed to create user")

@router.put("/users/{user_id}")
async def update_user(
    user_id: str,
    request: UserUpdateRequest,
    current_session = Depends(require_permission("users.update"))
):
    """
    Update user information (admin only)
    
    Args:
        user_id: User ID to update
        request: Update details
        current_session: Current admin session
        
    Returns:
        Updated user information
    """
    try:
        with auth_db.get_connection() as conn:
            updates = []
            params = []
            
            if request.email is not None:
                updates.append("email = ?")
                params.append(request.email)
            
            if request.role is not None:
                # Verify role exists
                if not rbac_manager._role_exists(request.role):
                    raise HTTPException(status_code=400, detail="Invalid role")
                updates.append("role = ?")
                params.append(request.role)
            
            if request.is_active is not None:
                updates.append("is_active = ?")
                params.append(1 if request.is_active else 0)
            
            if request.is_verified is not None:
                updates.append("is_verified = ?")
                params.append(1 if request.is_verified else 0)
            
            if not updates:
                raise HTTPException(status_code=400, detail="No updates provided")
            
            updates.append("updated_at = ?")
            params.append(datetime.now().isoformat())
            params.append(user_id)
            
            conn.execute(f"""
                UPDATE users 
                SET {', '.join(updates)}
                WHERE user_id = ?
            """, params)
            
            if conn.total_changes == 0:
                raise HTTPException(status_code=404, detail="User not found")
            
            # Invalidate user's permission cache if role changed
            if request.role is not None:
                rbac_manager.permission_cache.invalidate_user(user_id)

        # Get updated user info
        updated_user = auth_manager.get_user_info(user_id)
        return {"message": "User updated successfully", "user": updated_user}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Update user error: {e}")
        raise HTTPException(status_code=500, detail="Failed to update user")

@router.delete("/users/{user_id}")
async def delete_user(
    user_id: str,
    permanent: bool = Query(False, description="Permanently delete user data"),
    current_session = Depends(require_permission("users.delete"))
):
    """
    Delete user (admin only)
    
    Args:
        user_id: User ID to delete
        permanent: If true, permanently delete. Otherwise just deactivate.
        current_session: Current admin session
        
    Returns:
        Success message
    """
    try:
        # Prevent self-deletion
        if user_id == current_session.user_id:
            raise HTTPException(status_code=400, detail="Cannot delete your own account")

        with auth_db.get_connection() as conn:
            if permanent:
                # Permanently delete user and all related data
                # Delete related records first (foreign key constraints)
                conn.execute("DELETE FROM passcodes WHERE user_id = ?", (user_id,))
                # Note: Permissions are role-based, not per-user, so no user_permissions table
                conn.execute("DELETE FROM audit_logs WHERE user_id = ?", (user_id,))
                conn.execute("DELETE FROM auth_sessions WHERE user_id = ?", (user_id,))
                
                # Delete user
                conn.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
                
                if conn.total_changes == 0:
                    raise HTTPException(status_code=404, detail="User not found")
                    
                message = "User permanently deleted"
            else:
                # Deactivate instead of deleting for audit purposes
                conn.execute("""
                    UPDATE users 
                    SET is_active = 0, updated_at = ?
                    WHERE user_id = ?
                """, (datetime.now().isoformat(), user_id))
                
                if conn.total_changes == 0:
                    raise HTTPException(status_code=404, detail="User not found")
                    
                message = "User deactivated successfully"

        # Invalidate all user sessions
        session_manager.invalidate_user_sessions(user_id)
        
        # Clear permission cache
        rbac_manager.permission_cache.invalidate_user(user_id)

        return {"message": message}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Delete user error: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete user")

@router.post("/users/{user_id}/reset-password")
async def reset_user_password(
    user_id: str,
    current_session = Depends(require_permission("users.reset_password"))
):
    """
    Reset user password and generate temporary password (admin only)
    
    Args:
        user_id: User ID to reset password for
        current_session: Current admin session
        
    Returns:
        Temporary password
    """
    try:
        success, message, temp_password = auth_manager.reset_password(
            user_id, current_session.user_id
        )

        if success:
            return {
                "message": message,
                "temporary_password": temp_password,
                "note": "User must change password on next login"
            }
        else:
            raise HTTPException(status_code=400, detail=message)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Password reset error: {e}")
        raise HTTPException(status_code=500, detail="Failed to reset password")

@router.post("/users/{user_id}/unlock")
async def unlock_user_account(
    user_id: str,
    current_session = Depends(require_permission("users.unlock"))
):
    """
    Unlock user account (admin only)
    
    Args:
        user_id: User ID to unlock
        current_session: Current admin session
        
    Returns:
        Success message
    """
    try:
        success = auth_manager.unlock_account(user_id, current_session.user_id)
        if success:
            return {"message": "Account unlocked successfully"}
        else:
            raise HTTPException(status_code=404, detail="User not found or not locked")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Account unlock error: {e}")
        raise HTTPException(status_code=500, detail="Failed to unlock account")

class PasscodeSetRequest(BaseModel):
    """Admin passcode set request"""
    passcode: str = Field(..., min_length=4, max_length=8, pattern=r'^\d+$')

@router.post("/users/{user_id}/passcode")
async def set_user_passcode(
    user_id: str,
    request: PasscodeSetRequest,
    current_session = Depends(require_permission("users.update"))
):
    """
    Set or update passcode for a user (admin only)
    
    Args:
        user_id: User ID to set passcode for
        request: Passcode details
        current_session: Current admin session
        
    Returns:
        Success message
    """
    try:
        # Verify user exists
        user_info = auth_manager.get_user_info(user_id)
        if not user_info:
            raise HTTPException(status_code=404, detail="User not found")
        
        # Set passcode
        success, message = passcode_manager.create_passcode(
            user_id,
            request.passcode
        )

        if success:
            return {"message": f"Passcode set for user {user_info['username']}"}
        else:
            raise HTTPException(status_code=400, detail=message)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Set user passcode error: {e}")
        raise HTTPException(status_code=500, detail="Failed to set passcode")

@router.delete("/users/{user_id}/passcode")
async def remove_user_passcode(
    user_id: str,
    current_session = Depends(require_permission("users.update"))
):
    """
    Remove passcode from a user (admin only)
    
    Args:
        user_id: User ID to remove passcode from
        current_session: Current admin session
        
    Returns:
        Success message
    """
    try:
        # Verify user exists
        user_info = auth_manager.get_user_info(user_id)
        if not user_info:
            raise HTTPException(status_code=404, detail="User not found")
        
        # Remove passcode
        success = passcode_manager.disable_passcode(user_id)

        if success:
            return {"message": f"Passcode removed from user {user_info['username']}"}
        else:
            raise HTTPException(status_code=404, detail="No passcode found for this user")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Remove user passcode error: {e}")
        raise HTTPException(status_code=500, detail="Failed to remove passcode")

@router.get("/users/{user_id}/sessions")
async def get_user_sessions(
    user_id: str,
    current_session = Depends(require_permission("users.read"))
):
    """
    Get all active sessions for a specific user (admin only)
    
    Args:
        user_id: User ID to get sessions for
        current_session: Current admin session
        
    Returns:
        List of active sessions for the user
    """
    try:
        # Get user info first to verify user exists
        user_info = auth_manager.get_user_info(user_id)
        if not user_info:
            raise HTTPException(status_code=404, detail="User not found")
        
        # Get user sessions
        user_sessions = session_manager.get_user_sessions(user_id)
        
        sessions = []
        for session in user_sessions:
            session_info = {
                "session_id": session.session_id,
                "user_id": session.user_id,
                "session_type": session.session_type.value,
                "auth_method": session.auth_method.value,
                "device_info": session.device_info,
                "ip_address": session.metadata.get("ip_address") if session.metadata else None,
                "user_agent": session.metadata.get("user_agent") if session.metadata else None,
                "created_at": session.created_at.isoformat(),
                "last_activity": session.last_activity.isoformat(),
                "expires_at": session.expires_at.isoformat(),
                "is_current": session.session_id == current_session.session_id
            }
            sessions.append(session_info)
        
        return {
            "user_id": user_id,
            "username": user_info["username"],
            "sessions": sessions,
            "total": len(sessions)
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get user sessions error: {e}")
        raise HTTPException(status_code=500, detail="Failed to get user sessions")

# Role Management Endpoints
@router.get("/roles")
async def list_roles(
    current_session = Depends(require_permission("roles.read"))
):
    """
    List all roles
    
    Args:
        current_session: Current authenticated session
        
    Returns:
        List of roles with permissions
    """
    try:
        roles = []
        with auth_db.get_connection() as conn:
            cursor = conn.execute("""
                SELECT role_id, name, description, permissions, inherits_from, is_system, created_at
                FROM roles
                ORDER BY is_system DESC, name
            """)
            
            for row in cursor.fetchall():
                role = {
                    "role_id": row[0],
                    "name": row[1],
                    "description": row[2],
                    "permissions": json.loads(row[3] or '[]'),
                    "inherits_from": row[4],
                    "is_system": bool(row[5]),
                    "created_at": row[6]
                }
                
                # Get effective permissions (including inherited)
                role["effective_permissions"] = list(rbac_manager.get_role_permissions(row[0]))
                
                roles.append(role)

        return {"roles": roles}

    except Exception as e:
        logger.error(f"List roles error: {e}")
        raise HTTPException(status_code=500, detail="Failed to list roles")

@router.post("/roles")
async def create_role(
    request: RoleCreateRequest,
    current_session = Depends(require_permission("roles.create"))
):
    """
    Create custom role (admin only)
    
    Args:
        request: Role creation details
        current_session: Current admin session
        
    Returns:
        Created role information
    """
    try:
        success = rbac_manager.create_custom_role(
            role_id=request.role_id,
            name=request.name,
            description=request.description,
            permissions=request.permissions,
            created_by=current_session.user_id,
            inherits_from=request.inherits_from
        )

        if success:
            return {"message": "Role created successfully", "role_id": request.role_id}
        else:
            raise HTTPException(status_code=400, detail="Failed to create role")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Create role error: {e}")
        raise HTTPException(status_code=500, detail="Failed to create role")

# Session Management Endpoints
@router.get("/sessions")
async def list_all_sessions(
    user_id: Optional[str] = Query(None, description="Filter by user ID"),
    current_session = Depends(require_permission("system.monitor"))
):
    """
    List all active sessions (admin only)
    
    Args:
        user_id: Optional user filter
        current_session: Current admin session
        
    Returns:
        List of active sessions
    """
    try:
        all_sessions = []
        
        for session in session_manager.active_sessions.values():
            if user_id and session.user_id != user_id:
                continue
                
            session_info = {
                "session_id": session.session_id,
                "user_id": session.user_id,
                "session_type": session.session_type.value,
                "auth_method": session.auth_method.value,
                "device_info": session.device_info,
                "created_at": session.created_at.isoformat(),
                "last_activity": session.last_activity.isoformat(),
                "expires_at": session.expires_at.isoformat(),
                "metadata": session.metadata
            }
            
            # Add user info
            user_info = auth_manager.get_user_info(session.user_id)
            if user_info:
                session_info["username"] = user_info["username"]
                session_info["user_role"] = user_info["role"]
            
            all_sessions.append(session_info)

        return {"sessions": all_sessions, "total": len(all_sessions)}

    except Exception as e:
        logger.error(f"List sessions error: {e}")
        raise HTTPException(status_code=500, detail="Failed to list sessions")

@router.delete("/sessions/{session_id}")
async def invalidate_session_admin(
    session_id: str,
    current_session = Depends(require_permission("system.monitor"))
):
    """
    Invalidate any session (admin only)
    
    Args:
        session_id: Session ID to invalidate
        current_session: Current admin session
        
    Returns:
        Success message
    """
    try:
        success = session_manager.invalidate_session(session_id)
        if success:
            return {"message": "Session invalidated successfully"}
        else:
            raise HTTPException(status_code=404, detail="Session not found")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Session invalidation error: {e}")
        raise HTTPException(status_code=500, detail="Failed to invalidate session")

# System Statistics and Monitoring
@router.get("/stats")
async def get_system_stats(
    current_session = Depends(require_permission("system.monitor"))
):
    """
    Get system statistics (admin only)
    
    Args:
        current_session: Current admin session
        
    Returns:
        System statistics
    """
    try:
        # Get user statistics
        with auth_db.get_connection() as conn:
            # User counts by role
            cursor = conn.execute("""
                SELECT role, COUNT(*) as count
                FROM users
                WHERE is_active = 1
                GROUP BY role
            """)
            user_stats = {row[0]: row[1] for row in cursor.fetchall()}
            
            # Total users
            cursor = conn.execute("SELECT COUNT(*) FROM auth_users WHERE is_active = 1")
            total_users = cursor.fetchone()[0]
            
            # Recent logins (last 24 hours)
            cursor = conn.execute("""
                SELECT COUNT(*) FROM users 
                WHERE last_login > datetime('now', '-1 day')
            """)
            recent_logins = cursor.fetchone()[0]

        # Session statistics
        session_stats = {
            "total_active": len(session_manager.active_sessions),
            "by_type": {},
            "by_auth_method": {}
        }
        
        for session in session_manager.active_sessions.values():
            session_type = session.session_type.value
            auth_method = session.auth_method.value
            
            session_stats["by_type"][session_type] = session_stats["by_type"].get(session_type, 0) + 1
            session_stats["by_auth_method"][auth_method] = session_stats["by_auth_method"].get(auth_method, 0) + 1

        # Cache statistics
        from touch_panel.cache import cache_manager
        cache_stats = cache_manager.get_all_cache_stats()

        return {
            "users": {
                "total": total_users,
                "by_role": user_stats,
                "recent_logins_24h": recent_logins
            },
            "sessions": session_stats,
            "touch_panel_caches": cache_stats,
            "timestamp": datetime.now().isoformat()
        }

    except Exception as e:
        logger.error(f"System stats error: {e}")
        raise HTTPException(status_code=500, detail="Failed to get system stats")

# Audit Log Endpoints
@router.get("/audit-logs")
async def get_audit_logs(
    user_id: Optional[str] = Query(None, description="Filter by user ID"),
    action: Optional[str] = Query(None, description="Filter by action"),
    limit: int = Query(100, description="Number of records to return", ge=1, le=1000),
    offset: int = Query(0, description="Number of records to skip", ge=0),
    current_session = Depends(require_permission("audit.read"))
):
    """
    Get audit logs (admin only)
    
    Args:
        user_id: Optional user filter
        action: Optional action filter
        limit: Number of records to return
        offset: Number of records to skip
        current_session: Current admin session
        
    Returns:
        Audit log entries
    """
    try:
        query = "SELECT * FROM audit_logs WHERE 1=1"
        params = []
        
        if user_id:
            query += " AND user_id = ?"
            params.append(user_id)
        
        if action:
            query += " AND action LIKE ?"
            params.append(f"%{action}%")
        
        query += " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        logs = []
        with auth_db.get_connection() as conn:
            cursor = conn.execute(query, params)
            
            for row in cursor.fetchall():
                log_entry = {
                    "log_id": row[0],
                    "user_id": row[1],
                    "action": row[2],
                    "resource": row[3],
                    "result": row[4],
                    "ip_address": row[5],
                    "user_agent": row[6],
                    "details": row[7],
                    "timestamp": row[8]
                }
                logs.append(log_entry)

        # Get total count for pagination
        count_query = "SELECT COUNT(*) FROM audit_logs WHERE 1=1"
        count_params = []
        
        if user_id:
            count_query += " AND user_id = ?"
            count_params.append(user_id)
        
        if action:
            count_query += " AND action LIKE ?"
            count_params.append(f"%{action}%")

        with auth_db.get_connection() as conn:
            cursor = conn.execute(count_query, count_params)
            total_count = cursor.fetchone()[0]

        return {
            "logs": logs,
            "total": total_count,
            "limit": limit,
            "offset": offset
        }

    except Exception as e:
        logger.error(f"Audit logs error: {e}")
        raise HTTPException(status_code=500, detail="Failed to get audit logs")

# Data Export for Touch Panel Sync
@router.get("/sync-data")
async def get_sync_data(
    device_id: str = Query(..., description="Touch panel device ID"),
    current_session = Depends(require_permission("system.monitor"))
):
    """
    Get sync data for touch panel cache
    
    Args:
        device_id: Touch panel device identifier
        current_session: Current admin session
        
    Returns:
        Sync data for touch panel
    """
    try:
        # Get users with passcodes
        users = []
        with auth_db.get_connection() as conn:
            cursor = conn.execute("""
                SELECT u.user_id, u.username, u.role, p.passcode_hash
                FROM users u
                LEFT JOIN passcodes p ON u.user_id = p.user_id AND p.is_active = 1
                WHERE u.is_active = 1
            """)
            
            for row in cursor.fetchall():
                user_data = {
                    "user_id": row[0],
                    "username": row[1],
                    "role": row[2],
                    "passcode_hash": row[3],
                    "permissions": rbac_manager.list_user_permissions(row[0])
                }
                users.append(user_data)

        sync_data = {
            "users": users,
            "sync_time": datetime.now().isoformat(),
            "device_id": device_id
        }

        return sync_data

    except Exception as e:
        logger.error(f"Sync data error: {e}")
        raise HTTPException(status_code=500, detail="Failed to get sync data")
