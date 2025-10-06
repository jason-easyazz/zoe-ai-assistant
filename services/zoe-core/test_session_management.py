"""
Comprehensive Test Suite for Session Management System
Tests all session management functionality
"""

import pytest
import tempfile
import os
import time
from datetime import datetime, timedelta
from unittest.mock import patch

from session_manager import SessionManager, Session
from routers.sessions import router as session_router
from session_middleware import SessionMiddleware
from session_auth import require_session, optional_session

class TestSessionManager:
    """Test cases for SessionManager class"""
    
    @pytest.fixture
    def temp_db(self):
        """Create temporary database for testing"""
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = f.name
        yield db_path
        os.unlink(db_path)
    
    @pytest.fixture
    def session_manager(self, temp_db):
        """Create SessionManager instance for testing"""
        return SessionManager(db_path=temp_db, default_timeout=60)
    
    def test_create_session(self, session_manager):
        """Test session creation"""
        session = session_manager.create_session("user123")
        
        assert session.user_id == "user123"
        assert session.is_active is True
        assert session.session_id is not None
        assert session.created_at <= datetime.now()
        assert session.expires_at > session.created_at
    
    def test_get_session(self, session_manager):
        """Test session retrieval"""
        session = session_manager.create_session("user123")
        retrieved = session_manager.get_session(session.session_id)
        
        assert retrieved is not None
        assert retrieved.session_id == session.session_id
        assert retrieved.user_id == session.user_id
    
    def test_get_nonexistent_session(self, session_manager):
        """Test retrieval of non-existent session"""
        retrieved = session_manager.get_session("nonexistent")
        assert retrieved is None
    
    def test_update_session_activity(self, session_manager):
        """Test session activity update"""
        session = session_manager.create_session("user123")
        original_activity = session.last_activity
        
        time.sleep(0.1)  # Small delay
        success = session_manager.update_session_activity(session.session_id)
        
        assert success is True
        updated_session = session_manager.get_session(session.session_id)
        assert updated_session.last_activity > original_activity
    
    def test_extend_session(self, session_manager):
        """Test session extension"""
        session = session_manager.create_session("user123", timeout=60)
        original_expiry = session.expires_at
        
        success = session_manager.extend_session(session.session_id, 120)
        
        assert success is True
        extended_session = session_manager.get_session(session.session_id)
        assert extended_session.expires_at > original_expiry
    
    def test_invalidate_session(self, session_manager):
        """Test session invalidation"""
        session = session_manager.create_session("user123")
        session_id = session.session_id
        
        success = session_manager.invalidate_session(session_id)
        
        assert success is True
        invalidated = session_manager.get_session(session_id)
        assert invalidated is None
    
    def test_invalidate_user_sessions(self, session_manager):
        """Test invalidating all user sessions"""
        # Create multiple sessions for same user
        session1 = session_manager.create_session("user123")
        session2 = session_manager.create_session("user123")
        session3 = session_manager.create_session("user456")
        
        count = session_manager.invalidate_user_sessions("user123")
        
        assert count == 2
        assert session_manager.get_session(session1.session_id) is None
        assert session_manager.get_session(session2.session_id) is None
        assert session_manager.get_session(session3.session_id) is not None
    
    def test_get_user_sessions(self, session_manager):
        """Test getting all user sessions"""
        session1 = session_manager.create_session("user123")
        session2 = session_manager.create_session("user123")
        session3 = session_manager.create_session("user456")
        
        user_sessions = session_manager.get_user_sessions("user123")
        
        assert len(user_sessions) == 2
        session_ids = [s.session_id for s in user_sessions]
        assert session1.session_id in session_ids
        assert session2.session_id in session_ids
        assert session3.session_id not in session_ids
    
    def test_session_expiry(self, session_manager):
        """Test session expiration"""
        # Create session with very short timeout
        session = session_manager.create_session("user123", timeout=1)
        session_id = session.session_id
        
        # Wait for session to expire
        time.sleep(1.1)
        
        expired_session = session_manager.get_session(session_id)
        assert expired_session is None
    
    def test_session_stats(self, session_manager):
        """Test session statistics"""
        # Create some sessions
        session_manager.create_session("user123")
        session_manager.create_session("user123")
        session_manager.create_session("user456")
        
        stats = session_manager.get_session_stats()
        
        assert stats["active_sessions"] == 3
        assert stats["unique_users"] == 2
        assert stats["total_sessions"] == 3
        assert "user123" in stats["sessions_per_user"]
        assert "user456" in stats["sessions_per_user"]
    
    def test_session_metadata(self, session_manager):
        """Test session metadata handling"""
        metadata = {"role": "admin", "permissions": ["read", "write"]}
        session = session_manager.create_session("user123", metadata=metadata)
        
        assert session.metadata == metadata
        
        retrieved = session_manager.get_session(session.session_id)
        assert retrieved.metadata == metadata
    
    def test_concurrent_sessions(self, session_manager):
        """Test concurrent session handling"""
        import threading
        import queue
        
        results = queue.Queue()
        
        def create_session_worker(user_id):
            session = session_manager.create_session(user_id)
            results.put(session.session_id)
        
        # Create multiple sessions concurrently
        threads = []
        for i in range(10):
            thread = threading.Thread(target=create_session_worker, args=(f"user{i}",))
            threads.append(thread)
            thread.start()
        
        # Wait for all threads to complete
        for thread in threads:
            thread.join()
        
        # Verify all sessions were created
        assert results.qsize() == 10
        
        # Verify all sessions are valid
        while not results.empty():
            session_id = results.get()
            session = session_manager.get_session(session_id)
            assert session is not None

