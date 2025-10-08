"""
Enhanced Database Models for Zoe Authentication System
Supports passcode authentication, RBAC, and SSO integration
"""

import sqlite3
import os
import json
import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, asdict
from enum import Enum
import logging

logger = logging.getLogger(__name__)

class UserRole(Enum):
    """Predefined user roles with hierarchy"""
    GUEST = "guest"
    CHILD = "child"
    FAMILY = "family"
    USER = "user"
    ADMIN = "admin"
    SUPER_ADMIN = "super_admin"

class AuthMethod(Enum):
    """Supported authentication methods"""
    PASSWORD = "password"
    PASSCODE = "passcode"
    SSO = "sso"
    API_KEY = "api_key"

class SessionType(Enum):
    """Different session types with varying security levels"""
    STANDARD = "standard"       # Full password auth
    PASSCODE = "passcode"      # Quick passcode auth - limited permissions
    GUEST = "guest"            # Guest access - minimal permissions
    API = "api"                # API access via token
    SSO = "sso"                # Single sign-on session

@dataclass
class User:
    """Enhanced user model with role-based access"""
    user_id: str
    username: str
    email: str
    password_hash: Optional[str]
    passcode_hash: Optional[str]
    role: UserRole
    is_active: bool = True
    is_verified: bool = False
    created_at: datetime = None
    updated_at: datetime = None
    last_login: Optional[datetime] = None
    failed_login_attempts: int = 0
    locked_until: Optional[datetime] = None
    settings: Dict[str, Any] = None
    metadata: Dict[str, Any] = None

@dataclass 
class Role:
    """Role definition with permissions"""
    role_id: str
    name: str
    description: str
    permissions: List[str]
    inherits_from: Optional[str] = None
    is_system: bool = False
    created_at: datetime = None
    metadata: Dict[str, Any] = None

@dataclass
class Permission:
    """Individual permission definition"""
    permission_id: str
    name: str
    description: str
    resource: str
    action: str
    created_at: datetime = None

@dataclass
class Passcode:
    """Passcode configuration for users"""
    user_id: str
    passcode_hash: str
    algorithm: str = "argon2"
    salt: str = ""
    created_at: datetime = None
    last_used: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    failed_attempts: int = 0
    max_attempts: int = 5
    is_active: bool = True

@dataclass
class AuthSession:
    """Enhanced session with role and permission caching"""
    session_id: str
    user_id: str
    session_type: SessionType
    auth_method: AuthMethod
    device_info: Dict[str, Any]
    created_at: datetime
    last_activity: datetime
    expires_at: datetime
    is_active: bool = True
    permissions_cache: List[str] = None
    role_cache: str = None
    metadata: Dict[str, Any] = None

@dataclass
class AuditLog:
    """Security audit logging"""
    log_id: str
    user_id: Optional[str]
    action: str
    resource: Optional[str]
    result: str  # success, failure, blocked
    ip_address: Optional[str]
    user_agent: Optional[str]
    details: Dict[str, Any]
    timestamp: datetime

@dataclass
class ServiceAccount:
    """SSO service account configurations"""
    service_id: str
    service_name: str
    service_type: str  # homeassistant, n8n, matrix, etc.
    config: Dict[str, Any]
    is_active: bool = True
    created_at: datetime = None
    updated_at: datetime = None

