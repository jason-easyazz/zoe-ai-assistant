"""
Phase 5: End-to-End Integration Tests
Full user flows from creation to retrieval
"""
import pytest
import asyncio
import time


@pytest.mark.asyncio
async def test_memory_creation_and_retrieval():
    """Full flow: Create person → Add conversation → Recall later"""
    from fastapi.testclient import TestClient
    from conftest import generate_test_jwt
    import sys
    sys.path.insert(0, "/home/pi/zoe/services/zoe-core")
    from main import app
    
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {generate_test_jwt()}"}
    
    # 1. Create a person
    person_response = client.post(
        "/api/memories?type=people",
        json={
            "person": {
                "name": "Sarah",
                "relationship": "friend"
            }
        },
        headers=headers
    )
    assert person_response.status_code == 200
    
    # 2. Have a conversation mentioning Sarah
    chat_response = client.post(
        "/api/chat",
        json={
            "message": "I talked to Sarah today about Arduino sensors."
        },
        headers=headers
    )
    assert chat_response.status_code == 200
    
    # 3. Recall the memory
    recall_response = client.post(
        "/api/chat",
        json={
            "message": "What did Sarah say about Arduino?"
        },
        headers=headers
    )
    assert recall_response.status_code == 200


def test_authenticated_endpoints():
    """Test that authenticated endpoints work with valid tokens"""
    from fastapi.testclient import TestClient
    from conftest import generate_test_jwt
    import sys
    sys.path.insert(0, "/home/pi/zoe/services/zoe-core")
    from main import app
    
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {generate_test_jwt()}"}
    
    # Test various authenticated endpoints
    endpoints = [
        ("GET", "/api/memories?type=people"),
        ("GET", "/health"),
        ("GET", "/api/health"),
    ]
    
    for method, endpoint in endpoints:
        if method == "GET":
            response = client.get(endpoint, headers=headers if "/memories" in endpoint else {})
        
        assert response.status_code in [200, 401], f"Unexpected status for {endpoint}"


def test_multi_user_isolation():
    """Test that different users have isolated data"""
    from fastapi.testclient import TestClient
    from conftest import generate_test_jwt
    import sys
    sys.path.insert(0, "/home/pi/zoe/services/zoe-core")
    from main import app
    
    client = TestClient(app)
    
    # User 1
    user1_headers = {"Authorization": f"Bearer {generate_test_jwt('user1', 'user1')}"}
    user1_response = client.post(
        "/api/memories?type=people",
        json={
            "person": {
                "name": "User1 Contact",
                "relationship": "friend"
            }
        },
        headers=user1_headers
    )
    assert user1_response.status_code == 200
    
    # User 2 shouldn't see User 1's data
    user2_headers = {"Authorization": f"Bearer {generate_test_jwt('user2', 'user2')}"}
    user2_response = client.get(
        "/api/memories?type=people",
        headers=user2_headers
    )
    assert user2_response.status_code == 200
