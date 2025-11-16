"""
Role-Based Access Control (RBAC) System
Advanced permission management with inheritance and caching
"""

import json
import sqlite3
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set, Tuple, Any, Union
from dataclasses import dataclass
from enum import Enum
import logging
import threading
import time

from models.database import auth_db, UserRole, Role, Permission

logger = logging.getLogger(__name__)

class PermissionResult(Enum):
    """Permission check results"""
    GRANTED = "granted"
    DENIED = "denied"
    CONDITIONAL = "conditional"  # Requires additional checks

@dataclass
class PermissionCheck:
    """Result of permission verification"""
    result: PermissionResult
    user_id: str
    permission: str
    resource: Optional[str] = None
    context: Optional[Dict[str, Any]] = None
    reason: Optional[str] = None
    expires_at: Optional[datetime] = None

@dataclass
class AccessContext:
    """Context for permission checks"""
    user_id: str
    session_type: str
    device_info: Dict[str, Any]
    ip_address: Optional[str] = None
    time_constraints: Optional[Dict[str, Any]] = None
    resource_owner: Optional[str] = None

class PermissionCache:
    """High-performance permission caching"""
    
    def __init__(self, ttl_seconds: int = 300):  # 5 minutes default
        self.cache: Dict[str, Tuple[Set[str], datetime]] = {}
        self.ttl = timedelta(seconds=ttl_seconds)
        self.lock = threading.RLock()
        self._start_cleanup_thread()

    def get_permissions(self, user_id: str) -> Optional[Set[str]]:
        """Get cached permissions for user"""
        with self.lock:
            if user_id in self.cache:
                permissions, cached_at = self.cache[user_id]
                if datetime.now() - cached_at < self.ttl:
                    return permissions.copy()
                else:
                    del self.cache[user_id]
            return None

    def set_permissions(self, user_id: str, permissions: Set[str]):
        """Cache permissions for user"""
        with self.lock:
            self.cache[user_id] = (permissions.copy(), datetime.now())

    def invalidate_user(self, user_id: str):
        """Invalidate cache for specific user"""
        with self.lock:
            self.cache.pop(user_id, None)

    def invalidate_all(self):
        """Clear entire cache"""
        with self.lock:
            self.cache.clear()

    def _start_cleanup_thread(self):
        """Start background thread for cache cleanup"""
        def cleanup():
            while True:
                time.sleep(60)  # Check every minute
                with self.lock:
                    now = datetime.now()
                    expired = [
                        user_id for user_id, (_, cached_at) in self.cache.items()
                        if now - cached_at >= self.ttl
                    ]
                    for user_id in expired:
                        del self.cache[user_id]
                    
                    if expired:
                        logger.debug(f"Cleaned up {len(expired)} expired permission cache entries")

        cleanup_thread = threading.Thread(target=cleanup, daemon=True)
        cleanup_thread.start()

