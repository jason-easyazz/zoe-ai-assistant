"""
Security Features and Controls
Rate limiting, audit logging, and advanced security measures
"""

import time
import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass
from collections import defaultdict
import threading
import logging
import ipaddress

from models.database import auth_db, AuditLog

logger = logging.getLogger(__name__)

@dataclass
class RateLimitRule:
    """Rate limiting rule configuration"""
    action: str
    max_attempts: int
    window_seconds: int
    block_duration_seconds: int
    scope: str = "ip"  # "ip", "user", "global"


@dataclass
class ThrottleRule:
    """Configuration for the auth brute-force throttle (progressive backoff).

    The throttle is deliberately designed so it can NEVER refuse a correct
    credential coming from an IP that isn't itself hammering:

    * ``free_attempts`` recent failures from an IP are free (no delay) — generous
      so NAT/proxy households sharing one public IP are not punished for a few
      fat-fingered passwords.
    * Beyond that, each further failure from the SAME IP adds an exponentially
      growing (but capped) delay. A valid login is only *slowed*, never denied,
      and a clean IP is never delayed.
    * ``hard_block`` is a volumetric backstop scoped to the (IP, username) PAIR,
      not the username alone and not the whole IP. So a flood against one account
      from one IP is eventually blocked without letting an attacker lock a victim
      out from other IPs, and without locking every account behind a shared IP.
    """
    action: str
    free_attempts: int
    window_seconds: int
    base_delay_seconds: float
    max_delay_seconds: float
    hard_block_attempts: int
    hard_block_seconds: int

@dataclass
class SecurityEvent:
    """Security event for monitoring"""
    event_type: str
    severity: str  # "low", "medium", "high", "critical"
    user_id: Optional[str]
    ip_address: Optional[str]
    details: Dict[str, Any]
    timestamp: datetime

