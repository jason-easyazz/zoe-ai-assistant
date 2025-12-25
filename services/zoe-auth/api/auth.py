"""
Authentication API Endpoints
RESTful API for authentication, session management, and user operations
"""

from fastapi import APIRouter, HTTPException, Depends, Request, Header
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr, Field
from typing import Optional, List, Dict, Any
from datetime import datetime
import logging

from core.auth import auth_manager
from core.passcode import passcode_manager
from core.sessions import session_manager, AuthenticationRequest, SessionType, AuthMethod
from core.rbac import rbac_manager
from models.database import auth_db
from api.dependencies import get_current_session, require_permission, validate_session_timeout

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["authentication"])
security = HTTPBearer()

# Pydantic models for request/response
class LoginRequest(BaseModel):
    """Login request model"""
    username: str = Field(..., min_length=1, max_length=50)
    password: str = Field(..., min_length=1)
    device_info: Dict[str, Any] = Field(default_factory=dict)
    remember_me: bool = False

class PasscodeLoginRequest(BaseModel):
    """Passcode login request model"""
    username: str = Field(..., min_length=1, max_length=50)
    passcode: str = Field(..., min_length=4, max_length=8, pattern=r'^\d+$')
    device_info: Dict[str, Any] = Field(default_factory=dict)

class RegisterRequest(BaseModel):
    """User registration request model"""
    username: str = Field(..., min_length=3, max_length=50)
    email: EmailStr
    password: str = Field(..., min_length=8)
    role: str = Field(default="user")

class PasswordChangeRequest(BaseModel):
    """Password change request model"""
    current_password: str
    new_password: str = Field(..., min_length=8)

class InitialPasswordSetupRequest(BaseModel):
    """Initial password setup for new users"""
    username: str = Field(..., min_length=1, max_length=50)
    new_password: str = Field(..., min_length=8)
    confirm_password: str = Field(..., min_length=8)

class PasscodeSetupRequest(BaseModel):
    """Passcode setup request model"""
    passcode: str = Field(..., min_length=4, max_length=8, pattern=r'^\d+$')
    expires_at: Optional[datetime] = None

class SessionEscalationRequest(BaseModel):
    """Session escalation request model"""
    password: str

class AuthResponse(BaseModel):
    """Authentication response model"""
    success: bool
    session_id: Optional[str] = None
    session_type: Optional[str] = None
    expires_at: Optional[datetime] = None
    user_info: Optional[Dict[str, Any]] = None
    requires_escalation: bool = False
    error_message: Optional[str] = None

class UserResponse(BaseModel):
    """User information response model"""
    user_id: str
    username: str
    email: str
    role: str
    is_active: bool
    is_verified: bool
    created_at: str
    last_login: Optional[str] = None
    has_passcode: bool = False
    permissions: List[str] = []

# Authentication endpoints
@router.get("/profiles")
async def get_user_profiles():
    """
    Get list of active user profiles for login page
    No authentication required - public endpoint
    
    Returns:
        List of active users with basic info
    """
    try:
        profiles = []
        with auth_db.get_connection() as conn:
            cursor = conn.execute("""
                SELECT user_id, username, role
                FROM auth_users
                WHERE user_id != 'system'
                ORDER BY username
            """)
            
            for row in cursor.fetchall():
                profiles.append({
                    "user_id": row["user_id"],
                    "username": row["username"],
                    "role": row["role"],
                    "avatar": row["username"][0].upper() if row["username"] else "?"
                })
        
        return profiles
    except Exception as e:
        logger.error(f"Get profiles error: {e}", exc_info=True)
        return []  # Return empty list on error for login page

