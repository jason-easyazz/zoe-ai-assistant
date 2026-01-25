"""
Utilities Package
=================

Shared utilities for error handling, retries, and common operations.
"""

from .retry import retry, RetryConfig, RetryError
from .errors import (
    MusicError,
    MusicProviderError,
    MusicAuthError,
    MusicStreamError,
    MusicQueueError,
    MusicPlaybackError,
    CastingError,
    DeviceNotFoundError,
    DeviceOfflineError,
    HouseholdError
)

__all__ = [
    # Retry
    "retry",
    "RetryConfig",
    "RetryError",
    # Errors
    "MusicError",
    "MusicProviderError",
    "MusicAuthError",
    "MusicStreamError",
    "MusicQueueError",
    "MusicPlaybackError",
    "CastingError",
    "DeviceNotFoundError",
    "DeviceOfflineError",
    "HouseholdError"
]

