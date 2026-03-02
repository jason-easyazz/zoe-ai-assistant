"""
Database models and connection for Zoe Authentication Service.
"""

import os
import sqlite3
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)


class SessionType(Enum):
    STANDARD = "standard"
    PASSCODE = "passcode"
    GUEST = "guest"
    API = "api"
    SSO = "sso"


class AuthMethod(Enum):
    PASSWORD = "password"
    PASSCODE = "passcode"
    API_KEY = "api_key"
    SSO = "sso"


@dataclass
class User:
    user_id: str
    username: str
    email: str
    password_hash: str
    role: str = "user"
    is_active: bool = True
    is_verified: bool = True
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    last_login: Optional[str] = None
    failed_login_attempts: int = 0
    locked_until: Optional[str] = None
    settings: Optional[str] = None


@dataclass
class UserRole:
    user_id: str
    role: str
    assigned_at: Optional[str] = None
    assigned_by: Optional[str] = None


@dataclass
class Role:
    name: str
    description: str = ""
    permissions: List[str] = field(default_factory=list)


@dataclass
class Permission:
    name: str
    description: str = ""
    resource: str = ""


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
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AuditLog:
    log_id: str
    user_id: str
    action: str
    resource: str
    result: str
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    details: Optional[str] = None
    timestamp: Optional[str] = None


class AuthDatabase:
    """Database wrapper for auth service."""

    def __init__(self):
        self.db_path = os.getenv("DATABASE_PATH", "/app/data/zoe.db")
        logger.info(f"Using database: {self.db_path}")

    def get_connection(self):
        conn = sqlite3.connect(self.db_path, timeout=5.0, check_same_thread=False, isolation_level=None)
        conn.row_factory = sqlite3.Row
        return conn

    def __enter__(self):
        self._conn = self.get_connection()
        return self._conn

    def __exit__(self, exc_type, exc_val, exc_tb):
        if hasattr(self, "_conn"):
            self._conn.close()
        return False

    def init_database(self):
        pass

    def create_migration_from_existing(self):
        logger.info("Using existing database schema")


auth_db = AuthDatabase()
