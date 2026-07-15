"""
Authentication API Endpoints
RESTful API for authentication, session management, and user operations
"""

from fastapi import APIRouter, HTTPException, Depends, Request, Header
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr, Field
from typing import Optional, List, Dict, Any
from datetime import datetime
import asyncio
import logging

from core.auth import auth_manager
from core.passcode import passcode_manager
from core.sessions import session_manager, AuthenticationRequest, SessionType, AuthMethod
from core.rbac import rbac_manager
from core.security import rate_limiter
from core.account_setup import setup_token_manager
from models.database import auth_db
from api.dependencies import get_current_session, require_permission, validate_session_timeout
from api.dependencies import optional_session

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
    username: Optional[str] = Field(None, min_length=1, max_length=50)
    user_id: Optional[str] = Field(None, min_length=1, max_length=50)
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
    """Initial password setup for new users.

    Requires proof of authorization: a one-time ``setup_token`` (bootstrap or
    admin-minted) OR an authenticated admin session on the request. Without it,
    an attacker could claim a pre-provisioned username before the real user.
    """
    username: str = Field(..., min_length=1, max_length=50)
    new_password: str = Field(..., min_length=8)
    confirm_password: str = Field(..., min_length=8)
    setup_token: Optional[str] = Field(None, max_length=256)

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
async def get_user_profiles(panel_id: Optional[str] = None):
    """
    Get list of active user profiles for login page.
    No authentication required - public endpoint.

    Args:
        panel_id: optional touch-panel identifier. When provided and that
            panel has user bindings configured, the response is filtered to
            the bound users and enriched with panel context so the touch
            kiosk can auto-select a default user or hide the guest tile.

    Returns:
        - No panel_id (or unbound panel): a flat list of profiles (legacy
          shape; desktop login + unconfigured panels rely on this).
        - panel_id with bindings: an object
              {
                "profiles":        [ {user_id, username, role, avatar}, ... ],
                "default_user_id": "jason" | null,
                "allow_guest":     true | false,
                "panel_name":      "Kitchen Panel",
                "panel_id":        "kitchen-panel"
              }
    """
    try:
        profiles = []
        with auth_db.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT user_id, username, role
                FROM auth_users
                WHERE user_id != 'system'
                ORDER BY username
                """
            )
            for row in cursor.fetchall():
                profiles.append({
                    "user_id": row["user_id"],
                    "username": row["username"],
                    "role": row["role"],
                    "avatar": row["username"][0].upper() if row["username"] else "?",
                })

        if not panel_id:
            return profiles  # legacy flat-array shape

        # Panel-aware response: read panel + bindings from the same shared
        # SQLite (zoe-data writes, zoe-auth reads). Missing tables just mean
        # zoe-data hasn't run yet — treat as unbound and fall back silently.
        panel_name = None
        allow_guest = True
        default_user_id: Optional[str] = None
        allowed_user_ids: List[str] = []
        try:
            with auth_db.get_connection() as conn:
                prow = conn.execute(
                    "SELECT panel_id, name, allow_guest FROM panels WHERE panel_id = ?",
                    (panel_id,),
                ).fetchone()
                if prow:
                    panel_name = prow["name"]
                    try:
                        allow_guest = bool(prow["allow_guest"])
                    except (IndexError, KeyError):
                        allow_guest = True
                    brows = conn.execute(
                        "SELECT user_id, binding_type FROM panel_user_bindings WHERE panel_id = ?",
                        (panel_id,),
                    ).fetchall()
                    for b in brows:
                        if b["binding_type"] == "default":
                            default_user_id = b["user_id"]
                        elif b["binding_type"] == "allowed":
                            allowed_user_ids.append(b["user_id"])
        except Exception as inner:
            # Tables may not exist yet (zoe-data never booted) — graceful fallback.
            logger.debug("Panel lookup failed for %s: %s", panel_id, inner)

        bound_ids = set(allowed_user_ids) | ({default_user_id} if default_user_id else set())
        filtered = [p for p in profiles if p["user_id"] in bound_ids] if bound_ids else profiles

        return {
            "panel_id": panel_id,
            "panel_name": panel_name,
            "allow_guest": allow_guest,
            "default_user_id": default_user_id if default_user_id in {p["user_id"] for p in filtered} else None,
            "profiles": filtered,
        }
    except Exception as e:
        logger.error(f"Get profiles error: {e}", exc_info=True)
        return [] if not panel_id else {
            "panel_id": panel_id,
            "panel_name": None,
            "allow_guest": True,
            "default_user_id": None,
            "profiles": [],
        }

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
        ip_address = http_request.client.host if http_request.client else None

        # Resolve the account first so throttle buckets are keyed on user_id —
        # the same identity the session-layer gate uses — avoiding username vs
        # user_id drift that would leave some paths under-throttled.
        with auth_db.get_connection() as conn:
            cursor = conn.execute("SELECT user_id, password_hash FROM auth_users WHERE username = ?", (request.username,))
            user_row = cursor.fetchone()
            user_id = user_row["user_id"] if user_row else None
            password_hash = user_row["password_hash"] if user_row else None

        # Throttle identity: the DB user_id for known accounts, else the
        # submitted username (so login enumeration is still throttled).
        throttle_id = user_id or request.username

        # Brute-force throttle. The hard block is scoped to this exact
        # (IP, user) pair — checked first so a blocked pair is denied immediately
        # — so it can neither lock a victim out from other IPs nor lock a whole
        # NAT/proxy; progressive backoff then slows a hammering IP without ever
        # denying a valid credential from a clean IP.
        if rate_limiter.is_hard_blocked("login", ip_address, throttle_id):
            return AuthResponse(
                success=False,
                error_message="Too many login attempts. Please try again later."
            )
        delay = rate_limiter.delay_for("login", ip_address, throttle_id)
        if delay:
            await asyncio.sleep(delay)

        if not user_row:
            # Count unknown-user attempts too so username enumeration via the
            # login endpoint is throttled.
            rate_limiter.register_failed_attempt("login", ip_address, throttle_id)
            return AuthResponse(
                success=False,
                error_message="Invalid credentials"
            )

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
            rate_limiter.register_failed_attempt("login", ip_address, throttle_id)
            return AuthResponse(
                success=False,
                error_message=auth_result.error_message
            )

        # Reset-on-success: a proven credential clears this (IP, user) pair's
        # accumulated failures + hard block, so a legitimate user who fumbled a
        # few times isn't left throttled. Scoped to the pair (never the shared IP
        # bucket), so it can't be abused to wipe an attacker's IP throttle.
        rate_limiter.reset_for("login", ip_address=ip_address, user_id=throttle_id)

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

        # Honour "Keep me signed in" — extend session to 30 days
        if request.remember_me and session_result.session:
            session_manager.extend_session(session_result.session.session_id, days=30)

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
    Authenticate with username (or user_id) and passcode.

    Accepts either ``username`` or ``user_id`` — the panel/voice system passes
    ``user_id`` while the web login page passes ``username``.

    Args:
        request: Passcode credentials and device info
        http_request: FastAPI request object for IP extraction
        
    Returns:
        AuthResponse with limited session details
    """
    try:
        # Get client IP
        ip_address = http_request.client.host if http_request.client else None
        user_id: Optional[str] = None

        # Resolve user_id: accept either username (web login) or user_id (panel/voice).
        with auth_db.get_connection() as conn:
            if request.user_id:
                cursor = conn.execute(
                    "SELECT user_id FROM auth_users WHERE user_id = ?",
                    (request.user_id,)
                )
            elif request.username:
                cursor = conn.execute(
                    "SELECT user_id FROM auth_users WHERE LOWER(username) = LOWER(?)",
                    (request.username,)
                )
            else:
                # Voice/panel PIN challenges may not include identity. If exactly
                # one active passcode exists, use that user as the implicit target.
                active = conn.execute(
                    "SELECT user_id FROM passcodes WHERE is_active = 1"
                ).fetchall()
                if len(active) == 1:
                    user_id = active[0]["user_id"] if hasattr(active[0], "keys") else active[0][0]
                    cursor = None
                elif len(active) == 0:
                    return AuthResponse(success=False, error_message="No passcode configured")
                else:
                    return AuthResponse(success=False, error_message="username or user_id required")
            if user_id is None:
                row = cursor.fetchone()
                if not row:
                    return AuthResponse(
                        success=False,
                        error_message="Invalid credentials"
                    )
                user_id = row[0]

        # Throttle keyed on the resolved user_id — the SAME identity that
        # verify_passcode records failures under — so the pair hard-block and
        # progressive backoff actually feed back into this route. Pair hard-block
        # short-circuits first; the block is also re-checked inside verify_passcode.
        if rate_limiter.is_hard_blocked("passcode", ip_address, user_id):
            return AuthResponse(
                success=False,
                error_message="Too many attempts. Please try again later."
            )
        delay = rate_limiter.delay_for("passcode", ip_address, user_id)
        if delay:
            await asyncio.sleep(delay)

        # Verify passcode
        passcode_result = passcode_manager.verify_passcode(user_id, request.passcode, ip_address)
        if not passcode_result.is_valid:
            return AuthResponse(
                success=False,
                error_message=passcode_result.error_message
            )

        # Reset-on-success: clear this (IP, user) pair's accumulated passcode
        # failures + hard block (same anti-lockout property as login). Pair-scoped
        # only, so the shared-IP backoff is untouched.
        rate_limiter.reset_for("passcode", ip_address=ip_address, user_id=user_id)

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
            # Guest is a synthetic identity (guest_login mints a session with
            # user_id="guest" but no auth_users row), so get_user_info returns
            # None. Return the guest profile instead of 404 — otherwise every
            # guest session fails validation and the kiosk loops on the sign-in
            # card. Mirrors the guest_user_info that guest_login returns.
            if current_session.session_type == SessionType.GUEST or current_session.user_id == "guest":
                created = current_session.created_at.isoformat() if hasattr(current_session.created_at, "isoformat") else str(current_session.created_at)
                last = current_session.last_activity.isoformat() if hasattr(current_session.last_activity, "isoformat") else str(current_session.last_activity)
                return UserResponse(
                    user_id="guest",
                    username="Guest",
                    email="",
                    role="guest",
                    is_active=True,
                    is_verified=False,
                    created_at=created,
                    last_login=last,
                    has_passcode=False,
                    permissions=rbac_manager.list_user_permissions("guest"),
                )
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

