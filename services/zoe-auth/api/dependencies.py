"""
FastAPI Dependencies for Authentication
Reusable dependencies for session validation, permission checking, etc.
"""

from fastapi import Depends, HTTPException, Request, Header
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from typing import Optional, Callable, Annotated
import logging

from core.sessions import session_manager, AuthSession
from core.rbac import rbac_manager

logger = logging.getLogger(__name__)

# Make HTTPBearer optional (auto_error=False)
security = HTTPBearer(auto_error=False)

def get_session_id_from_header(
    x_session_id: Annotated[Optional[str], Header(alias="X-Session-ID")] = None
) -> Optional[str]:
    """Extract session ID from X-Session-ID header"""
    return x_session_id

def get_session_id_from_bearer_token(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)
) -> Optional[str]:
    """Extract session ID from Authorization Bearer token"""
    return credentials.credentials if credentials else None

def get_current_session(
    header_session_id: Optional[str] = Depends(get_session_id_from_header),
    bearer_session_id: Optional[str] = Depends(get_session_id_from_bearer_token)
) -> AuthSession:
    """
    Get current session from either header or bearer token
    
    Args:
        header_session_id: Session ID from X-Session-ID header
        bearer_session_id: Session ID from Authorization header
        
    Returns:
        Current valid session
        
    Raises:
        HTTPException: If no valid session found
    """
    # Try header first, then bearer token
    session_id = header_session_id or bearer_session_id
    
    if not session_id:
        raise HTTPException(
            status_code=401,
            detail="Authentication required. Provide session ID in X-Session-ID header or Authorization Bearer token."
        )
    
    session = session_manager.get_session(session_id)
    if not session:
        logger.warning(f"Session not found or expired: {session_id[:20]}...")
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired session. Please authenticate again."
        )

    return session

def optional_session(
    header_session_id: Optional[str] = Depends(get_session_id_from_header),
    bearer_session_id: Optional[str] = Depends(get_session_id_from_bearer_token)
) -> Optional[AuthSession]:
    """
    Optional session dependency - doesn't raise error if no session
    
    Args:
        header_session_id: Session ID from X-Session-ID header
        bearer_session_id: Session ID from Authorization header
        
    Returns:
        Session if available, None otherwise
    """
    session_id = header_session_id or bearer_session_id
    if session_id:
        return session_manager.get_session(session_id)
    return None

def validate_session_timeout(
    current_session: AuthSession = Depends(get_current_session)
) -> AuthSession:
    """
    Validate that session hasn't expired and update activity
    
    Args:
        current_session: Current session from dependency injection
        
    Returns:
        Valid session with updated activity
        
    Raises:
        HTTPException: If session has expired
    """
    # Session manager already validates expiration in get_session
    # Update last activity
    session_manager.refresh_session(current_session.session_id)
    return current_session

def require_permission(permission: str) -> Callable:
    """
    Create a dependency that requires specific permission
    
    Args:
        permission: Required permission string
        
    Returns:
        Dependency function that validates permission
    """
    def check_permission(
        current_session: AuthSession = Depends(validate_session_timeout)
    ) -> AuthSession:
        has_permission = session_manager.validate_session_permission(
            current_session.session_id, permission
        )
        
        if not has_permission:
            raise HTTPException(
                status_code=403,
                detail=f"Permission '{permission}' required. Contact administrator if you believe this is an error."
            )
        
        return current_session
    
    return check_permission

def require_role(role: str) -> Callable:
    """
    Create a dependency that requires specific role
    
    Args:
        role: Required role string
        
    Returns:
        Dependency function that validates role
    """
    def check_role(
        current_session: AuthSession = Depends(validate_session_timeout)
    ) -> AuthSession:
        user_role = rbac_manager.get_user_role(current_session.user_id)
        
        if user_role != role:
            raise HTTPException(
                status_code=403,
                detail=f"Role '{role}' required. Current role: {user_role}"
            )
        
        return current_session
    
    return check_role

def require_admin() -> Callable:
    """Require admin role"""
    return require_role("admin")

