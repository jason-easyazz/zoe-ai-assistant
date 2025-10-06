"""
Security Features Tests
Unit tests for rate limiting, audit logging, and security monitoring
"""

import pytest
import time
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

from ..core.security import RateLimiter, SecurityMonitor, AuditLogger, SecurityEvent
from ..models.database import AuthDatabase

@pytest.fixture
def rate_limiter():
    """Create rate limiter for testing"""
    return RateLimiter()

@pytest.fixture
def security_monitor():
    """Create security monitor for testing"""
    return SecurityMonitor()

@pytest.fixture
def audit_logger():
    """Create audit logger for testing"""
    return AuditLogger()

class TestRateLimiter:
    """Test rate limiting functionality"""

    def test_rate_limit_allow_within_limit(self, rate_limiter):
        """Test that requests within limit are allowed"""
        # Should allow first few requests
        for i in range(3):
            allowed, blocked_until = rate_limiter.check_rate_limit("login", "192.168.1.100")
            assert allowed is True
            assert blocked_until is None

    def test_rate_limit_block_when_exceeded(self, rate_limiter):
        """Test blocking when rate limit is exceeded"""
        ip = "192.168.1.100"
        
        # Make requests up to the limit
        for i in range(5):
            allowed, _ = rate_limiter.check_rate_limit("login", ip)
            assert allowed is True
        
        # Next request should be blocked
        allowed, blocked_until = rate_limiter.check_rate_limit("login", ip)
        assert allowed is False
        assert blocked_until is not None
        assert blocked_until > datetime.now()

    def test_rate_limit_different_actions(self, rate_limiter):
        """Test that different actions have separate limits"""
        ip = "192.168.1.100"
        
        # Exhaust login limit
        for i in range(5):
            rate_limiter.check_rate_limit("login", ip)
        
        # Login should be blocked
        allowed, _ = rate_limiter.check_rate_limit("login", ip)
        assert allowed is False
        
        # But passcode should still be allowed
        allowed, _ = rate_limiter.check_rate_limit("passcode", ip)
        assert allowed is True

    def test_rate_limit_user_scope(self, rate_limiter):
        """Test user-scoped rate limiting"""
        # API requests are user-scoped
        user1 = "user1"
        user2 = "user2"
        ip = "192.168.1.100"
        
        # Exhaust limit for user1
        for i in range(100):
            rate_limiter.check_rate_limit("api_request", ip, user1)
        
        # user1 should be blocked
        allowed, _ = rate_limiter.check_rate_limit("api_request", ip, user1)
        assert allowed is False
        
        # user2 should still be allowed
        allowed, _ = rate_limiter.check_rate_limit("api_request", ip, user2)
        assert allowed is True

    def test_rate_limit_clear(self, rate_limiter):
        """Test clearing rate limits"""
        ip = "192.168.1.100"
        
        # Exhaust limit
        for i in range(5):
            rate_limiter.check_rate_limit("login", ip)
        
        # Should be blocked
        allowed, _ = rate_limiter.check_rate_limit("login", ip)
        assert allowed is False
        
        # Clear the limit
        rate_limiter.clear_rate_limit("login", ip)
        
        # Should be allowed again
        allowed, _ = rate_limiter.check_rate_limit("login", ip)
        assert allowed is True

    def test_rate_limit_status(self, rate_limiter):
        """Test getting rate limit status"""
        ip = "192.168.1.100"
        
        # Make some requests
        for i in range(3):
            rate_limiter.check_rate_limit("login", ip)
        
        # Check status
        status = rate_limiter.get_rate_limit_status("login", ip)
        assert status["limited"] is False
        assert status["current_attempts"] == 3
        assert status["remaining_attempts"] == 2

    def test_rate_limit_window_expiry(self, rate_limiter):
        """Test that rate limits reset after window expires"""
        ip = "192.168.1.100"
        
        # Mock time to make requests
        with patch('time.time') as mock_time:
            mock_time.return_value = 1000
            
            # Make requests up to limit
            for i in range(5):
                rate_limiter.check_rate_limit("login", ip)
            
            # Should be blocked
            allowed, _ = rate_limiter.check_rate_limit("login", ip)
            assert allowed is False
            
            # Move time forward past the window
            mock_time.return_value = 1000 + 400  # Past 300 second window
            
            # Should be allowed again
            allowed, _ = rate_limiter.check_rate_limit("login", ip)
            assert allowed is True