class AuthDatabase:
    """Enhanced database manager for authentication system"""
    
    def __init__(self, db_path: str = "/app/data/zoe_auth.db"):
        self.db_path = db_path
        self.ensure_directory()
        self.init_database()
        self.setup_default_roles()

    def ensure_directory(self):
        """Ensure database directory exists"""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

    def get_connection(self) -> sqlite3.Connection:
        """Get database connection with proper settings"""
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def init_database(self):
        """Initialize all database tables"""
        with self.get_connection() as conn:
            # Enhanced users table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL,
                    email TEXT UNIQUE NOT NULL,
                    password_hash TEXT,
                    passcode_hash TEXT,
                    role TEXT NOT NULL DEFAULT 'user',
                    is_active INTEGER NOT NULL DEFAULT 1,
                    is_verified INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    last_login TEXT,
                    failed_login_attempts INTEGER NOT NULL DEFAULT 0,
                    locked_until TEXT,
                    settings TEXT DEFAULT '{}',
                    metadata TEXT DEFAULT '{}'
                )
            """)

            # Roles table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS roles (
                    role_id TEXT PRIMARY KEY,
                    name TEXT UNIQUE NOT NULL,
                    description TEXT,
                    permissions TEXT NOT NULL DEFAULT '[]',
                    inherits_from TEXT,
                    is_system INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    metadata TEXT DEFAULT '{}',
                    FOREIGN KEY (inherits_from) REFERENCES roles(role_id)
                )
            """)

            # Permissions table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS permissions (
                    permission_id TEXT PRIMARY KEY,
                    name TEXT UNIQUE NOT NULL,
                    description TEXT,
                    resource TEXT NOT NULL,
                    action TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Passcodes table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS passcodes (
                    user_id TEXT PRIMARY KEY,
                    passcode_hash TEXT NOT NULL,
                    algorithm TEXT NOT NULL DEFAULT 'argon2',
                    salt TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    last_used TEXT,
                    expires_at TEXT,
                    failed_attempts INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 5,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
            """)

            # Enhanced sessions table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS auth_sessions (
                    session_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    session_type TEXT NOT NULL DEFAULT 'standard',
                    auth_method TEXT NOT NULL,
                    device_info TEXT DEFAULT '{}',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    last_activity TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    expires_at TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    permissions_cache TEXT DEFAULT '[]',
                    role_cache TEXT,
                    metadata TEXT DEFAULT '{}',
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
            """)

            # Audit logs table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS audit_logs (
                    log_id TEXT PRIMARY KEY,
                    user_id TEXT,
                    action TEXT NOT NULL,
                    resource TEXT,
                    result TEXT NOT NULL,
                    ip_address TEXT,
                    user_agent TEXT,
                    details TEXT DEFAULT '{}',
                    timestamp TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE SET NULL
                )
            """)

            # Service accounts for SSO
            conn.execute("""
                CREATE TABLE IF NOT EXISTS service_accounts (
                    service_id TEXT PRIMARY KEY,
                    service_name TEXT UNIQUE NOT NULL,
                    service_type TEXT NOT NULL,
                    config TEXT DEFAULT '{}',
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Rate limiting table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS rate_limits (
                    identifier TEXT NOT NULL,
                    action TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 1,
                    window_start TEXT NOT NULL,
                    blocked_until TEXT,
                    PRIMARY KEY (identifier, action)
                )
            """)

            # Guest codes table for temporary access
            conn.execute("""
                CREATE TABLE IF NOT EXISTS guest_codes (
                    code_id TEXT PRIMARY KEY,
                    code_hash TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    description TEXT,
                    permissions TEXT DEFAULT '[]',
                    expires_at TEXT NOT NULL,
                    max_uses INTEGER,
                    current_uses INTEGER DEFAULT 0,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (created_by) REFERENCES users(user_id)
                )
            """)

            self._create_indexes(conn)
            conn.commit()

    def _create_indexes(self, conn: sqlite3.Connection):
        """Create performance indexes"""
        indexes = [
            "CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)",
            "CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)",
            "CREATE INDEX IF NOT EXISTS idx_users_role ON users(role)",
            "CREATE INDEX IF NOT EXISTS idx_users_active ON users(is_active)",
            "CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON auth_sessions(user_id)",
            "CREATE INDEX IF NOT EXISTS idx_sessions_expires ON auth_sessions(expires_at)",
            "CREATE INDEX IF NOT EXISTS idx_sessions_active ON auth_sessions(is_active)",
            "CREATE INDEX IF NOT EXISTS idx_audit_user_id ON audit_logs(user_id)",
            "CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_logs(timestamp)",
            "CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_logs(action)",
            "CREATE INDEX IF NOT EXISTS idx_passcodes_active ON passcodes(is_active)",
            "CREATE INDEX IF NOT EXISTS idx_rate_limits_identifier ON rate_limits(identifier)",
            "CREATE INDEX IF NOT EXISTS idx_guest_codes_active ON guest_codes(is_active, expires_at)"
        ]
        
        for index_sql in indexes:
            conn.execute(index_sql)

    def setup_default_roles(self):
        """Create default system roles"""
        default_roles = [
            Role(
                role_id="super_admin",
                name="Super Administrator", 
                description="Full system access including user management",
                permissions=["*"],
                is_system=True
            ),
            Role(
                role_id="admin",
                name="Administrator",
                description="Administrative access with user management",
                permissions=[
                    "users.read", "users.create", "users.update", "users.delete",
                    "roles.read", "roles.update", "system.monitor", "audit.read",
                    "calendar.*", "lists.*", "memories.*", "ai.*"
                ],
                is_system=True
            ),
            Role(
                role_id="user",
                name="Regular User",
                description="Standard user with personal data access",
                permissions=[
                    "profile.read", "profile.update", "calendar.read", "calendar.create",
                    "calendar.update", "lists.read", "lists.create", "lists.update",
                    "memories.read", "memories.create", "ai.chat", "ai.assist"
                ],
                is_system=True
            ),
            Role(
                role_id="family",
                name="Family Member",
                description="Family member with shared resource access",
                permissions=[
                    "profile.read", "profile.update", "calendar.read", "calendar.create",
                    "lists.read", "lists.create", "shared.calendar.read", 
                    "shared.lists.read", "ai.chat", "homeassistant.basic"
                ],
                inherits_from="user",
                is_system=True
            ),
            Role(
                role_id="child",
                name="Child User",
                description="Restricted access for children with parental controls",
                permissions=[
                    "profile.read", "calendar.read", "lists.read", "ai.chat.supervised",
                    "entertainment.approved"
                ],
                is_system=True
            ),
            Role(
                role_id="guest",
                name="Guest User",
                description="Temporary limited access for guests",
                permissions=[
                    "weather.read", "time.read", "music.basic", "lights.basic"
                ],
                is_system=True
            )
        ]

        with self.get_connection() as conn:
            for role in default_roles:
                conn.execute("""
                    INSERT OR IGNORE INTO roles 
                    (role_id, name, description, permissions, inherits_from, is_system, created_at, metadata)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    role.role_id, role.name, role.description,
                    json.dumps(role.permissions), role.inherits_from,
                    1 if role.is_system else 0,
                    datetime.now().isoformat(),
                    json.dumps(role.metadata or {})
                ))

    def create_migration_from_existing(self):
        """Migrate existing users from old auth system"""
        old_db_path = "/app/data/zoe.db"
        if not os.path.exists(old_db_path):
            logger.info("No existing database to migrate from")
            return

        try:
            old_conn = sqlite3.connect(old_db_path)
            old_conn.row_factory = sqlite3.Row
            
            cursor = old_conn.execute("""
                SELECT user_id, username, email, password_hash, is_active, is_admin, created_at
                FROM users
            """)
            
            migrated = 0
            with self.get_connection() as new_conn:
                for row in cursor.fetchall():
                    role = "admin" if row["is_admin"] else "user"
                    
                    new_conn.execute("""
                        INSERT OR IGNORE INTO users 
                        (user_id, username, email, password_hash, role, is_active, is_verified, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        row["user_id"], row["username"], row["email"],
                        row["password_hash"], role, row["is_active"], 1,
                        row["created_at"] or datetime.now().isoformat()
                    ))
                    migrated += 1
            
            old_conn.close()
            logger.info(f"Migrated {migrated} users from existing database")
            
        except Exception as e:
            logger.error(f"Migration failed: {e}")
            raise

# Global database instance
auth_db = AuthDatabase()

