"""Current-contract API tests for zoe-auth."""

from __future__ import annotations

import sqlite3
import tempfile
import types
from datetime import datetime, timedelta

from fastapi.testclient import TestClient
import pytest
import bcrypt

from sqlite_compat import SQLiteCompatConnection
import models.database as db_module
from core.auth import AuthManager
from core.security import rate_limiter
from core.sessions import session_manager, SessionType
from core.account_setup import setup_token_manager
from main import app


@pytest.fixture(autouse=True)
def _reset_security_state():
    """Rate-limit + setup-token state is process-global; isolate every test."""
    rate_limiter.reset()
    setup_token_manager.reset(bootstrap_token="test-bootstrap-token")
    yield
    rate_limiter.reset()
    setup_token_manager.reset(bootstrap_token="test-bootstrap-token")


def _seed_user(db_path: str, *, user_id="zoe", username="zoe", password=None,
               password_hash=None, role="user"):
    if password is not None:
        password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    now = datetime.now().isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT INTO auth_users(
            user_id, username, email, role, password_hash, created_at, updated_at,
            is_active, is_verified, failed_login_attempts
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, 1, 0)
        """,
        (user_id, username, f"{username}@example.com", role, password_hash, now, now),
    )
    conn.commit()
    conn.close()


def _password_hash_of(db_path: str, user_id="zoe"):
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT password_hash FROM auth_users WHERE user_id = ?", (user_id,)
    ).fetchone()
    conn.close()
    return row[0] if row else None


def _init_auth_tables(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS auth_users (
            user_id TEXT PRIMARY KEY,
            username TEXT NOT NULL,
            email TEXT,
            role TEXT NOT NULL,
            password_hash TEXT,
            created_at TEXT,
            updated_at TEXT,
            last_login TEXT,
            is_active INTEGER DEFAULT 1,
            is_verified INTEGER DEFAULT 1,
            failed_login_attempts INTEGER DEFAULT 0,
            locked_until TEXT,
            settings TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS panels (
            panel_id TEXT PRIMARY KEY,
            name TEXT,
            allow_guest INTEGER DEFAULT 1
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS panel_user_bindings (
            panel_id TEXT,
            user_id TEXT,
            binding_type TEXT
        )
        """
    )
    conn.commit()
    conn.close()


@pytest.fixture
def sqlite_auth_db(monkeypatch):
    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        _init_auth_tables(tmp.name)
        monkeypatch.setattr(
            db_module.auth_db,
            "get_connection",
            lambda: SQLiteCompatConnection(tmp.name),
        )
        yield tmp.name


def test_health_endpoint_reports_healthy_for_reachable_db(sqlite_auth_db):
    client = TestClient(app)
    response = client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "healthy"
    assert payload["database"] == "postgresql"


def test_profiles_endpoint_returns_non_system_users(sqlite_auth_db):
    conn = sqlite3.connect(sqlite_auth_db)
    conn.execute(
        "INSERT INTO auth_users(user_id, username, role, password_hash) VALUES (?, ?, ?, ?)",
        ("jason", "jason", "admin", "hash"),
    )
    conn.execute(
        "INSERT INTO auth_users(user_id, username, role, password_hash) VALUES (?, ?, ?, ?)",
        ("system", "system", "system", "hash"),
    )
    conn.commit()
    conn.close()

    client = TestClient(app)
    response = client.get("/api/auth/profiles")

    assert response.status_code == 200
    profiles = response.json()
    assert isinstance(profiles, list)
    assert len(profiles) == 1
    assert profiles[0]["user_id"] == "jason"
    assert profiles[0]["avatar"] == "J"


def test_profiles_endpoint_returns_panel_scoped_identity_picker_shape(sqlite_auth_db):
    conn = sqlite3.connect(sqlite_auth_db)
    conn.executemany(
        "INSERT INTO auth_users(user_id, username, role, password_hash) VALUES (?, ?, ?, ?)",
        [
            ("jason", "Jason", "admin", "hash"),
            ("sarah", "Sarah", "user", "hash"),
            ("guestish", "Guestish", "user", "hash"),
        ],
    )
    conn.execute(
        "INSERT INTO panels(panel_id, name, allow_guest) VALUES (?, ?, ?)",
        ("zoe-touch-pi", "Kitchen", 0),
    )
    conn.executemany(
        "INSERT INTO panel_user_bindings(panel_id, user_id, binding_type) VALUES (?, ?, ?)",
        [
            ("zoe-touch-pi", "jason", "default"),
            ("zoe-touch-pi", "sarah", "allowed"),
        ],
    )
    conn.commit()
    conn.close()

    client = TestClient(app)
    response = client.get("/api/auth/profiles?panel_id=zoe-touch-pi")

    assert response.status_code == 200
    payload = response.json()
    assert payload["panel_id"] == "zoe-touch-pi"
    assert payload["panel_name"] == "Kitchen"
    assert payload["allow_guest"] is False
    assert payload["default_user_id"] == "jason"
    assert [p["user_id"] for p in payload["profiles"]] == ["jason", "sarah"]
    assert payload["profiles"][0]["avatar"] == "J"


