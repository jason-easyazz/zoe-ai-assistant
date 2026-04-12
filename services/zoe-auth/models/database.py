"""
Database connection module for Zoe Auth
This module provides the auth_db instance that will be monkey-patched by main.py
"""

import sqlite3
import os
import logging
from enum import Enum
from typing import Optional, Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)

# Enums
class UserRole(str, Enum):
    """User roles"""
    ADMIN = "admin"
    USER = "user"
    GUEST = "guest"
    FAMILY = "family"

class AuthMethod(str, Enum):
    """Authentication methods"""
    PASSWORD = "password"
    PASSCODE = "passcode"
    SSO = "sso"
    QUICK_AUTH = "quick_auth"

class SessionType(str, Enum):
    """Session types"""
    STANDARD = "standard"
    PASSWORD = "password"
    PASSCODE = "passcode"
    SSO = "sso"
    QUICK_AUTH = "quick_auth"
    GUEST = "guest"
    API = "api"

# Data classes
class User:
    """User model"""
    def __init__(self, user_id: str, username: str, email: Optional[str] = None, 
                 role: str = "user", is_active: bool = True):
        self.user_id = user_id
        self.username = username
        self.email = email
        self.role = role
        self.is_active = is_active

class AuthSession:
    """Auth session model"""
    def __init__(self, session_id: str, user_id: str, session_type: str, 
                 expires_at: datetime, device_info: Optional[Dict[str, Any]] = None,
                 auth_method: Optional[str] = None, created_at: Optional[datetime] = None,
                 ip_address: Optional[str] = None, user_agent: Optional[str] = None,
                 last_activity: Optional[datetime] = None, **kwargs):
        self.session_id = session_id
        self.user_id = user_id
        self.session_type = session_type
        self.expires_at = expires_at
        self.device_info = device_info or {}
        self.auth_method = auth_method
        self.created_at = created_at or datetime.now()
        self.ip_address = ip_address
        self.user_agent = user_agent
        self.last_activity = last_activity or datetime.now()
        # Accept any additional kwargs to be flexible
        for key, value in kwargs.items():
            setattr(self, key, value)

class Role:
    """Role model"""
    def __init__(self, role_id: str, role_name: str, description: Optional[str] = None):
        self.role_id = role_id
        self.role_name = role_name
        self.description = description

class Permission:
    """Permission model"""
    def __init__(self, permission_id: str, permission_name: str, 
                 description: Optional[str] = None):
        self.permission_id = permission_id
        self.permission_name = permission_name
        self.description = description

class AuditLog:
    """Audit log model"""
    def __init__(self, log_id: str, user_id: str, action: str, 
                 timestamp: datetime, details: Optional[Dict[str, Any]] = None):
        self.log_id = log_id
        self.user_id = user_id
        self.action = action
        self.timestamp = timestamp
        self.details = details or {}

class AuthDatabase:
    """Database wrapper for auth service"""
    
    def __init__(self):
        self.db_path = os.getenv("DATABASE_PATH", "/app/data/zoe.db")
        logger.info(f"Initializing AuthDatabase with path: {self.db_path}")
    
    def get_connection(self):
        """Get a database connection"""
        conn = sqlite3.connect(self.db_path, timeout=5.0, check_same_thread=False, isolation_level=None)
        conn.row_factory = sqlite3.Row
        return conn
    
    def __enter__(self):
        """Context manager support"""
        self._conn = self.get_connection()
        return self._conn
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager support"""
        if hasattr(self, '_conn'):
            self._conn.close()
        return False

# Global auth_db instance (will be monkey-patched by main.py)
auth_db = AuthDatabase()