class RBACManager:
    """Role-Based Access Control Manager"""
    
    def __init__(self):
        self.permission_cache = PermissionCache()
        self._resource_patterns = {}  # Compiled regex patterns for resource matching
        self._initialize_default_permissions()

    def check_permission(self, user_id: str, permission: str, 
                        resource: Optional[str] = None,
                        context: Optional[AccessContext] = None) -> PermissionCheck:
        """
        Check if user has specific permission
        
        Args:
            user_id: User identifier
            permission: Permission string (e.g., 'calendar.read', 'admin.*')
            resource: Optional specific resource (e.g., 'calendar.event.123')
            context: Optional access context for conditional permissions
            
        Returns:
            PermissionCheck with result and details
        """
        try:
            # Get user permissions (cached or fresh)
            user_permissions = self._get_user_permissions(user_id)
            
            # Check direct permission match
            if permission in user_permissions:
                return PermissionCheck(
                    result=PermissionResult.GRANTED,
                    user_id=user_id,
                    permission=permission,
                    resource=resource,
                    reason="direct_match"
                )

            # Check wildcard permissions
            granted_wildcards = self._check_wildcard_permissions(permission, user_permissions)
            if granted_wildcards:
                return PermissionCheck(
                    result=PermissionResult.GRANTED,
                    user_id=user_id,
                    permission=permission,
                    resource=resource,
                    reason=f"wildcard_match: {granted_wildcards[0]}"
                )

            # Check resource-specific permissions
            if resource:
                resource_result = self._check_resource_permission(
                    user_id, permission, resource, user_permissions, context
                )
                if resource_result.result != PermissionResult.DENIED:
                    return resource_result

            # Check conditional permissions based on context
            if context:
                conditional_result = self._check_conditional_permissions(
                    user_id, permission, resource, user_permissions, context
                )
                if conditional_result.result != PermissionResult.DENIED:
                    return conditional_result

            # Permission denied
            return PermissionCheck(
                result=PermissionResult.DENIED,
                user_id=user_id,
                permission=permission,
                resource=resource,
                reason="no_matching_permission"
            )

        except Exception as e:
            logger.error(f"Permission check failed for user {user_id}, permission {permission}: {e}")
            return PermissionCheck(
                result=PermissionResult.DENIED,
                user_id=user_id,
                permission=permission,
                resource=resource,
                reason=f"error: {str(e)}"
            )

    def check_multiple_permissions(self, user_id: str, permissions: List[str],
                                 require_all: bool = True) -> Dict[str, PermissionCheck]:
        """
        Check multiple permissions at once
        
        Args:
            user_id: User identifier
            permissions: List of permissions to check
            require_all: If True, all must be granted; if False, any can be granted
            
        Returns:
            Dictionary mapping permission -> PermissionCheck
        """
        results = {}
        for permission in permissions:
            results[permission] = self.check_permission(user_id, permission)
            
            # Early exit optimization for require_all=False
            if not require_all and results[permission].result == PermissionResult.GRANTED:
                # Fill remaining with DENIED to maintain contract
                for remaining in permissions[len(results):]:
                    results[remaining] = PermissionCheck(
                        result=PermissionResult.DENIED,
                        user_id=user_id,
                        permission=remaining,
                        reason="not_checked_due_to_early_success"
                    )
                break
                
        return results

    def get_user_role(self, user_id: str) -> Optional[str]:
        """Get user's current role"""
        try:
            with auth_db.get_connection() as conn:
                cursor = conn.execute(
                    "SELECT role FROM users WHERE user_id = ? AND is_active = 1",
                    (user_id,)
                )
                row = cursor.fetchone()
                return row[0] if row else None
                
        except Exception as e:
            logger.error(f"Failed to get role for user {user_id}: {e}")
            return None

    def assign_role(self, user_id: str, role: str, assigned_by: str) -> bool:
        """
        Assign role to user
        
        Args:
            user_id: User to assign role to
            role: Role identifier
            assigned_by: User performing the assignment
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # Verify role exists
            if not self._role_exists(role):
                logger.warning(f"Attempted to assign non-existent role {role} to user {user_id}")
                return False

            # Verify assigner has permission
            assigner_check = self.check_permission(assigned_by, "users.assign_role")
            if assigner_check.result != PermissionResult.GRANTED:
                logger.warning(f"User {assigned_by} lacks permission to assign roles")
                return False

            with auth_db.get_connection() as conn:
                conn.execute("""
                    UPDATE users 
                    SET role = ?, updated_at = ?
                    WHERE user_id = ?
                """, (role, datetime.now().isoformat(), user_id))

                if conn.total_changes > 0:
                    # Invalidate permission cache
                    self.permission_cache.invalidate_user(user_id)
                    
                    # Log role change
                    self._log_role_change(user_id, role, assigned_by)
                    logger.info(f"Assigned role {role} to user {user_id} by {assigned_by}")
                    return True

            return False

        except Exception as e:
            logger.error(f"Failed to assign role {role} to user {user_id}: {e}")
            return False

    def create_custom_role(self, role_id: str, name: str, description: str,
                          permissions: List[str], created_by: str,
                          inherits_from: Optional[str] = None) -> bool:
        """
        Create a custom role
        
        Args:
            role_id: Unique role identifier
            name: Human-readable role name
            description: Role description
            permissions: List of permissions
            created_by: User creating the role
            inherits_from: Optional parent role to inherit from
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # Verify creator has permission
            creator_check = self.check_permission(created_by, "roles.create")
            if creator_check.result != PermissionResult.GRANTED:
                logger.warning(f"User {created_by} lacks permission to create roles")
                return False

            # Validate permissions
            invalid_permissions = self._validate_permissions(permissions)
            if invalid_permissions:
                logger.warning(f"Invalid permissions in role {role_id}: {invalid_permissions}")
                return False

            with auth_db.get_connection() as conn:
                conn.execute("""
                    INSERT INTO roles 
                    (role_id, name, description, permissions, inherits_from, is_system, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    role_id, name, description, json.dumps(permissions),
                    inherits_from, 0, datetime.now().isoformat()
                ))

                logger.info(f"Created custom role {role_id} by {created_by}")
                return True

        except Exception as e:
            logger.error(f"Failed to create role {role_id}: {e}")
            return False

    def get_role_permissions(self, role: str) -> Set[str]:
        """Get all permissions for a role, including inherited ones"""
        try:
            permissions = set()
            visited_roles = set()  # Prevent infinite recursion
            
            def collect_permissions(current_role: str):
                if current_role in visited_roles:
                    return
                visited_roles.add(current_role)
                
                with auth_db.get_connection() as conn:
                    cursor = conn.execute("""
                        SELECT permissions, inherits_from 
                        FROM roles 
                        WHERE role_id = ?
                    """, (current_role,))
                    
                    row = cursor.fetchone()
                    if row:
                        role_permissions = json.loads(row[0] or '[]')
                        permissions.update(role_permissions)
                        
                        # Recursively collect from parent role
                        if row[1]:  # inherits_from
                            collect_permissions(row[1])

            collect_permissions(role)
            return permissions

        except Exception as e:
            logger.error(f"Failed to get permissions for role {role}: {e}")
            return set()

    def list_user_permissions(self, user_id: str) -> List[str]:
        """Get sorted list of all effective permissions for user"""
        permissions = self._get_user_permissions(user_id)
        return sorted(list(permissions))

    def _get_user_permissions(self, user_id: str) -> Set[str]:
        """Get user permissions with caching"""
        # Check cache first
        cached_permissions = self.permission_cache.get_permissions(user_id)
        if cached_permissions is not None:
            return cached_permissions

        # Load from database
        permissions = set()
        try:
            user_role = self.get_user_role(user_id)
            if user_role:
                permissions = self.get_role_permissions(user_role)

            # Cache the result
            self.permission_cache.set_permissions(user_id, permissions)

        except Exception as e:
            logger.error(f"Failed to load permissions for user {user_id}: {e}")

        return permissions

    def _check_wildcard_permissions(self, permission: str, user_permissions: Set[str]) -> List[str]:
        """Check if any wildcard permissions grant access"""
        granted = []
        
        for user_perm in user_permissions:
            if '*' in user_perm:
                # Convert wildcard to regex pattern
                pattern = user_perm.replace('*', '.*').replace('.', r'\.')
                if re.match(f"^{pattern}$", permission):
                    granted.append(user_perm)
                    
        return granted

    def _check_resource_permission(self, user_id: str, permission: str, 
                                 resource: str, user_permissions: Set[str],
                                 context: Optional[AccessContext]) -> PermissionCheck:
        """Check resource-specific permissions"""
        # Resource ownership check
        if context and context.resource_owner == user_id:
            # Users can usually modify their own resources
            if permission.endswith('.read') or permission.endswith('.update'):
                return PermissionCheck(
                    result=PermissionResult.GRANTED,
                    user_id=user_id,
                    permission=permission,
                    resource=resource,
                    reason="resource_owner"
                )

        # Check for resource-specific permissions in database
        # This could be extended for fine-grained resource permissions
        
        return PermissionCheck(
            result=PermissionResult.DENIED,
            user_id=user_id,
            permission=permission,
            resource=resource,
            reason="no_resource_permission"
        )

    def _check_conditional_permissions(self, user_id: str, permission: str,
                                     resource: Optional[str], user_permissions: Set[str],
                                     context: AccessContext) -> PermissionCheck:
        """Check conditional permissions based on context"""
        
        # Time-based restrictions
        if context.time_constraints:
            if not self._check_time_constraints(context.time_constraints):
                return PermissionCheck(
                    result=PermissionResult.DENIED,
                    user_id=user_id,
                    permission=permission,
                    resource=resource,
                    reason="time_constraint_violation"
                )

        # Session type restrictions
        if context.session_type == "passcode":
            # Passcode sessions have limited permissions
            limited_permissions = {
                "calendar.read", "lists.read", "weather.read", "time.read",
                "lights.basic", "music.basic", "ai.chat.basic"
            }
            
            if permission not in limited_permissions:
                return PermissionCheck(
                    result=PermissionResult.DENIED,
                    user_id=user_id,
                    permission=permission,
                    resource=resource,
                    reason="passcode_session_limited"
                )

        # Device-based restrictions
        device_type = context.device_info.get("type")
        if device_type == "touch_panel":
            # Touch panels might have restricted permissions
            if permission.startswith("admin."):
                return PermissionCheck(
                    result=PermissionResult.DENIED,
                    user_id=user_id,
                    permission=permission,
                    resource=resource,
                    reason="admin_not_allowed_on_touch_panel"
                )

        return PermissionCheck(
            result=PermissionResult.DENIED,
            user_id=user_id,
            permission=permission,
            resource=resource,
            reason="no_conditional_match"
        )

    def _check_time_constraints(self, constraints: Dict[str, Any]) -> bool:
        """Check if current time satisfies constraints"""
        now = datetime.now()
        
        # Check allowed hours
        if "allowed_hours" in constraints:
            allowed_start, allowed_end = constraints["allowed_hours"]
            current_hour = now.hour
            if not (allowed_start <= current_hour <= allowed_end):
                return False

        # Check allowed days
        if "allowed_days" in constraints:
            allowed_days = constraints["allowed_days"]  # 0-6, Monday=0
            if now.weekday() not in allowed_days:
                return False

        return True

    def _role_exists(self, role: str) -> bool:
        """Check if role exists"""
        try:
            with auth_db.get_connection() as conn:
                cursor = conn.execute("SELECT 1 FROM roles WHERE role_id = ?", (role,))
                return cursor.fetchone() is not None
        except Exception:
            return False

    def _validate_permissions(self, permissions: List[str]) -> List[str]:
        """Validate permission strings, return list of invalid ones"""
        invalid = []
        valid_patterns = [
            r'^[a-zA-Z][a-zA-Z0-9_]*(\.[a-zA-Z][a-zA-Z0-9_]*)*(\.\*)?$',  # Standard permissions
            r'^\*$'  # Global wildcard
        ]
        
        for permission in permissions:
            if not any(re.match(pattern, permission) for pattern in valid_patterns):
                invalid.append(permission)
                
        return invalid

    def _log_role_change(self, user_id: str, new_role: str, changed_by: str):
        """Log role change for audit"""
        try:
            with auth_db.get_connection() as conn:
                conn.execute("""
                    INSERT INTO audit_logs 
                    (log_id, user_id, action, resource, result, details, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    f"role_change_{int(time.time())}", user_id, "role_assigned",
                    "user_role", "success",
                    json.dumps({"new_role": new_role, "changed_by": changed_by}),
                    datetime.now().isoformat()
                ))
        except Exception as e:
            logger.error(f"Failed to log role change: {e}")

    def _initialize_default_permissions(self):
        """Initialize default permission definitions"""
        default_permissions = [
            # User management
            ("users.read", "View user information", "users", "read"),
            ("users.create", "Create new users", "users", "create"),
            ("users.update", "Update user information", "users", "update"),
            ("users.delete", "Delete users", "users", "delete"),
            ("users.assign_role", "Assign roles to users", "users", "assign_role"),
            
            # Profile management
            ("profile.read", "View own profile", "profile", "read"),
            ("profile.update", "Update own profile", "profile", "update"),
            
            # Calendar permissions
            ("calendar.read", "View calendar events", "calendar", "read"),
            ("calendar.create", "Create calendar events", "calendar", "create"),
            ("calendar.update", "Update calendar events", "calendar", "update"),
            ("calendar.delete", "Delete calendar events", "calendar", "delete"),
            
            # Shared calendar permissions
            ("shared.calendar.read", "View shared calendar", "shared_calendar", "read"),
            ("shared.calendar.create", "Create shared events", "shared_calendar", "create"),
            
            # Lists permissions
            ("lists.read", "View lists", "lists", "read"),
            ("lists.create", "Create lists", "lists", "create"),
            ("lists.update", "Update lists", "lists", "update"),
            ("lists.delete", "Delete lists", "lists", "delete"),
            
            # Shared lists permissions
            ("shared.lists.read", "View shared lists", "shared_lists", "read"),
            ("shared.lists.create", "Create shared lists", "shared_lists", "create"),
            
            # AI permissions
            ("ai.chat", "Chat with AI assistant", "ai", "chat"),
            ("ai.chat.basic", "Basic AI chat (limited)", "ai", "chat_basic"),
            ("ai.chat.supervised", "Supervised AI chat for children", "ai", "chat_supervised"),
            ("ai.assist", "AI assistance features", "ai", "assist"),
            
            # Home automation permissions
            ("homeassistant.basic", "Basic home control", "homeassistant", "basic"),
            ("lights.basic", "Control lights", "lights", "basic"),
            ("music.basic", "Control music", "music", "basic"),
            
            # System permissions
            ("system.monitor", "Monitor system status", "system", "monitor"),
            ("audit.read", "View audit logs", "audit", "read"),
            
            # Role management
            ("roles.read", "View roles", "roles", "read"),
            ("roles.create", "Create custom roles", "roles", "create"),
            ("roles.update", "Update roles", "roles", "update"),
            ("roles.delete", "Delete custom roles", "roles", "delete"),
            
            # Utility permissions
            ("weather.read", "View weather information", "weather", "read"),
            ("time.read", "View time information", "time", "read"),
            ("entertainment.approved", "Access approved entertainment", "entertainment", "approved"),
        ]

        try:
            with auth_db.get_connection() as conn:
                for perm_id, name, resource, action in default_permissions:
                    conn.execute("""
                        INSERT OR IGNORE INTO permissions 
                        (permission_id, name, description, resource, action, created_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (perm_id, name, name, resource, action, datetime.now().isoformat()))

        except Exception as e:
            logger.error(f"Failed to initialize default permissions: {e}")

# Global RBAC manager instance
rbac_manager = RBACManager()

