"""Current-contract API tests for zoe-auth."""

from __future__ import annotations

import sqlite3
import tempfile
from datetime import datetime, timedelta

from fastapi.testclient import TestClient
import pytest
import bcrypt

from sqlite_compat import SQLiteCompatConnection
import models.database as db_module
from core.auth import AuthManager
from main import app


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
