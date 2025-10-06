"""
Authentication System Tests
Unit tests for password and passcode authentication
"""

import pytest
import tempfile
import os
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

from ..core.auth import AuthManager, PasswordPolicy
from ..core.passcode import PasscodeManager, PasscodePolicy
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
def auth_manager(temp_db):
    """Create auth manager with test database"""
    return AuthManager()

@pytest.fixture
def passcode_manager(temp_db):
    """Create passcode manager with test database"""
    return PasscodeManager()

class TestAuthManager:
    """Test authentication manager functionality"""

    def test_create_user_success(self, auth_manager):
        """Test successful user creation"""
        success, user_id = auth_manager.create_user(
            username="testuser",
            email="test@example.com",
            password="SecurePass123!",
            role="user"
        )
        
        assert success is True
        assert user_id == "testuser"

    def test_create_user_invalid_password(self, auth_manager):
        """Test user creation with invalid password"""
        success, message = auth_manager.create_user(
            username="testuser",
            email="test@example.com", 
            password="weak",
            role="user"
        )
        
        assert success is False
        assert "Password must be at least 8 characters" in message

    def test_create_duplicate_user(self, auth_manager):
        """Test creation of duplicate user"""
        # Create first user
        auth_manager.create_user(
            username="testuser",
            email="test@example.com",
            password="SecurePass123!",
            role="user"
        )
        
        # Try to create duplicate
        success, message = auth_manager.create_user(
            username="testuser",
            email="test2@example.com",
            password="SecurePass123!",
            role="user"
        )
        
        assert success is False
        assert "already exists" in message

    def test_verify_password_success(self, auth_manager):
        """Test successful password verification"""
        # Create user
        auth_manager.create_user(
            username="testuser",
            email="test@example.com",
            password="SecurePass123!",
            role="user"
        )
        
        # Verify password
        result = auth_manager.verify_password("testuser", "SecurePass123!")
        assert result.success is True
        assert result.user_id == "testuser"

    def test_verify_password_failure(self, auth_manager):
        """Test failed password verification"""
        # Create user
        auth_manager.create_user(
            username="testuser",
            email="test@example.com",
            password="SecurePass123!",
            role="user"
        )
        
        # Verify wrong password
        result = auth_manager.verify_password("testuser", "WrongPassword")
        assert result.success is False
        assert "Invalid password" in result.error_message

    def test_password_lockout(self, auth_manager):
        """Test account lockout after failed attempts"""
        # Create user
        auth_manager.create_user(
            username="testuser",
            email="test@example.com",
            password="SecurePass123!",
            role="user"
        )
        
        # Multiple failed attempts
        for i in range(5):
            result = auth_manager.verify_password("testuser", "WrongPassword")
            assert result.success is False
        
        # Should be locked now
        result = auth_manager.verify_password("testuser", "WrongPassword")
        assert result.success is False
        assert result.locked_until is not None

    def test_change_password_success(self, auth_manager):
        """Test successful password change"""
        # Create user
        auth_manager.create_user(
            username="testuser",
            email="test@example.com",
            password="SecurePass123!",
            role="user"
        )
        
        # Change password
        success, message = auth_manager.change_password(
            user_id="testuser",
            current_password="SecurePass123!",
            new_password="NewSecurePass456!"
        )
        
        assert success is True
        
        # Verify new password works
        result = auth_manager.verify_password("testuser", "NewSecurePass456!")
        assert result.success is True

    def test_change_password_wrong_current(self, auth_manager):
        """Test password change with wrong current password"""
        # Create user
        auth_manager.create_user(
            username="testuser",
            email="test@example.com",
            password="SecurePass123!",
            role="user"
        )
        
        # Try to change with wrong current password
        success, message = auth_manager.change_password(
            user_id="testuser",
            current_password="WrongCurrent",
            new_password="NewSecurePass456!"
        )
        
        assert success is False
        assert "Current password is incorrect" in message

