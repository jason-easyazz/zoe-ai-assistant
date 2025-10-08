"""
Session Integration Example
Shows how to integrate session management with existing routers
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict, Any

from session_auth import (
    require_session, 
    optional_session, 
    get_session_user_id,
    get_session_metadata,
    admin_user,
    developer_user
)
from session_manager import Session

# Example router showing session integration
router = APIRouter(prefix="/example", tags=["example"])

class ChatMessage(BaseModel):
    message: str
    context: Optional[Dict[str, Any]] = None

class TaskRequest(BaseModel):
    title: str
    description: str
    priority: str = "medium"

@router.post("/chat")
async def chat_with_session(
    request: ChatMessage,
    session: Session = Depends(require_session)
):
    """
    Chat endpoint that requires authentication
    Shows user-specific context and session metadata
    """
    user_id = session.user_id
    session_metadata = session.metadata
    
    # Use session information for personalized responses
    response = {
        "message": f"Hello {user_id}! I received your message: {request.message}",
        "session_info": {
            "user_id": user_id,
            "session_id": session.session_id,
            "metadata": session_metadata
        },
        "context": request.context or {}
    }
    
    return response

@router.post("/chat/optional")
async def chat_optional_session(
    request: ChatMessage,
    session: Optional[Session] = Depends(optional_session)
):
    """
    Chat endpoint with optional authentication
    Provides different responses based on authentication status
    """
    if session:
        # Authenticated user
        response = {
            "message": f"Hello {session.user_id}! Your message: {request.message}",
            "authenticated": True,
            "session_id": session.session_id
        }
    else:
        # Anonymous user
        response = {
            "message": f"Hello anonymous user! Your message: {request.message}",
            "authenticated": False,
            "note": "Create a session for personalized experience"
        }
    
    return response

@router.post("/tasks")
async def create_task(
    request: TaskRequest,
    session: Session = Depends(require_session)
):
    """
    Create task endpoint with user context
    Automatically associates task with session user
    """
    user_id = session.user_id
    
    # Create task with user context
    task_data = {
        "id": f"task_{session.session_id}_{int(session.created_at.timestamp())}",
        "title": request.title,
        "description": request.description,
        "priority": request.priority,
        "created_by": user_id,
        "session_id": session.session_id,
        "created_at": session.created_at.isoformat()
    }
    
    return {
        "message": "Task created successfully",
        "task": task_data,
        "user_context": {
            "user_id": user_id,
            "session_metadata": session.metadata
        }
    }

@router.get("/admin/stats")
async def admin_stats(session: Session = Depends(admin_user)):
    """
    Admin-only endpoint
    Requires admin role in session metadata
    """
    from session_manager import session_manager
    
    stats = session_manager.get_session_stats()
    
    return {
        "admin_user": session.user_id,
        "session_stats": stats,
        "admin_actions": ["view_stats", "manage_users", "system_config"]
    }

@router.get("/developer/tools")
async def developer_tools(session: Session = Depends(developer_user)):
    """
    Developer-only endpoint
    Requires developer role in session metadata
    """
    return {
        "developer_user": session.user_id,
        "available_tools": [
            "code_analysis",
            "debugging",
            "testing",
            "deployment"
        ],
        "session_info": {
            "session_id": session.session_id,
            "role": session.metadata.get('role', 'unknown'),
            "permissions": session.metadata.get('permissions', [])
        }
    }

@router.get("/profile")
async def get_user_profile(session: Session = Depends(require_session)):
    """
    Get user profile based on session
    """
    return {
        "user_id": session.user_id,
        "session_id": session.session_id,
        "created_at": session.created_at.isoformat(),
        "last_activity": session.last_activity.isoformat(),
        "expires_at": session.expires_at.isoformat(),
        "profile": {
            "role": session.metadata.get('role', 'user'),
            "permissions": session.metadata.get('permissions', []),
            "preferences": session.metadata.get('preferences', {}),
            "custom_data": session.metadata.get('custom_data', {})
        }
    }

@router.put("/profile")
async def update_user_profile(
    updates: Dict[str, Any],
    session: Session = Depends(require_session)
):
    """
    Update user profile in session metadata
    """
    # Update session metadata
    session.metadata.update(updates)
    
    # Save updated session
    from session_manager import session_manager
    session_manager._save_session_to_db(session)
    
    return {
        "message": "Profile updated successfully",
        "updated_fields": list(updates.keys()),
        "current_profile": session.metadata
    }

@router.get("/sessions/current")
async def get_current_session(session: Session = Depends(require_session)):
    """
    Get current session information
    """
    return {
        "session_id": session.session_id,
        "user_id": session.user_id,
        "created_at": session.created_at.isoformat(),
        "last_activity": session.last_activity.isoformat(),
        "expires_at": session.expires_at.isoformat(),
        "is_active": session.is_active,
        "metadata": session.metadata
    }

@router.post("/sessions/refresh")
async def refresh_session(session: Session = Depends(require_session)):
    """
    Refresh session activity
    """
    from session_manager import session_manager
    
    # Update activity
    session_manager.update_session_activity(session.session_id)
    
    return {
        "message": "Session refreshed successfully",
        "new_last_activity": session.last_activity.isoformat()
    }

@router.post("/sessions/extend")
async def extend_session(
    additional_seconds: int,
    session: Session = Depends(require_session)
):
    """
    Extend session expiration
    """
    from session_manager import session_manager
    
    success = session_manager.extend_session(session.session_id, additional_seconds)
    
    if success:
        return {
            "message": f"Session extended by {additional_seconds} seconds",
            "new_expires_at": session.expires_at.isoformat()
        }
    else:
        raise HTTPException(status_code=500, detail="Failed to extend session")
