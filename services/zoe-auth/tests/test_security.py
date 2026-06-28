"""Current-contract security utility tests."""

from __future__ import annotations

import sqlite3
import tempfile

import models.database as db_module
from core.security import AuditLogger, RateLimiter, SecurityMonitor
from core.passcode import passcode_manager


def _init_audit_table(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_logs (
            log_id TEXT PRIMARY KEY,
            user_id TEXT,
            action TEXT NOT NULL,
            resource TEXT NOT NULL,
            result TEXT NOT NULL,
            ip_address TEXT,
            user_agent TEXT,
            details TEXT,
            timestamp TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()


def test_rate_limiter_blocks_when_limit_exceeded():
    limiter = RateLimiter()
    ip = "192.168.1.100"

    for _ in range(5):
        allowed, _ = limiter.check_rate_limit("login", ip)
        assert allowed is True

    allowed, blocked_until = limiter.check_rate_limit("login", ip)
    assert allowed is False
    assert blocked_until is not None


def test_rate_limiter_clear_resets_block():
    limiter = RateLimiter()
    ip = "192.168.1.101"

    for _ in range(6):
        limiter.check_rate_limit("login", ip)

    limiter.clear_rate_limit("login", ip)
    allowed, _ = limiter.check_rate_limit("login", ip)
    assert allowed is True


def test_failure_window_blocks_by_ip():
    limiter = RateLimiter()
    ip = "203.0.113.7"
    assert limiter.is_limited("login", ip, "zoe") is False
    # "login" rule allows 5; the 5th failure trips the block.
    for _ in range(5):
        limiter.register_failed_attempt("login", ip, "zoe")
    assert limiter.is_limited("login", ip, "zoe") is True


def test_failure_window_blocks_by_username_across_ips():
    """A botnet hitting one username from many IPs still trips the user bucket."""
    limiter = RateLimiter()
    for i in range(5):
        limiter.register_failed_attempt("login", f"198.51.100.{i}", "zoe")
    # New IP, same username -> blocked via the username bucket.
    assert limiter.is_limited("login", "198.51.100.250", "zoe") is True
    # A different username from that fresh IP is unaffected.
    assert limiter.is_limited("login", "198.51.100.250", "sarah") is False


def test_failure_window_reset_clears_state():
    limiter = RateLimiter()
    for _ in range(5):
        limiter.register_failed_attempt("passcode", "203.0.113.9", "zoe")
    assert limiter.is_limited("passcode", "203.0.113.9", "zoe") is True
    limiter.reset()
    assert limiter.is_limited("passcode", "203.0.113.9", "zoe") is False


def test_passcode_manager_uses_shared_limiter():
    """PasscodeManager delegates its (previously dead) rate-limit hook."""
    from core.security import rate_limiter
    rate_limiter.reset()
    try:
        assert passcode_manager._is_rate_limited("zoe", "203.0.113.11") is False
        for _ in range(5):
            passcode_manager._register_rate_limit_failure("zoe", "203.0.113.11")
        assert passcode_manager._is_rate_limited("zoe", "203.0.113.11") is True
    finally:
        rate_limiter.reset()


def test_security_monitor_ip_classification():
    assert SecurityMonitor.check_suspicious_ip("224.0.0.1") is True
    assert SecurityMonitor.check_suspicious_ip("8.8.8.8") is False


def test_audit_logger_persists_permission_checks():
    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        db_module.auth_db.db_path = tmp.name
        _init_audit_table(tmp.name)

        AuditLogger.log_permission_check(
            user_id="user-1",
            permission="calendar.read",
            resource="calendar.event.1",
            result=True,
            session_id="session-1",
        )

        conn = sqlite3.connect(tmp.name)
        row = conn.execute(
            "SELECT action, result FROM audit_logs WHERE action = 'permission_check'"
        ).fetchone()
        conn.close()

    assert row is not None
    assert row[0] == "permission_check"
    assert row[1] == "granted"