def require_standard_session() -> Callable:
    """
    Require full password-authenticated session (not passcode)
    
    Returns:
        Dependency function that validates session type
    """
    def check_session_type(
        current_session: AuthSession = Depends(validate_session_timeout)
    ) -> AuthSession:
        from models.database import SessionType
        
        if current_session.session_type == SessionType.PASSCODE:
            raise HTTPException(
                status_code=403,
                detail="This operation requires full authentication. Please escalate your session with your password."
            )
        
        return current_session
    
    return check_session_type

def require_self_or_permission(permission: str) -> Callable:
    """
    Create a dependency that allows access to own resources or requires permission for others
    
    Args:
        permission: Permission required for accessing other users' resources
        
    Returns:
        Dependency function
    """
    def check_self_or_permission(
        target_user_id: str,
        current_session: AuthSession = Depends(validate_session_timeout)
    ) -> AuthSession:
        # Allow access to own resources
        if current_session.user_id == target_user_id:
            return current_session
        
        # Check permission for other users' resources
        has_permission = session_manager.validate_session_permission(
            current_session.session_id, permission
        )
        
        if not has_permission:
            raise HTTPException(
                status_code=403,
                detail=f"Permission '{permission}' required to access other users' resources."
            )
        
        return current_session
    
    return check_self_or_permission

def get_client_info(request: Request) -> dict:
    """
    Extract client information from request
    
    Args:
        request: FastAPI request object
        
    Returns:
        Dictionary with client information
    """
    return {
        "ip_address": request.client.host if request.client else None,
        "user_agent": request.headers.get("user-agent"),
        "forwarded_for": request.headers.get("x-forwarded-for"),
        "real_ip": request.headers.get("x-real-ip")
    }

def require_device_type(allowed_types: list) -> Callable:
    """
    Create a dependency that requires specific device types
    
    Args:
        allowed_types: List of allowed device types
        
    Returns:
        Dependency function that validates device type
    """
    def check_device_type(
        current_session: AuthSession = Depends(validate_session_timeout)
    ) -> AuthSession:
        device_type = current_session.device_info.get("type", "unknown")
        
        if device_type not in allowed_types:
            raise HTTPException(
                status_code=403,
                detail=f"Device type '{device_type}' not allowed. Allowed types: {allowed_types}"
            )
        
        return current_session
    
    return check_device_type

def rate_limit(max_requests: int, window_seconds: int) -> Callable:
    """
    Create a rate limiting dependency with Redis-based sliding window
    
    Args:
        max_requests: Maximum requests allowed
        window_seconds: Time window in seconds
        
    Returns:
        Dependency function that enforces rate limits
    """
    def check_rate_limit(
        request: Request,
        current_session: Optional[AuthSession] = Depends(optional_session)
    ) -> None:
        import time
        
        # Get identifier - prefer user_id, fallback to IP
        identifier = current_session.user_id if current_session else (
            request.client.host if request.client else "unknown"
        )
        
        # Try to use Redis if available
        try:
            import redis
            import os
            
            redis_host = os.getenv("REDIS_HOST", "zoe-redis")
            redis_port = int(os.getenv("REDIS_PORT", "6379"))
            
            # Connect to Redis
            r = redis.Redis(
                host=redis_host,
                port=redis_port,
                decode_responses=True,
                socket_connect_timeout=1,
                socket_timeout=1
            )
            
            # Rate limit key
            key = f"rate_limit:{identifier}:{request.url.path}"
            current_time = time.time()
            window_start = current_time - window_seconds
            
            # Use sorted set for sliding window
            pipe = r.pipeline()
            
            # Remove old entries
            pipe.zremrangebyscore(key, 0, window_start)
            
            # Count requests in window
            pipe.zcard(key)
            
            # Add current request
            pipe.zadd(key, {str(current_time): current_time})
            
            # Set expiry
            pipe.expire(key, window_seconds + 10)
            
            results = pipe.execute()
            request_count = results[1]
            
            if request_count >= max_requests:
                logger.warning(
                    f"Rate limit exceeded for {identifier} on {request.url.path}: "
                    f"{request_count}/{max_requests} in {window_seconds}s"
                )
                raise HTTPException(
                    status_code=429,
                    detail=f"Rate limit exceeded. Max {max_requests} requests per {window_seconds} seconds.",
                    headers={"Retry-After": str(window_seconds)}
                )
                
        except ImportError:
            # Redis not available - fall back to in-memory tracking
            logger.warning("Redis not available for rate limiting - using fallback")
            _in_memory_rate_limit(identifier, request.url.path, max_requests, window_seconds)
        except Exception as e:
            # Redis connection failed or other error - fall back to in-memory tracking
            logger.warning(f"Redis error ({type(e).__name__}): {e} - using fallback")
            _in_memory_rate_limit(identifier, request.url.path, max_requests, window_seconds)
    
    return check_rate_limit


