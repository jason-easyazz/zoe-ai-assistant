"""
Phase 1: Security tests for authentication hardening
Tests that invalid/missing tokens return 401, valid tokens return 200
"""
import pytest
import jwt
from datetime import datetime, timedelta


def test_no_token_raises_401(client):
    """Missing token should return 401, not default user"""
    response = client.get("/api/memories?type=people")
    assert response.status_code == 401
    assert "invalid" in response.json()["detail"].lower() or "authentication" in response.json()["detail"].lower()


def test_invalid_token_raises_401(client):
    """Invalid token should return 401"""
    response = client.get(
        "/api/memories?type=people",
        headers={"Authorization": "Bearer invalid_token_gibberish"}
    )
    assert response.status_code == 401
    assert "invalid" in response.json()["detail"].lower() or "authentication" in response.json()["detail"].lower()


def test_expired_token_raises_401(client):
    """Expired token should return 401"""
    from conftest import SECRET_KEY, ALGORITHM
    
    # Create expired token
    payload = {
        "user_id": "test_user",
        "username": "test_user",
        "exp": datetime.utcnow() - timedelta(hours=1),  # Expired 1 hour ago
    }
    expired_token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
    
    response = client.get(
        "/api/memories?type=people",
        headers={"Authorization": f"Bearer {expired_token}"}
    )
    assert response.status_code == 401
    assert "expired" in response.json()["detail"].lower() or "invalid" in response.json()["detail"].lower()


def test_valid_token_succeeds(client, auth_headers):
    """Valid token should work"""
    response = client.get("/api/memories?type=people", headers=auth_headers)
    assert response.status_code == 200
    assert "memories" in response.json()


def test_token_missing_user_id_raises_401(client):
    """Token without user_id should return 401"""
    from conftest import SECRET_KEY, ALGORITHM
    
    # Create token with missing user_id
    payload = {
        "username": "test_user",
        "exp": datetime.utcnow() + timedelta(hours=1),
    }
    invalid_token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
    
    response = client.get(
        "/api/memories?type=people",
        headers={"Authorization": f"Bearer {invalid_token}"}
    )
    assert response.status_code == 401
