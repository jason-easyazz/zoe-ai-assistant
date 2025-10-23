"""
Session Authentication Dependencies
Provides FastAPI dependencies for session-based authentication
"""

from fastapi import Depends, HTTPException, Request, Header
from typing import Optional, Annotated, Callable
import logging

from session_manager import session_manager, Session

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def get_current_session(
    request: Request,
    x_session_id: Annotated[Optional[str], Header(alias="X-Session-ID")] = None
) -> Optional[Session]:
    """
    Get current session from request state or header
    
    Args:
        request: FastAPI request object
        x_session_id: Session ID from X-Session-ID header
        
    Returns:
        Current session or None if not found
    """
    # First try to get from request state (set by middleware)
    if hasattr(request.state, 'session') and request.state.session:
        return request.state.session
    
    # Fallback to header-based lookup
    if x_session_id:
        return session_manager.get_session(x_session_id)
    
    return None

def require_session(
    session: Annotated[Optional[Session], Depends(get_current_session)]
) -> Session:
    """
    Require a valid session for the endpoint
    
    Args:
        session: Current session from dependency injection
        
    Returns:
        Valid session
        
    Raises:
        HTTPException: If no valid session found
    """
    if not session:
        raise HTTPException(
            status_code=401,
            detail="Authentication required. Please provide a valid session ID in X-Session-ID header."
        )
    
    return session

def require_user_session(user_id: str) -> Callable:
    """
    Create a dependency that requires a session for a specific user
    
    Args:
        user_id: Required user ID
        
    Returns:
        Dependency function
    """
    def check_user_session(
        session: Annotated[Session, Depends(require_session)]
    ) -> Session:
        if session.user_id != user_id:
            raise HTTPException(
                status_code=403,
                detail=f"Access denied. Session belongs to user {session.user_id}, but {user_id} is required."
            )
        return session
    
    return check_user_session

def optional_session(
    session: Annotated[Optional[Session], Depends(get_current_session)]
) -> Optional[Session]:
    """
    Optional session dependency - doesn't raise error if no session
    
    Args:
        session: Current session from dependency injection
        
    Returns:
        Session if available, None otherwise
    """
    return session

def get_session_user_id(session: Annotated[Session, Depends(require_session)]) -> str:
    """
    Get user ID from current session
    
    Args:
        session: Current session from dependency injection
        
    Returns:
        User ID
    """
    return session.user_id

def get_session_metadata(session: Annotated[Session, Depends(require_session)]) -> dict:
    """
    Get session metadata
    
    Args:
        session: Current session from dependency injection
        
    Returns:
        Session metadata dictionary
    """
    return session.metadata

def check_session_permission(permission: str) -> Callable:
    """
    Create a dependency that checks for specific session permission
    
    Args:
        permission: Required permission string
        
    Returns:
        Dependency function
    """
    def check_permission(
        session: Annotated[Session, Depends(require_session)]
    ) -> Session:
        permissions = session.metadata.get('permissions', [])
        if permission not in permissions:
            raise HTTPException(
                status_code=403,
                detail=f"Permission '{permission}' required. Available permissions: {permissions}"
            )
        return session
    
    return check_permission

def check_session_role(role: str) -> Callable:
    """
    Create a dependency that checks for specific session role
    
    Args:
        role: Required role string
        
    Returns:
        Dependency function
    """
    def check_role(
        session: Annotated[Session, Depends(require_session)]
    ) -> Session:
        user_role = session.metadata.get('role', 'user')
        if user_role != role:
            raise HTTPException(
                status_code=403,
                detail=f"Role '{role}' required. Current role: {user_role}"
            )
        return session
    
    return check_role

def validate_session_timeout(session: Annotated[Session, Depends(require_session)]) -> Session:
    """
    Validate that session hasn't expired
    
    Args:
        session: Current session from dependency injection
        
    Returns:
        Valid session
        
    Raises:
        HTTPException: If session has expired
    """
    from datetime import datetime
    
    if datetime.now() >= session.expires_at:
        # Clean up expired session
        session_manager.invalidate_session(session.session_id)
        raise HTTPException(
            status_code=401,
            detail="Session has expired. Please create a new session."
        )
    
    return session

def update_session_activity(session: Annotated[Session, Depends(require_session)]) -> Session:
    """
    Update session activity and return session
    
    Args:
        session: Current session from dependency injection
        
    Returns:
        Updated session
    """
    session_manager.update_session_activity(session.session_id)
    return session

# Common session dependency combinations
def authenticated_user() -> Callable:
    """Get authenticated user with session validation"""
    return Depends(require_session)

def admin_user() -> Callable:
    """Require admin role"""
    return Depends(check_session_role('admin'))

def developer_user() -> Callable:
    """Require developer role"""
    return Depends(check_session_role('developer'))

def user_with_permission(permission: str) -> Callable:
    """Require specific permission"""
    return Depends(check_session_permission(permission))

def optional_authenticated_user() -> Callable:
    """Optional authentication - doesn't fail if no session"""
    return Depends(optional_session)