# In-memory fallback for rate limiting (not production-ready for multi-instance)
_rate_limit_storage = {}
_rate_limit_lock = None

def _in_memory_rate_limit(identifier: str, path: str, max_requests: int, window_seconds: int):
    """Fallback in-memory rate limiting (not suitable for production multi-instance)"""
    import time
    import threading
    
    global _rate_limit_lock
    if _rate_limit_lock is None:
        _rate_limit_lock = threading.Lock()
    
    key = f"{identifier}:{path}"
    current_time = time.time()
    window_start = current_time - window_seconds
    
    with _rate_limit_lock:
        # Initialize if not exists
        if key not in _rate_limit_storage:
            _rate_limit_storage[key] = []
        
        # Remove old entries
        _rate_limit_storage[key] = [
            ts for ts in _rate_limit_storage[key] 
            if ts > window_start
        ]
        
        # Check limit
        if len(_rate_limit_storage[key]) >= max_requests:
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded. Max {max_requests} requests per {window_seconds} seconds.",
                headers={"Retry-After": str(window_seconds)}
            )
        
        # Add current request
        _rate_limit_storage[key].append(current_time)
        
        # Cleanup old keys periodically (every 100 requests)
        if len(_rate_limit_storage) > 1000:
            _cleanup_rate_limit_storage(window_seconds)


def _cleanup_rate_limit_storage(window_seconds: int):
    """Clean up old rate limit entries"""
    import time
    current_time = time.time()
    window_start = current_time - (window_seconds * 2)  # Keep 2x window for safety
    
    keys_to_remove = []
    for key, timestamps in _rate_limit_storage.items():
        # Remove old timestamps
        _rate_limit_storage[key] = [ts for ts in timestamps if ts > window_start]
        # Mark empty keys for removal
        if not _rate_limit_storage[key]:
            keys_to_remove.append(key)
    
    for key in keys_to_remove:
        del _rate_limit_storage[key]

class PermissionChecker:
    """Helper class for complex permission checking"""
    
    def __init__(self, session: AuthSession):
        self.session = session
        
    def has_permission(self, permission: str, resource: Optional[str] = None) -> bool:
        """Check if session has specific permission"""
        return session_manager.validate_session_permission(
            self.session.session_id, permission, resource
        )
        
    def has_any_permission(self, permissions: list) -> bool:
        """Check if session has any of the listed permissions"""
        return any(self.has_permission(perm) for perm in permissions)
        
    def has_all_permissions(self, permissions: list) -> bool:
        """Check if session has all of the listed permissions"""
        return all(self.has_permission(perm) for perm in permissions)
        
    def can_access_user_resource(self, target_user_id: str, permission: str) -> bool:
        """Check if can access another user's resource"""
        return (self.session.user_id == target_user_id or 
                self.has_permission(permission))

def get_permission_checker(
    current_session: AuthSession = Depends(validate_session_timeout)
) -> PermissionChecker:
    """Get permission checker instance for current session"""
    return PermissionChecker(current_session)

# Common permission dependencies
require_user_create = require_permission("users.create")
require_user_read = require_permission("users.read")
require_user_update = require_permission("users.update")
require_user_delete = require_permission("users.delete")

require_role_manage = require_permission("roles.create")
require_audit_read = require_permission("audit.read")
require_system_monitor = require_permission("system.monitor")

# Common session type dependencies
require_full_auth = require_standard_session()

# Common device type dependencies
require_web_or_api = require_device_type(["web", "api", "unknown"])
allow_touch_panel = require_device_type(["web", "api", "touch_panel", "unknown"])