class TestPasscodeManager:
    """Test passcode authentication functionality"""

    def test_create_passcode_success(self, passcode_manager):
        """Test successful passcode creation"""
        success, message = passcode_manager.create_passcode(
            user_id="testuser",
            passcode="1234"
        )
        
        assert success is True
        assert "successfully" in message

    def test_create_passcode_too_short(self, passcode_manager):
        """Test passcode creation with invalid length"""
        success, message = passcode_manager.create_passcode(
            user_id="testuser",
            passcode="12"
        )
        
        assert success is False
        assert "4-8 digits" in message

    def test_create_passcode_non_numeric(self, passcode_manager):
        """Test passcode creation with non-numeric characters"""
        success, message = passcode_manager.create_passcode(
            user_id="testuser",
            passcode="12ab"
        )
        
        assert success is False
        assert "only digits" in message

    def test_create_passcode_common_pattern(self, passcode_manager):
        """Test rejection of common passcode patterns"""
        success, message = passcode_manager.create_passcode(
            user_id="testuser",
            passcode="1234"
        )
        
        assert success is False
        assert "too common" in message

    def test_verify_passcode_success(self, passcode_manager):
        """Test successful passcode verification"""
        # Create unique passcode
        passcode_manager.create_passcode("testuser", "2468")
        
        # Verify passcode
        result = passcode_manager.verify_passcode("testuser", "2468")
        assert result.is_valid is True
        assert result.user_id == "testuser"

    def test_verify_passcode_failure(self, passcode_manager):
        """Test failed passcode verification"""
        # Create passcode
        passcode_manager.create_passcode("testuser", "2468")
        
        # Verify wrong passcode
        result = passcode_manager.verify_passcode("testuser", "1357")
        assert result.is_valid is False
        assert "Invalid passcode" in result.error_message

    def test_passcode_lockout(self, passcode_manager):
        """Test passcode lockout after failed attempts"""
        # Create passcode
        passcode_manager.create_passcode("testuser", "2468")
        
        # Multiple failed attempts
        for i in range(5):
            result = passcode_manager.verify_passcode("testuser", "0000")
            assert result.is_valid is False
        
        # Should be locked now
        result = passcode_manager.verify_passcode("testuser", "0000")
        assert result.is_valid is False
        assert result.locked_until is not None

    def test_disable_passcode(self, passcode_manager):
        """Test passcode disabling"""
        # Create passcode
        passcode_manager.create_passcode("testuser", "2468")
        
        # Disable passcode
        success = passcode_manager.disable_passcode("testuser")
        assert success is True
        
        # Verify passcode no longer works
        result = passcode_manager.verify_passcode("testuser", "2468")
        assert result.is_valid is False

    def test_passcode_info(self, passcode_manager):
        """Test getting passcode information"""
        # No passcode initially
        info = passcode_manager.get_passcode_info("testuser")
        assert info["has_passcode"] is False
        
        # Create passcode
        passcode_manager.create_passcode("testuser", "2468")
        
        # Check info
        info = passcode_manager.get_passcode_info("testuser")
        assert info["has_passcode"] is True
        assert info["is_active"] is True
        assert info["failed_attempts"] == 0

class TestPasswordPolicy:
    """Test password policy enforcement"""

    def test_password_policy_length(self):
        """Test password length requirements"""
        policy = PasswordPolicy(min_length=10)
        auth_manager = AuthManager(policy)
        
        success, message = auth_manager.create_user(
            username="testuser",
            email="test@example.com",
            password="Short1!",  # Only 7 characters
            role="user"
        )
        
        assert success is False
        assert "at least 10 characters" in message

    def test_password_policy_complexity(self):
        """Test password complexity requirements"""
        policy = PasswordPolicy(
            require_uppercase=True,
            require_lowercase=True,
            require_numbers=True,
            require_special=True
        )
        auth_manager = AuthManager(policy)
        
        # Test missing uppercase
        success, message = auth_manager.create_user(
            username="testuser1",
            email="test1@example.com",
            password="lowercase123!",
            role="user"
        )
        assert success is False
        assert "uppercase" in message

        # Test missing special character
        success, message = auth_manager.create_user(
            username="testuser2",
            email="test2@example.com",
            password="Password123",
            role="user"
        )
        assert success is False
        assert "special character" in message

    def test_password_policy_common_passwords(self):
        """Test rejection of common passwords"""
        policy = PasswordPolicy(prevent_common=True)
        auth_manager = AuthManager(policy)
        
        success, message = auth_manager.create_user(
            username="testuser",
            email="test@example.com",
            password="password",
            role="user"
        )
        
        assert success is False
        assert "too common" in message

class TestPasscodePolicy:
    """Test passcode policy enforcement"""

    def test_passcode_policy_length(self):
        """Test passcode length requirements"""
        policy = PasscodePolicy(min_length=6, max_length=8)
        passcode_manager = PasscodeManager(policy)
        
        success, message = passcode_manager.create_passcode("testuser", "1234")
        assert success is False
        assert "6-8 digits" in message

    def test_passcode_policy_uniqueness(self):
        """Test passcode uniqueness requirement"""
        policy = PasscodePolicy(require_unique=True)
        passcode_manager = PasscodeManager(policy)
        
        # Create first passcode
        passcode_manager.create_passcode("user1", "1357")
        
        # Try to create same passcode for different user
        success, message = passcode_manager.create_passcode("user2", "1357")
        assert success is False
        assert "already in use" in message

    def test_passcode_policy_expiry(self):
        """Test passcode expiry"""
        expiry_time = datetime.now() + timedelta(days=1)
        
        passcode_manager = PasscodeManager()
        passcode_manager.create_passcode("testuser", "2468", expiry_time)
        
        # Should work now
        result = passcode_manager.verify_passcode("testuser", "2468")
        assert result.is_valid is True
        
        # Mock expired passcode
        with patch('datetime.datetime') as mock_datetime:
            mock_datetime.now.return_value = expiry_time + timedelta(hours=1)
            result = passcode_manager.verify_passcode("testuser", "2468")
            assert result.is_valid is False
            assert "expired" in result.error_message

if __name__ == "__main__":
    pytest.main([__file__])

