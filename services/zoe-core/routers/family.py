"""
Family/Group Management Router
Inspired by Skylight Calendar - family organization and shared events
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
import sqlite3
import json
import uuid
import logging

from user_context import user_context
from routers.auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/family", tags=["family"])

# Pydantic models
class FamilyCreate(BaseModel):
    name: str
    description: Optional[str] = None
    family_type: str = "family"  # "family", "household", "group"

class FamilyMember(BaseModel):
    user_id: str
    role: str = "member"  # "admin", "member", "child", "parent"

class FamilyInvite(BaseModel):
    email: EmailStr
    role: str = "member"
    message: Optional[str] = None

class SharedEventCreate(BaseModel):
    title: str
    description: Optional[str] = None
    event_type: str = "family"  # "personal", "family", "child_activity", "household"
    visibility: str = "family"  # "family", "private", "public"
    assigned_to: Optional[str] = None  # specific family member
    start_time: datetime
    end_time: Optional[datetime] = None
    recurring: Optional[Dict[str, Any]] = None

class FamilyResponse(BaseModel):
    family_id: str
    name: str
    description: Optional[str]
    family_type: str
    member_count: int
    created_at: str
    members: List[Dict[str, Any]]

class SharedEventResponse(BaseModel):
    event_id: str
    family_id: str
    title: str
    description: Optional[str]
    event_type: str
    visibility: str
    assigned_to: Optional[str]
    created_by: str
    start_time: datetime
    end_time: Optional[datetime]
    created_at: str

class FamilyManager:
    """Family/Group management system"""
    
    def __init__(self, db_path: str = "/app/data/zoe.db"):
        self.db_path = db_path
        self._initialize_database()
    
    def _initialize_database(self):
        """Initialize family-related database tables"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Families table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS families (
                    family_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT,
                    family_type TEXT DEFAULT 'family',
                    created_by TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Family members table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS family_members (
                    family_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    role TEXT DEFAULT 'member',
                    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    invited_by TEXT,
                    status TEXT DEFAULT 'active', -- 'active', 'pending', 'left'
                    PRIMARY KEY (family_id, user_id),
                    FOREIGN KEY (family_id) REFERENCES families (family_id)
                )
            ''')
            
            # Family invitations table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS family_invitations (
                    invitation_id TEXT PRIMARY KEY,
                    family_id TEXT NOT NULL,
                    email TEXT NOT NULL,
                    role TEXT DEFAULT 'member',
                    message TEXT,
                    invited_by TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    expires_at TIMESTAMP,
                    status TEXT DEFAULT 'pending', -- 'pending', 'accepted', 'declined', 'expired'
                    FOREIGN KEY (family_id) REFERENCES families (family_id)
                )
            ''')
            
            # Shared events table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS shared_events (
                    event_id TEXT PRIMARY KEY,
                    family_id TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    assigned_to TEXT,
                    title TEXT NOT NULL,
                    description TEXT,
                    event_type TEXT DEFAULT 'family',
                    visibility TEXT DEFAULT 'family',
                    start_time TIMESTAMP NOT NULL,
                    end_time TIMESTAMP,
                    recurring TEXT, -- JSON for recurring rules
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (family_id) REFERENCES families (family_id)
                )
            ''')
            
            # Event participants table (for complex event assignments)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS event_participants (
                    event_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    role TEXT DEFAULT 'participant', -- 'organizer', 'participant', 'observer'
                    status TEXT DEFAULT 'pending', -- 'pending', 'accepted', 'declined'
                    PRIMARY KEY (event_id, user_id),
                    FOREIGN KEY (event_id) REFERENCES shared_events (event_id)
                )
            ''')
            
            conn.commit()
            conn.close()
            logger.info("Family database tables initialized successfully")
            
        except Exception as e:
            logger.error(f"Failed to initialize family database: {e}")
            raise
    
    def create_family(self, name: str, description: str, family_type: str, created_by: str) -> Dict[str, Any]:
        """Create a new family/group"""
        try:
            family_id = str(uuid.uuid4())[:8]
            
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Create family
            cursor.execute('''
                INSERT INTO families (family_id, name, description, family_type, created_by)
                VALUES (?, ?, ?, ?, ?)
            ''', (family_id, name, description, family_type, created_by))
            
            # Add creator as admin
            cursor.execute('''
                INSERT INTO family_members (family_id, user_id, role, invited_by, status)
                VALUES (?, ?, ?, ?, ?)
            ''', (family_id, created_by, 'admin', created_by, 'active'))
            
            conn.commit()
            conn.close()
            
            return {
                "success": True,
                "family_id": family_id,
                "message": f"Family '{name}' created successfully"
            }
            
        except Exception as e:
            logger.error(f"Failed to create family: {e}")
            return {
                "success": False,
                "error": f"Failed to create family: {str(e)}"
            }
    
    def get_user_families(self, user_id: str) -> List[Dict[str, Any]]:
        """Get all families a user belongs to"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT f.family_id, f.name, f.description, f.family_type, f.created_at,
                       fm.role, fm.joined_at,
                       COUNT(fm2.user_id) as member_count
                FROM families f
                JOIN family_members fm ON f.family_id = fm.family_id
                LEFT JOIN family_members fm2 ON f.family_id = fm2.family_id AND fm2.status = 'active'
                WHERE fm.user_id = ? AND fm.status = 'active'
                GROUP BY f.family_id
                ORDER BY f.created_at DESC
            ''', (user_id,))
            
            families = []
            for row in cursor.fetchall():
                families.append({
                    "family_id": row[0],
                    "name": row[1],
                    "description": row[2],
                    "family_type": row[3],
                    "created_at": row[4],
                    "user_role": row[5],
                    "joined_at": row[6],
                    "member_count": row[7]
                })
            
            conn.close()
            return families
            
        except Exception as e:
            logger.error(f"Failed to get user families: {e}")
            return []
    
    def get_family_members(self, family_id: str, user_id: str) -> List[Dict[str, Any]]:
        """Get all members of a family (user must be a member)"""
        try:
            # Check if user is a member
            if not self._is_family_member(family_id, user_id):
                return []
            
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT fm.user_id, fm.role, fm.joined_at, fm.status,
                       u.username, u.display_name, u.email
                FROM family_members fm
                LEFT JOIN users u ON fm.user_id = u.id
                WHERE fm.family_id = ? AND fm.status = 'active'
                ORDER BY fm.role, fm.joined_at
            ''', (family_id,))
            
            members = []
            for row in cursor.fetchall():
                members.append({
                    "user_id": row[0],
                    "role": row[1],
                    "joined_at": row[2],
                    "status": row[3],
                    "username": row[4],
                    "display_name": row[5],
                    "email": row[6]
                })
            
            conn.close()
            return members
            
        except Exception as e:
            logger.error(f"Failed to get family members: {e}")
            return []
    
    def invite_member(self, family_id: str, email: str, role: str, message: str, invited_by: str) -> Dict[str, Any]:
        """Invite a new member to the family"""
        try:
            # Check if inviter is admin
            if not self._is_family_admin(family_id, invited_by):
                return {
                    "success": False,
                    "error": "Only family admins can invite members"
                }
            
            invitation_id = str(uuid.uuid4())[:8]
            expires_at = datetime.now() + timedelta(days=7)  # 7 days to accept
            
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Create invitation
            cursor.execute('''
                INSERT INTO family_invitations 
                (invitation_id, family_id, email, role, message, invited_by, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (invitation_id, family_id, email, role, message, invited_by, expires_at))
            
            conn.commit()
            conn.close()
            
            return {
                "success": True,
                "invitation_id": invitation_id,
                "message": f"Invitation sent to {email}"
            }
            
        except Exception as e:
            logger.error(f"Failed to invite member: {e}")
            return {
                "success": False,
                "error": f"Failed to send invitation: {str(e)}"
            }
    
    def create_shared_event(self, family_id: str, event_data: Dict[str, Any], created_by: str) -> Dict[str, Any]:
        """Create a shared event in a family"""
        try:
            # Check if user is a family member
            if not self._is_family_member(family_id, created_by):
                return {
                    "success": False,
                    "error": "You must be a family member to create events"
                }
            
            event_id = str(uuid.uuid4())[:8]
            
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Create event
            cursor.execute('''
                INSERT INTO shared_events 
                (event_id, family_id, created_by, assigned_to, title, description, 
                 event_type, visibility, start_time, end_time, recurring)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                event_id, family_id, created_by, event_data.get('assigned_to'),
                event_data['title'], event_data.get('description'),
                event_data.get('event_type', 'family'), event_data.get('visibility', 'family'),
                event_data['start_time'], event_data.get('end_time'),
                json.dumps(event_data.get('recurring')) if event_data.get('recurring') else None
            ))
            
            # Add participants if assigned_to is specified
            if event_data.get('assigned_to'):
                cursor.execute('''
                    INSERT INTO event_participants (event_id, user_id, role, status)
                    VALUES (?, ?, ?, ?)
                ''', (event_id, event_data['assigned_to'], 'organizer', 'accepted'))
            
            # Add creator as participant
            cursor.execute('''
                INSERT OR IGNORE INTO event_participants (event_id, user_id, role, status)
                VALUES (?, ?, ?, ?)
            ''', (event_id, created_by, 'organizer', 'accepted'))
            
            conn.commit()
            conn.close()
            
            return {
                "success": True,
                "event_id": event_id,
                "message": "Shared event created successfully"
            }
            
        except Exception as e:
            logger.error(f"Failed to create shared event: {e}")
            return {
                "success": False,
                "error": f"Failed to create event: {str(e)}"
            }
    
    def get_family_events(self, family_id: str, user_id: str, start_date: Optional[datetime] = None, 
                         end_date: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """Get events for a family (user must be a member)"""
        try:
            # Check if user is a family member
            if not self._is_family_member(family_id, user_id):
                return []
            
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Build date filter
            date_filter = ""
            params = [family_id]
            
            if start_date and end_date:
                date_filter = "AND start_time BETWEEN ? AND ?"
                params.extend([start_date, end_date])
            elif start_date:
                date_filter = "AND start_time >= ?"
                params.append(start_date)
            elif end_date:
                date_filter = "AND start_time <= ?"
                params.append(end_date)
            
            cursor.execute(f'''
                SELECT e.event_id, e.title, e.description, e.event_type, e.visibility,
                       e.assigned_to, e.created_by, e.start_time, e.end_time, e.created_at,
                       u.username as created_by_username
                FROM shared_events e
                LEFT JOIN users u ON e.created_by = u.id
                WHERE e.family_id = ? {date_filter}
                ORDER BY e.start_time ASC
            ''', params)
            
            events = []
            for row in cursor.fetchall():
                events.append({
                    "event_id": row[0],
                    "title": row[1],
                    "description": row[2],
                    "event_type": row[3],
                    "visibility": row[4],
                    "assigned_to": row[5],
                    "created_by": row[6],
                    "start_time": row[7],
                    "end_time": row[8],
                    "created_at": row[9],
                    "created_by_username": row[10]
                })
            
            conn.close()
            return events
            
        except Exception as e:
            logger.error(f"Failed to get family events: {e}")
            return []
    
    def _is_family_member(self, family_id: str, user_id: str) -> bool:
        """Check if user is a member of the family"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT 1 FROM family_members 
                WHERE family_id = ? AND user_id = ? AND status = 'active'
            ''', (family_id, user_id))
            
            result = cursor.fetchone() is not None
            conn.close()
            return result
            
        except Exception:
            return False
    
    def _is_family_admin(self, family_id: str, user_id: str) -> bool:
        """Check if user is an admin of the family"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT 1 FROM family_members 
                WHERE family_id = ? AND user_id = ? AND role = 'admin' AND status = 'active'
            ''', (family_id, user_id))
            
            result = cursor.fetchone() is not None
            conn.close()
            return result
            
        except Exception:
            return False

