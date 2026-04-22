"""Current-contract security utility tests."""

from __future__ import annotations

import sqlite3
import tempfile

import models.database as db_module
from core.security import AuditLogger, RateLimiter, SecurityMonitor


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

