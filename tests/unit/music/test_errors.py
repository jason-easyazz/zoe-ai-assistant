"""
Unit Tests for Custom Errors
============================

Tests custom exception classes and error handling.
"""

import pytest

import sys
sys.path.insert(0, '/home/zoe/assistant/services/zoe-core')

from utils.errors import (
    ZoeError,
    MusicError,
    MusicProviderError,
    MusicAuthError,
    MusicStreamError,
    MusicQueueError,
    TrackNotFoundError,
    StreamExpiredError,
    RateLimitError,
    CastingError,
    DeviceNotFoundError,
    DeviceOfflineError,
    HouseholdError,
    map_provider_error
)


class TestZoeError:
    """Tests for base ZoeError."""
    
    def test_basic_error(self):
        """Test basic error creation."""
        error = ZoeError("Something went wrong")
        
        assert str(error) == "Something went wrong"
        assert error.message == "Something went wrong"
        assert error.status_code == 500
        assert error.error_code == "INTERNAL_ERROR"
        assert error.retryable is False
    
    def test_error_with_details(self):
        """Test error with additional details."""
        error = ZoeError(
            "Error occurred",
            details={"key": "value", "count": 42}
        )
        
        assert error.details["key"] == "value"
        assert error.details["count"] == 42
    
    def test_error_with_cause(self):
        """Test error wrapping another exception."""
        original = ValueError("Original error")
        error = ZoeError("Wrapped error", cause=original)
        
        assert error.cause is original
    
    def test_to_dict(self):
        """Test converting error to dictionary."""
        error = ZoeError(
            "Test error",
            details={"info": "test"}
        )
        
        result = error.to_dict()
        
        assert result["error"] == "INTERNAL_ERROR"
        assert result["message"] == "Test error"
        assert result["details"]["info"] == "test"
        assert result["retryable"] is False


class TestMusicErrors:
    """Tests for music-specific errors."""
    
    def test_music_provider_error(self):
        """Test MusicProviderError."""
        error = MusicProviderError(
            "YouTube Music error",
            provider="youtube_music",
            original_error="API returned 500"
        )
        
        assert error.status_code == 502
        assert error.error_code == "PROVIDER_ERROR"
        assert error.retryable is True
        assert error.provider == "youtube_music"
        assert error.details["provider"] == "youtube_music"
        assert error.details["original_error"] == "API returned 500"
    
    def test_music_auth_error(self):
        """Test MusicAuthError."""
        error = MusicAuthError(provider="spotify")
        
        assert error.status_code == 401
        assert error.error_code == "MUSIC_AUTH_ERROR"
        assert error.retryable is False
        assert error.details["provider"] == "spotify"
    
    def test_music_stream_error(self):
        """Test MusicStreamError."""
        error = MusicStreamError(
            track_id="abc123",
            reason="unavailable"
        )
        
        assert error.status_code == 502
        assert error.error_code == "STREAM_ERROR"
        assert error.retryable is True
        assert error.details["track_id"] == "abc123"
        assert error.details["reason"] == "unavailable"
    
    def test_track_not_found_error(self):
        """Test TrackNotFoundError."""
        error = TrackNotFoundError(
            track_id="xyz789",
            provider="youtube_music"
        )
        
        assert error.status_code == 404
        assert error.error_code == "TRACK_NOT_FOUND"
        assert error.retryable is False
    
    def test_stream_expired_error(self):
        """Test StreamExpiredError."""
        error = StreamExpiredError(track_id="abc123")
        
        assert error.error_code == "STREAM_EXPIRED"
        assert error.retryable is True
        assert error.details["reason"] == "expired"
    
    def test_rate_limit_error(self):
        """Test RateLimitError."""
        error = RateLimitError(
            provider="spotify",
            retry_after=30
        )
        
        assert error.status_code == 429
        assert error.error_code == "RATE_LIMITED"
        assert error.retryable is True
        assert error.details["retry_after"] == 30


class TestCastingErrors:
    """Tests for casting-related errors."""
    
    def test_device_not_found_error(self):
        """Test DeviceNotFoundError."""
        error = DeviceNotFoundError(
            device_name="Living Room Speaker"
        )
        
        assert error.status_code == 404
        assert error.error_code == "DEVICE_NOT_FOUND"
        assert error.retryable is False
        assert error.details["device_name"] == "Living Room Speaker"
    
    def test_device_offline_error(self):
        """Test DeviceOfflineError."""
        error = DeviceOfflineError("Kitchen Display")
        
        assert error.status_code == 503
        assert error.error_code == "DEVICE_OFFLINE"
        assert error.retryable is True
        assert "Kitchen Display" in error.message


class TestErrorMapping:
    """Tests for error mapping utility."""
    
    def test_map_401_to_auth_error(self):
        """Test mapping 401 errors to MusicAuthError."""
        original = Exception("401 Unauthorized")
        
        result = map_provider_error("spotify", original)
        
        assert isinstance(result, MusicAuthError)
        assert result.cause is original
    
    def test_map_429_to_rate_limit(self):
        """Test mapping 429 errors to RateLimitError."""
        original = Exception("429 Rate Limited")
        
        result = map_provider_error("youtube_music", original)
        
        assert isinstance(result, RateLimitError)
    
    def test_map_404_to_not_found(self):
        """Test mapping 404 errors to TrackNotFoundError."""
        original = Exception("404 not found")
        
        result = map_provider_error("apple_music", original)
        
        assert isinstance(result, TrackNotFoundError)
    
    def test_map_timeout(self):
        """Test mapping timeout errors."""
        original = Exception("Request timeout after 30s")
        
        result = map_provider_error("spotify", original)
        
        assert isinstance(result, MusicProviderError)
        assert "Timeout" in result.message
    
    def test_map_generic_error(self):
        """Test mapping unknown errors."""
        original = Exception("Something weird happened")
        
        result = map_provider_error("youtube_music", original)
        
        assert isinstance(result, MusicProviderError)
        assert result.provider == "youtube_music"


class TestErrorInheritance:
    """Tests for error class hierarchy."""
    
    def test_music_errors_inherit_from_zoe_error(self):
        """Test music errors inherit from ZoeError."""
        assert issubclass(MusicError, ZoeError)
        assert issubclass(MusicProviderError, MusicError)
        assert issubclass(MusicAuthError, MusicError)
        assert issubclass(MusicStreamError, MusicError)
    
    def test_casting_errors_inherit_from_zoe_error(self):
        """Test casting errors inherit from ZoeError."""
        assert issubclass(CastingError, ZoeError)
        assert issubclass(DeviceNotFoundError, CastingError)
        assert issubclass(DeviceOfflineError, CastingError)
    
    def test_household_errors_inherit_from_zoe_error(self):
        """Test household errors inherit from ZoeError."""
        assert issubclass(HouseholdError, ZoeError)
    
    def test_catch_all_zoe_errors(self):
        """Test catching all ZoeError subclasses."""
        errors = [
            MusicProviderError("test", provider="test"),
            MusicAuthError(),
            DeviceNotFoundError(),
            HouseholdError("test")
        ]
        
        for error in errors:
            try:
                raise error
            except ZoeError as e:
                assert e is error  # Should catch all

