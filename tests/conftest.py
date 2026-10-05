"""
Pytest Configuration
====================

Shared fixtures and configuration for all tests.
"""

import pytest
import asyncio
import sys
import os

# Add active source paths. services/zoe-core (the dormant Pi brain lane) is
# TypeScript and is not on the Python path; docs/archive no longer exists.
#
# zoe-data ONLY. services/zoe-auth used to be inserted here too (4b8a944b, for
# "auth contract tests" that live under services/zoe-auth/tests and never load
# this conftest), and because it was inserted LAST it sat at sys.path[0]: its
# `models/` package and `main.py` shadowed zoe-data's `models.py` and `main.py`
# for every test under tests/. Alone, tests/unit still passed because nothing
# there imports `models` by that name; any run that collects tests/unit together
# with a services/zoe-data test whose router does `from models import ...`
# (one pytest invocation, as a developer does) failed those tests with
# `ImportError: cannot import name 'PersonCreate' from 'models'` (2026-10-04,
# PR #1827). The zoe-auth lane sets its own PYTHONPATH (validate.yml) and does
# not need this file. Pinned by tests/unit/test_conftest_path_isolation.py.
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'services/zoe-data'))

# Pin the household palace off the live directory for every test under tests/ too (the
# zoe-data suite has its own pin in services/zoe-data/tests/conftest.py). Without it a tests/
# module that imports memory_service opens ~/.mempalace; live_store_guard now refuses that
# from a pytest session, and the pin is what makes the integration lane run against a scratch
# store instead. Reuse the zoe-data pin's directory when both conftests load in one session.
import atexit
import shutil
import tempfile

if "memory_service" not in sys.modules:
    _stores = os.environ.get("ZOE_TEST_STORES_DIR") or tempfile.mkdtemp(prefix="zoe-test-stores-")
    if "ZOE_TEST_STORES_DIR" not in os.environ:
        os.environ["ZOE_TEST_STORES_DIR"] = _stores
        atexit.register(shutil.rmtree, _stores, ignore_errors=True)
    os.environ["MEMPALACE_DATA_DIR"] = os.path.join(_stores, "mempalace")
    os.environ["ZOE_MEMORY_REJECT_LEDGER"] = os.path.join(_stores, "memory-reject-ledger.json")


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