def test_login_requires_initial_password_setup_marker(sqlite_auth_db):
    conn = sqlite3.connect(sqlite_auth_db)
    conn.execute(
        "INSERT INTO auth_users(user_id, username, role, password_hash) VALUES (?, ?, ?, ?)",
        ("zoe", "zoe", "user", "SETUP_REQUIRED"),
    )
    conn.commit()
    conn.close()

    client = TestClient(app)
    response = client.post(
        "/api/auth/login",
        json={"username": "zoe", "password": "anything", "device_info": {}},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is False
    assert payload["error_message"] == "PASSWORD_SETUP_REQUIRED"
    assert payload["requires_escalation"] is True


def test_expired_lockout_resets_failed_attempt_window(sqlite_auth_db):
    password_hash = bcrypt.hashpw(b"correct-password", bcrypt.gensalt()).decode("utf-8")
    expired_lock = (datetime.now() - timedelta(minutes=1)).isoformat()
    now = datetime.now().isoformat()

    conn = sqlite3.connect(sqlite_auth_db)
    conn.execute(
        """
        INSERT INTO auth_users(
            user_id, username, email, role, password_hash, created_at, updated_at,
            is_active, is_verified, failed_login_attempts, locked_until
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "zoe",
            "zoe",
            "zoe@example.com",
            "user",
            password_hash,
            now,
            now,
            1,
            1,
            5,
            expired_lock,
        ),
    )
    conn.commit()
    conn.close()

    result = AuthManager().verify_password("zoe", "wrong-password")

    assert result.success is False
    assert result.locked_until is None

    conn = sqlite3.connect(sqlite_auth_db)
    attempts, locked_until = conn.execute(
        "SELECT failed_login_attempts, locked_until FROM auth_users WHERE user_id = ?",
        ("zoe",),
    ).fetchone()
    conn.close()
    assert attempts == 1
    assert locked_until is None


# ── Finding 2: login rate limiting (IP + username sliding window) ─────────────


def test_login_blocks_after_repeated_failed_attempts(sqlite_auth_db):
    """Brute-forcing a password is throttled once the failure window fills."""
    _seed_user(sqlite_auth_db, password="Correct-Horse-9")
    client = TestClient(app)

    # The "login" rule allows 5 attempts; the 5th failure trips the block.
    for _ in range(5):
        resp = client.post(
            "/api/auth/login",
            json={"username": "zoe", "password": "wrong", "device_info": {}},
        )
        assert resp.status_code == 200
        # Bad password (vs unknown user) surfaces as "Invalid password".
        assert resp.json()["error_message"] == "Invalid password"

    blocked = client.post(
        "/api/auth/login",
        json={"username": "zoe", "password": "wrong", "device_info": {}},
    )
    assert blocked.status_code == 200
    assert blocked.json()["error_message"] == "Too many login attempts. Please try again later."


def test_login_succeeds_for_valid_credentials(sqlite_auth_db, monkeypatch):
    """Regression: a correct password still authenticates and is never throttled."""
    _seed_user(sqlite_auth_db, password="Correct-Horse-9")

    fake_session = types.SimpleNamespace(
        session_id="sess-xyz",
        session_type=SessionType.STANDARD,
        expires_at=datetime.now() + timedelta(hours=8),
    )
    monkeypatch.setattr(
        session_manager, "authenticate",
        lambda req: types.SimpleNamespace(success=True, session=fake_session),
    )
    monkeypatch.setattr(
        __import__("core.auth", fromlist=["auth_manager"]).auth_manager,
        "get_user_info",
        lambda uid: {"user_id": uid, "username": "zoe", "role": "user"},
    )

    client = TestClient(app)
    resp = client.post(
        "/api/auth/login",
        json={"username": "zoe", "password": "Correct-Horse-9", "device_info": {}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["session_id"] == "sess-xyz"


def test_login_failures_do_not_block_a_different_username(sqlite_auth_db):
    """The username bucket is scoped: hammering 'zoe' must not lock out 'sarah'."""
    _seed_user(sqlite_auth_db, user_id="zoe", username="zoe", password="pw-zoe-123")
    client = TestClient(app)
    for _ in range(6):
        client.post(
            "/api/auth/login",
            json={"username": "zoe", "password": "wrong", "device_info": {}},
        )
    # 'sarah' shares the (test) loopback IP but has her own username bucket. The
    # IP bucket is also tripped here, so we assert sarah is not *username*-blocked
    # by checking the limiter directly rather than the shared-IP endpoint.
    assert rate_limiter.is_limited("login", None, "sarah") is False


# ── Finding 1: /password/setup authorization gate ────────────────────────────


def _seed_pending(db_path, username="zoe"):
    _seed_user(db_path, user_id=username, username=username, password_hash="SETUP_REQUIRED")


def test_password_setup_rejected_without_token_or_admin(sqlite_auth_db):
    """The core race: an attacker cannot claim a pending account with no proof."""
    _seed_pending(sqlite_auth_db)
    client = TestClient(app)
    resp = client.post(
        "/api/auth/password/setup",
        json={
            "username": "zoe",
            "new_password": "Attacker-Pw-1",
            "confirm_password": "Attacker-Pw-1",
        },
    )
    assert resp.status_code == 403
    # Password must remain unset so the real user can still claim it.
    assert _password_hash_of(sqlite_auth_db) == "SETUP_REQUIRED"


def test_password_setup_succeeds_with_bootstrap_token(sqlite_auth_db):
    """Legitimate first-run: the local operator's bootstrap token completes setup."""
    _seed_pending(sqlite_auth_db)
    client = TestClient(app)
    resp = client.post(
        "/api/auth/password/setup",
        json={
            "username": "zoe",
            "new_password": "Real-User-Pw-1",
            "confirm_password": "Real-User-Pw-1",
            "setup_token": "test-bootstrap-token",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["success"] is True
    new_hash = _password_hash_of(sqlite_auth_db)
    assert new_hash not in (None, "SETUP_REQUIRED")
    assert bcrypt.checkpw(b"Real-User-Pw-1", new_hash.encode())


def test_password_setup_succeeds_with_admin_minted_token(sqlite_auth_db):
    """An admin-minted one-time token also completes setup (additional users)."""
    _seed_pending(sqlite_auth_db)
    token = setup_token_manager.issue_token("zoe")
    client = TestClient(app)
    resp = client.post(
        "/api/auth/password/setup",
        json={
            "username": "zoe",
            "new_password": "Real-User-Pw-1",
            "confirm_password": "Real-User-Pw-1",
            "setup_token": token,
        },
    )
    assert resp.status_code == 200
    # One-time: the same token cannot be replayed (password is already set now).
    replay = client.post(
        "/api/auth/password/setup",
        json={
            "username": "zoe",
            "new_password": "Another-Pw-2",
            "confirm_password": "Another-Pw-2",
            "setup_token": token,
        },
    )
    assert replay.status_code == 400  # password already set


def test_password_setup_rejects_wrong_token(sqlite_auth_db):
    _seed_pending(sqlite_auth_db)
    client = TestClient(app)
    resp = client.post(
        "/api/auth/password/setup",
        json={
            "username": "zoe",
            "new_password": "Whatever-Pw-1",
            "confirm_password": "Whatever-Pw-1",
            "setup_token": "not-the-token",
        },
    )
    assert resp.status_code == 403
    assert _password_hash_of(sqlite_auth_db) == "SETUP_REQUIRED"


def test_password_setup_allows_authenticated_admin(sqlite_auth_db, monkeypatch):
    """Admin path: a session with users.create may complete setup without a token."""
    _seed_pending(sqlite_auth_db)
    admin_session = types.SimpleNamespace(session_id="admin-sess", user_id="admin")
    monkeypatch.setattr(session_manager, "get_session", lambda sid: admin_session)
    monkeypatch.setattr(
        session_manager, "validate_session_permission",
        lambda session_id, permission, resource=None: permission == "users.create",
    )
    client = TestClient(app)
    resp = client.post(
        "/api/auth/password/setup",
        headers={"X-Session-ID": "admin-sess"},
        json={
            "username": "zoe",
            "new_password": "Admin-Set-Pw-1",
            "confirm_password": "Admin-Set-Pw-1",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["success"] is True


def test_password_setup_password_mismatch_still_400(sqlite_auth_db):
    """Existing contract preserved: mismatched passwords fail before the gate."""
    _seed_pending(sqlite_auth_db)
    client = TestClient(app)
    resp = client.post(
        "/api/auth/password/setup",
        json={
            "username": "zoe",
            "new_password": "Real-User-Pw-1",
            "confirm_password": "different-Pw-2",
            "setup_token": "test-bootstrap-token",
        },
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Passwords do not match"


def test_password_setup_rate_limited(sqlite_auth_db):
    """Repeated unauthorized setup attempts are throttled (token brute-force)."""
    _seed_pending(sqlite_auth_db)
    client = TestClient(app)
    for _ in range(5):
        r = client.post(
            "/api/auth/password/setup",
            json={
                "username": "zoe",
                "new_password": "Guess-Pw-0001",
                "confirm_password": "Guess-Pw-0001",
                "setup_token": "guessing",
            },
        )
        assert r.status_code == 403
    blocked = client.post(
        "/api/auth/password/setup",
        json={
            "username": "zoe",
            "new_password": "Guess-Pw-0001",
            "confirm_password": "Guess-Pw-0001",
            "setup_token": "guessing",
        },
    )
    assert blocked.status_code == 429
