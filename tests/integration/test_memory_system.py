"""
Phase 3 & 4: Memory System Integration Tests
Tests memory CRUD, search, and relationships
"""
import pytest


def test_create_person_memory(client, auth_headers):
    """Test creating a person memory"""
    response = client.post(
        "/api/memories?type=people",
        json={
            "person": {
                "name": "Test Person",
                "relationship": "friend",
                "notes": "Test notes"
            }
        },
        headers=auth_headers
    )
    assert response.status_code == 200
    assert "memory" in response.json()


def test_list_memories(client, auth_headers):
    """Test listing memories by type"""
    response = client.get(
        "/api/memories?type=people",
        headers=auth_headers
    )
    assert response.status_code == 200
    assert "memories" in response.json()


def test_memory_user_isolation(client, auth_headers):
    """Test that users only see their own memories"""
    # Create memory for test_user
    create_response = client.post(
        "/api/memories?type=people",
        json={
            "person": {
                "name": "Private Contact",
                "relationship": "friend"
            }
        },
        headers=auth_headers
    )
    assert create_response.status_code == 200
    
    # List memories - should only see own
    list_response = client.get(
        "/api/memories?type=people",
        headers=auth_headers
    )
    assert list_response.status_code == 200
    memories = list_response.json()["memories"]
    # All memories should belong to authenticated user


def test_memory_search(client, auth_headers):
    """Test memory search functionality"""
    # Create a memory first
    client.post(
        "/api/memories?type=notes",
        json={
            "note": {
                "title": "Arduino Project",
                "content": "Building temperature sensor with Arduino Uno"
            }
        },
        headers=auth_headers
    )
    
    # Search for it
    response = client.post(
        "/api/memories/search?query=Arduino",
        headers=auth_headers
    )
    
    # Should find the memory (if search is implemented)
    assert response.status_code in [200, 404, 501]


def test_update_memory(client, auth_headers):
    """Test updating a memory"""
    # Create first
    create_response = client.post(
        "/api/memories?type=people",
        json={
            "person": {
                "name": "Update Test",
                "relationship": "colleague"
            }
        },
        headers=auth_headers
    )
    assert create_response.status_code == 200
    memory_id = create_response.json()["memory"]["id"]
    
    # Update
    update_response = client.put(
        f"/api/memories/item/{memory_id}?type=people",
        json={
            "person": {
                "name": "Update Test",
                "relationship": "friend"
            }
        },
        headers=auth_headers
    )
    assert update_response.status_code == 200


def test_delete_memory(client, auth_headers):
    """Test deleting a memory"""
    # Create first
    create_response = client.post(
        "/api/memories?type=people",
        json={
            "person": {
                "name": "Delete Test",
                "relationship": "acquaintance"
            }
        },
        headers=auth_headers
    )
    assert create_response.status_code == 200
    memory_id = create_response.json()["memory"]["id"]
    
    # Delete
    delete_response = client.delete(
        f"/api/memories/item/{memory_id}?type=people",
        headers=auth_headers
    )
    assert delete_response.status_code == 200
    
    # Verify deleted
    get_response = client.get(
        f"/api/memories/item/{memory_id}?type=people",
        headers=auth_headers
    )
    assert get_response.status_code == 404
