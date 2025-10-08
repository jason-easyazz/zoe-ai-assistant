import os
import pytest
from fastapi.testclient import TestClient
import jwt
from datetime import datetime, timedelta
import sys

sys.path.insert(0, "/home/pi/zoe/services/zoe-core")

SECRET_KEY = os.getenv("ZOE_AUTH_SECRET_KEY", "change-me-in-prod")
ALGORITHM = "HS256"


def generate_test_jwt(user_id: str = "test_user", username: str = "test_user") -> str:
    """Generate valid JWT token for testing"""
    payload = {
        "user_id": user_id,
        "username": username,
        "exp": datetime.utcnow() + timedelta(hours=1),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


@pytest.fixture(scope="session")
def app():
    """FastAPI app instance"""
    from main import app
    return app


@pytest.fixture
def client(app):
    """Test client for API requests"""
    return TestClient(app)


@pytest.fixture
def auth_headers():
    """Valid JWT token headers for authenticated requests"""
    token = generate_test_jwt()
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def mock_memory_data():
    """Sample memory data for tests"""
    return {
        "people": [
            {"name": "Sarah", "relationship": "friend"},
            {"name": "John", "relationship": "colleague"}
        ],
        "conversations": [
            {"person": "Sarah", "topic": "Arduino", "date": "2024-05-12"}
        ]
    }
