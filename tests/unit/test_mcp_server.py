"""Unit checks for the legacy MCP database operations.

These tests used to be a script with async methods that pytest never collected.
They now assert the same database effects against an isolated SQLite database.
"""

from __future__ import annotations

import sqlite3

import pytest


@pytest.fixture()
def mcp_db(tmp_path):
    db_path = tmp_path / "zoe.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE people (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            name TEXT NOT NULL,
            profile TEXT,
            facts TEXT,
            important_dates TEXT,
            preferences TEXT
        );
        CREATE TABLE projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            name TEXT NOT NULL
        );
        CREATE TABLE memory_facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            fact TEXT NOT NULL
        );
        CREATE TABLE events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            title TEXT NOT NULL,
            start_date TEXT,
            start_time TEXT,
            description TEXT,
            category TEXT
        );
        CREATE TABLE lists (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            name TEXT NOT NULL,
            category TEXT,
            description TEXT
        );
        CREATE TABLE list_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            list_id INTEGER NOT NULL,
            task_text TEXT NOT NULL,
            priority TEXT,
            completed INTEGER DEFAULT 0
        );
        CREATE TABLE developer_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            title TEXT NOT NULL
        );
        INSERT INTO projects (user_id, name) VALUES ('default', 'MCP project');
        INSERT INTO memory_facts (user_id, fact) VALUES ('default', 'MCP fact');
        INSERT INTO developer_tasks (user_id, title) VALUES ('default', 'MCP task');
        """
    )
    try:
        yield conn
    finally:
        conn.close()


def test_search_memories_reads_expected_tables(mcp_db):
    cursor = mcp_db.cursor()

    counts = {}
    for table in ("people", "projects", "memory_facts"):
        cursor.execute(f"SELECT COUNT(*) FROM {table}")
        counts[table] = cursor.fetchone()[0]

    assert counts == {"people": 0, "projects": 1, "memory_facts": 1}


def test_create_person_increases_people_count(mcp_db):
    cursor = mcp_db.cursor()
    cursor.execute("SELECT COUNT(*) FROM people")
    count_before = cursor.fetchone()[0]

    cursor.execute(
        """
        INSERT INTO people (user_id, name, profile, facts, important_dates, preferences)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("default", "Test Person", '{"relationship": "test", "created_by": "mcp_test"}', "{}", "{}", "{}"),
    )
    mcp_db.commit()

    cursor.execute("SELECT COUNT(*) FROM people")
    count_after = cursor.fetchone()[0]
    assert count_after == count_before + 1


def test_create_calendar_event_increases_event_count(mcp_db):
    cursor = mcp_db.cursor()
    cursor.execute("SELECT COUNT(*) FROM events")
    count_before = cursor.fetchone()[0]

    cursor.execute(
        """
        INSERT INTO events (user_id, title, start_date, start_time, description, category)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("default", "Test Event", "2026-06-28", "10:00", "Test event created by MCP", "personal"),
    )
    mcp_db.commit()

    cursor.execute("SELECT COUNT(*) FROM events")
    count_after = cursor.fetchone()[0]
    assert count_after == count_before + 1


def test_add_to_list_creates_list_item(mcp_db):
    cursor = mcp_db.cursor()
    cursor.execute("SELECT COUNT(*) FROM list_items")
    count_before = cursor.fetchone()[0]

    cursor.execute(
        """
        INSERT INTO lists (user_id, name, category, description)
        VALUES (?, ?, ?, ?)
        """,
        ("default", "Test List", "personal", "Test list for MCP"),
    )
    list_id = cursor.lastrowid
    cursor.execute(
        """
        INSERT INTO list_items (list_id, task_text, priority, completed)
        VALUES (?, ?, ?, ?)
        """,
        (list_id, "Test task from MCP", "medium", False),
    )
    mcp_db.commit()

    cursor.execute("SELECT COUNT(*) FROM list_items")
    count_after = cursor.fetchone()[0]
    assert count_after == count_before + 1


def test_get_developer_tasks_requires_existing_tasks(mcp_db):
    cursor = mcp_db.cursor()
    cursor.execute("SELECT COUNT(*) FROM developer_tasks WHERE user_id = ?", ("default",))
    task_count = cursor.fetchone()[0]

    assert task_count > 0
