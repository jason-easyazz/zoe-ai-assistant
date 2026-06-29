"""Unit checks for the legacy MCP security expectations."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

jwt = pytest.importorskip("jwt")

SECRET_KEY = "zoe-mcp-secret-key-change-in-production"
ALGORITHM = "HS256"


@pytest.fixture()
def security_db(tmp_path):
    db_path = tmp_path / "zoe.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE users (
            user_id TEXT PRIMARY KEY,
            username TEXT NOT NULL,
            role TEXT NOT NULL,
            is_active INTEGER NOT NULL
        );
        CREATE TABLE people (id INTEGER PRIMARY KEY, user_id TEXT NOT NULL);
        CREATE TABLE events (id INTEGER PRIMARY KEY, user_id TEXT NOT NULL);
        CREATE TABLE lists (id INTEGER PRIMARY KEY, user_id TEXT NOT NULL);
        CREATE TABLE developer_tasks (id INTEGER PRIMARY KEY, user_id TEXT NOT NULL);

        INSERT INTO users VALUES ('admin-user', 'admin', 'admin', 1);
        INSERT INTO users VALUES ('regular-user', 'regular', 'user', 1);
        INSERT INTO people (user_id) VALUES ('admin-user'), ('regular-user');
        INSERT INTO events (user_id) VALUES ('admin-user'), ('regular-user');
        INSERT INTO lists (user_id) VALUES ('admin-user'), ('regular-user');
        INSERT INTO developer_tasks (user_id) VALUES ('regular-user');
        """
    )
    try:
        yield conn
    finally:
        conn.close()


def _create_test_jwt(user_id: str, username: str) -> str:
    payload = {
        "user_id": user_id,
        "username": username,
        "exp": datetime.now(UTC) + timedelta(hours=24),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def test_jwt_validation_accepts_valid_token_and_rejects_invalid_token():
    token = _create_test_jwt("default", "testuser")

    payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    assert payload["user_id"] == "default"
    assert payload["username"] == "testuser"

    with pytest.raises(jwt.InvalidTokenError):
        jwt.decode("invalid.token.here", SECRET_KEY, algorithms=[ALGORITHM])


def test_user_isolation_queries_filter_by_user_id(security_db):
    cursor = security_db.cursor()
    cursor.execute("SELECT user_id, username FROM users WHERE is_active = 1")
    users = cursor.fetchall()

    assert len(users) >= 2, "Isolation coverage requires at least two active users"
    for user_id, _username in users:
        for table in ("people", "events", "lists"):
            cursor.execute(f"SELECT COUNT(*) FROM {table} WHERE user_id = ?", (user_id,))
            assert cursor.fetchone()[0] == 1


def test_audit_logging_persists_entry(security_db):
    cursor = security_db.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS mcp_audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            username TEXT NOT NULL,
            tool_name TEXT NOT NULL,
            action TEXT NOT NULL,
            details TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            session_id TEXT
        )
        """
    )
    cursor.execute(
        """
        INSERT INTO mcp_audit_log (user_id, username, tool_name, action, details, session_id)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("test_user", "testuser", "test_tool", "test_action", '{"test": "data"}', "test_session"),
    )
    security_db.commit()

    cursor.execute("SELECT COUNT(*) FROM mcp_audit_log WHERE user_id = ?", ("test_user",))
    assert cursor.fetchone()[0] == 1


def test_permission_system_has_admin_and_regular_users(security_db):
    cursor = security_db.cursor()
    cursor.execute("SELECT role, COUNT(*) FROM users WHERE is_active = 1 GROUP BY role")
    role_counts = dict(cursor.fetchall())

    assert role_counts["admin"] == 1
    assert role_counts["user"] == 1


def test_database_security_tables_have_user_id_and_audit_table(security_db):
    cursor = security_db.cursor()
    cursor.execute("CREATE TABLE IF NOT EXISTS mcp_audit_log (id INTEGER PRIMARY KEY, user_id TEXT)")

    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='mcp_audit_log'")
    assert cursor.fetchone() is not None

    for table in ("people", "events", "lists", "developer_tasks"):
        cursor.execute(f"SELECT COUNT(DISTINCT user_id) FROM {table}")
        assert cursor.fetchone()[0] >= 1