class TestSecurityMonitor:
    """Test security monitoring functionality"""

    def test_analyze_login_patterns_brute_force(self, security_monitor):
        """Test detection of brute force patterns"""
        with patch.object(security_monitor, '_get_recent_failed_logins') as mock_get_failed:
            # Mock high number of failed logins
            mock_get_failed.return_value = 20
            
            events = security_monitor.analyze_login_patterns("192.168.1.100", "user1", False)
            
            # Should detect potential brute force
            brute_force_events = [e for e in events if e.event_type == "potential_brute_force"]
            assert len(brute_force_events) > 0
            assert brute_force_events[0].severity == "high"

    def test_analyze_login_patterns_user_enumeration(self, security_monitor):
        """Test detection of user enumeration"""
        with patch.object(security_monitor, '_get_unique_users_from_ip') as mock_get_users:
            # Mock high number of different users from same IP
            mock_get_users.return_value = 10
            
            events = security_monitor.analyze_login_patterns("192.168.1.100", "user1", False)
            
            # Should detect user enumeration
            enum_events = [e for e in events if e.event_type == "user_enumeration"]
            assert len(enum_events) > 0
            assert enum_events[0].severity == "medium"

    def test_analyze_login_patterns_credential_stuffing(self, security_monitor):
        """Test detection of credential stuffing"""
        with patch.object(security_monitor, '_get_unique_ips_for_user') as mock_get_ips:
            # Mock high number of different IPs for same user
            mock_get_ips.return_value = 8
            
            events = security_monitor.analyze_login_patterns("192.168.1.100", "user1", False)
            
            # Should detect credential stuffing
            stuffing_events = [e for e in events if e.event_type == "credential_stuffing"]
            assert len(stuffing_events) > 0
            assert stuffing_events[0].severity == "high"

    def test_check_suspicious_ip_malicious_ranges(self, security_monitor):
        """Test detection of suspicious IP ranges"""
        # Test various suspicious IPs
        assert security_monitor.check_suspicious_ip("0.0.0.1") is True    # Broadcast range
        assert security_monitor.check_suspicious_ip("127.0.0.1") is True  # Loopback
        assert security_monitor.check_suspicious_ip("169.254.1.1") is True # Link-local
        assert security_monitor.check_suspicious_ip("224.0.0.1") is True   # Multicast
        
        # Normal IPs should not be flagged
        assert security_monitor.check_suspicious_ip("192.168.1.100") is False
        assert security_monitor.check_suspicious_ip("8.8.8.8") is False

    def test_log_security_event(self, security_monitor):
        """Test security event logging"""
        with patch('logging.Logger.error') as mock_log:
            event = SecurityEvent(
                event_type="test_event",
                severity="high",
                user_id="test_user",
                ip_address="192.168.1.100",
                details={"test": "data"},
                timestamp=datetime.now()
            )
            
            security_monitor.log_security_event(event)
            
            # Should log based on severity
            mock_log.assert_called_once()