class TestSessionAPI:
    """Test cases for session API endpoints"""
    
    @pytest.fixture
    def client(self):
        """Create test client"""
        from fastapi.testclient import TestClient
        from fastapi import FastAPI
        
        app = FastAPI()
        app.include_router(session_router)
        return TestClient(app)
    
    def test_create_session_endpoint(self, client):
        """Test session creation endpoint"""
        response = client.post("/sessions/create", json={
            "user_id": "testuser",
            "timeout": 3600,
            "metadata": {"role": "user"}
        })
        
        assert response.status_code == 200
        data = response.json()
        assert "session_id" in data
        assert data["user_id"] == "testuser"
        assert data["is_active"] is True
    
    def test_get_session_endpoint(self, client):
        """Test session retrieval endpoint"""
        # First create a session
        create_response = client.post("/sessions/create", json={"user_id": "testuser"})
        session_id = create_response.json()["session_id"]
        
        # Then retrieve it
        response = client.get(f"/sessions/{session_id}")
        
        assert response.status_code == 200
        data = response.json()
        assert data["session_id"] == session_id
        assert data["user_id"] == "testuser"
    
    def test_get_nonexistent_session(self, client):
        """Test retrieval of non-existent session"""
        response = client.get("/sessions/nonexistent")
        assert response.status_code == 404
    
    def test_update_activity_endpoint(self, client):
        """Test session activity update endpoint"""
        # Create session
        create_response = client.post("/sessions/create", json={"user_id": "testuser"})
        session_id = create_response.json()["session_id"]
        
        # Update activity
        response = client.post(f"/sessions/{session_id}/activity")
        
        assert response.status_code == 200
        assert "updated successfully" in response.json()["message"]
    
    def test_extend_session_endpoint(self, client):
        """Test session extension endpoint"""
        # Create session
        create_response = client.post("/sessions/create", json={"user_id": "testuser"})
        session_id = create_response.json()["session_id"]
        
        # Extend session
        response = client.post(f"/sessions/{session_id}/extend", json={
            "additional_seconds": 1800
        })
        
        assert response.status_code == 200
        assert "extended" in response.json()["message"]
    
    def test_invalidate_session_endpoint(self, client):
        """Test session invalidation endpoint"""
        # Create session
        create_response = client.post("/sessions/create", json={"user_id": "testuser"})
        session_id = create_response.json()["session_id"]
        
        # Invalidate session
        response = client.delete(f"/sessions/{session_id}")
        
        assert response.status_code == 200
        assert "invalidated" in response.json()["message"]
        
        # Verify session is invalidated
        get_response = client.get(f"/sessions/{session_id}")
        assert get_response.status_code == 404
    
    def test_get_user_sessions_endpoint(self, client):
        """Test get user sessions endpoint"""
        # Create multiple sessions for same user
        client.post("/sessions/create", json={"user_id": "testuser"})
        client.post("/sessions/create", json={"user_id": "testuser"})
        client.post("/sessions/create", json={"user_id": "otheruser"})
        
        # Get sessions for testuser
        response = client.get("/sessions/user/testuser/sessions")
        
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        assert all(session["user_id"] == "testuser" for session in data)
    
    def test_session_stats_endpoint(self, client):
        """Test session statistics endpoint"""
        # Create some sessions
        client.post("/sessions/create", json={"user_id": "user1"})
        client.post("/sessions/create", json={"user_id": "user1"})
        client.post("/sessions/create", json={"user_id": "user2"})
        
        # Get stats
        response = client.get("/sessions/stats")
        
        assert response.status_code == 200
        data = response.json()
        assert data["active_sessions"] == 3
        assert data["unique_users"] == 2
    
    def test_validate_session_endpoint(self, client):
        """Test session validation endpoint"""
        # Create session
        create_response = client.post("/sessions/create", json={"user_id": "testuser"})
        session_id = create_response.json()["session_id"]
        
        # Validate session
        response = client.post(f"/sessions/validate/{session_id}")
        
        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is True
        assert data["session_id"] == session_id

class TestSessionMiddleware:
    """Test cases for session middleware"""
    
    def test_middleware_skips_excluded_paths(self):
        """Test that middleware skips excluded paths"""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        
        app = FastAPI()
        middleware = SessionMiddleware(app, excluded_paths=["/health", "/docs"])
        app.add_middleware(SessionMiddleware, 
                          excluded_paths=["/health", "/docs"],
                          protected_paths=["/api"])
        
        @app.get("/health")
        async def health():
            return {"status": "ok"}
        
        @app.get("/api/test")
        async def protected():
            return {"message": "protected"}
        
        client = TestClient(app)
        
        # Health endpoint should work without session
        response = client.get("/health")
        assert response.status_code == 200
        
        # Protected endpoint should require session
        response = client.get("/api/test")
        assert response.status_code == 401

def run_session_tests():
    """Run all session management tests"""
    print("Running Session Management Tests...")
    
    # Test SessionManager
    print("Testing SessionManager...")
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    
    try:
        sm = SessionManager(db_path=db_path, default_timeout=60)
        
        # Test basic functionality
        session = sm.create_session("testuser")
        assert session.user_id == "testuser"
        
        retrieved = sm.get_session(session.session_id)
        assert retrieved is not None
        assert retrieved.user_id == "testuser"
        
        # Test activity update
        success = sm.update_session_activity(session.session_id)
        assert success is True
        
        # Test session extension
        success = sm.extend_session(session.session_id, 120)
        assert success is True
        
        # Test invalidation
        success = sm.invalidate_session(session.session_id)
        assert success is True
        
        # Test stats
        stats = sm.get_session_stats()
        assert "active_sessions" in stats
        
        print("✓ SessionManager tests passed")
        
    finally:
        os.unlink(db_path)
    
    print("✓ All session management tests passed!")

if __name__ == "__main__":
    run_session_tests()
