"""Current-contract security utility tests."""

from __future__ import annotations

import sqlite3
import tempfile

import models.database as db_module
from sqlite_compat import SQLiteCompatConnection
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


def _hard_block_threshold(action="login"):
    return RateLimiter().throttle_rules[action].hard_block_attempts


def _free_attempts(action="login"):
    return RateLimiter().throttle_rules[action].free_attempts


def test_victim_username_not_lockable_from_other_ips():
    """(a) Failures against a username from OTHER IPs must NOT throttle a clean IP.

    This is the property the previous (buggy) username-global bucket violated.
    """
    limiter = RateLimiter()
    # Hammer 'zoe' hard from many different attacker IPs.
    for i in range(200):
        limiter.register_failed_attempt("login", f"198.51.100.{i % 50}", "zoe")
    # The real zoe arrives from her own clean IP: never delayed, never blocked.
    assert limiter.delay_for("login", "203.0.113.5", "zoe") == 0.0
    assert limiter.is_hard_blocked("login", "203.0.113.5", "zoe") is False
    assert limiter.is_limited("login", "203.0.113.5", "zoe") is False


def test_shared_ip_household_not_globally_locked_by_one_bad_actor():
    """(b) One bad actor on a shared IP must not lock the whole household out."""
    limiter = RateLimiter()
    shared_ip = "203.0.113.9"
    # A bad actor on the household IP fails a bunch trying 'attacker-target'.
    for _ in range(20):
        limiter.register_failed_attempt("login", shared_ip, "attacker-target")
    # A different family member on the same IP is not hard-blocked...
    assert limiter.is_hard_blocked("login", shared_ip, "jason") is False
    assert limiter.is_limited("login", shared_ip, "jason") is False
    # ...the IP is merely slowed (bounded delay), never denied.
    assert limiter.delay_for("login", shared_ip, "jason") <= limiter.throttle_rules["login"].max_delay_seconds


def test_single_hammering_ip_is_throttled_progressively():
    """(d) A single brute-forcing IP IS slowed: delay grows with failures."""
    limiter = RateLimiter()
    ip = "198.51.100.200"
    free = _free_attempts()
    # Under the free allowance: no delay.
    for _ in range(free):
        limiter.register_failed_attempt("login", ip, "zoe")
    assert limiter.delay_for("login", ip, "zoe") == 0.0
    # Beyond it: delay appears and increases.
    limiter.register_failed_attempt("login", ip, "zoe")
    d1 = limiter.delay_for("login", ip, "zoe")
    limiter.register_failed_attempt("login", ip, "zoe")
    d2 = limiter.delay_for("login", ip, "zoe")
    assert d1 > 0.0
    assert d2 > d1


def test_pair_hard_block_only_affects_that_ip_username_pair():
    """A focused flood on one (IP, user) pair blocks ONLY that pair."""
    limiter = RateLimiter()
    attacker_ip = "198.51.100.7"
    threshold = _hard_block_threshold()
    for _ in range(threshold):
        limiter.register_failed_attempt("login", attacker_ip, "zoe")
    # That exact pair is now hard-blocked...
    assert limiter.is_hard_blocked("login", attacker_ip, "zoe") is True
    # ...but the victim from any other IP is unaffected (no victim lockout).
    assert limiter.is_hard_blocked("login", "203.0.113.50", "zoe") is False


def test_reset_for_user_clears_that_users_pair_block():
    """(e) Admin recovery: reset_for(user_id) lifts that user's pair hard-block."""
    limiter = RateLimiter()
    threshold = _hard_block_threshold()
    for _ in range(threshold):
        limiter.register_failed_attempt("login", "198.51.100.8", "zoe")
    assert limiter.is_hard_blocked("login", "198.51.100.8", "zoe") is True
    cleared = limiter.reset_for(user_id="zoe")
    assert cleared >= 1
    assert limiter.is_hard_blocked("login", "198.51.100.8", "zoe") is False


def test_reset_for_ip_scope_is_selective():
    limiter = RateLimiter()
    for _ in range(20):
        limiter.register_failed_attempt("login", "10.0.0.1", "zoe")
        limiter.register_failed_attempt("login", "10.0.0.2", "zoe")
    limiter.reset_for(ip_address="10.0.0.1")
    assert limiter.delay_for("login", "10.0.0.1", "zoe") == 0.0
    # The other IP's accumulated failures are untouched.
    assert limiter.delay_for("login", "10.0.0.2", "zoe") > 0.0


def test_passcode_manager_uses_shared_limiter():
    """PasscodeManager delegates its (previously dead) rate-limit hook."""
    from core.security import rate_limiter
    rate_limiter.reset()
    try:
        threshold = rate_limiter.throttle_rules["passcode"].hard_block_attempts
        assert passcode_manager._is_rate_limited("zoe", "203.0.113.11") is False
        for _ in range(threshold):
            passcode_manager._register_rate_limit_failure("zoe", "203.0.113.11")
        # The focused (IP, user) flood is blocked for that pair...
        assert passcode_manager._is_rate_limited("zoe", "203.0.113.11") is True
        # ...but the same user from a clean IP is not.
        assert passcode_manager._is_rate_limited("zoe", "203.0.113.99") is False
    finally:
        rate_limiter.reset()


def test_security_monitor_ip_classification():
    assert SecurityMonitor.check_suspicious_ip("224.0.0.1") is True
    assert SecurityMonitor.check_suspicious_ip("8.8.8.8") is False


def test_audit_logger_persists_permission_checks(monkeypatch):
    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        # auth_db is Postgres-backed now; route it onto sqlite the same way
        # test_auth.py / test_smoke.py do (the old `db_path` attribute is gone).
        monkeypatch.setattr(
            db_module.auth_db,
            "get_connection",
            lambda: SQLiteCompatConnection(tmp.name),
        )
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

