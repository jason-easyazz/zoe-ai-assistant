"""Current-contract API tests for zoe-auth."""

from __future__ import annotations

import sqlite3
import tempfile

from fastapi.testclient import TestClient

import models.database as db_module
from main import app


def _init_auth_tables(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS auth_users (
            user_id TEXT PRIMARY KEY,
            username TEXT NOT NULL,
            role TEXT NOT NULL,
            password_hash TEXT
        )
        """
    )
    conn.commit()
    conn.close()


def test_health_endpoint_reports_healthy_for_reachable_db():
    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        db_module.auth_db.db_path = tmp.name
        client = TestClient(app)
        response = client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "healthy"
    assert payload["database"] == "connected"


def test_profiles_endpoint_returns_non_system_users():
    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        db_module.auth_db.db_path = tmp.name
        _init_auth_tables(tmp.name)
        conn = sqlite3.connect(tmp.name)
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


def test_login_requires_initial_password_setup_marker():
    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        db_module.auth_db.db_path = tmp.name
        _init_auth_tables(tmp.name)
        conn = sqlite3.connect(tmp.name)
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