def _is_admin_session(current_session) -> bool:
    """True if the request carries an admin session allowed to set passwords."""
    if not current_session:
        return False
    return session_manager.validate_session_permission(
        current_session.session_id, "users.create"
    )


@router.post("/password/setup")
async def setup_initial_password(
    request: InitialPasswordSetupRequest,
    http_request: Request,
    current_session = Depends(optional_session),
):
    """
    Set the initial password for a pending user (NULL / 'SETUP_REQUIRED' hash).

    Authorization is REQUIRED — without it any caller could claim a
    pre-provisioned username before the real person. The request must present
    one of:
      * a valid one-time ``setup_token`` (bootstrap token from the service log /
        ``ZOE_AUTH_SETUP_TOKEN``, or an admin-minted per-user token), or
      * an authenticated admin session (``users.create`` permission).

    The endpoint is rate-limited (IP + username) to blunt brute-forcing of the
    token. Legitimate first-run setup keeps working: the local operator reads the
    bootstrap token from the service journal (or sets the env var) and an admin
    mints per-user tokens for everyone else.

    Args:
        request: Username, new password, and (optionally) a setup token
        http_request: FastAPI request object
        current_session: Optional authenticated session (admin path)

    Returns:
        Success message
    """
    ip_address = http_request.client.host if http_request.client else None
    try:
        # Brute-force throttle for token guessing: a (IP, username)-pair hard
        # block plus per-IP progressive backoff. A clean IP is never delayed or
        # denied, so the real user's first-run setup is unaffected.
        if rate_limiter.is_hard_blocked("password_setup", ip_address, request.username):
            raise HTTPException(
                status_code=429,
                detail="Too many setup attempts. Please try again later.",
            )
        delay = rate_limiter.delay_for("password_setup", ip_address, request.username)
        if delay:
            await asyncio.sleep(delay)

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
                rate_limiter.register_failed_attempt("password_setup", ip_address, request.username)
                raise HTTPException(status_code=404, detail="User not found")

            user_id = user_row["user_id"]
            password_hash = user_row["password_hash"]

            # Only allow if password is NULL or 'SETUP_REQUIRED' (not set)
            if password_hash is not None and password_hash != 'SETUP_REQUIRED':
                raise HTTPException(status_code=400, detail="Password already set. Use password change instead.")

        # AUTHORIZATION: require a valid setup token OR an admin session. reserve()
        # verifies the token and holds it in-flight (blocking concurrent reuse)
        # but does NOT burn it — we only finalize after the password write commits,
        # so a failed write never locks the user out of retrying.
        admin_ok = _is_admin_session(current_session)
        token_kind = None
        if admin_ok:
            setup_token_manager.clear_pending(user_id)  # drop any stale token
        else:
            token_kind = setup_token_manager.reserve(user_id, request.setup_token)
            if not token_kind:
                rate_limiter.register_failed_attempt("password_setup", ip_address, request.username)
                logger.warning(
                    "Rejected unauthorized password setup for user '%s' from %s",
                    request.username, ip_address,
                )
                raise HTTPException(
                    status_code=403,
                    detail="Setup authorization required: provide a valid setup token or use an admin account.",
                )

        try:
            import bcrypt
            new_hash = bcrypt.hashpw(request.new_password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

            with auth_db.get_connection() as conn:
                # Conditional UPDATE so a row claimed concurrently isn't overwritten.
                conn.execute(
                    "UPDATE auth_users SET password_hash = ?, updated_at = ? WHERE user_id = ?"
                    " AND (password_hash IS NULL OR password_hash = 'SETUP_REQUIRED')",
                    (new_hash, datetime.utcnow().isoformat(), user_id)
                )
                if conn.total_changes == 0:
                    raise HTTPException(status_code=409, detail="Password already set. Use password change instead.")
                # Burn a pinned (env) bootstrap token durably in the SAME
                # transaction (fail closed): if it can't be recorded, roll the
                # password write back too, so the token can't later revalidate.
                if token_kind == "bootstrap" and setup_token_manager.bootstrap_needs_durable_record():
                    if not setup_token_manager._persist_bootstrap_consumed(request.setup_token, conn):
                        raise HTTPException(
                            status_code=503,
                            detail="Setup temporarily unavailable. Please try again.",
                        )
        except Exception:
            # Write failed/rolled back — release the reservation so retry works.
            if token_kind:
                setup_token_manager.release(user_id, token_kind, request.setup_token)
            raise

        # Committed: now consume the reserved token (one-time).
        if token_kind:
            setup_token_manager.finalize(user_id, token_kind, request.setup_token)
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
            try:
                invalidated = session_manager.invalidate_user_sessions(
                    current_session.user_id,
                    except_session=current_session.session_id
                )
                logger.info(
                    "Invalidated %s other sessions after password change for user %s",
                    invalidated,
                    current_session.user_id
                )
            except Exception:
                logger.exception(
                    "Failed to invalidate other sessions after password change for user %s",
                    current_session.user_id
                )
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


