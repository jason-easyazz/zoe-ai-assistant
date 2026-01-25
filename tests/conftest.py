"""
Pytest Configuration
====================

Shared fixtures and configuration for all tests.
"""

import pytest
import asyncio
import sys
import os

# Add source paths
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../services/zoe-core'))


@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def mock_db():
    """Create a mock database connection."""
    import tempfile
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    yield path
    os.unlink(path)


@pytest.fixture
def mock_user():
    """Return a mock user for testing."""
    return {
        "id": "test_user_123",
        "username": "testuser",
        "email": "test@example.com"
    }


@pytest.fixture
def mock_track():
    """Return a mock track for testing."""
    return {
        "track_id": "test_track_123",
        "provider": "youtube_music",
        "title": "Test Song",
        "artist": "Test Artist",
        "album": "Test Album",
        "duration_ms": 180000,
        "album_art_url": "https://example.com/art.jpg"
    }


@pytest.fixture
def mock_playlist():
    """Return a mock playlist for testing."""
    return {
        "id": "test_playlist_123",
        "name": "Test Playlist",
        "track_count": 10,
        "user_id": "test_user_123"
    }


@pytest.fixture
def mock_device():
    """Return a mock device for testing."""
    return {
        "id": "test_device_123",
        "name": "Test Speaker",
        "type": "speaker",
        "room": "Living Room",
        "is_online": True,
        "capabilities": ["audio"]
    }


@pytest.fixture
def mock_household():
    """Return a mock household for testing."""
    return {
        "id": "test_household_123",
        "name": "Test Family",
        "owner_id": "test_user_123",
        "members": [
            {"user_id": "test_user_123", "role": "owner"},
            {"user_id": "test_user_456", "role": "member"}
        ]
    }
