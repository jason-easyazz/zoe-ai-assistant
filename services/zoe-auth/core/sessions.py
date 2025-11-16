"""
Enhanced Session Management for Multi-Factor Authentication
Supports different session types with varying security levels
"""

import secrets
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, asdict
from enum import Enum
import logging
import hashlib
import threading

from models.database import auth_db, AuthSession, SessionType, AuthMethod
from core.rbac import rbac_manager, AccessContext

logger = logging.getLogger(__name__)

@dataclass
class SessionConfig:
    """Configuration for different session types"""
    session_type: SessionType
    default_duration_minutes: int
    can_escalate: bool = True
    requires_password_for: List[str] = None  # List of permissions requiring password
    max_concurrent: Optional[int] = None
    persistent: bool = False

@dataclass
class AuthenticationRequest:
    """Authentication request details"""
    user_id: str
    auth_method: AuthMethod
    credentials: Dict[str, Any]
    device_info: Dict[str, Any]
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    requested_session_type: Optional[SessionType] = None

@dataclass
class AuthenticationResult:
    """Result of authentication attempt"""
    success: bool
    session: Optional[AuthSession] = None
    error_message: Optional[str] = None
    requires_escalation: bool = False
    escalation_methods: List[AuthMethod] = None
    rate_limited: bool = False
    account_locked: bool = False

class SessionSecurityPolicy:
    """Security policies for session management"""
    
    # Session configurations by type
    SESSION_CONFIGS = {
        SessionType.STANDARD: SessionConfig(
            session_type=SessionType.STANDARD,
            default_duration_minutes=480,  # 8 hours
            can_escalate=False,
            max_concurrent=50,  # Increased to allow multiple devices/PWAs
            persistent=True
        ),
        SessionType.PASSCODE: SessionConfig(
            session_type=SessionType.PASSCODE,
            default_duration_minutes=60,   # 1 hour
            can_escalate=True,
            requires_password_for=[
                "admin.*", "users.*", "roles.*", "system.*",
                "sensitive.*", "audit.*", "delete.*"
            ],
            max_concurrent=3,
            persistent=False
        ),
        SessionType.GUEST: SessionConfig(
            session_type=SessionType.GUEST,
            default_duration_minutes=30,   # 30 minutes
            can_escalate=False,
            requires_password_for=["*"],   # Everything requires escalation
            max_concurrent=10,
            persistent=False
        ),
        SessionType.API: SessionConfig(
            session_type=SessionType.API,
            default_duration_minutes=1440, # 24 hours
            can_escalate=False,
            max_concurrent=None,  # Unlimited for API
            persistent=True
        ),
        SessionType.SSO: SessionConfig(
            session_type=SessionType.SSO,
            default_duration_minutes=480,  # 8 hours
            can_escalate=False,
            max_concurrent=50,  # Increased to allow multiple devices/PWAs
            persistent=True
        )
    }

    @classmethod
    def get_config(cls, session_type: SessionType) -> SessionConfig:
        """Get configuration for session type"""
        return cls.SESSION_CONFIGS.get(session_type, cls.SESSION_CONFIGS[SessionType.STANDARD])

    @classmethod
    def requires_password_escalation(cls, session_type: SessionType, permission: str) -> bool:
        """Check if permission requires password escalation for session type"""
        config = cls.get_config(session_type)
        if not config.requires_password_for:
            return False
            
        for pattern in config.requires_password_for:
            if pattern == "*" or permission.startswith(pattern.replace("*", "")):
                return True
                
        return False