# Global family manager instance
family_manager = FamilyManager()

# API Endpoints
@router.post("/create", response_model=Dict[str, Any])
async def create_family(family_data: FamilyCreate, current_user = Depends(get_current_user)):
    """Create a new family/group"""
    result = family_manager.create_family(
        family_data.name,
        family_data.description,
        family_data.family_type,
        current_user["user_id"]
    )
    
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["error"])
    
    return result

@router.get("/my-families", response_model=List[Dict[str, Any]])
async def get_my_families(current_user = Depends(get_current_user)):
    """Get all families the current user belongs to"""
    return family_manager.get_user_families(current_user["user_id"])

@router.get("/{family_id}/members", response_model=List[Dict[str, Any]])
async def get_family_members(family_id: str, current_user = Depends(get_current_user)):
    """Get all members of a family"""
    return family_manager.get_family_members(family_id, current_user["user_id"])

@router.post("/{family_id}/invite", response_model=Dict[str, Any])
async def invite_family_member(family_id: str, invite_data: FamilyInvite, 
                              current_user = Depends(get_current_user)):
    """Invite a new member to the family"""
    result = family_manager.invite_member(
        family_id,
        invite_data.email,
        invite_data.role,
        invite_data.message or "",
        current_user["user_id"]
    )
    
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["error"])
    
    return result

@router.post("/{family_id}/events", response_model=Dict[str, Any])
async def create_family_event(family_id: str, event_data: SharedEventCreate,
                             current_user = Depends(get_current_user)):
    """Create a shared event in the family"""
    result = family_manager.create_shared_event(
        family_id,
        event_data.dict(),
        current_user["user_id"]
    )
    
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["error"])
    
    return result

@router.get("/{family_id}/events", response_model=List[Dict[str, Any]])
async def get_family_events(family_id: str, 
                           start_date: Optional[datetime] = None,
                           end_date: Optional[datetime] = None,
                           current_user = Depends(get_current_user)):
    """Get events for a family"""
    return family_manager.get_family_events(
        family_id, 
        current_user["user_id"], 
        start_date, 
        end_date
    )


