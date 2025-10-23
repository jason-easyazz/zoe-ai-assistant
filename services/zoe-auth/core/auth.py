"""
Main Authentication Manager
Orchestrates password, passcode, and multi-factor authentication
"""

import bcrypt
import secrets
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass
import logging
import hashlib
import re

from models.database import auth_db, User, UserRole, AuthMethod
from core.passcode import passcode_manager, PasscodeValidationResult
from core.rbac import rbac_manager

logger = logging.getLogger(__name__)

@dataclass
class PasswordPolicy:
    """Password security policy"""
    min_length: int = 8
    max_length: int = 128
    require_uppercase: bool = True
    require_lowercase: bool = True
    require_numbers: bool = True
    require_special: bool = True
    prevent_common: bool = True
    prevent_username: bool = True
    prevent_email: bool = True
    prevent_reuse_count: int = 5
    max_age_days: Optional[int] = 90

@dataclass
class AuthValidationResult:
    """Result of authentication validation"""
    success: bool
    user_id: Optional[str] = None
    error_message: Optional[str] = None
    locked_until: Optional[datetime] = None
    requires_password_change: bool = False
    account_verified: bool = True

class AuthManager:
    """Main authentication manager"""
    
    def __init__(self, password_policy: Optional[PasswordPolicy] = None):
        self.password_policy = password_policy or PasswordPolicy()
        
        # Common passwords to reject
        self.common_passwords = {
            "password", "123456", "12345678", "qwerty", "abc123", "password123",
            "admin", "letmein", "welcome", "monkey", "dragon", "1234567890",
            "football", "iloveyou", "master", "sunshine", "princess", "flower"
        }

    def create_user(self, username: str, email: str, password: str, 
                   role: str = "user", created_by: Optional[str] = None) -> Tuple[bool, str]:
        """
        Create new user account
        
        Args:
            username: Unique username
            email: User email address
            password: Plain text password
            role: User role (default: "user")
            created_by: User creating this account (for audit)
            
        Returns:
            Tuple of (success, message/user_id)
        """
        try:
            # Validate inputs
            if not self._validate_username(username):
                return False, "Invalid username format"
                
            if not self._validate_email(email):
                return False, "Invalid email format"
                
            is_valid, validation_message = self._validate_password(password, username, email)
            if not is_valid:
                return False, validation_message

            # Check if user already exists
            if self._user_exists(username, email):
                return False, "Username or email already exists"

            # Hash password
            password_hash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
            
            # Generate user ID
            user_id = self._generate_user_id(username)
            
            # Create user record
            with auth_db.get_connection() as conn:
                conn.execute("""
                    INSERT INTO users 
                    (user_id, username, email, password_hash, role, is_active, is_verified, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    user_id, username, email, password_hash, role, 1, 1,
                    datetime.now().isoformat()
                ))

                # Store password history
                self._store_password_history(conn, user_id, password_hash)

            self._log_user_created(user_id, username, email, role, created_by)
            logger.info(f"Created user {username} with role {role}")
            return True, user_id

        except Exception as e:
            logger.error(f"Failed to create user {username}: {e}")
            return False, "Failed to create user account"

    def verify_password(self, user_id: str, password: str, 
                       ip_address: Optional[str] = None) -> AuthValidationResult:
        """
        Verify user password
        
        Args:
            user_id: User identifier
            password: Plain text password
            ip_address: Client IP for audit logging
            
        Returns:
            AuthValidationResult with verification details
        """
        try:
            # Get user data
            with auth_db.get_connection() as conn:
                cursor = conn.execute("""
                    SELECT username, password_hash, is_active, failed_login_attempts, 
                           locked_until, created_at, updated_at
                    FROM users 
                    WHERE user_id = ?
                """, (user_id,))
                
                row = cursor.fetchone()
                if not row:
                    self._log_auth_attempt(user_id, "password", "failure", 
                                         "user_not_found", ip_address)
                    return AuthValidationResult(
                        success=False,
                        error_message="Invalid credentials"
                    )

                username, password_hash, is_active, failed_attempts, locked_until, created_at, updated_at = row

                # Check if account is active
                if not is_active:
                    self._log_auth_attempt(user_id, "password", "blocked", 
                                         "account_disabled", ip_address)
                    return AuthValidationResult(
                        success=False,
                        error_message="Account is disabled"
                    )

                # Check if account is locked
                if locked_until:
                    locked_until_dt = datetime.fromisoformat(locked_until)
                    if datetime.now() < locked_until_dt:
                        self._log_auth_attempt(user_id, "password", "blocked", 
                                             "account_locked", ip_address)
                        return AuthValidationResult(
                            success=False,
                            error_message="Account is temporarily locked",
                            locked_until=locked_until_dt
                        )
                    else:
                        # Lock period expired, reset failed attempts
                        conn.execute("""
                            UPDATE users 
                            SET failed_login_attempts = 0, locked_until = NULL
                            WHERE user_id = ?
                        """, (user_id,))

                # Verify password
                if password_hash and bcrypt.checkpw(password.encode('utf-8'), password_hash.encode('utf-8')):
                    # Success - reset failed attempts and update last login
                    conn.execute("""
                        UPDATE users 
                        SET failed_login_attempts = 0, locked_until = NULL, last_login = ?
                        WHERE user_id = ?
                    """, (datetime.now().isoformat(), user_id))

                    # Check if password change is required
                    requires_change = self._password_requires_change(created_at, updated_at)

                    self._log_auth_attempt(user_id, "password", "success", 
                                         "valid_password", ip_address)
                    
                    return AuthValidationResult(
                        success=True,
                        user_id=user_id,
                        requires_password_change=requires_change
                    )
                else:
                    # Failed password - increment counter
                    new_failed_attempts = failed_attempts + 1
                    locked_until_new = None
                    
                    # Lock account after 5 failed attempts
                    if new_failed_attempts >= 5:
                        locked_until_new = datetime.now() + timedelta(minutes=15)
                    
                    conn.execute("""
                        UPDATE users 
                        SET failed_login_attempts = ?, locked_until = ?
                        WHERE user_id = ?
                    """, (new_failed_attempts, 
                          locked_until_new.isoformat() if locked_until_new else None,
                          user_id))

                    self._log_auth_attempt(user_id, "password", "failure", 
                                         "invalid_password", ip_address)
                    
                    if locked_until_new:
                        return AuthValidationResult(
                            success=False,
                            error_message="Too many failed attempts. Account locked.",
                            locked_until=locked_until_new
                        )
                    else:
                        remaining = 5 - new_failed_attempts
                        return AuthValidationResult(
                            success=False,
                            error_message=f"Invalid password. {remaining} attempts remaining."
                        )

        except Exception as e:
            logger.error(f"Password verification error for user {user_id}: {e}")
            self._log_auth_attempt(user_id, "password", "failure", 
                                 f"error: {str(e)}", ip_address)
            return AuthValidationResult(
                success=False,
                error_message="Authentication failed"
            )

    def change_password(self, user_id: str, current_password: str, 
                       new_password: str, changed_by: Optional[str] = None) -> Tuple[bool, str]:
        """
        Change user password
        
        Args:
            user_id: User identifier
            current_password: Current password for verification
            new_password: New password
            changed_by: User making the change (for admin changes)
            
        Returns:
            Tuple of (success, message)
        """
        try:
            # If changed_by is provided, verify admin permission
            if changed_by and changed_by != user_id:
                permission_check = rbac_manager.check_permission(changed_by, "users.update")
                if permission_check.result.value != "granted":
                    return False, "Insufficient permissions"
                # Admin can change password without current password
                verify_current = False
            else:
                verify_current = True

            # Verify current password if needed
            if verify_current:
                verification = self.verify_password(user_id, current_password)
                if not verification.success:
                    return False, "Current password is incorrect"

            # Validate new password
            with auth_db.get_connection() as conn:
                cursor = conn.execute(
                    "SELECT username, email FROM users WHERE user_id = ?",
                    (user_id,)
                )
                row = cursor.fetchone()
                if not row:
                    return False, "User not found"
                    
                username, email = row

            is_valid, validation_message = self._validate_password(new_password, username, email)
            if not is_valid:
                return False, validation_message

            # Check password reuse
            if self._password_recently_used(user_id, new_password):
                return False, f"Cannot reuse recent passwords"

            # Hash new password
            new_password_hash = bcrypt.hashpw(new_password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

            # Update password
            with auth_db.get_connection() as conn:
                conn.execute("""
                    UPDATE users 
                    SET password_hash = ?, updated_at = ?, failed_login_attempts = 0, locked_until = NULL
                    WHERE user_id = ?
                """, (new_password_hash, datetime.now().isoformat(), user_id))

                # Store in password history
                self._store_password_history(conn, user_id, new_password_hash)

            self._log_password_change(user_id, changed_by)
            logger.info(f"Password changed for user {user_id}")
            return True, "Password changed successfully"

        except Exception as e:
            logger.error(f"Failed to change password for user {user_id}: {e}")
            return False, "Failed to change password"

    def reset_password(self, user_id: str, reset_by: str) -> Tuple[bool, str, Optional[str]]:
        """
        Reset user password (admin function)
        
        Args:
            user_id: User to reset password for
            reset_by: Admin performing the reset
            
        Returns:
            Tuple of (success, message, temporary_password)
        """
        try:
            # Verify admin permission
            permission_check = rbac_manager.check_permission(reset_by, "users.reset_password")
            if permission_check.result.value != "granted":
                return False, "Insufficient permissions", None

            # Generate temporary password
            temp_password = self._generate_temporary_password()
            temp_password_hash = bcrypt.hashpw(temp_password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

            # Update user with temporary password and force change
            with auth_db.get_connection() as conn:
                conn.execute("""
                    UPDATE users 
                    SET password_hash = ?, updated_at = ?, failed_login_attempts = 0, 
                        locked_until = NULL, settings = json_set(COALESCE(settings, '{}'), '$.force_password_change', true)
                    WHERE user_id = ?
                """, (temp_password_hash, datetime.now().isoformat(), user_id))

                # Store in password history
                self._store_password_history(conn, user_id, temp_password_hash)

            self._log_password_reset(user_id, reset_by)
            logger.info(f"Password reset for user {user_id} by {reset_by}")
            return True, "Password reset successfully", temp_password

        except Exception as e:
            logger.error(f"Failed to reset password for user {user_id}: {e}")
            return False, "Failed to reset password", None

    def unlock_account(self, user_id: str, unlocked_by: str) -> bool:
        """
        Unlock user account (admin function)
        
        Args:
            user_id: User to unlock
            unlocked_by: Admin performing the unlock
            
        Returns:
            True if successful
        """
        try:
            # Verify admin permission
            permission_check = rbac_manager.check_permission(unlocked_by, "users.unlock")
            if permission_check.result.value != "granted":
                return False

            with auth_db.get_connection() as conn:
                conn.execute("""
                    UPDATE users 
                    SET failed_login_attempts = 0, locked_until = NULL
                    WHERE user_id = ?
                """, (user_id,))

                if conn.total_changes > 0:
                    self._log_account_unlock(user_id, unlocked_by)
                    # Also reset passcode failed attempts
                    passcode_manager.reset_failed_attempts(user_id)
                    return True

            return False

        except Exception as e:
            logger.error(f"Failed to unlock account {user_id}: {e}")
            return False

    def get_user_info(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Get user information (without sensitive data)"""
        try:
            with auth_db.get_connection() as conn:
                cursor = conn.execute("""
                    SELECT username, email, role, is_active, is_verified, created_at, 
                           last_login, failed_login_attempts, locked_until, settings
                    FROM users 
                    WHERE user_id = ?
                """, (user_id,))
                
                row = cursor.fetchone()
                if row:
                    return {
                        "user_id": user_id,
                        "username": row[0],
                        "email": row[1],
                        "role": row[2],
                        "is_active": bool(row[3]),
                        "is_verified": bool(row[4]),
                        "created_at": row[5],
                        "last_login": row[6],
                        "failed_login_attempts": row[7],
                        "locked_until": row[8],
                        "is_locked": bool(row[8] and datetime.now() < datetime.fromisoformat(row[8])),
                        "settings": row[9] or "{}"
                    }
                    
        except Exception as e:
            logger.error(f"Failed to get user info for {user_id}: {e}")
            
        return None

    def list_users(self, requested_by: str, active_only: bool = True) -> List[Dict[str, Any]]:
        """
        List users (admin function)
        
        Args:
            requested_by: User requesting the list
            active_only: Only return active users
            
        Returns:
            List of user information dictionaries
        """
        try:
            # Verify admin permission
            permission_check = rbac_manager.check_permission(requested_by, "users.read")
            if permission_check.result.value != "granted":
                return []

            users = []
            with auth_db.get_connection() as conn:
                query = """
                    SELECT user_id, username, email, role, is_active, is_verified, 
                           created_at, last_login, failed_login_attempts, locked_until
                    FROM users
                """
                params = []
                
                if active_only:
                    query += " WHERE is_active = 1"
                    
                query += " ORDER BY created_at DESC"
                
                cursor = conn.execute(query, params)
                for row in cursor.fetchall():
                    users.append({
                        "user_id": row[0],
                        "username": row[1],
                        "email": row[2],
                        "role": row[3],
                        "is_active": bool(row[4]),
                        "is_verified": bool(row[5]),
                        "created_at": row[6],
                        "last_login": row[7],
                        "failed_login_attempts": row[8],
                        "is_locked": bool(row[9] and datetime.now() < datetime.fromisoformat(row[9]))
                    })

            return users

        except Exception as e:
            logger.error(f"Failed to list users: {e}")
            return []

    def _validate_username(self, username: str) -> bool:
        """Validate username format"""
        if not username or len(username) < 3 or len(username) > 50:
            return False
        return re.match(r'^[a-zA-Z0-9_.-]+$', username) is not None

    def _validate_email(self, email: str) -> bool:
        """Validate email format"""
        pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        return re.match(pattern, email) is not None

    def _validate_password(self, password: str, username: str, email: str) -> Tuple[bool, str]:
        """Validate password against policy"""
        if len(password) < self.password_policy.min_length:
            return False, f"Password must be at least {self.password_policy.min_length} characters"
            
        if len(password) > self.password_policy.max_length:
            return False, f"Password must be no more than {self.password_policy.max_length} characters"

        if self.password_policy.require_uppercase and not re.search(r'[A-Z]', password):
            return False, "Password must contain at least one uppercase letter"

        if self.password_policy.require_lowercase and not re.search(r'[a-z]', password):
            return False, "Password must contain at least one lowercase letter"

        if self.password_policy.require_numbers and not re.search(r'\d', password):
            return False, "Password must contain at least one number"

        if self.password_policy.require_special and not re.search(r'[!@#$%^&*(),.?":{}|<>]', password):
            return False, "Password must contain at least one special character"

        if self.password_policy.prevent_common and password.lower() in self.common_passwords:
            return False, "Password is too common. Please choose a stronger password."

        if self.password_policy.prevent_username and username.lower() in password.lower():
            return False, "Password cannot contain your username"

        if self.password_policy.prevent_email and email.split('@')[0].lower() in password.lower():
            return False, "Password cannot contain your email address"

        return True, "Valid"

    def _user_exists(self, username: str, email: str) -> bool:
        """Check if user with username or email already exists"""
        try:
            with auth_db.get_connection() as conn:
                cursor = conn.execute("""
                    SELECT 1 FROM users 
                    WHERE username = ? OR email = ?
                """, (username, email))
                return cursor.fetchone() is not None
        except Exception:
            return True  # Err on side of caution

    def _generate_user_id(self, username: str) -> str:
        """Generate unique user ID"""
        base_id = username.lower()
        counter = 1
        
        with auth_db.get_connection() as conn:
            while True:
                user_id = base_id if counter == 1 else f"{base_id}_{counter}"
                cursor = conn.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,))
                if not cursor.fetchone():
                    return user_id
                counter += 1

    def _generate_temporary_password(self) -> str:
        """Generate secure temporary password"""
        # Generate password that meets policy requirements
        import string
        
        chars = string.ascii_letters + string.digits + "!@#$%^&*"
        password = ''.join(secrets.choice(chars) for _ in range(12))
        
        # Ensure it meets requirements
        while not self._validate_password(password, "temp", "temp@example.com")[0]:
            password = ''.join(secrets.choice(chars) for _ in range(12))
            
        return password

    def _password_requires_change(self, created_at: str, updated_at: str) -> bool:
        """Check if password change is required due to age"""
        if not self.password_policy.max_age_days:
            return False
            
        # Use updated_at if available, otherwise created_at
        last_change = updated_at or created_at
        if not last_change:
            return True
            
        last_change_dt = datetime.fromisoformat(last_change)
        age_days = (datetime.now() - last_change_dt).days
        
        return age_days > self.password_policy.max_age_days

    def _password_recently_used(self, user_id: str, password: str) -> bool:
        """Check if password was recently used"""
        if self.password_policy.prevent_reuse_count <= 0:
            return False
            
        try:
            with auth_db.get_connection() as conn:
                cursor = conn.execute("""
                    SELECT password_hash FROM password_history 
                    WHERE user_id = ? 
                    ORDER BY created_at DESC 
                    LIMIT ?
                """, (user_id, self.password_policy.prevent_reuse_count))
                
                for row in cursor.fetchall():
                    if bcrypt.checkpw(password.encode('utf-8'), row[0].encode('utf-8')):
                        return True
                        
        except Exception as e:
            logger.error(f"Error checking password history: {e}")
            
        return False

    def _store_password_history(self, conn: sqlite3.Connection, user_id: str, password_hash: str):
        """Store password in history"""
        # Create table if not exists
        conn.execute("""
            CREATE TABLE IF NOT EXISTS password_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
            )
        """)
        
        # Add to history
        conn.execute("""
            INSERT INTO password_history (user_id, password_hash, created_at)
            VALUES (?, ?, ?)
        """, (user_id, password_hash, datetime.now().isoformat()))
        
        # Clean up old entries
        conn.execute("""
            DELETE FROM password_history 
            WHERE user_id = ? AND id NOT IN (
                SELECT id FROM password_history 
                WHERE user_id = ? 
                ORDER BY created_at DESC 
                LIMIT ?
            )
        """, (user_id, user_id, self.password_policy.prevent_reuse_count))

    def _log_user_created(self, user_id: str, username: str, email: str, role: str, created_by: Optional[str]):
        """Log user creation"""
        try:
            with auth_db.get_connection() as conn:
                conn.execute("""
                    INSERT INTO audit_logs 
                    (log_id, user_id, action, resource, result, details, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    f"user_created_{user_id}", user_id, "user_creation",
                    "user", "success",
                    f'{{"username": "{username}", "email": "{email}", "role": "{role}", "created_by": "{created_by or "system"}"}}',
                    datetime.now().isoformat()
                ))
        except Exception as e:
            logger.error(f"Failed to log user creation: {e}")

    def _log_auth_attempt(self, user_id: str, method: str, result: str, reason: str, ip_address: Optional[str]):
        """Log authentication attempt - non-blocking, errors suppressed"""
        try:
            conn = auth_db.get_connection()
            conn.execute("""
                INSERT INTO audit_logs 
                (log_id, user_id, action, resource, result, ip_address, details, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                f"auth_{secrets.token_hex(8)}", user_id, f"auth_{method}",
                "authentication", result, ip_address,
                f'{{"reason": "{reason}", "method": "{method}"}}',
                datetime.now().isoformat()
            ))
            conn.commit()
            conn.close()
        except Exception as e:
            # Non-blocking - just log and continue
            logger.debug(f"Audit log failed (non-critical): {e}")

    def _log_password_change(self, user_id: str, changed_by: Optional[str]):
        """Log password change"""
        try:
            with auth_db.get_connection() as conn:
                conn.execute("""
                    INSERT INTO audit_logs 
                    (log_id, user_id, action, resource, result, details, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    f"pwd_change_{user_id}", user_id, "password_change",
                    "user_password", "success",
                    f'{{"changed_by": "{changed_by or user_id}"}}',
                    datetime.now().isoformat()
                ))
        except Exception as e:
            logger.error(f"Failed to log password change: {e}")

    def _log_password_reset(self, user_id: str, reset_by: str):
        """Log password reset"""
        try:
            with auth_db.get_connection() as conn:
                conn.execute("""
                    INSERT INTO audit_logs 
                    (log_id, user_id, action, resource, result, details, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    f"pwd_reset_{user_id}", user_id, "password_reset",
                    "user_password", "success",
                    f'{{"reset_by": "{reset_by}"}}',
                    datetime.now().isoformat()
                ))
        except Exception as e:
            logger.error(f"Failed to log password reset: {e}")

    def _log_account_unlock(self, user_id: str, unlocked_by: str):
        """Log account unlock"""
        try:
            with auth_db.get_connection() as conn:
                conn.execute("""
                    INSERT INTO audit_logs 
                    (log_id, user_id, action, resource, result, details, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    f"unlock_{user_id}", user_id, "account_unlock",
                    "user_account", "success",
                    f'{{"unlocked_by": "{unlocked_by}"}}',
                    datetime.now().isoformat()
                ))
        except Exception as e:
            logger.error(f"Failed to log account unlock: {e}")

# Global auth manager instance
auth_manager = AuthManager()

