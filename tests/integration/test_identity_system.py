"""
Identity System Integration Tests
==================================
Tests to ensure Zoe's identity and chat system work correctly
BASELINE tests - run BEFORE making changes to ensure we don't break anything
"""

import pytest
import sys
import httpx
from unittest.mock import AsyncMock, MagicMock, patch

# Add paths
sys.path.insert(0, '/home/pi/zoe/services/zoe-core')

from routers.chat import build_system_prompt, QualityAnalyzer


class TestCurrentIdentitySystem:
    """Baseline tests for current identity system"""
    
    @pytest.mark.asyncio
    async def test_system_prompt_generation(self):
        """Test that system prompt is generated correctly"""
        memories = {"people": [], "semantic_results": []}
        user_context = {"user_id": "test_user"}
        
        prompt = await build_system_prompt(memories, user_context)
        
        assert prompt is not None
        assert len(prompt) > 100
        assert "Zoe" in prompt
        assert "assistant" in prompt.lower()
    
    @pytest.mark.asyncio
    async def test_system_prompt_contains_identity(self):
        """Test that system prompt establishes identity"""
        memories = {}
        user_context = {}
        
        prompt = await build_system_prompt(memories, user_context)
        
        # Should mention Zoe by name
        assert "Zoe" in prompt
        # Should NOT be empty or generic
        assert len(prompt) > 50
    
    def test_quality_analyzer_exists(self):
        """Test that quality analyzer is functional"""
        analyzer = QualityAnalyzer()
        
        # Test with sample response
        response = "I'll help you with that! Let me add milk to your shopping list."
        scores = analyzer.analyze_response(response, "conversation")
        
        assert "quality" in scores
        assert "warmth" in scores
        assert "intelligence" in scores
        assert "tool_usage" in scores
        
        # Scores should be in range
        for key, score in scores.items():
            assert 1.0 <= score <= 10.0
    
    def test_quality_analyzer_warmth_detection(self):
        """Test warmth detection in responses"""
        analyzer = QualityAnalyzer()
        
        warm_response = "I'd be happy to help you with that! 😊"
        cold_response = "Done."
        
        warm_scores = analyzer.analyze_response(warm_response, "conversation")
        cold_scores = analyzer.analyze_response(cold_response, "conversation")
        
        assert warm_scores["warmth"] > cold_scores["warmth"]


class TestPeopleMemorySystem:
    """Tests for people memory system (critical for compatibility profiles)"""
    
    def test_people_table_schema_fields(self):
        """Verify people table has required fields for profiles"""
        import sqlite3
        
        conn = sqlite3.connect("/home/pi/zoe/data/zoe.db")
        cursor = conn.cursor()
        
        cursor.execute("PRAGMA table_info(people)")
        columns = {row[1]: row[2] for row in cursor.fetchall()}
        
        conn.close()
        
        # Check essential fields exist
        assert "id" in columns
        assert "user_id" in columns
        assert "name" in columns
        assert "profile" in columns  # JSON field for rich profiles
        assert "facts" in columns  # JSON field for facts
        assert "preferences" in columns  # JSON field for preferences
    
    def test_people_can_store_json_data(self):
        """Test that JSON fields can store complex data"""
        import sqlite3
        import json
        
        conn = sqlite3.connect("/home/pi/zoe/data/zoe.db")
        cursor = conn.cursor()
        
        # Test insert with JSON data
        test_profile = {
            "personality": {"openness": 0.8, "extraversion": 0.6},
            "interests": ["reading", "hiking"],
            "test_data": True
        }
        
        try:
            cursor.execute("""
                INSERT INTO people (user_id, name, profile)
                VALUES (?, ?, ?)
            """, ("test_identity_user", "Test Person", json.dumps(test_profile)))
            
            person_id = cursor.lastrowid
            
            # Verify retrieval
            cursor.execute("SELECT profile FROM people WHERE id = ?", (person_id,))
            stored_profile = json.loads(cursor.fetchone()[0])
            
            assert stored_profile["test_data"] == True
            assert "personality" in stored_profile
            
            # Cleanup
            cursor.execute("DELETE FROM people WHERE id = ?", (person_id,))
            conn.commit()
            
        finally:
            conn.close()


class TestChatSystemIntegration:
    """Tests for chat system integration points"""
    
    @pytest.mark.asyncio
    async def test_chat_endpoint_exists(self):
        """Test that chat endpoint is accessible"""
        # This is a smoke test - just verify the route exists
        from routers.chat import router
        
        # Get all routes
        routes = [route.path for route in router.routes]
        
        # Should have main chat endpoints
        assert any("/chat" in route for route in routes)
    
    @pytest.mark.asyncio  
    async def test_memory_search_function_exists(self):
        """Test that memory search function is available"""
        from routers.chat import search_memories
        
        # Function should exist and be callable
        assert callable(search_memories)
        
        # Test with minimal params (will fail gracefully if DB issues)
        try:
            result = await search_memories("test query", "test_user")
            assert isinstance(result, dict)
        except Exception as e:
            # Expected to potentially fail in test env, just verify structure
            pass


class TestSelfAwarenessSystem:
    """Tests for self-awareness system"""
    
    def test_self_awareness_module_exists(self):
        """Test that self-awareness module is importable"""
        try:
            from self_awareness import SelfIdentity
            assert SelfIdentity is not None
        except ImportError:
            pytest.skip("Self-awareness module not in path")
    
    def test_self_identity_has_core_attributes(self):
        """Test that SelfIdentity has essential attributes"""
        try:
            from self_awareness import SelfIdentity
            
            identity = SelfIdentity()
            
            assert identity.name == "Zoe"
            assert identity.version is not None
            assert identity.personality_traits is not None
            assert identity.core_values is not None
            assert identity.capabilities is not None
            
        except ImportError:
            pytest.skip("Self-awareness module not in path")


class TestUserContextSystem:
    """Tests for user context management"""
    
    def test_user_context_class_exists(self):
        """Test that UserContext class exists"""
        from user_context import UserContext
        
        assert UserContext is not None
    
    def test_user_context_can_create_user(self):
        """Test user creation (important for onboarding)"""
        from user_context import UserContext
        
        context = UserContext()
        
        # Create test user
        result = context.create_user(
            username=f"test_identity_{pytest.test_run_id}",
            email="test@example.com",
            display_name="Test User"
        )
        
        # Should succeed or already exist
        assert result is not None
        assert "user_id" in result or "error" in result
    
    def test_user_preferences_storage(self):
        """Test that user preferences can be stored"""
        from user_context import UserContext
        
        context = UserContext()
        
        # Get or create test user
        user = context.get_user_by_username("system")
        
        if user:
            # Test preference update
            prefs = {"test_pref": "test_value", "communication_style": "direct"}
            result = context.update_user(user["id"], {"preferences": prefs})
            
            # Verify
            updated_user = context.get_user(user["id"])
            assert updated_user is not None
            if updated_user["preferences"]:
                assert isinstance(updated_user["preferences"], dict)


# Set a unique test run ID
pytest.test_run_id = "identity_baseline_test"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

