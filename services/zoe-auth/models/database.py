"""
Shared data models and DB handle for zoe-auth.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

import psycopg2
import psycopg2.extras
import psycopg2.pool


class UserRole(str, Enum):
    ADMIN = "admin"
    USER = "user"
    FAMILY_MEMBER = "family_member"
    CHILD = "child"
    GUEST = "guest"


class SessionType(str, Enum):
    STANDARD = "standard"
    PASSCODE = "passcode"
    GUEST = "guest"
    API = "api"
    SSO = "sso"


class AuthMethod(str, Enum):
    PASSWORD = "password"
    PASSCODE = "passcode"
    API_KEY = "api_key"
    SSO = "sso"


@dataclass
class User:
    user_id: str
    username: str
    email: str
    role: str
    is_active: bool = True
    is_verified: bool = True
    created_at: Optional[datetime] = None
    last_login: Optional[datetime] = None


@dataclass
class Role:
    role_id: str
    name: str
    description: str
    permissions: List[str]
    inherits_from: Optional[str] = None
    is_system: bool = False
    created_at: Optional[datetime] = None


@dataclass
class Permission:
    permission_id: str
    name: str
    description: str
    resource: str
    action: str
    created_at: Optional[datetime] = None


@dataclass
class AuthSession:
    session_id: str
    user_id: str
    session_type: SessionType
    auth_method: AuthMethod
    device_info: Dict[str, Any]
    created_at: datetime
    last_activity: datetime
    expires_at: datetime
    is_active: bool = True
    permissions_cache: Optional[List[str]] = None
    role_cache: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class AuditLog:
    log_id: str
    user_id: Optional[str]
    action: str
    resource: str
    result: str
    ip_address: Optional[str]
    user_agent: Optional[str]
    details: Dict[str, Any]
    timestamp: datetime


class _ConnWrapper:
    """Emulates the sqlite3 Connection API used throughout zoe-auth."""

    def __init__(self, raw_conn, pool):
        self._conn = raw_conn
        self._pool = pool
        self._last_rowcount = 0

    def execute(self, sql: str, params=()):
        sql = sql.replace("?", "%s")
        cur = self._conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cur.execute(sql, params if params else None)
        self._last_rowcount = cur.rowcount if cur.rowcount >= 0 else 0
        return cur

    @property
    def total_changes(self) -> int:
        return self._last_rowcount

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._pool.putconn(self._conn)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self._conn.rollback()
        else:
            self._conn.commit()
        self._pool.putconn(self._conn)
        return False


_pool: Optional[psycopg2.pool.ThreadedConnectionPool] = None


def _get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    global _pool
    if _pool is None:
        dsn = os.getenv("POSTGRES_URL", "postgresql://zoe:zoe@zoe-database:5432/zoe")
        _pool = psycopg2.pool.ThreadedConnectionPool(minconn=2, maxconn=10, dsn=dsn)
    return _pool


class AuthDatabase:
    """PostgreSQL connection wrapper with sqlite3-compatible API."""

    def get_connection(self) -> _ConnWrapper:
        pool = _get_pool()
        conn = pool.getconn()
        conn.autocommit = False
        return _ConnWrapper(conn, pool)


auth_db = AuthDatabase()
