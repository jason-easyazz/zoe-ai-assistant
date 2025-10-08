"""
RBAC System Tests
Unit tests for role-based access control functionality
"""

import pytest
import tempfile
import os
from unittest.mock import patch, MagicMock

from ..core.rbac import RBACManager, PermissionCheck, PermissionResult, AccessContext
from ..models.database import AuthDatabase

@pytest.fixture
def temp_db():
    """Create temporary database for testing"""
    fd, path = tempfile.mkstemp()
    os.close(fd)
    
    # Initialize with temp database
    test_db = AuthDatabase(path)
    yield test_db
    
    # Cleanup
    os.unlink(path)

@pytest.fixture
def rbac_manager(temp_db):
    """Create RBAC manager with test database"""
    return RBACManager()

@pytest.fixture
def test_user(temp_db):
    """Create test user with specific role"""
    from ..core.auth import AuthManager
    auth_manager = AuthManager()
    
    success, user_id = auth_manager.create_user(
        username="testuser",
        email="test@example.com",
        password="SecurePass123!",
        role="user"
    )
    
    return user_id

class TestRBACManager:
    """Test role-based access control functionality"""

    def test_check_permission_granted(self, rbac_manager, test_user):
        """Test permission check for granted permission"""
        result = rbac_manager.check_permission(test_user, "calendar.read")
        assert result.result == PermissionResult.GRANTED
        assert result.user_id == test_user

    def test_check_permission_denied(self, rbac_manager, test_user):
        """Test permission check for denied permission"""
        result = rbac_manager.check_permission(test_user, "admin.delete_user")
        assert result.result == PermissionResult.DENIED
        assert "no_matching_permission" in result.reason

    def test_check_wildcard_permission(self, rbac_manager):
        """Test wildcard permission matching"""
        # Create admin user
        from ..core.auth import AuthManager
        auth_manager = AuthManager()
        success, admin_id = auth_manager.create_user(
            username="admin",
            email="admin@example.com",
            password="AdminPass123!",
            role="admin"
        )
        
        # Admin should have calendar.* permission
        result = rbac_manager.check_permission(admin_id, "calendar.delete")
        assert result.result == PermissionResult.GRANTED

    def test_role_assignment(self, rbac_manager, test_user):
        """Test role assignment"""
        # Initially user role
        role = rbac_manager.get_user_role(test_user)
        assert role == "user"
        
        # Change to admin role (would need admin permission in real scenario)
        success = rbac_manager.assign_role(test_user, "admin", "admin_user")
        assert success is True
        
        # Verify role changed
        role = rbac_manager.get_user_role(test_user)
        assert role == "admin"

    def test_permission_inheritance(self, rbac_manager):
        """Test permission inheritance from parent roles"""
        # Family role inherits from user role
        family_permissions = rbac_manager.get_role_permissions("family")
        user_permissions = rbac_manager.get_role_permissions("user")
        
        # Family should have all user permissions plus additional ones
        assert user_permissions.issubset(family_permissions)
        assert "shared.calendar.read" in family_permissions

    def test_multiple_permissions_require_all(self, rbac_manager, test_user):
        """Test checking multiple permissions with require_all=True"""
        permissions = ["calendar.read", "lists.read", "admin.delete_user"]
        results = rbac_manager.check_multiple_permissions(
            test_user, permissions, require_all=True
        )
        
        assert results["calendar.read"].result == PermissionResult.GRANTED
        assert results["lists.read"].result == PermissionResult.GRANTED
        assert results["admin.delete_user"].result == PermissionResult.DENIED

    def test_multiple_permissions_require_any(self, rbac_manager, test_user):
        """Test checking multiple permissions with require_all=False"""
        permissions = ["calendar.read", "admin.delete_user"]
        results = rbac_manager.check_multiple_permissions(
            test_user, permissions, require_all=False
        )
        
        # Should grant access if any permission is granted
        assert results["calendar.read"].result == PermissionResult.GRANTED

    def test_create_custom_role(self, rbac_manager):
        """Test creating custom role"""
        success = rbac_manager.create_custom_role(
            role_id="custom_role",
            name="Custom Role",
            description="Test custom role",
            permissions=["calendar.read", "lists.read"],
            created_by="admin"
        )
        
        assert success is True
        
        # Verify role has correct permissions
        permissions = rbac_manager.get_role_permissions("custom_role")
        assert "calendar.read" in permissions
        assert "lists.read" in permissions

    def test_permission_caching(self, rbac_manager, test_user):
        """Test permission caching functionality"""
        # First check should load from database
        result1 = rbac_manager.check_permission(test_user, "calendar.read")
        
        # Second check should use cache
        result2 = rbac_manager.check_permission(test_user, "calendar.read")
        
        assert result1.result == result2.result
        
        # Cache should contain user permissions
        cached_permissions = rbac_manager.permission_cache.get_permissions(test_user)
        assert cached_permissions is not None
        assert "calendar.read" in cached_permissions

    def test_cache_invalidation(self, rbac_manager, test_user):
        """Test cache invalidation when roles change"""
        # Initial permission check
        rbac_manager.check_permission(test_user, "calendar.read")
        
        # Change user role
        rbac_manager.assign_role(test_user, "admin", "admin_user")
        
        # Cache should be invalidated - verify by checking admin permission
        result = rbac_manager.check_permission(test_user, "users.create")
        assert result.result == PermissionResult.GRANTED

