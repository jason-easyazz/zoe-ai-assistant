"""
Session Management API Router
Provides REST endpoints for session management
"""

from fastapi import APIRouter, HTTPException, Depends, Header
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
from datetime import datetime
import logging

from session_manager import session_manager, Session

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sessions", tags=["sessions"])

# Pydantic models for request/response
class CreateSessionRequest(BaseModel):
    user_id: str
    timeout: Optional[int] = None
    metadata: Optional[Dict[str, Any]] = None

class SessionResponse(BaseModel):
    session_id: str
    user_id: str
    created_at: str
    last_activity: str
    expires_at: str
    is_active: bool
    metadata: Dict[str, Any]

class ExtendSessionRequest(BaseModel):
    additional_seconds: int

class SessionStatsResponse(BaseModel):
    active_sessions: int
    expired_sessions: int
    unique_users: int
    sessions_per_user: Dict[str, int]
    total_sessions: int

def get_session_from_header(x_session_id: Optional[str] = Header(None)) -> Optional[Session]:
    """Extract session from X-Session-ID header"""
    if not x_session_id:
        return None
    
    session = session_manager.get_session(x_session_id)
    if not session:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    
    return session

@router.post("/create", response_model=SessionResponse)
async def create_session(request: CreateSessionRequest):
    """
    Create a new user session
    
    Args:
        request: Session creation parameters
        
    Returns:
        Created session information
    """
    try:
        session = session_manager.create_session(
            user_id=request.user_id,
            timeout=request.timeout,
            metadata=request.metadata
        )
        
        return SessionResponse(
            session_id=session.session_id,
            user_id=session.user_id,
            created_at=session.created_at.isoformat(),
            last_activity=session.last_activity.isoformat(),
            expires_at=session.expires_at.isoformat(),
            is_active=session.is_active,
            metadata=session.metadata
        )
    except Exception as e:
        logger.error(f"Failed to create session: {e}")
        raise HTTPException(status_code=500, detail="Failed to create session")

@router.get("/{session_id}", response_model=SessionResponse)
async def get_session(session_id: str):
    """
    Get session information by ID
    
    Args:
        session_id: Session identifier
        
    Returns:
        Session information
    """
    session = session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    return SessionResponse(
        session_id=session.session_id,
        user_id=session.user_id,
        created_at=session.created_at.isoformat(),
        last_activity=session.last_activity.isoformat(),
        expires_at=session.expires_at.isoformat(),
        is_active=session.is_active,
        metadata=session.metadata
    )

@router.post("/{session_id}/activity")
async def update_session_activity(session_id: str):
    """
    Update session last activity time
    
    Args:
        session_id: Session identifier
        
    Returns:
        Success status
    """
    success = session_manager.update_session_activity(session_id)
    if not success:
        raise HTTPException(status_code=404, detail="Session not found")
    
    return {"message": "Session activity updated successfully"}

@router.post("/{session_id}/extend")
async def extend_session(session_id: str, request: ExtendSessionRequest):
    """
    Extend session expiration time
    
    Args:
        session_id: Session identifier
        request: Extension parameters
        
    Returns:
        Success status
    """
    success = session_manager.extend_session(session_id, request.additional_seconds)
    if not success:
        raise HTTPException(status_code=404, detail="Session not found")
    
    return {"message": f"Session extended by {request.additional_seconds} seconds"}

@router.delete("/{session_id}")
async def invalidate_session(session_id: str):
    """
    Invalidate a session
    
    Args:
        session_id: Session identifier
        
    Returns:
        Success status
    """
    success = session_manager.invalidate_session(session_id)
    if not success:
        raise HTTPException(status_code=404, detail="Session not found")
    
    return {"message": "Session invalidated successfully"}

@router.delete("/user/{user_id}")
async def invalidate_user_sessions(user_id: str):
    """
    Invalidate all sessions for a user
    
    Args:
        user_id: User identifier
        
    Returns:
        Number of sessions invalidated
    """
    count = session_manager.invalidate_user_sessions(user_id)
    return {"message": f"Invalidated {count} sessions for user {user_id}"}

@router.get("/user/{user_id}/sessions", response_model=List[SessionResponse])
async def get_user_sessions(user_id: str):
    """
    Get all active sessions for a user
    
    Args:
        user_id: User identifier
        
    Returns:
        List of active sessions
    """
    sessions = session_manager.get_user_sessions(user_id)
    
    return [
        SessionResponse(
            session_id=session.session_id,
            user_id=session.user_id,
            created_at=session.created_at.isoformat(),
            last_activity=session.last_activity.isoformat(),
            expires_at=session.expires_at.isoformat(),
            is_active=session.is_active,
            metadata=session.metadata
        )
        for session in sessions
    ]

@router.get("/stats", response_model=SessionStatsResponse)
async def get_session_stats():
    """
    Get session statistics
    
    Returns:
        Session statistics
    """
    stats = session_manager.get_session_stats()
    return SessionStatsResponse(**stats)

@router.get("/validate")
async def validate_session(session: Optional[Session] = Depends(get_session_from_header)):
    """
    Validate current session (requires X-Session-ID header)
    
    Returns:
        Session validation status
    """
    if not session:
        raise HTTPException(status_code=401, detail="No session provided")
    
    return {
        "valid": True,
        "session_id": session.session_id,
        "user_id": session.user_id,
        "expires_at": session.expires_at.isoformat()
    }

@router.post("/validate/{session_id}")
async def validate_session_by_id(session_id: str):
    """
    Validate session by ID
    
    Args:
        session_id: Session identifier
        
    Returns:
        Session validation status
    """
    session = session_manager.get_session(session_id)
    if not session:
        return {"valid": False, "reason": "Session not found or expired"}
    
    return {
        "valid": True,
        "session_id": session.session_id,
        "user_id": session.user_id,
        "expires_at": session.expires_at.isoformat()
    }