@router.post("/guest")
async def guest_login(request: dict, http_request: Request):
    """
    Guest login with temporary session
    No authentication required - creates limited guest session
    
    Args:
        request: Device info
        http_request: FastAPI request object
        
    Returns:
        Guest session details
    """
    try:
        device_info = request.get("device_info", {})
        ip_address = http_request.client.host if http_request.client else None
        
        # Create guest session using session manager
        result = session_manager.create_guest_session(
            device_info=device_info
        )
        
        if result:
            guest_user_info = {
                "user_id": "guest",
                "username": "Guest",
                "email": "",
                "role": "guest",
                "is_active": True,
                "is_verified": False,
                "created_at": datetime.now().isoformat(),
                "last_login": datetime.now().isoformat(),
                "failed_login_attempts": 0,
                "locked_until": None,
                "is_locked": False,
                "settings": "{}"
            }
            
            return {
                "success": True,
                "user_id": "guest",
                "username": "Guest",
                "role": "guest",
                "session_id": result.session_id,
                "session_type": "guest",
                "expires_at": result.expires_at.isoformat() if result.expires_at else None,
                "user_info": guest_user_info,
                "requires_escalation": False,
                "error_message": None
            }
        else:
            raise HTTPException(status_code=500, detail="Guest login failed")
    except Exception as e:
        logger.error(f"Guest login error: {e}")
        raise HTTPException(status_code=500, detail="Guest login failed")

@router.post("/login", response_model=AuthResponse)
async def login(request: LoginRequest, http_request: Request):
    """
    Authenticate with username and password
    
    Args:
        request: Login credentials and device info
        http_request: FastAPI request object for IP extraction
        
    Returns:
        AuthResponse with session details or error
    """
    try:
        # Get client IP
        ip_address = http_request.client.host
        
        # Look up user by username first
        with auth_db.get_connection() as conn:
            cursor = conn.execute("SELECT user_id, password_hash FROM auth_users WHERE username = ?", (request.username,))
            user_row = cursor.fetchone()
            
            if not user_row:
                return AuthResponse(
                    success=False,
                    error_message="Invalid credentials"
                )
            
            user_id = user_row["user_id"]
            password_hash = user_row["password_hash"]
            
            # Check if user needs to set password (NULL or 'SETUP_REQUIRED' password_hash)
            if password_hash is None or password_hash == 'SETUP_REQUIRED':
                return AuthResponse(
                    success=False,
                    error_message="PASSWORD_SETUP_REQUIRED",
                    requires_escalation=True,
                    user_info={"user_id": user_id, "username": request.username}
                )
        
        # Now verify password with user_id
        auth_result = auth_manager.verify_password(user_id, request.password, ip_address)
        if not auth_result.success:
            return AuthResponse(
                success=False,
                error_message=auth_result.error_message
            )

        # Create authentication request
        auth_request = AuthenticationRequest(
            user_id=auth_result.user_id,
            auth_method=AuthMethod.PASSWORD,
            credentials={"password": request.password},
            device_info=request.device_info,
            ip_address=ip_address,
            user_agent=http_request.headers.get("user-agent"),
            requested_session_type=SessionType.STANDARD
        )

        # Create session
        session_result = session_manager.authenticate(auth_request)
        if not session_result.success:
            return AuthResponse(
                success=False,
                error_message=session_result.error_message
            )

        # Get user info
        user_info = auth_manager.get_user_info(auth_result.user_id)
        
        return AuthResponse(
            success=True,
            session_id=session_result.session.session_id,
            session_type=session_result.session.session_type.value,
            expires_at=session_result.session.expires_at,
            user_info=user_info,
            requires_escalation=auth_result.requires_password_change
        )

    except Exception as e:
        logger.error(f"Login error: {e}")
        raise HTTPException(status_code=500, detail="Authentication failed")

