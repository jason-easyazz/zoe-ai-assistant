"""
Event Permissions System
Handles event-level permissions and visibility controls for family/group events
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime
import sqlite3
import json
import logging

from .family import family_manager
from .auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/events", tags=["event_permissions"])

class EventPermissionRequest(BaseModel):
    event_id: str
    user_id: str
    permission: str  # "read", "write", "delete", "invite"

class EventVisibilityRequest(BaseModel):
    event_id: str
    visibility: str  # "private", "family", "public"
    family_id: Optional[str] = None

class EventShareRequest(BaseModel):
    event_id: str
    share_with: List[str]  # List of user IDs
    permission_level: str = "read"  # "read", "write"

class EventPermissionResponse(BaseModel):
    event_id: str
    user_id: str
    permissions: List[str]
    can_read: bool
    can_write: bool
    can_delete: bool
    can_invite: bool
    visibility: str
    family_id: Optional[str]

class EventPermissionManager:
    """Manages event-level permissions and visibility"""
    
    def __init__(self, db_path: str = "/app/data/zoe.db"):
        self.db_path = db_path
        self._initialize_database()
    
    def _initialize_database(self):
        """Initialize event permissions tables"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Event permissions table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS event_permissions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    permission TEXT NOT NULL,
                    granted_by TEXT NOT NULL,
                    granted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(event_id, user_id, permission)
                )
            ''')
            
            # Event visibility table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS event_visibility (
                    event_id TEXT PRIMARY KEY,
                    visibility TEXT NOT NULL,
                    family_id TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Event shares table (for sharing with specific users)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS event_shares (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL,
                    shared_with TEXT NOT NULL,
                    permission_level TEXT NOT NULL,
                    shared_by TEXT NOT NULL,
                    shared_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(event_id, shared_with)
                )
            ''')
            
            conn.commit()
            conn.close()
            logger.info("Event permissions database initialized")
            
        except Exception as e:
            logger.error(f"Failed to initialize event permissions database: {e}")
            raise
    
    def check_event_permission(self, event_id: str, user_id: str, permission: str) -> bool:
        """Check if user has specific permission for an event"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Check direct permission
            cursor.execute('''
                SELECT 1 FROM event_permissions 
                WHERE event_id = ? AND user_id = ? AND permission = ?
            ''', (event_id, user_id, permission))
            
            if cursor.fetchone():
                conn.close()
                return True
            
            # Check if user is the event creator
            cursor.execute('''
                SELECT created_by FROM shared_events WHERE event_id = ?
                UNION
                SELECT user_id FROM events WHERE id = ?
            ''', (event_id, event_id))
            
            creator = cursor.fetchone()
            if creator and creator[0] == user_id:
                conn.close()
                return True
            
            # Check family permissions
            cursor.execute('''
                SELECT family_id FROM shared_events WHERE event_id = ?
            ''', (event_id,))
            
            family_result = cursor.fetchone()
            if family_result:
                family_id = family_result[0]
                if family_manager._is_family_member(family_id, user_id):
                    # Family members have read access by default
                    if permission == "read":
                        conn.close()
                        return True
                    
                    # Check if user is family admin for write/delete permissions
                    if family_manager._is_family_admin(family_id, user_id):
                        conn.close()
                        return True
            
            conn.close()
            return False
            
        except Exception as e:
            logger.error(f"Error checking event permission: {e}")
            return False
    
    def get_event_permissions(self, event_id: str, user_id: str) -> EventPermissionResponse:
        """Get all permissions for a user on an event"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Get event visibility
            cursor.execute('''
                SELECT visibility, family_id FROM event_visibility WHERE event_id = ?
            ''', (event_id,))
            
            visibility_result = cursor.fetchone()
            visibility = "private"
            family_id = None
            
            if visibility_result:
                visibility = visibility_result[0]
                family_id = visibility_result[1]
            
            # Check permissions
            can_read = self.check_event_permission(event_id, user_id, "read")
            can_write = self.check_event_permission(event_id, user_id, "write")
            can_delete = self.check_event_permission(event_id, user_id, "delete")
            can_invite = self.check_event_permission(event_id, user_id, "invite")
            
            # Get all granted permissions
            cursor.execute('''
                SELECT permission FROM event_permissions 
                WHERE event_id = ? AND user_id = ?
            ''', (event_id, user_id))
            
            permissions = [row[0] for row in cursor.fetchall()]
            
            conn.close()
            
            return EventPermissionResponse(
                event_id=event_id,
                user_id=user_id,
                permissions=permissions,
                can_read=can_read,
                can_write=can_write,
                can_delete=can_delete,
                can_invite=can_invite,
                visibility=visibility,
                family_id=family_id
            )
            
        except Exception as e:
            logger.error(f"Error getting event permissions: {e}")
            return EventPermissionResponse(
                event_id=event_id,
                user_id=user_id,
                permissions=[],
                can_read=False,
                can_write=False,
                can_delete=False,
                can_invite=False,
                visibility="private",
                family_id=None
            )
    
    def grant_event_permission(self, event_id: str, user_id: str, permission: str, granted_by: str) -> bool:
        """Grant permission to a user for an event"""
        try:
            # Check if granter has permission to grant this permission
            if not self.check_event_permission(event_id, granted_by, "invite"):
                return False
            
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT OR REPLACE INTO event_permissions 
                (event_id, user_id, permission, granted_by)
                VALUES (?, ?, ?, ?)
            ''', (event_id, user_id, permission, granted_by))
            
            conn.commit()
            conn.close()
            
            logger.info(f"Granted {permission} permission for event {event_id} to user {user_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error granting event permission: {e}")
            return False
    
    def revoke_event_permission(self, event_id: str, user_id: str, permission: str, revoked_by: str) -> bool:
        """Revoke permission from a user for an event"""
        try:
            # Check if revoker has permission to revoke this permission
            if not self.check_event_permission(event_id, revoked_by, "invite"):
                return False
            
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
                DELETE FROM event_permissions 
                WHERE event_id = ? AND user_id = ? AND permission = ?
            ''', (event_id, user_id, permission))
            
            conn.commit()
            conn.close()
            
            logger.info(f"Revoked {permission} permission for event {event_id} from user {user_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error revoking event permission: {e}")
            return False
    
    def set_event_visibility(self, event_id: str, visibility: str, family_id: Optional[str], updated_by: str) -> bool:
        """Set event visibility level"""
        try:
            # Check if updater has permission to change visibility
            if not self.check_event_permission(event_id, updated_by, "write"):
                return False
            
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT OR REPLACE INTO event_visibility 
                (event_id, visibility, family_id, updated_at)
                VALUES (?, ?, ?, ?)
            ''', (event_id, visibility, family_id, datetime.now().isoformat()))
            
            conn.commit()
            conn.close()
            
            logger.info(f"Set event {event_id} visibility to {visibility}")
            return True
            
        except Exception as e:
            logger.error(f"Error setting event visibility: {e}")
            return False
    
    def share_event(self, event_id: str, share_with: List[str], permission_level: str, shared_by: str) -> bool:
        """Share event with specific users"""
        try:
            # Check if sharer has permission to share
            if not self.check_event_permission(event_id, shared_by, "invite"):
                return False
            
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            for user_id in share_with:
                cursor.execute('''
                    INSERT OR REPLACE INTO event_shares 
                    (event_id, shared_with, permission_level, shared_by)
                    VALUES (?, ?, ?, ?)
                ''', (event_id, user_id, permission_level, shared_by))
                
                # Grant appropriate permissions based on permission level
                if permission_level == "read":
                    self.grant_event_permission(event_id, user_id, "read", shared_by)
                elif permission_level == "write":
                    self.grant_event_permission(event_id, user_id, "read", shared_by)
                    self.grant_event_permission(event_id, user_id, "write", shared_by)
            
            conn.commit()
            conn.close()
            
            logger.info(f"Shared event {event_id} with {len(share_with)} users")
            return True
            
        except Exception as e:
            logger.error(f"Error sharing event: {e}")
            return False
    
    def get_shared_events(self, user_id: str) -> List[Dict[str, Any]]:
        """Get events shared with a user"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT es.event_id, es.permission_level, es.shared_at, es.shared_by,
                       se.title, se.description, se.start_time, se.family_id,
                       f.name as family_name
                FROM event_shares es
                LEFT JOIN shared_events se ON es.event_id = se.event_id
                LEFT JOIN families f ON se.family_id = f.family_id
                WHERE es.shared_with = ?
                ORDER BY es.shared_at DESC
            ''', (user_id,))
            
            events = []
            for row in cursor.fetchall():
                events.append({
                    "event_id": row[0],
                    "permission_level": row[1],
                    "shared_at": row[2],
                    "shared_by": row[3],
                    "title": row[4],
                    "description": row[5],
                    "start_time": row[6],
                    "family_id": row[7],
                    "family_name": row[8]
                })
            
            conn.close()
            return events
            
        except Exception as e:
            logger.error(f"Error getting shared events: {e}")
            return []