class TestAuditLogger:
    """Test audit logging functionality"""

    def test_log_authentication_attempt_success(self, audit_logger):
        """Test logging successful authentication"""
        with patch.object(audit_logger, '_store_audit_log') as mock_store:
            audit_logger.log_authentication_attempt(
                user_id="test_user",
                method="password",
                result="success",
                ip_address="192.168.1.100",
                user_agent="TestAgent/1.0"
            )
            
            mock_store.assert_called_once()
            call_args = mock_store.call_args[0][0]  # First argument (AuditLog)
            assert call_args.action == "authentication_password"
            assert call_args.result == "success"

    def test_log_authentication_attempt_failure(self, audit_logger):
        """Test logging failed authentication"""
        with patch.object(audit_logger, '_store_audit_log') as mock_store, \
             patch.object(SecurityMonitor, 'analyze_login_patterns') as mock_analyze:
            
            # Mock security analysis
            mock_analyze.return_value = []
            
            audit_logger.log_authentication_attempt(
                user_id="test_user",
                method="password",
                result="failure",
                ip_address="192.168.1.100",
                user_agent="TestAgent/1.0"
            )
            
            mock_store.assert_called_once()
            mock_analyze.assert_called_once_with("192.168.1.100", "test_user", False)

    def test_log_permission_check(self, audit_logger):
        """Test logging permission checks"""
        with patch.object(audit_logger, '_store_audit_log') as mock_store:
            audit_logger.log_permission_check(
                user_id="test_user",
                permission="calendar.read",
                resource="calendar.event.123",
                result=True,
                session_id="session123"
            )
            
            mock_store.assert_called_once()
            call_args = mock_store.call_args[0][0]  # First argument (AuditLog)
            assert call_args.action == "permission_check"
            assert call_args.result == "granted"

    def test_log_admin_action(self, audit_logger):
        """Test logging administrative actions"""
        with patch.object(audit_logger, '_store_audit_log') as mock_store:
            audit_logger.log_admin_action(
                admin_user_id="admin_user",
                action="user_created",
                target_user_id="new_user",
                details={"username": "newuser", "role": "user"},
                ip_address="192.168.1.100"
            )
            
            mock_store.assert_called_once()
            call_args = mock_store.call_args[0][0]  # First argument (AuditLog)
            assert call_args.action == "admin_user_created"
            assert call_args.details["target_user_id"] == "new_user"

    def test_get_security_summary(self, audit_logger):
        """Test security summary generation"""
        with patch.object(audit_logger, '_query_audit_logs') as mock_query:
            # Mock database responses
            mock_query.side_effect = [
                [(10,)],  # failed_auths
                [(50,)],  # successful_auths
                [(2,)],   # security_events
                [("192.168.1.100", 5), ("192.168.1.101", 3)],  # top_failed_ips
                [(1,)]    # permission_denials
            ]
            
            summary = audit_logger.get_security_summary(24)
            
            assert summary["failed_authentications"] == 10
            assert summary["successful_authentications"] == 50
            assert summary["security_events"] == 2
            assert len(summary["top_failed_ips"]) == 2
            assert summary["permission_denials"] == 1

class TestSecurityIntegration:
    """Test integration between security components"""

    def test_rate_limiter_triggers_security_events(self, rate_limiter):
        """Test that rate limiting triggers security events"""
        with patch.object(SecurityMonitor, 'log_security_event') as mock_log:
            ip = "192.168.1.100"
            
            # Exceed rate limit
            for i in range(6):  # Login limit is 5
                rate_limiter.check_rate_limit("login", ip, "test_user")
            
            # Should have logged security event
            mock_log.assert_called()
            event = mock_log.call_args[0][0]
            assert event.event_type == "rate_limit_exceeded"

    def test_security_monitor_integration_with_audit(self):
        """Test that security monitoring integrates with audit logging"""
        with patch('..models.database.auth_db.get_connection') as mock_conn:
            # Mock database connection and queries
            mock_cursor = MagicMock()
            mock_conn.return_value.__enter__.return_value.execute.return_value = mock_cursor
            mock_cursor.fetchone.return_value = (20,)  # High failed login count
            
            events = SecurityMonitor.analyze_login_patterns("192.168.1.100", "user1", False)
            
            # Should detect suspicious activity
            assert len(events) > 0
            assert any(e.event_type == "potential_brute_force" for e in events)

    def test_comprehensive_security_workflow(self):
        """Test complete security workflow from detection to logging"""
        with patch.multiple(
            SecurityMonitor,
            analyze_login_patterns=MagicMock(return_value=[
                SecurityEvent(
                    event_type="potential_brute_force",
                    severity="high",
                    user_id="user1",
                    ip_address="192.168.1.100",
                    details={"attempts": 20},
                    timestamp=datetime.now()
                )
            ]),
            log_security_event=MagicMock()
        ):
            # Simulate authentication attempt that triggers monitoring
            AuditLogger.log_authentication_attempt(
                user_id="user1",
                method="password",
                result="failure",
                ip_address="192.168.1.100",
                user_agent="BadBot/1.0"
            )
            
            # Verify security analysis was called
            SecurityMonitor.analyze_login_patterns.assert_called_once()
            
            # Verify security event was logged
            SecurityMonitor.log_security_event.assert_called_once()

if __name__ == "__main__":
    pytest.main([__file__])