class RateLimiter:
    """Advanced rate limiting system"""
    
    def __init__(self):
        self.memory_store: Dict[str, List[float]] = defaultdict(list)
        self.blocked_until: Dict[str, float] = {}
        self.lock = threading.RLock()

        # Default rate limit rules (legacy per-call gate; see check_rate_limit).
        self.rules = {
            "login": RateLimitRule("login", 5, 300, 900, "ip"),  # 5 attempts per 5min, block 15min
            "passcode": RateLimitRule("passcode", 3, 180, 600, "ip"),  # 3 attempts per 3min, block 10min
            "password_reset": RateLimitRule("password_reset", 3, 3600, 3600, "ip"),  # 3 per hour
            "api_request": RateLimitRule("api_request", 100, 60, 300, "user"),  # 100 per minute
            "user_creation": RateLimitRule("user_creation", 10, 3600, 7200, "ip"),  # 10 per hour
        }

        # Auth brute-force throttle (failure-only, progressive backoff). State is
        # kept separate from the legacy gate above so the two never interfere.
        self._throttle_events: Dict[str, List[float]] = defaultdict(list)
        self._throttle_blocks: Dict[str, float] = {}
        self._throttle_writes = 0
        self.throttle_rules = {
            # Generous free allowance + capped backoff so shared NAT/proxy IPs are
            # never hard-denied; hard block is (IP,username)-pair scoped only.
            "login": ThrottleRule("login", free_attempts=8, window_seconds=900,
                                  base_delay_seconds=0.5, max_delay_seconds=8.0,
                                  hard_block_attempts=50, hard_block_seconds=900),
            "passcode": ThrottleRule("passcode", free_attempts=8, window_seconds=600,
                                     base_delay_seconds=0.5, max_delay_seconds=8.0,
                                     hard_block_attempts=50, hard_block_seconds=600),
            "password_setup": ThrottleRule("password_setup", free_attempts=8, window_seconds=900,
                                           base_delay_seconds=0.5, max_delay_seconds=8.0,
                                           hard_block_attempts=30, hard_block_seconds=1800),
        }

    # ------------------------------------------------------------------
    # Auth brute-force throttle (IP-progressive-delay + pair hard-block)
    #
    # Design contract — these properties are load-bearing and tested:
    #   * A correct credential from an IP that isn't itself hammering is NEVER
    #     delayed and NEVER denied (no username-global or IP-global hard deny).
    #   * Repeated failures from one IP add growing, capped delay (brute force is
    #     slowed) but a valid login from that IP still eventually succeeds.
    #   * Only a focused flood against a single (IP, username) PAIR is hard
    #     blocked, so an attacker cannot lock a victim out from other IPs and a
    #     shared IP is not globally locked.
    # Failures are counted; successful sign-ins never call register_*.
    # ------------------------------------------------------------------

    @staticmethod
    def _ip_key(action: str, ip_address: Optional[str]) -> Optional[str]:
        return f"{action}:ip:{ip_address}" if ip_address else None

    @staticmethod
    def _pair_key(action: str, ip_address: Optional[str], user_id: Optional[str]) -> Optional[str]:
        if not user_id:
            return None
        return f"{action}:pair:{ip_address or '-'}|{user_id}"

    def _recent(self, key: Optional[str], window_start: float) -> int:
        """Count (and prune in place) failures still inside the window for ``key``."""
        if not key:
            return 0
        events = self._throttle_events.get(key)
        if not events:
            return 0
        events[:] = [t for t in events if t > window_start]
        if not events:
            self._throttle_events.pop(key, None)
            return 0
        return len(events)

    def _sweep_locked(self, now: float) -> None:
        """Drop fully-expired buckets/blocks so memory stays bounded."""
        for action, rule in self.throttle_rules.items():
            window_start = now - rule.window_seconds
            prefix = f"{action}:"
            for key in [k for k in self._throttle_events if k.startswith(prefix)]:
                events = self._throttle_events[key]
                events[:] = [t for t in events if t > window_start]
                if not events:
                    self._throttle_events.pop(key, None)
        for key in [k for k, until in self._throttle_blocks.items() if until <= now]:
            self._throttle_blocks.pop(key, None)

    def delay_for(self, action: str, ip_address: Optional[str] = None,
                  user_id: Optional[str] = None) -> float:
        """Return the progressive-backoff delay (seconds) for this IP's next try.

        0.0 means "not throttled" — a clean IP always gets 0. The delay grows
        with the number of *recent failures from this IP* beyond the free
        allowance, capped at ``max_delay_seconds``. It only slows; it never denies.
        """
        rule = self.throttle_rules.get(action)
        if not rule or not ip_address:
            return 0.0
        with self.lock:
            now = time.time()
            window_start = now - rule.window_seconds
            ip_recent = self._recent(self._ip_key(action, ip_address), window_start)
            over = ip_recent - rule.free_attempts
            if over <= 0:
                return 0.0
            return min(rule.max_delay_seconds, rule.base_delay_seconds * (2 ** (over - 1)))

    def is_hard_blocked(self, action: str, ip_address: Optional[str] = None,
                        user_id: Optional[str] = None) -> bool:
        """True only if this (IP, username) pair is inside its volumetric block.

        Scoped to the pair, never the username alone or the IP alone, so it can
        neither lock a victim out from a different IP nor lock a whole NAT.
        """
        rule = self.throttle_rules.get(action)
        if not rule:
            return False
        pair_key = self._pair_key(action, ip_address, user_id)
        if not pair_key:
            return False
        with self.lock:
            now = time.time()
            until = self._throttle_blocks.get(pair_key)
            if until is not None:
                if now < until:
                    return True
                self._throttle_blocks.pop(pair_key, None)
            # Re-derive from the live window in case the block was cleared.
            window_start = now - rule.window_seconds
            if self._recent(pair_key, window_start) >= rule.hard_block_attempts:
                self._throttle_blocks[pair_key] = now + rule.hard_block_seconds
                return True
            return False

    def is_limited(self, action: str, ip_address: Optional[str] = None,
                   user_id: Optional[str] = None) -> bool:
        """Hard-deny gate for sync callers — pair-scoped volumetric block only.

        This intentionally does NOT reflect the progressive delay (that is applied
        by the async callers via :meth:`delay_for`), and never denies based on
        username-global or IP-global counts.
        """
        return self.is_hard_blocked(action, ip_address, user_id)

    def register_failed_attempt(self, action: str, ip_address: Optional[str] = None,
                                user_id: Optional[str] = None) -> None:
        """Record one failed attempt for the throttle (IP + pair buckets).

        Call only on genuine credential/authorization failures, never on success.
        Trips the pair hard block when a single (IP, username) pair floods.
        """
        rule = self.throttle_rules.get(action)
        if not rule:
            return
        with self.lock:
            now = time.time()
            window_start = now - rule.window_seconds
            for key in (self._ip_key(action, ip_address),
                        self._pair_key(action, ip_address, user_id)):
                if not key:
                    continue
                events = self._throttle_events[key]
                events[:] = [t for t in events if t > window_start]
                events.append(now)

            pair_key = self._pair_key(action, ip_address, user_id)
            if pair_key and len(self._throttle_events.get(pair_key, [])) >= rule.hard_block_attempts:
                self._throttle_blocks[pair_key] = now + rule.hard_block_seconds
                SecurityMonitor.log_security_event(SecurityEvent(
                    event_type="rate_limit_exceeded",
                    severity="medium",
                    user_id=user_id,
                    ip_address=ip_address,
                    details={
                        "action": action,
                        "scope": "ip+username pair",
                        "attempts": len(self._throttle_events[pair_key]),
                        "limit": rule.hard_block_attempts,
                        "window_seconds": rule.window_seconds,
                    },
                    timestamp=datetime.now(),
                ))

            # Opportunistic memory sweep so unbounded distinct IPs don't pile up.
            self._throttle_writes += 1
            if self._throttle_writes >= 256 or len(self._throttle_events) > 1024:
                self._throttle_writes = 0
                self._sweep_locked(now)

    def reset(self) -> None:
        """Clear all throttle state. Intended for tests and admin recovery."""
        with self.lock:
            self._throttle_events.clear()
            self._throttle_blocks.clear()
            self._throttle_writes = 0

    def reset_for(self, action: Optional[str] = None, ip_address: Optional[str] = None,
                  user_id: Optional[str] = None) -> int:
        """Clear throttle buckets/blocks matching the given filters (admin recovery).

        Any combination of action / ip / user may be given; an entry is cleared
        when it matches every filter that was provided. Returns how many buckets
        and blocks were removed. With no filters this clears everything.
        """
        def _matches(key: str) -> bool:
            # key forms: "{action}:ip:{ip}" or "{action}:pair:{ip}|{user}"
            try:
                k_action, kind, rest = key.split(":", 2)
            except ValueError:
                return False
            if action is not None and k_action != action:
                return False
            if kind == "ip":
                k_ip, k_user = rest, None
            else:  # pair
                k_ip, _, k_user = rest.partition("|")
            if ip_address is not None and k_ip != ip_address:
                return False
            if user_id is not None and k_user != user_id:
                return False
            return True

        with self.lock:
            removed = 0
            for key in [k for k in self._throttle_events if _matches(k)]:
                self._throttle_events.pop(key, None)
                removed += 1
            for key in [k for k in self._throttle_blocks if _matches(k)]:
                self._throttle_blocks.pop(key, None)
                removed += 1
            return removed

    def check_rate_limit(self, action: str, identifier: str,
                        user_id: Optional[str] = None) -> Tuple[bool, Optional[datetime]]:
        """
        Check if action is rate limited
        
        Args:
            action: Action being performed
            identifier: IP address or user ID
            user_id: User ID for user-scoped limits
            
        Returns:
            Tuple of (is_allowed, blocked_until)
        """
        rule = self.rules.get(action)
        if not rule:
            return True, None

        # Determine the key based on scope
        if rule.scope == "user" and user_id:
            key = f"{action}:user:{user_id}"
        elif rule.scope == "global":
            key = f"{action}:global"
        else:  # IP scope
            key = f"{action}:ip:{identifier}"

        with self.lock:
            now = time.time()
            
            # Check if currently blocked
            if key in self.blocked_until:
                if now < self.blocked_until[key]:
                    blocked_until = datetime.fromtimestamp(self.blocked_until[key])
                    return False, blocked_until
                else:
                    del self.blocked_until[key]

            # Clean old attempts
            attempts = self.memory_store[key]
            window_start = now - rule.window_seconds
            attempts[:] = [t for t in attempts if t > window_start]

            # Check if limit exceeded
            if len(attempts) >= rule.max_attempts:
                # Block the identifier
                self.blocked_until[key] = now + rule.block_duration_seconds
                blocked_until = datetime.fromtimestamp(self.blocked_until[key])
                
                # Log security event
                SecurityMonitor.log_security_event(SecurityEvent(
                    event_type="rate_limit_exceeded",
                    severity="medium",
                    user_id=user_id,
                    ip_address=identifier if rule.scope == "ip" else None,
                    details={
                        "action": action,
                        "attempts": len(attempts) + 1,
                        "limit": rule.max_attempts,
                        "window_seconds": rule.window_seconds
                    },
                    timestamp=datetime.now()
                ))
                
                return False, blocked_until

            # Record this attempt
            attempts.append(now)
            return True, None

    def clear_rate_limit(self, action: str, identifier: str, user_id: Optional[str] = None):
        """Clear rate limit for identifier (admin function)"""
        rule = self.rules.get(action)
        if not rule:
            return

        if rule.scope == "user" and user_id:
            key = f"{action}:user:{user_id}"
        elif rule.scope == "global":
            key = f"{action}:global"
        else:
            key = f"{action}:ip:{identifier}"

        with self.lock:
            self.memory_store.pop(key, None)
            self.blocked_until.pop(key, None)

    def get_rate_limit_status(self, action: str, identifier: str, 
                            user_id: Optional[str] = None) -> Dict[str, Any]:
        """Get current rate limit status"""
        rule = self.rules.get(action)
        if not rule:
            return {"limited": False}

        if rule.scope == "user" and user_id:
            key = f"{action}:user:{user_id}"
        elif rule.scope == "global":
            key = f"{action}:global"
        else:
            key = f"{action}:ip:{identifier}"

        with self.lock:
            now = time.time()
            
            # Check if blocked
            if key in self.blocked_until and now < self.blocked_until[key]:
                return {
                    "limited": True,
                    "blocked_until": datetime.fromtimestamp(self.blocked_until[key]),
                    "reason": "rate_limit_exceeded"
                }

            # Get current attempt count
            attempts = self.memory_store.get(key, [])
            window_start = now - rule.window_seconds
            current_attempts = len([t for t in attempts if t > window_start])

            return {
                "limited": False,
                "current_attempts": current_attempts,
                "max_attempts": rule.max_attempts,
                "window_seconds": rule.window_seconds,
                "remaining_attempts": max(0, rule.max_attempts - current_attempts)
            }

