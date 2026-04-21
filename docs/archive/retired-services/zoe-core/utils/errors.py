"""
Custom Exceptions
=================

Defines custom exceptions for the music and household systems.
Provides structured error handling and user-friendly messages.
"""

from typing import Optional, Dict, Any


class ZoeError(Exception):
    """Base exception for all Zoe errors."""
    
    # HTTP status code for API responses
    status_code: int = 500
    
    # Error code for client identification
    error_code: str = "INTERNAL_ERROR"
    
    # Whether this error should be retried
    retryable: bool = False
    
    def __init__(
        self,
        message: str,
        details: Optional[Dict[str, Any]] = None,
        cause: Optional[Exception] = None
    ):
        super().__init__(message)
        self.message = message
        self.details = details or {}
        self.cause = cause
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API response."""
        return {
            "error": self.error_code,
            "message": self.message,
            "details": self.details,
            "retryable": self.retryable
        }


# ========================================
# Music Errors
# ========================================

class MusicError(ZoeError):
    """Base exception for music-related errors."""
    error_code = "MUSIC_ERROR"


class MusicProviderError(MusicError):
    """Error from a music provider (YouTube, Spotify, etc.)."""
    status_code = 502
    error_code = "PROVIDER_ERROR"
    retryable = True
    
    def __init__(
        self,
        message: str,
        provider: str,
        original_error: Optional[str] = None,
        **kwargs
    ):
        super().__init__(message, **kwargs)
        self.provider = provider
        self.details["provider"] = provider
        if original_error:
            self.details["original_error"] = original_error


class MusicAuthError(MusicError):
    """Authentication error with music provider."""
    status_code = 401
    error_code = "MUSIC_AUTH_ERROR"
    retryable = False
    
    def __init__(
        self,
        message: str = "Music provider authentication failed",
        provider: Optional[str] = None,
        **kwargs
    ):
        super().__init__(message, **kwargs)
        if provider:
            self.details["provider"] = provider


class MusicStreamError(MusicError):
    """Error getting or playing audio stream."""
    status_code = 502
    error_code = "STREAM_ERROR"
    retryable = True
    
    def __init__(
        self,
        message: str = "Failed to get audio stream",
        track_id: Optional[str] = None,
        reason: Optional[str] = None,
        **kwargs
    ):
        super().__init__(message, **kwargs)
        if track_id:
            self.details["track_id"] = track_id
        if reason:
            self.details["reason"] = reason


class MusicQueueError(MusicError):
    """Error with music queue operations."""
    status_code = 400
    error_code = "QUEUE_ERROR"
    retryable = False
    
    def __init__(
        self,
        message: str = "Queue operation failed",
        operation: Optional[str] = None,
        **kwargs
    ):
        super().__init__(message, **kwargs)
        if operation:
            self.details["operation"] = operation


class MusicPlaybackError(MusicError):
    """Error during music playback."""
    status_code = 500
    error_code = "PLAYBACK_ERROR"
    retryable = True
    
    def __init__(
        self,
        message: str = "Playback error",
        state: Optional[str] = None,
        **kwargs
    ):
        super().__init__(message, **kwargs)
        if state:
            self.details["state"] = state


class TrackNotFoundError(MusicError):
    """Track could not be found."""
    status_code = 404
    error_code = "TRACK_NOT_FOUND"
    retryable = False
    
    def __init__(
        self,
        message: str = "Track not found",
        track_id: Optional[str] = None,
        provider: Optional[str] = None,
        **kwargs
    ):
        super().__init__(message, **kwargs)
        if track_id:
            self.details["track_id"] = track_id
        if provider:
            self.details["provider"] = provider


class PlaylistNotFoundError(MusicError):
    """Playlist could not be found."""
    status_code = 404
    error_code = "PLAYLIST_NOT_FOUND"
    retryable = False


class StreamExpiredError(MusicStreamError):
    """Audio stream URL has expired."""
    error_code = "STREAM_EXPIRED"
    retryable = True
    
    def __init__(
        self,
        track_id: Optional[str] = None,
        **kwargs
    ):
        super().__init__(
            message="Stream URL has expired",
            track_id=track_id,
            reason="expired",
            **kwargs
        )


class RateLimitError(MusicProviderError):
    """Hit rate limit on music provider."""
    status_code = 429
    error_code = "RATE_LIMITED"
    retryable = True
    
    def __init__(
        self,
        provider: str,
        retry_after: Optional[int] = None,
        **kwargs
    ):
        super().__init__(
            message=f"Rate limited by {provider}",
            provider=provider,
            **kwargs
        )
        if retry_after:
            self.details["retry_after"] = retry_after


# ========================================
# Casting/Output Errors
# ========================================

class CastingError(ZoeError):
    """Base exception for casting/output errors."""
    error_code = "CASTING_ERROR"
    retryable = True


class DeviceNotFoundError(CastingError):
    """Target device could not be found."""
    status_code = 404
    error_code = "DEVICE_NOT_FOUND"
    retryable = False
    
    def __init__(
        self,
        message: str = "Device not found",
        device_id: Optional[str] = None,
        device_name: Optional[str] = None,
        **kwargs
    ):
        super().__init__(message, **kwargs)
        if device_id:
            self.details["device_id"] = device_id
        if device_name:
            self.details["device_name"] = device_name


class DeviceOfflineError(CastingError):
    """Device is offline or unreachable."""
    status_code = 503
    error_code = "DEVICE_OFFLINE"
    retryable = True
    
    def __init__(
        self,
        device_name: str,
        **kwargs
    ):
        super().__init__(
            message=f"Device '{device_name}' is offline",
            **kwargs
        )
        self.details["device_name"] = device_name


class CastConnectionError(CastingError):
    """Could not connect to cast device."""
    status_code = 502
    error_code = "CAST_CONNECTION_ERROR"
    retryable = True


class CastPlaybackError(CastingError):
    """Error during cast playback."""
    error_code = "CAST_PLAYBACK_ERROR"
    retryable = True


# ========================================
# Household Errors
# ========================================

class HouseholdError(ZoeError):
    """Base exception for household errors."""
    error_code = "HOUSEHOLD_ERROR"


class HouseholdNotFoundError(HouseholdError):
    """Household could not be found."""
    status_code = 404
    error_code = "HOUSEHOLD_NOT_FOUND"
    retryable = False


class NotHouseholdMemberError(HouseholdError):
    """User is not a member of the household."""
    status_code = 403
    error_code = "NOT_HOUSEHOLD_MEMBER"
    retryable = False


class InsufficientPermissionsError(HouseholdError):
    """User lacks permissions for this action."""
    status_code = 403
    error_code = "INSUFFICIENT_PERMISSIONS"
    retryable = False
    
    def __init__(
        self,
        message: str = "Insufficient permissions",
        required_role: Optional[str] = None,
        **kwargs
    ):
        super().__init__(message, **kwargs)
        if required_role:
            self.details["required_role"] = required_role


class DeviceBindingError(HouseholdError):
    """Error with device binding operations."""
    error_code = "DEVICE_BINDING_ERROR"


# ========================================
# Voice Errors
# ========================================

class VoiceError(ZoeError):
    """Base exception for voice-related errors."""
    error_code = "VOICE_ERROR"


class WakeWordError(VoiceError):
    """Error with wake word detection."""
    error_code = "WAKE_WORD_ERROR"


class SpeechRecognitionError(VoiceError):
    """Error with speech recognition."""
    error_code = "SPEECH_RECOGNITION_ERROR"
    retryable = True


class TextToSpeechError(VoiceError):
    """Error with text-to-speech."""
    error_code = "TTS_ERROR"
    retryable = True


# ========================================
# Error Handler Utilities
# ========================================

def map_provider_error(provider: str, error: Exception) -> MusicProviderError:
    """Map a provider-specific exception to our error types."""
    error_str = str(error)
    
    # Check for common patterns
    if "401" in error_str or "unauthorized" in error_str.lower():
        return MusicAuthError(
            f"Authentication failed for {provider}",
            provider=provider,
            cause=error
        )
    
    if "429" in error_str or "rate limit" in error_str.lower():
        return RateLimitError(provider=provider, cause=error)
    
    if "404" in error_str or "not found" in error_str.lower():
        return TrackNotFoundError(provider=provider, cause=error)
    
    if "timeout" in error_str.lower():
        return MusicProviderError(
            f"Timeout connecting to {provider}",
            provider=provider,
            original_error=error_str,
            cause=error
        )
    
    # Default provider error
    return MusicProviderError(
        f"Error from {provider}: {error}",
        provider=provider,
        original_error=error_str,
        cause=error
    )

