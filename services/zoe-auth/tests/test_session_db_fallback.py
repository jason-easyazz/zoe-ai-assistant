"""get_session must fall back to the persisted store on a cache miss.

Regression: the panel mints a guest session, but a long-lived auth worker only
had sessions it loaded at startup (or that it minted itself) in its in-memory
`active_sessions` cache. `get_session` was cache-only, so a valid persisted
session 404'd on any worker that didn't hold it — walling the kiosk on the
sign-in card. The DB is the source of truth; the cache is just a cache.
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

from sqlite_compat import SQLiteCompatConnection
import models.database as db_module
from core.sessions import session_manager, SessionType


def _make_sessions_db(path: str) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS auth_sessions (
            session_id TEXT PRIMARY KEY,
            user_id TEXT,
            session_type TEXT,
            auth_method TEXT,
            device_info TEXT,
            created_at TEXT,
            last_activity TEXT,
            expires_at TEXT,
            is_active INTEGER,
            permissions_cache TEXT,
            role_cache TEXT,
            metadata TEXT
        )
        """
    )
    conn.commit()
    conn.close()


def _insert_session(path: str, session_id: str, *, expires_at: str,
                    is_active: int = 1, user_id: str = "guest",
                    session_type: str = "guest") -> None:
    conn = sqlite3.connect(path)
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO auth_sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (session_id, user_id, session_type, "api_key", json.dumps({}),
         now, now, expires_at, is_active, json.dumps([]), user_id, json.dumps({})),
    )
    conn.commit()
    conn.close()


@pytest.fixture
def sessions_db(monkeypatch):
    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        _make_sessions_db(tmp.name)
        monkeypatch.setattr(
            db_module.auth_db, "get_connection",
            lambda: SQLiteCompatConnection(tmp.name),
        )
        # Start from an empty in-memory cache so we exercise the DB fallback.
        monkeypatch.setattr(session_manager, "active_sessions", {})
        yield tmp.name


def test_get_session_falls_back_to_db_on_cache_miss(sessions_db):
    sid = "guest-not-in-cache"
    future = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
    _insert_session(sessions_db, sid, expires_at=future)

    assert sid not in session_manager.active_sessions        # genuinely a cache miss
    session = session_manager.get_session(sid)
    assert session is not None                               # was 404 before the fix
    assert session.session_id == sid
    assert session.session_type == SessionType.GUEST
    assert sid in session_manager.active_sessions            # and now cached


def test_get_session_tolerates_naive_expires_at(sessions_db):
    sid = "guest-naive-expiry"
    naive_future = (datetime.now(timezone.utc) + timedelta(minutes=30)).replace(tzinfo=None).isoformat()
    _insert_session(sessions_db, sid, expires_at=naive_future)

    # A naive expires_at must not raise (aware < naive TypeError) — treat as UTC.
    assert session_manager.get_session(sid) is not None


def test_get_session_rejects_expired_db_session(sessions_db):
    sid = "guest-expired"
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    _insert_session(sessions_db, sid, expires_at=past)

    assert session_manager.get_session(sid) is None


def test_get_session_ignores_inactive_db_session(sessions_db):
    sid = "guest-inactive"
    future = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
    _insert_session(sessions_db, sid, expires_at=future, is_active=0)

    assert session_manager.get_session(sid) is None