class SecurityMonitor:
    """Security monitoring and threat detection"""
    
    suspicious_patterns = {
        "brute_force": {
            "multiple_users_same_ip": {"threshold": 10, "window": 600},
            "rapid_failed_logins": {"threshold": 20, "window": 300},
        },
        "credential_stuffing": {
            "many_ips_same_user": {"threshold": 5, "window": 300},
        },
        "enumeration": {
            "user_not_found_rate": {"threshold": 50, "window": 600},
        }
    }

    @staticmethod
    def analyze_login_patterns(ip_address: str, user_id: Optional[str], 
                             success: bool) -> List[SecurityEvent]:
        """Analyze login patterns for suspicious activity"""
        events = []
        now = datetime.now()
        
        try:
            with auth_db.get_connection() as conn:
                # Check for multiple failed logins from same IP
                cursor = conn.execute("""
                    SELECT COUNT(*) FROM audit_logs 
                    WHERE ip_address = ? AND action LIKE '%auth%' 
                    AND result = 'failure' AND timestamp > ?
                """, (ip_address, (now - timedelta(minutes=10)).isoformat()))
                
                failed_count = cursor.fetchone()[0]
                if failed_count > 15:
                    events.append(SecurityEvent(
                        event_type="potential_brute_force",
                        severity="high",
                        user_id=user_id,
                        ip_address=ip_address,
                        details={"failed_attempts": failed_count, "timeframe": "10_minutes"},
                        timestamp=now
                    ))

                # Check for login attempts across multiple users from same IP
                cursor = conn.execute("""
                    SELECT COUNT(DISTINCT user_id) FROM audit_logs 
                    WHERE ip_address = ? AND action LIKE '%auth%' 
                    AND timestamp > ?
                """, (ip_address, (now - timedelta(minutes=10)).isoformat()))
                
                user_count = cursor.fetchone()[0]
                if user_count > 8:
                    events.append(SecurityEvent(
                        event_type="user_enumeration",
                        severity="medium",
                        user_id=user_id,
                        ip_address=ip_address,
                        details={"unique_users": user_count, "timeframe": "10_minutes"},
                        timestamp=now
                    ))

                # Check for credential stuffing (same user, multiple IPs)
                if user_id:
                    cursor = conn.execute("""
                        SELECT COUNT(DISTINCT ip_address) FROM audit_logs 
                        WHERE user_id = ? AND action LIKE '%auth%' 
                        AND result = 'failure' AND timestamp > ?
                    """, (user_id, (now - timedelta(minutes=5)).isoformat()))
                    
                    ip_count = cursor.fetchone()[0]
                    if ip_count > 5:
                        events.append(SecurityEvent(
                            event_type="credential_stuffing",
                            severity="high",
                            user_id=user_id,
                            ip_address=ip_address,
                            details={"unique_ips": ip_count, "timeframe": "5_minutes"},
                            timestamp=now
                        ))

        except Exception as e:
            logger.error(f"Error analyzing login patterns: {e}")

        return events

    @staticmethod
    def check_suspicious_ip(ip_address: str) -> bool:
        """Check if IP address is suspicious"""
        try:
            ip = ipaddress.ip_address(ip_address)
            
            # Check for common malicious IP ranges
            malicious_ranges = [
                ipaddress.ip_network("0.0.0.0/8"),      # Broadcast
                ipaddress.ip_network("127.0.0.0/8"),    # Loopback
                ipaddress.ip_network("169.254.0.0/16"), # Link-local
                ipaddress.ip_network("224.0.0.0/4"),    # Multicast
            ]
            
            for malicious_range in malicious_ranges:
                if ip in malicious_range and not ip.is_private:
                    return True

            # Additional checks could include:
            # - Known bot/scanner IP lists
            # - Tor exit nodes
            # - VPN/proxy detection
            # - Geolocation anomalies

            return False

        except Exception:
            return False

    @staticmethod
    def log_security_event(event: SecurityEvent):
        """Log security event to database and monitoring systems"""
        try:
            # Store in audit logs with security event marker
            audit_log = AuditLog(
                log_id=f"security_{secrets.token_hex(8)}",
                user_id=event.user_id,
                action=f"security_{event.event_type}",
                resource="security",
                result=event.severity,
                ip_address=event.ip_address,
                user_agent=None,
                details=event.details,
                timestamp=event.timestamp
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

            # Log to application logs based on severity
            if event.severity == "critical":
                logger.critical(f"Security Event: {event.event_type} - {event.details}")
            elif event.severity == "high":
                logger.error(f"Security Event: {event.event_type} - {event.details}")
            elif event.severity == "medium":
                logger.warning(f"Security Event: {event.event_type} - {event.details}")
            else:
                logger.info(f"Security Event: {event.event_type} - {event.details}")

            # Could also send to external monitoring systems here
            # - SIEM systems
            # - Slack/Discord notifications  
            # - Email alerts for critical events

        except Exception as e:
            logger.error(f"Failed to log security event: {e}")

class AuditLogger:
    """Enhanced audit logging system"""

    @staticmethod
    def log_authentication_attempt(user_id: Optional[str], method: str, result: str,
                                 ip_address: Optional[str], user_agent: Optional[str],
                                 details: Optional[Dict[str, Any]] = None):
        """Log authentication attempt with enhanced details"""
        try:
            # Analyze for suspicious patterns
            if ip_address:
                security_events = SecurityMonitor.analyze_login_patterns(
                    ip_address, user_id, result == "success"
                )
                
                for event in security_events:
                    SecurityMonitor.log_security_event(event)

            # Standard audit log
            audit_log = AuditLog(
                log_id=f"auth_{secrets.token_hex(8)}",
                user_id=user_id,
                action=f"authentication_{method}",
                resource="authentication",
                result=result,
                ip_address=ip_address,
                user_agent=user_agent,
                details=details or {},
                timestamp=datetime.now()
            )

            with auth_db.get_connection() as conn:
                conn.execute("""
                    INSERT INTO audit_logs 
                    (log_id, user_id, action, resource, result, ip_address, user_agent, details, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    audit_log.log_id, audit_log.user_id, audit_log.action,
                    audit_log.resource, audit_log.result, audit_log.ip_address,
                    audit_log.user_agent, str(audit_log.details), 
                    audit_log.timestamp.isoformat()
                ))

        except Exception as e:
            logger.error(f"Failed to log authentication attempt: {e}")

    @staticmethod
    def log_permission_check(user_id: str, permission: str, resource: Optional[str],
                           result: bool, session_id: str):
        """Log permission check for audit trail"""
        try:
            audit_log = AuditLog(
                log_id=f"perm_{secrets.token_hex(8)}",
                user_id=user_id,
                action="permission_check",
                resource=resource or permission,
                result="granted" if result else "denied",
                ip_address=None,
                user_agent=None,
                details={
                    "permission": permission,
                    "session_id": session_id,
                    "resource": resource
                },
                timestamp=datetime.now()
            )

            with auth_db.get_connection() as conn:
                conn.execute("""
                    INSERT INTO audit_logs 
                    (log_id, user_id, action, resource, result, details, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    audit_log.log_id, audit_log.user_id, audit_log.action,
                    audit_log.resource, audit_log.result, str(audit_log.details),
                    audit_log.timestamp.isoformat()
                ))

        except Exception as e:
            logger.error(f"Failed to log permission check: {e}")

    @staticmethod
    def log_admin_action(admin_user_id: str, action: str, target_user_id: Optional[str],
                        details: Dict[str, Any], ip_address: Optional[str]):
        """Log administrative actions"""
        try:
            audit_log = AuditLog(
                log_id=f"admin_{secrets.token_hex(8)}",
                user_id=admin_user_id,
                action=f"admin_{action}",
                resource="user_management",
                result="success",
                ip_address=ip_address,
                user_agent=None,
                details={
                    **details,
                    "target_user_id": target_user_id,
                    "admin_user_id": admin_user_id
                },
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
            logger.error(f"Failed to log admin action: {e}")

    @staticmethod
    def get_security_summary(hours: int = 24) -> Dict[str, Any]:
        """Get security summary for the last N hours"""
        try:
            since = datetime.now() - timedelta(hours=hours)
            
            with auth_db.get_connection() as conn:
                # Failed authentication attempts
                cursor = conn.execute("""
                    SELECT COUNT(*) FROM audit_logs 
                    WHERE action LIKE '%auth%' AND result = 'failure' 
                    AND timestamp > ?
                """, (since.isoformat(),))
                failed_auths = cursor.fetchone()[0]

                # Successful authentications
                cursor = conn.execute("""
                    SELECT COUNT(*) FROM audit_logs 
                    WHERE action LIKE '%auth%' AND result = 'success' 
                    AND timestamp > ?
                """, (since.isoformat(),))
                successful_auths = cursor.fetchone()[0]

                # Security events
                cursor = conn.execute("""
                    SELECT COUNT(*) FROM audit_logs 
                    WHERE action LIKE 'security_%' 
                    AND timestamp > ?
                """, (since.isoformat(),))
                security_events = cursor.fetchone()[0]

                # Top IPs by failed attempts
                cursor = conn.execute("""
                    SELECT ip_address, COUNT(*) as count FROM audit_logs 
                    WHERE action LIKE '%auth%' AND result = 'failure' 
                    AND timestamp > ? AND ip_address IS NOT NULL
                    GROUP BY ip_address 
                    ORDER BY count DESC 
                    LIMIT 10
                """, (since.isoformat(),))
                top_failed_ips = [{"ip": row[0], "attempts": row[1]} for row in cursor.fetchall()]

                # Permission denials
                cursor = conn.execute("""
                    SELECT COUNT(*) FROM audit_logs 
                    WHERE action = 'permission_check' AND result = 'denied' 
                    AND timestamp > ?
                """, (since.isoformat(),))
                permission_denials = cursor.fetchone()[0]

                return {
                    "timeframe_hours": hours,
                    "failed_authentications": failed_auths,
                    "successful_authentications": successful_auths,
                    "security_events": security_events,
                    "permission_denials": permission_denials,
                    "top_failed_ips": top_failed_ips,
                    "generated_at": datetime.now().isoformat()
                }

        except Exception as e:
            logger.error(f"Failed to generate security summary: {e}")
            return {"error": str(e)}

# Global instances
rate_limiter = RateLimiter()
security_monitor = SecurityMonitor()
audit_logger = AuditLogger()