@router.post("/login/passcode", response_model=AuthResponse)
async def login_passcode(request: PasscodeLoginRequest, http_request: Request):
    """
    Authenticate with username and passcode
    
    Args:
        request: Passcode credentials and device info
        http_request: FastAPI request object for IP extraction
        
    Returns:
        AuthResponse with limited session details
    """
    try:
        # Get client IP
        ip_address = http_request.client.host
        
        # Get user ID from username
        with auth_db.get_connection() as conn:
            cursor = conn.execute(
                "SELECT user_id FROM auth_users WHERE username = ? AND is_active = 1",
                (request.username,)
            )
            row = cursor.fetchone()
            if not row:
                return AuthResponse(
                    success=False,
                    error_message="Invalid credentials"
                )
            user_id = row[0]

        # Verify passcode
        passcode_result = passcode_manager.verify_passcode(user_id, request.passcode, ip_address)
        if not passcode_result.is_valid:
            return AuthResponse(
                success=False,
                error_message=passcode_result.error_message
            )

        # Create passcode session
        auth_request = AuthenticationRequest(
            user_id=user_id,
            auth_method=AuthMethod.PASSCODE,
            credentials={"passcode": request.passcode},
            device_info=request.device_info,
            ip_address=ip_address,
            user_agent=http_request.headers.get("user-agent"),
            requested_session_type=SessionType.PASSCODE
        )

        session_result = session_manager.authenticate(auth_request)
        if not session_result.success:
            return AuthResponse(
                success=False,
                error_message=session_result.error_message
            )

        # Get user info
        user_info = auth_manager.get_user_info(user_id)
        
        return AuthResponse(
            success=True,
            session_id=session_result.session.session_id,
            session_type=session_result.session.session_type.value,
            expires_at=session_result.session.expires_at,
            user_info=user_info,
            requires_escalation=False  # Passcode sessions can escalate later
        )

    except Exception as e:
        logger.error(f"Passcode login error: {e}")
        raise HTTPException(status_code=500, detail="Authentication failed")

