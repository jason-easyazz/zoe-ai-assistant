"""
Passcode Authentication System
Secure handling of 4-8 digit PIN codes with advanced security features
"""

import argon2
import secrets
import re
import sqlite3
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple, Any
import logging
from dataclasses import dataclass

from models.database import auth_db, AuthMethod, AuditLog

logger = logging.getLogger(__name__)

@dataclass
class PasscodeValidationResult:
    """Result of passcode validation"""
    is_valid: bool
    user_id: Optional[str] = None
    error_message: Optional[str] = None
    remaining_attempts: Optional[int] = None
    locked_until: Optional[datetime] = None

@dataclass
class PasscodePolicy:
    """Passcode security policy configuration"""
    min_length: int = 4
    max_length: int = 8
    max_attempts: int = 5
    lockout_duration_minutes: int = 15
    require_unique: bool = True  # No duplicate passcodes across users
    prevent_common: bool = True  # Prevent 1234, 1111, etc.
    expiry_days: Optional[int] = None  # Optional expiry
    prevent_reuse_count: int = 3  # Prevent reusing last N passcodes

class PasscodeManager:
    """Manages passcode authentication with advanced security"""
    
    def __init__(self, policy: Optional[PasscodePolicy] = None):
        self.policy = policy or PasscodePolicy()
        self.hasher = argon2.PasswordHasher(
            time_cost=2,    # Faster for PINs
            memory_cost=65536,  # 64MB
            parallelism=1,
            hash_len=32,
            salt_len=16
        )
        
        # Common/weak passcode patterns to block
        self.blocked_patterns = {
            "1234", "4321", "1111", "2222", "3333", "4444", "5555", "6666", 
            "7777", "8888", "9999", "0000", "1122", "2233", "3344", "4455", 
            "5566", "6677", "7788", "8899", "9900", "0011", "1212", "2323",
            "3434", "4545", "5656", "6767", "7878", "8989", "9090", "0101"
        }

    def create_passcode(self, user_id: str, passcode: str, 
                       expires_at: Optional[datetime] = None) -> Tuple[bool, str]:
        """
        Create or update passcode for user
        
        Args:
            user_id: User identifier
            passcode: Plain text passcode (will be hashed)
            expires_at: Optional expiration datetime
            
        Returns:
            Tuple of (success, message)
        """
        try:
            # Validate passcode
            is_valid, message = self._validate_passcode(passcode, user_id)
            if not is_valid:
                self._log_audit(user_id, "passcode_create_failed", "passcode", 
                              "failure", {"reason": message})
                return False, message

            # Hash passcode
            salt = secrets.token_hex(16)
            passcode_hash = self.hasher.hash(passcode + salt)
            
            # Calculate expiry if policy requires it
            if self.policy.expiry_days and not expires_at:
                expires_at = datetime.now() + timedelta(days=self.policy.expiry_days)

            # Store in database
            with auth_db.get_connection() as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO passcodes 
                    (user_id, passcode_hash, algorithm, salt, created_at, expires_at, 
                     failed_attempts, max_attempts, is_active)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    user_id, passcode_hash, "argon2", salt,
                    datetime.now().isoformat(),
                    expires_at.isoformat() if expires_at else None,
                    0, self.policy.max_attempts, 1
                ))

                # Store passcode history if policy requires it
                if self.policy.prevent_reuse_count > 0:
                    self._store_passcode_history(conn, user_id, passcode_hash)

            self._log_audit(user_id, "passcode_created", "passcode", "success")
            logger.info(f"Passcode created for user {user_id}")
            return True, "Passcode created successfully"

        except Exception as e:
            logger.error(f"Failed to create passcode for user {user_id}: {e}")
            self._log_audit(user_id, "passcode_create_error", "passcode", 
                          "failure", {"error": str(e)})
            return False, "Failed to create passcode"

    def verify_passcode(self, user_id: str, passcode: str, 
                       ip_address: Optional[str] = None) -> PasscodeValidationResult:
        """
        Verify passcode for user
        
        Args:
            user_id: User identifier
            passcode: Plain text passcode to verify
            ip_address: Client IP for audit logging
            
        Returns:
            PasscodeValidationResult with verification details
        """
        try:
            # Check rate limiting first
            if self._is_rate_limited(user_id, ip_address):
                self._log_audit(user_id, "passcode_verify_blocked", "passcode", 
                              "blocked", {"reason": "rate_limited", "ip": ip_address})
                return PasscodeValidationResult(
                    is_valid=False,
                    error_message="Too many attempts. Please try again later."
                )

            # Get passcode data from database
            with auth_db.get_connection() as conn:
                cursor = conn.execute("""
                    SELECT passcode_hash, salt, failed_attempts, max_attempts, 
                           expires_at, is_active, last_used
                    FROM passcodes 
                    WHERE user_id = ? AND is_active = 1
                """, (user_id,))
                
                row = cursor.fetchone()
                if not row:
                    self._log_audit(user_id, "passcode_verify_failed", "passcode", 
                                  "failure", {"reason": "no_passcode", "ip": ip_address})
                    return PasscodeValidationResult(
                        is_valid=False,
                        error_message="No passcode configured"
                    )

                passcode_hash, salt, failed_attempts, max_attempts, expires_at, is_active, last_used = row

                # Check if account is locked
                if failed_attempts >= max_attempts:
                    locked_until = self._calculate_lockout_end(last_used, failed_attempts)
                    if locked_until and datetime.now() < locked_until:
                        self._log_audit(user_id, "passcode_verify_blocked", "passcode", 
                                      "blocked", {"reason": "locked", "ip": ip_address})
                        return PasscodeValidationResult(
                            is_valid=False,
                            error_message="Account temporarily locked",
                            locked_until=locked_until
                        )

                # Check expiry
                if expires_at and datetime.now() > datetime.fromisoformat(expires_at):
                    self._log_audit(user_id, "passcode_verify_failed", "passcode", 
                                  "failure", {"reason": "expired", "ip": ip_address})
                    return PasscodeValidationResult(
                        is_valid=False,
                        error_message="Passcode has expired"
                    )

                # Verify passcode
                try:
                    self.hasher.verify(passcode_hash, passcode + salt)
                    
                    # Success - reset failed attempts and update last used
                    conn.execute("""
                        UPDATE passcodes 
                        SET failed_attempts = 0, last_used = ?
                        WHERE user_id = ?
                    """, (datetime.now().isoformat(), user_id))

                    self._log_audit(user_id, "passcode_verify_success", "passcode", 
                                  "success", {"ip": ip_address})
                    
                    return PasscodeValidationResult(
                        is_valid=True,
                        user_id=user_id
                    )

                except argon2.exceptions.VerifyMismatchError:
                    # Failed verification - increment counter
                    new_failed_attempts = failed_attempts + 1
                    conn.execute("""
                        UPDATE passcodes 
                        SET failed_attempts = ?, last_used = ?
                        WHERE user_id = ?
                    """, (new_failed_attempts, datetime.now().isoformat(), user_id))

                    remaining = max(0, max_attempts - new_failed_attempts)
                    
                    self._log_audit(user_id, "passcode_verify_failed", "passcode", 
                                  "failure", {
                                      "reason": "invalid_passcode", 
                                      "attempts": new_failed_attempts,
                                      "ip": ip_address
                                  })
                    
                    return PasscodeValidationResult(
                        is_valid=False,
                        error_message=f"Invalid passcode. {remaining} attempts remaining.",
                        remaining_attempts=remaining
                    )

        except Exception as e:
            logger.error(f"Passcode verification error for user {user_id}: {e}")
            self._log_audit(user_id, "passcode_verify_error", "passcode", 
                          "failure", {"error": str(e), "ip": ip_address})
            return PasscodeValidationResult(
                is_valid=False,
                error_message="Verification failed"
            )

    def disable_passcode(self, user_id: str) -> bool:
        """
        Disable passcode for user
        
        Args:
            user_id: User identifier
            
        Returns:
            True if successful, False otherwise
        """
        try:
            with auth_db.get_connection() as conn:
                conn.execute("""
                    UPDATE passcodes 
                    SET is_active = 0
                    WHERE user_id = ?
                """, (user_id,))
                
                if conn.total_changes > 0:
                    self._log_audit(user_id, "passcode_disabled", "passcode", "success")
                    return True
                    
            return False

        except Exception as e:
            logger.error(f"Failed to disable passcode for user {user_id}: {e}")
            return False

    def get_passcode_info(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Get passcode information for user (without hash)"""
        try:
            with auth_db.get_connection() as conn:
                cursor = conn.execute("""
                    SELECT created_at, last_used, expires_at, failed_attempts, 
                           max_attempts, is_active
                    FROM passcodes 
                    WHERE user_id = ?
                """, (user_id,))
                
                row = cursor.fetchone()
                if row:
                    return {
                        "has_passcode": True,
                        "created_at": row[0],
                        "last_used": row[1],
                        "expires_at": row[2],
                        "failed_attempts": row[3],
                        "max_attempts": row[4],
                        "is_active": bool(row[5]),
                        "is_locked": row[3] >= row[4]
                    }
                    
            return {"has_passcode": False}

        except Exception as e:
            logger.error(f"Failed to get passcode info for user {user_id}: {e}")
            return None

    def reset_failed_attempts(self, user_id: str) -> bool:
        """Reset failed attempts counter (admin function)"""
        try:
            with auth_db.get_connection() as conn:
                conn.execute("""
                    UPDATE passcodes 
                    SET failed_attempts = 0
                    WHERE user_id = ?
                """, (user_id,))
                
                if conn.total_changes > 0:
                    self._log_audit(user_id, "passcode_unlocked", "passcode", "success")
                    return True
                    
            return False

        except Exception as e:
            logger.error(f"Failed to reset attempts for user {user_id}: {e}")
            return False

    def _validate_passcode(self, passcode: str, user_id: str) -> Tuple[bool, str]:
        """Validate passcode against policy"""
        # Length check
        if len(passcode) < self.policy.min_length or len(passcode) > self.policy.max_length:
            return False, f"Passcode must be {self.policy.min_length}-{self.policy.max_length} digits"

        # Numeric check
        if not re.match(r'^\d+$', passcode):
            return False, "Passcode must contain only digits"

        # Common pattern check
        if self.policy.prevent_common and passcode in self.blocked_patterns:
            return False, "Passcode is too common. Please choose a different one."

        # Uniqueness check across users
        if self.policy.require_unique:
            if self._passcode_exists(passcode, exclude_user=user_id):
                return False, "Passcode is already in use. Please choose a different one."

        # Reuse check
        if self.policy.prevent_reuse_count > 0:
            if self._passcode_recently_used(user_id, passcode):
                return False, f"Cannot reuse recent passcodes. Choose a different one."

        return True, "Valid"

    def _passcode_exists(self, passcode: str, exclude_user: str) -> bool:
        """Check if passcode is already used by another user"""
        try:
            with auth_db.get_connection() as conn:
                cursor = conn.execute("""
                    SELECT passcode_hash, salt FROM passcodes 
                    WHERE user_id != ? AND is_active = 1
                """, (exclude_user,))
                
                for row in cursor.fetchall():
                    passcode_hash, salt = row
                    try:
                        self.hasher.verify(passcode_hash, passcode + salt)
                        return True  # Found a match
                    except argon2.exceptions.VerifyMismatchError:
                        continue
                        
            return False
            
        except Exception as e:
            logger.error(f"Error checking passcode uniqueness: {e}")
            return True  # Err on side of caution

    def _passcode_recently_used(self, user_id: str, passcode: str) -> bool:
        """Check if passcode was recently used by this user"""
        if self.policy.prevent_reuse_count <= 0:
            return False
            
        try:
            with auth_db.get_connection() as conn:
                cursor = conn.execute("""
                    SELECT passcode_hash, salt FROM passcode_history 
                    WHERE user_id = ? 
                    ORDER BY created_at DESC 
                    LIMIT ?
                """, (user_id, self.policy.prevent_reuse_count))
                
                for row in cursor.fetchall():
                    passcode_hash, salt = row
                    try:
                        self.hasher.verify(passcode_hash, passcode + salt)
                        return True  # Found in recent history
                    except argon2.exceptions.VerifyMismatchError:
                        continue
                        
            return False
            
        except Exception as e:
            logger.error(f"Error checking passcode history: {e}")
            return False

    def _store_passcode_history(self, conn: sqlite3.Connection, user_id: str, passcode_hash: str):
        """Store passcode in history for reuse prevention"""
        # Create history table if it doesn't exist
        conn.execute("""
            CREATE TABLE IF NOT EXISTS passcode_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                passcode_hash TEXT NOT NULL,
                salt TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
            )
        """)
        
        # Add current passcode to history
        conn.execute("""
            INSERT INTO passcode_history (user_id, passcode_hash, salt, created_at)
            VALUES (?, ?, ?, ?)
        """, (user_id, passcode_hash, "", datetime.now().isoformat()))
        
        # Clean up old history entries
        conn.execute("""
            DELETE FROM passcode_history 
            WHERE user_id = ? AND id NOT IN (
                SELECT id FROM passcode_history 
                WHERE user_id = ? 
                ORDER BY created_at DESC 
                LIMIT ?
            )
        """, (user_id, user_id, self.policy.prevent_reuse_count))

    def _is_rate_limited(self, user_id: str, ip_address: Optional[str]) -> bool:
        """Check if user/IP is rate limited"""
        # Implementation for rate limiting checks
        # This would check against rate_limits table
        return False  # Simplified for now

    def _calculate_lockout_end(self, last_attempt: str, failed_attempts: int) -> Optional[datetime]:
        """Calculate when lockout period ends"""
        if not last_attempt:
            return None
            
        last_attempt_dt = datetime.fromisoformat(last_attempt)
        lockout_duration = timedelta(minutes=self.policy.lockout_duration_minutes)
        
        # Exponential backoff for repeated lockouts
        if failed_attempts > self.policy.max_attempts:
            multiplier = min(failed_attempts - self.policy.max_attempts + 1, 8)
            lockout_duration *= multiplier
            
        return last_attempt_dt + lockout_duration

    def _log_audit(self, user_id: Optional[str], action: str, resource: str, 
                  result: str, details: Optional[Dict[str, Any]] = None):
        """Log audit event"""
        try:
            audit_log = AuditLog(
                log_id=secrets.token_hex(16),
                user_id=user_id,
                action=action,
                resource=resource,
                result=result,
                ip_address=details.get("ip") if details else None,
                user_agent=None,
                details=details or {},
                timestamp=datetime.now()
            )
            
            with auth_db.get_connection() as conn:
                conn.execute("""
                    INSERT INTO audit_logs 
                    (log_id, user_id, action, resource, result, ip_address, details, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    audit_log.log_id, audit_log.user_id, audit_log.action,
                    audit_log.resource, audit_log.result, audit_log.ip_address,
                    str(audit_log.details), audit_log.timestamp.isoformat()
                ))
                
        except Exception as e:
            logger.error(f"Failed to log audit event: {e}")

# Global passcode manager instance
passcode_manager = PasscodeManager()