class TestAccessContext:
    """Test access context and conditional permissions"""

    def test_resource_owner_permission(self, rbac_manager, test_user):
        """Test resource ownership permissions"""
        context = AccessContext(
            user_id=test_user,
            session_type="standard",
            device_info={"type": "web"},
            resource_owner=test_user  # User owns the resource
        )
        
        result = rbac_manager.check_permission(
            test_user, "calendar.update", 
            resource=f"user.{test_user}.calendar.event.123",
            context=context
        )
        
        # Should be granted based on resource ownership
        assert result.result == PermissionResult.GRANTED

    def test_session_type_restrictions(self, rbac_manager, test_user):
        """Test session type restrictions"""
        context = AccessContext(
            user_id=test_user,
            session_type="passcode",  # Limited session
            device_info={"type": "touch_panel"}
        )
        
        # Admin operations should be denied for passcode sessions
        result = rbac_manager.check_permission(
            test_user, "admin.create_user",
            context=context
        )
        
        assert result.result == PermissionResult.DENIED
        assert "passcode_session_limited" in result.reason

    def test_device_type_restrictions(self, rbac_manager, test_user):
        """Test device type restrictions"""
        context = AccessContext(
            user_id=test_user,
            session_type="standard",
            device_info={"type": "touch_panel"}
        )
        
        # Admin operations not allowed on touch panels
        result = rbac_manager.check_permission(
            test_user, "admin.system_config",
            context=context
        )
        
        assert result.result == PermissionResult.DENIED
        assert "admin_not_allowed_on_touch_panel" in result.reason

    def test_time_constraints(self, rbac_manager, test_user):
        """Test time-based permission constraints"""
        # Mock current time to be outside allowed hours
        with patch('datetime.datetime') as mock_datetime:
            # Mock time to be 3 AM (outside normal hours)
            mock_datetime.now.return_value.hour = 3
            
            context = AccessContext(
                user_id=test_user,
                session_type="standard",
                device_info={"type": "web"},
                time_constraints={"allowed_hours": (8, 22)}  # 8 AM to 10 PM
            )
            
            result = rbac_manager.check_permission(
                test_user, "some_restricted_permission",
                context=context
            )
            
            assert result.result == PermissionResult.DENIED
            assert "time_constraint_violation" in result.reason

class TestPermissionValidation:
    """Test permission string validation"""

    def test_valid_permission_format(self, rbac_manager):
        """Test validation of valid permission strings"""
        valid_permissions = [
            "calendar.read",
            "users.create",
            "admin.system.monitor",
            "calendar.*",
            "*"
        ]
        
        invalid_perms = rbac_manager._validate_permissions(valid_permissions)
        assert len(invalid_perms) == 0

    def test_invalid_permission_format(self, rbac_manager):
        """Test validation of invalid permission strings"""
        invalid_permissions = [
            "invalid-permission",
            ".start_with_dot",
            "end.with.dot.",
            "has spaces",
            "has@symbols"
        ]
        
        invalid_perms = rbac_manager._validate_permissions(invalid_permissions)
        assert len(invalid_perms) == len(invalid_permissions)

class TestRoleHierarchy:
    """Test role hierarchy and inheritance"""

    def test_default_roles_exist(self, rbac_manager):
        """Test that default system roles exist"""
        system_roles = ["admin", "user", "family", "child", "guest"]
        
        for role in system_roles:
            permissions = rbac_manager.get_role_permissions(role)
            assert len(permissions) > 0

    def test_admin_has_all_permissions(self, rbac_manager):
        """Test that admin role has comprehensive permissions"""
        admin_permissions = rbac_manager.get_role_permissions("admin")
        
        # Admin should have user management permissions
        required_admin_perms = [
            "users.read", "users.create", "users.update", "users.delete",
            "roles.read", "system.monitor", "audit.read"
        ]
        
        for perm in required_admin_perms:
            assert perm in admin_permissions

    def test_child_role_restrictions(self, rbac_manager):
        """Test that child role has appropriate restrictions"""
        child_permissions = rbac_manager.get_role_permissions("child")
        
        # Child should not have admin permissions
        admin_perms = ["users.create", "users.delete", "system.config"]
        for perm in admin_perms:
            assert perm not in child_permissions
        
        # Child should have supervised AI access
        assert "ai.chat.supervised" in child_permissions

    def test_guest_role_minimal_permissions(self, rbac_manager):
        """Test that guest role has minimal permissions"""
        guest_permissions = rbac_manager.get_role_permissions("guest")
        
        # Guest should only have basic read permissions
        expected_guest_perms = ["weather.read", "time.read", "music.basic", "lights.basic"]
        
        for perm in expected_guest_perms:
            assert perm in guest_permissions
        
        # Guest should not have write permissions
        write_perms = ["calendar.create", "lists.create", "users.update"]
        for perm in write_perms:
            assert perm not in guest_permissions

if __name__ == "__main__":
    pytest.main([__file__])