@router.post("/register", response_model=AuthResponse)
async def register(request: RegisterRequest, 
                  current_session = Depends(require_permission("users.create"))):
    """
    Register new user (admin only)
    
    Args:
        request: User registration details
        current_session: Current authenticated session with user.create permission
        
    Returns:
        AuthResponse indicating success or failure
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
            return AuthResponse(
                success=True,
                user_info={"user_id": result}
            )
        else:
            return AuthResponse(
                success=False,
                error_message=result
            )

    except Exception as e:
        logger.error(f"Registration error: {e}")
        raise HTTPException(status_code=500, detail="Registration failed")

@router.post("/logout")
async def logout(session_id: str = Header(..., alias="X-Session-ID")):
    """
    Logout and invalidate session
    
    Args:
        session_id: Session ID from header
        
    Returns:
        Success message
    """
    try:
        success = session_manager.invalidate_session(session_id)
        if success:
            return {"message": "Logged out successfully"}
        else:
            raise HTTPException(status_code=404, detail="Session not found")

    except Exception as e:
        logger.error(f"Logout error: {e}")
        raise HTTPException(status_code=500, detail="Logout failed")

@router.post("/logout/all")
async def logout_all(current_session = Depends(get_current_session)):
    """
    Logout from all sessions for current user
    
    Args:
        current_session: Current authenticated session
        
    Returns:
        Number of sessions invalidated
    """
    try:
        count = session_manager.invalidate_user_sessions(
            current_session.user_id, 
            except_session=current_session.session_id
        )
        return {"message": f"Logged out from {count} other sessions"}

    except Exception as e:
        logger.error(f"Logout all error: {e}")
        raise HTTPException(status_code=500, detail="Logout failed")

@router.post("/refresh")
async def refresh_session(current_session = Depends(validate_session_timeout)):
    """
    Refresh current session expiration
    
    Args:
        current_session: Current authenticated session
        
    Returns:
        New expiration time
    """
    try:
        success = session_manager.refresh_session(current_session.session_id)
        if success:
            # Get updated session
            updated_session = session_manager.get_session(current_session.session_id)
            return {
                "message": "Session refreshed",
                "expires_at": updated_session.expires_at
            }
        else:
            raise HTTPException(status_code=404, detail="Session not found")

    except Exception as e:
        logger.error(f"Session refresh error: {e}")
        raise HTTPException(status_code=500, detail="Session refresh failed")

@router.post("/escalate", response_model=AuthResponse)
async def escalate_session(request: SessionEscalationRequest,
                          current_session = Depends(get_current_session)):
    """
    Escalate passcode session to full session with password verification
    
    Args:
        request: Password for escalation
        current_session: Current passcode session
        
    Returns:
        AuthResponse with escalated session details
    """
    try:
        if current_session.session_type != SessionType.PASSCODE:
            raise HTTPException(status_code=400, detail="Only passcode sessions can be escalated")

        success = session_manager.escalate_session(current_session.session_id, request.password)
        if success:
            # Get updated session
            escalated_session = session_manager.get_session(current_session.session_id)
            return AuthResponse(
                success=True,
                session_id=escalated_session.session_id,
                session_type=escalated_session.session_type.value,
                expires_at=escalated_session.expires_at
            )
        else:
            return AuthResponse(
                success=False,
                error_message="Invalid password"
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Session escalation error: {e}")
        raise HTTPException(status_code=500, detail="Escalation failed")

# User management endpoints
@router.get("/user", response_model=UserResponse)
async def get_current_user(current_session = Depends(get_current_session)):
    """
    Get current user information
    
    Args:
        current_session: Current authenticated session
        
    Returns:
        UserResponse with user details
    """
    try:
        user_info = auth_manager.get_user_info(current_session.user_id)
        if not user_info:
            raise HTTPException(status_code=404, detail="User not found")

        # Get passcode info
        passcode_info = passcode_manager.get_passcode_info(current_session.user_id)
        
        # Get user permissions
        permissions = rbac_manager.list_user_permissions(current_session.user_id)

        return UserResponse(
            user_id=user_info["user_id"],
            username=user_info["username"],
            email=user_info["email"],
            role=user_info["role"],
            is_active=user_info["is_active"],
            is_verified=user_info["is_verified"],
            created_at=user_info["created_at"],
            last_login=user_info["last_login"],
            has_passcode=passcode_info.get("has_passcode", False),
            permissions=permissions
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get user error: {e}")
        raise HTTPException(status_code=500, detail="Failed to get user information")

@router.get("/profile", response_model=UserResponse)
async def get_user_profile(current_session = Depends(get_current_session)):
    """
    Get current user profile (alias for /user for UI compatibility)
    
    Args:
        current_session: Current authenticated session
        
    Returns:
        UserResponse with user details
    """
    return await get_current_user(current_session)

@router.post("/password/setup")
async def setup_initial_password(request: InitialPasswordSetupRequest, http_request: Request):
    """
    Set initial password for users with NULL password_hash
    This is used for first-time login after account creation
    
    Args:
        request: Username and new password
        http_request: FastAPI request object
        
    Returns:
        Success message and session
    """
    try:
        # Validate passwords match
        if request.new_password != request.confirm_password:
            raise HTTPException(status_code=400, detail="Passwords do not match")
        
        # Look up user
        with auth_db.get_connection() as conn:
            cursor = conn.execute(
                "SELECT user_id, password_hash FROM auth_users WHERE username = ?",
                (request.username,)
            )
            user_row = cursor.fetchone()
            
            if not user_row:
                raise HTTPException(status_code=404, detail="User not found")
            
            user_id = user_row["user_id"]
            password_hash = user_row["password_hash"]
            
            # Only allow if password is NULL or 'SETUP_REQUIRED' (not set)
            if password_hash is not None and password_hash != 'SETUP_REQUIRED':
                raise HTTPException(status_code=400, detail="Password already set. Use password change instead.")
            
            # Set the new password using auth_manager
            import bcrypt
            new_hash = bcrypt.hashpw(request.new_password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
            
            conn.execute(
                "UPDATE users SET password_hash = ?, updated_at = ? WHERE user_id = ?",
                (new_hash, datetime.utcnow(), user_id)
            )
            
            logger.info(f"Initial password set for user: {request.username}")
            
            return {
                "success": True,
                "message": "Password set successfully. You can now log in.",
                "user_id": user_id,
                "username": request.username
            }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Initial password setup error: {e}")
        raise HTTPException(status_code=500, detail="Password setup failed")

@router.post("/password/change")
async def change_password(request: PasswordChangeRequest,
                         current_session = Depends(get_current_session)):
    """
    Change current user's password
    
    Args:
        request: Current and new password
        current_session: Current authenticated session
        
    Returns:
        Success message
    """
    try:
        success, message = auth_manager.change_password(
            current_session.user_id,
            request.current_password,
            request.new_password
        )

        if success:
            return {"message": message}
        else:
            raise HTTPException(status_code=400, detail=message)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Password change error: {e}")
        raise HTTPException(status_code=500, detail="Password change failed")

@router.get("/passcode/status")
async def get_passcode_status(current_session = Depends(get_current_session)):
    """
    Get passcode status for current user
    
    Args:
        current_session: Current authenticated session
        
    Returns:
        Passcode status information
    """
    try:
        passcode_info = passcode_manager.get_passcode_info(current_session.user_id)
        return {
            "has_passcode": passcode_info.get("has_passcode", False),
            "passcode_required": False,  # Can be configured per user
            "last_updated": passcode_info.get("last_updated")
        }
    except Exception as e:
        logger.error(f"Get passcode status error: {e}")
        raise HTTPException(status_code=500, detail="Failed to get passcode status")

@router.post("/passcode/setup")
async def setup_passcode(request: PasscodeSetupRequest,
                        current_session = Depends(get_current_session)):
    """
    Setup or update passcode for current user
    
    Args:
        request: Passcode setup details
        current_session: Current authenticated session
        
    Returns:
        Success message
    """
    try:
        success, message = passcode_manager.create_passcode(
            current_session.user_id,
            request.passcode,
            request.expires_at
        )

        if success:
            return {"message": message}
        else:
            raise HTTPException(status_code=400, detail=message)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Passcode setup error: {e}")
        raise HTTPException(status_code=500, detail="Passcode setup failed")

@router.delete("/passcode")
async def disable_passcode(current_session = Depends(get_current_session)):
    """
    Disable passcode for current user
    
    Args:
        current_session: Current authenticated session
        
    Returns:
        Success message
    """
    try:
        success = passcode_manager.disable_passcode(current_session.user_id)
        if success:
            return {"message": "Passcode disabled successfully"}
        else:
            raise HTTPException(status_code=404, detail="No passcode found")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Passcode disable error: {e}")
        raise HTTPException(status_code=500, detail="Passcode disable failed")

# Session management endpoints
@router.get("/sessions")
async def get_user_sessions(current_session = Depends(get_current_session)):
    """
    Get all active sessions for current user
    
    Args:
        current_session: Current authenticated session
        
    Returns:
        List of active sessions
    """
    try:
        sessions = session_manager.get_user_sessions(current_session.user_id)
        
        session_list = []
        for session in sessions:
            session_list.append({
                "session_id": session.session_id,
                "session_type": session.session_type.value,
                "auth_method": session.auth_method.value,
                "device_info": session.device_info,
                "created_at": session.created_at,
                "last_activity": session.last_activity,
                "expires_at": session.expires_at,
                "is_current": session.session_id == current_session.session_id
            })

        return {"sessions": session_list}

    except Exception as e:
        logger.error(f"Get sessions error: {e}")
        raise HTTPException(status_code=500, detail="Failed to get sessions")

@router.delete("/sessions/{session_id}")
async def invalidate_specific_session(session_id: str,
                                    current_session = Depends(get_current_session)):
    """
    Invalidate a specific session (user can only invalidate their own sessions)
    
    Args:
        session_id: Session ID to invalidate
        current_session: Current authenticated session
        
    Returns:
        Success message
    """
    try:
        # Check if session belongs to current user
        target_session = session_manager.get_session(session_id)
        if not target_session or target_session.user_id != current_session.user_id:
            raise HTTPException(status_code=404, detail="Session not found")

        success = session_manager.invalidate_session(session_id)
        if success:
            return {"message": "Session invalidated successfully"}
        else:
            raise HTTPException(status_code=404, detail="Session not found")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Session invalidation error: {e}")
        raise HTTPException(status_code=500, detail="Session invalidation failed")

# Permission checking endpoint
@router.post("/check-permission")
async def check_permission(permission: str, resource: Optional[str] = None,
                          current_session = Depends(get_current_session)):
    """
    Check if current user has specific permission
    
    Args:
        permission: Permission to check
        resource: Optional resource identifier
        current_session: Current authenticated session
        
    Returns:
        Permission check result
    """
    try:
        has_permission = session_manager.validate_session_permission(
            current_session.session_id, permission, resource
        )

        return {
            "permission": permission,
            "resource": resource,
            "granted": has_permission
        }

    except Exception as e:
        logger.error(f"Permission check error: {e}")
        raise HTTPException(status_code=500, detail="Permission check failed")