# Global permission manager instance
permission_manager = EventPermissionManager()

# API Endpoints
@router.get("/{event_id}/permissions", response_model=EventPermissionResponse)
async def get_event_permissions(event_id: str, current_user = Depends(get_current_user)):
    """Get permissions for current user on an event"""
    return permission_manager.get_event_permissions(event_id, current_user["user_id"])

@router.post("/{event_id}/permissions/grant")
async def grant_event_permission(
    event_id: str, 
    request: EventPermissionRequest,
    current_user = Depends(get_current_user)
):
    """Grant permission to a user for an event"""
    success = permission_manager.grant_event_permission(
        event_id, request.user_id, request.permission, current_user["user_id"]
    )
    
    if not success:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    
    return {"message": "Permission granted successfully"}

@router.post("/{event_id}/permissions/revoke")
async def revoke_event_permission(
    event_id: str, 
    request: EventPermissionRequest,
    current_user = Depends(get_current_user)
):
    """Revoke permission from a user for an event"""
    success = permission_manager.revoke_event_permission(
        event_id, request.user_id, request.permission, current_user["user_id"]
    )
    
    if not success:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    
    return {"message": "Permission revoked successfully"}

@router.post("/{event_id}/visibility")
async def set_event_visibility(
    event_id: str, 
    request: EventVisibilityRequest,
    current_user = Depends(get_current_user)
):
    """Set event visibility level"""
    success = permission_manager.set_event_visibility(
        event_id, request.visibility, request.family_id, current_user["user_id"]
    )
    
    if not success:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    
    return {"message": "Event visibility updated successfully"}

@router.post("/{event_id}/share")
async def share_event(
    event_id: str, 
    request: EventShareRequest,
    current_user = Depends(get_current_user)
):
    """Share event with specific users"""
    success = permission_manager.share_event(
        event_id, request.share_with, request.permission_level, current_user["user_id"]
    )
    
    if not success:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    
    return {"message": f"Event shared with {len(request.share_with)} users"}

@router.get("/shared", response_model=List[Dict[str, Any]])
async def get_shared_events(current_user = Depends(get_current_user)):
    """Get events shared with current user"""
    return permission_manager.get_shared_events(current_user["user_id"])