class EnhancedSessionManager:
    """Enhanced session manager with multi-factor support"""
    
    def __init__(self):
        self.active_sessions: Dict[str, AuthSession] = {}
        self.session_lock = threading.RLock()
        self._load_active_sessions()
        self._start_cleanup_thread()

    def authenticate(self, request: AuthenticationRequest) -> AuthenticationResult:
        """
        Authenticate user and create session
        
        Args:
            request: Authentication request details
            
        Returns:
            AuthenticationResult with session or error details
        """
        try:
            # Check rate limiting
            if self._is_rate_limited(request.user_id, request.ip_address):
                return AuthenticationResult(
                    success=False,
                    error_message="Too many authentication attempts. Please try again later.",
                    rate_limited=True
                )

            # Verify credentials based on auth method
            auth_result = self._verify_credentials(request)
            if not auth_result.success:
                self._log_failed_auth(request, auth_result.error_message)
                return auth_result

            # Determine session type
            session_type = self._determine_session_type(request)
            
            # Check concurrent session limits
            if not self._check_concurrent_limit(request.user_id, session_type):
                return AuthenticationResult(
                    success=False,
                    error_message="Maximum concurrent sessions reached"
                )

            # Create session
            session = self._create_session(request, session_type)
            if session:
                self._log_successful_auth(request, session)
                return AuthenticationResult(
                    success=True,
                    session=session
                )
            else:
                return AuthenticationResult(
                    success=False,
                    error_message="Failed to create session"
                )

        except Exception as e:
            logger.error(f"Authentication error: {e}")
            return AuthenticationResult(
                success=False,
                error_message="Authentication failed"
            )

    def get_session(self, session_id: str) -> Optional[AuthSession]:
        """Get session by ID with validation"""
        with self.session_lock:
            session = self.active_sessions.get(session_id)
            if session and self._is_session_valid(session):
                return session
            elif session:
                # Session expired, remove it
                logger.debug(f"Session expired, removing: {session_id[:20]}...")
                self._remove_session(session_id)
                
            return None

    def validate_session_permission(self, session_id: str, permission: str,
                                  resource: Optional[str] = None) -> bool:
        """
        Validate that session has permission with escalation handling
        
        Args:
            session_id: Session identifier
            permission: Required permission
            resource: Optional resource identifier
            
        Returns:
            True if permission granted, False if denied or escalation required
        """
        session = self.get_session(session_id)
        if not session:
            return False

        # Check if this permission requires password escalation
        if SessionSecurityPolicy.requires_password_escalation(session.session_type, permission):
            # Passcode sessions need to escalate for sensitive operations
            if session.session_type == SessionType.PASSCODE:
                logger.info(f"Permission {permission} requires password escalation for passcode session {session_id}")
                return False

        # Check RBAC permission
        context = AccessContext(
            user_id=session.user_id,
            session_type=session.session_type.value,
            device_info=session.device_info,
            ip_address=session.metadata.get("ip_address"),
            resource_owner=self._get_resource_owner(resource)
        )

        permission_check = rbac_manager.check_permission(
            session.user_id, permission, resource, context
        )

        return permission_check.result.value == "granted"

    def escalate_session(self, session_id: str, password: str) -> bool:
        """
        Escalate passcode session to full session with password verification
        
        Args:
            session_id: Session to escalate
            password: Password for verification
            
        Returns:
            True if escalation successful
        """
        session = self.get_session(session_id)
        if not session or session.session_type != SessionType.PASSCODE:
            return False

        # Verify password
        if not self._verify_password(session.user_id, password):
            self._log_escalation_failed(session_id, "invalid_password")
            return False

        # Upgrade session
        with self.session_lock:
            session.session_type = SessionType.STANDARD
            session.auth_method = AuthMethod.PASSWORD
            session.metadata["escalated_at"] = datetime.now(timezone.utc).isoformat()
            
            # Extend expiration for standard session
            config = SessionSecurityPolicy.get_config(SessionType.STANDARD)
            session.expires_at = datetime.now(timezone.utc) + timedelta(minutes=config.default_duration_minutes)
            
            self._save_session_to_db(session)
            
        self._log_escalation_success(session_id)
        logger.info(f"Session {session_id} escalated to standard access")
        return True

    def refresh_session(self, session_id: str) -> bool:
        """Refresh session expiration"""
        session = self.get_session(session_id)
        if not session:
            return False

        with self.session_lock:
            config = SessionSecurityPolicy.get_config(session.session_type)
            session.expires_at = datetime.now(timezone.utc) + timedelta(minutes=config.default_duration_minutes)
            session.last_activity = datetime.now(timezone.utc)
            self._save_session_to_db(session)

        return True

    def invalidate_session(self, session_id: str) -> bool:
        """Invalidate specific session"""
        with self.session_lock:
            return self._remove_session(session_id)

    def invalidate_user_sessions(self, user_id: str, except_session: Optional[str] = None) -> int:
        """Invalidate all sessions for user except optionally one"""
        with self.session_lock:
            sessions_to_remove = [
                sid for sid, session in self.active_sessions.items()
                if session.user_id == user_id and sid != except_session
            ]
            
            for session_id in sessions_to_remove:
                self._remove_session(session_id)
                
            return len(sessions_to_remove)

    def get_user_sessions(self, user_id: str) -> List[AuthSession]:
        """Get all active sessions for user"""
        with self.session_lock:
            return [
                session for session in self.active_sessions.values()
                if session.user_id == user_id and self._is_session_valid(session)
            ]

    def create_guest_session(self, device_info: Dict[str, Any], 
                           permissions: Optional[List[str]] = None) -> Optional[AuthSession]:
        """Create temporary guest session"""
        guest_request = AuthenticationRequest(
            user_id="guest",
            auth_method=AuthMethod.API_KEY,  # Guest doesn't use traditional auth
            credentials={},
            device_info=device_info,
            requested_session_type=SessionType.GUEST
        )

        session = self._create_session(guest_request, SessionType.GUEST)
        if session:
            if permissions:
                session.permissions_cache = permissions
            # Always save guest sessions to DB
            self._save_session_to_db(session)
            # Also add to active_sessions cache
            with self.session_lock:
                self.active_sessions[session.session_id] = session

        return session

    def _verify_credentials(self, request: AuthenticationRequest) -> AuthenticationResult:
        """Verify credentials based on auth method"""
        if request.auth_method == AuthMethod.PASSWORD:
            return self._verify_password_auth(request)
        elif request.auth_method == AuthMethod.PASSCODE:
            return self._verify_passcode_auth(request)
        elif request.auth_method == AuthMethod.API_KEY:
            return self._verify_api_key_auth(request)
        else:
            return AuthenticationResult(
                success=False,
                error_message="Unsupported authentication method"
            )

    def _verify_password_auth(self, request: AuthenticationRequest) -> AuthenticationResult:
        """Verify password authentication"""
        from .auth import AuthManager  # Avoid circular import
        
        auth_manager = AuthManager()
        result = auth_manager.verify_password(
            request.user_id, 
            request.credentials.get("password", "")
        )
        
        return AuthenticationResult(
            success=result.success,
            error_message=result.error_message,
            account_locked=result.locked_until is not None
        )

    def _verify_passcode_auth(self, request: AuthenticationRequest) -> AuthenticationResult:
        """Verify passcode authentication"""
        from .passcode import passcode_manager  # Avoid circular import
        
        result = passcode_manager.verify_passcode(
            request.user_id,
            request.credentials.get("passcode", ""),
            request.ip_address
        )
        
        return AuthenticationResult(
            success=result.is_valid,
            error_message=result.error_message,
            account_locked=result.locked_until is not None
        )

    def _verify_api_key_auth(self, request: AuthenticationRequest) -> AuthenticationResult:
        """Verify API key authentication"""
        # Implementation for API key verification
        api_key = request.credentials.get("api_key", "")
        
        try:
            with auth_db.get_connection() as conn:
                cursor = conn.execute("""
                    SELECT user_id, permissions FROM api_keys 
                    WHERE key_hash = ? AND is_active = 1 AND expires_at > ?
                """, (
                    hashlib.sha256(api_key.encode()).hexdigest(),
                    datetime.now(timezone.utc).isoformat()
                ))
                
                row = cursor.fetchone()
                if row:
                    return AuthenticationResult(success=True)
                    
        except Exception as e:
            logger.error(f"API key verification error: {e}")
            
        return AuthenticationResult(
            success=False,
            error_message="Invalid API key"
        )

    def _verify_password(self, user_id: str, password: str) -> bool:
        """Verify user password for escalation"""
        try:
            with auth_db.get_connection() as conn:
                cursor = conn.execute(
                    "SELECT password_hash FROM users WHERE user_id = ? AND is_active = 1",
                    (user_id,)
                )
                row = cursor.fetchone()
                if row and row[0]:
                    import bcrypt
                    return bcrypt.checkpw(password.encode(), row[0].encode())
        except Exception as e:
            logger.error(f"Password verification error: {e}")
            
        return False

    def _determine_session_type(self, request: AuthenticationRequest) -> SessionType:
        """Determine appropriate session type based on request"""
        if request.requested_session_type:
            return request.requested_session_type
            
        if request.auth_method == AuthMethod.PASSWORD:
            return SessionType.STANDARD
        elif request.auth_method == AuthMethod.PASSCODE:
            return SessionType.PASSCODE
        elif request.auth_method == AuthMethod.API_KEY:
            return SessionType.API
        else:
            return SessionType.STANDARD

    def _create_session(self, request: AuthenticationRequest, 
                       session_type: SessionType) -> Optional[AuthSession]:
        """Create new session"""
        try:
            config = SessionSecurityPolicy.get_config(session_type)
            
            session = AuthSession(
                session_id=secrets.token_urlsafe(32),
                user_id=request.user_id,
                session_type=session_type,
                auth_method=request.auth_method,
                device_info=request.device_info,
                created_at=datetime.now(timezone.utc),
                last_activity=datetime.now(timezone.utc),
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=config.default_duration_minutes),
                is_active=True,
                permissions_cache=rbac_manager.list_user_permissions(request.user_id),
                role_cache=rbac_manager.get_user_role(request.user_id),
                metadata={
                    "ip_address": request.ip_address,
                    "user_agent": request.user_agent,
                    "created_method": request.auth_method.value
                }
            )

            with self.session_lock:
                self.active_sessions[session.session_id] = session
                self._save_session_to_db(session)

            logger.debug(f"Session created for user {request.user_id}: {len(self.active_sessions)} total active")
            return session

        except Exception as e:
            logger.error(f"Failed to create session: {e}")
            return None

    def _check_concurrent_limit(self, user_id: str, session_type: SessionType) -> bool:
        """Check if user is within concurrent session limits"""
        config = SessionSecurityPolicy.get_config(session_type)
        if config.max_concurrent is None:
            return True

        current_count = len([
            s for s in self.active_sessions.values()
            if s.user_id == user_id and s.session_type == session_type
        ])

        return current_count < config.max_concurrent

    def _is_session_valid(self, session: AuthSession) -> bool:
        """Check if session is still valid"""
        return (session.is_active and 
                datetime.now(timezone.utc) < session.expires_at)

    def _is_rate_limited(self, user_id: str, ip_address: Optional[str]) -> bool:
        """Check if user/IP is rate limited"""
        # Simplified rate limiting check
        # Could be enhanced with Redis or more sophisticated algorithms
        return False

    def _remove_session(self, session_id: str) -> bool:
        """Remove session from memory and mark inactive in DB"""
        removed = session_id in self.active_sessions
        if removed:
            del self.active_sessions[session_id]

        try:
            with auth_db.get_connection() as conn:
                conn.execute("""
                    UPDATE auth_sessions 
                    SET is_active = 0 
                    WHERE session_id = ?
                """, (session_id,))
        except Exception as e:
            logger.error(f"Failed to deactivate session in DB: {e}")

        return removed

    def _save_session_to_db(self, session: AuthSession):
        """Save session to database"""
        try:
            with auth_db.get_connection() as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO auth_sessions 
                    (session_id, user_id, session_type, auth_method, device_info,
                     created_at, last_activity, expires_at, is_active, 
                     permissions_cache, role_cache, metadata)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    session.session_id, session.user_id, session.session_type.value,
                    session.auth_method.value, json.dumps(session.device_info),
                    session.created_at.isoformat(), session.last_activity.isoformat(),
                    session.expires_at.isoformat(), 1 if session.is_active else 0,
                    json.dumps(session.permissions_cache or []),
                    session.role_cache, json.dumps(session.metadata or {})
                ))
        except Exception as e:
            logger.error(f"Failed to save session to DB: {e}")

    def _load_active_sessions(self):
        """Load active sessions from database on startup"""
        try:
            with auth_db.get_connection() as conn:
                cursor = conn.execute("""
                    SELECT session_id, user_id, session_type, auth_method, device_info,
                           created_at, last_activity, expires_at, permissions_cache,
                           role_cache, metadata
                    FROM auth_sessions 
                    WHERE is_active = 1 AND expires_at > ?
                """, (datetime.now(timezone.utc).isoformat(),))

                for row in cursor.fetchall():
                    session = AuthSession(
                        session_id=row[0],
                        user_id=row[1],
                        session_type=SessionType(row[2]),
                        auth_method=AuthMethod(row[3]),
                        device_info=json.loads(row[4]),
                        created_at=datetime.fromisoformat(row[5]),
                        last_activity=datetime.fromisoformat(row[6]),
                        expires_at=datetime.fromisoformat(row[7]),
                        is_active=True,
                        permissions_cache=json.loads(row[8] or '[]'),
                        role_cache=row[9],
                        metadata=json.loads(row[10] or '{}')
                    )
                    self.active_sessions[session.session_id] = session

            logger.info(f"Loaded {len(self.active_sessions)} active sessions")

        except Exception as e:
            logger.error(f"Failed to load active sessions: {e}")

    def _start_cleanup_thread(self):
        """Start background thread for session cleanup"""
        def cleanup():
            import time
            while True:
                try:
                    time.sleep(300)  # Every 5 minutes
                    self._cleanup_expired_sessions()
                except Exception as e:
                    logger.error(f"Session cleanup error: {e}")

        cleanup_thread = threading.Thread(target=cleanup, daemon=True)
        cleanup_thread.start()

    def _cleanup_expired_sessions(self):
        """Clean up expired sessions from memory and database"""
        # Clean up in-memory sessions
        with self.session_lock:
            expired = [
                sid for sid, session in self.active_sessions.items()
                if not self._is_session_valid(session)
            ]

            for session_id in expired:
                self._remove_session(session_id)

            if expired:
                logger.info(f"Cleaned up {len(expired)} expired in-memory sessions")
        
        # Clean up expired sessions from database (auth_sessions table)
        try:
            with auth_db.get_connection() as conn:
                # Clean up sessions table (if it exists in auth.db)
                try:
                    cursor = conn.execute(
                        "DELETE FROM sessions WHERE expires_at < ?",
                        (datetime.now(timezone.utc).isoformat(),)
                    )
                    deleted = cursor.rowcount
                    if deleted > 0:
                        logger.info(f"Cleaned up {deleted} expired sessions from database")
                except sqlite3.OperationalError:
                    pass  # Table doesn't exist in this database
                
                # Clean up auth_sessions table (main zoe.db)
                try:
                    cursor = conn.execute(
                        "DELETE FROM auth_sessions WHERE expires_at < ?",
                        (datetime.now(timezone.utc).isoformat(),)
                    )
                    deleted = cursor.rowcount
                    if deleted > 0:
                        logger.info(f"Cleaned up {deleted} expired auth_sessions from database")
                except sqlite3.OperationalError:
                    pass  # Table doesn't exist in this database
                    
        except Exception as e:
            logger.error(f"Failed to cleanup database sessions: {e}")

    def _get_resource_owner(self, resource: Optional[str]) -> Optional[str]:
        """Get owner of resource for permission checks"""
        if not resource:
            return None
            
        # Parse resource string to extract owner
        # Format: "resource_type.resource_id" or "user.user_id.resource_type.resource_id"
        parts = resource.split(".")
        if len(parts) >= 3 and parts[0] == "user":
            return parts[1]
            
        return None

    def _log_successful_auth(self, request: AuthenticationRequest, session: AuthSession):
        """Log successful authentication"""
        try:
            with auth_db.get_connection() as conn:
                conn.execute("""
                    INSERT INTO audit_logs 
                    (log_id, user_id, action, resource, result, ip_address, user_agent, details, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    f"auth_{session.session_id}", request.user_id, "authentication",
                    "session", "success", request.ip_address, request.user_agent,
                    json.dumps({
                        "method": request.auth_method.value,
                        "session_type": session.session_type.value,
                        "device": request.device_info.get("type", "unknown")
                    }),
                    datetime.now(timezone.utc).isoformat()
                ))
        except Exception as e:
            logger.error(f"Failed to log successful auth: {e}")

    def _log_failed_auth(self, request: AuthenticationRequest, reason: str):
        """Log failed authentication attempt"""
        try:
            with auth_db.get_connection() as conn:
                conn.execute("""
                    INSERT INTO audit_logs 
                    (log_id, user_id, action, resource, result, ip_address, user_agent, details, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    f"auth_fail_{secrets.token_hex(8)}", request.user_id, "authentication",
                    "session", "failure", request.ip_address, request.user_agent,
                    json.dumps({
                        "method": request.auth_method.value,
                        "reason": reason,
                        "device": request.device_info.get("type", "unknown")
                    }),
                    datetime.now(timezone.utc).isoformat()
                ))
        except Exception as e:
            logger.error(f"Failed to log failed auth: {e}")

    def _log_escalation_success(self, session_id: str):
        """Log successful session escalation"""
        try:
            with auth_db.get_connection() as conn:
                conn.execute("""
                    INSERT INTO audit_logs 
                    (log_id, user_id, action, resource, result, details, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    f"escalation_{session_id}", 
                    self.active_sessions[session_id].user_id,
                    "session_escalation", "session", "success",
                    json.dumps({"session_id": session_id}),
                    datetime.now(timezone.utc).isoformat()
                ))
        except Exception as e:
            logger.error(f"Failed to log escalation: {e}")

    def _log_escalation_failed(self, session_id: str, reason: str):
        """Log failed session escalation"""
        try:
            with auth_db.get_connection() as conn:
                conn.execute("""
                    INSERT INTO audit_logs 
                    (log_id, user_id, action, resource, result, details, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    f"escalation_fail_{session_id}",
                    self.active_sessions.get(session_id, {}).get("user_id", "unknown"),
                    "session_escalation", "session", "failure",
                    json.dumps({"session_id": session_id, "reason": reason}),
                    datetime.now(timezone.utc).isoformat()
                ))
        except Exception as e:
            logger.error(f"Failed to log escalation failure: {e}")

# Global session manager instance
session_manager = EnhancedSessionManager()

