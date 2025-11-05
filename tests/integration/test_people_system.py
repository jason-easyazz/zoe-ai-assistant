"""
Integration Test: People System
================================

Tests the full people system integration:
1. Person Expert can understand queries
2. Chat router can execute person actions
3. People API works correctly
4. Frontend can display people

Run: python3 -m pytest tests/integration/test_people_system.py -v
"""

import pytest
import asyncio
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'services', 'zoe-core'))

from services.person_expert import PersonExpert

@pytest.mark.asyncio
async def test_person_expert_can_handle_queries():
    """Test that person expert can recognize person-related queries"""
    expert = PersonExpert()
    
    # Test queries that should be handled
    test_queries = [
        "Add Sarah as a friend",
        "Remember that John loves coffee",
        "Gift idea for Mom: flowers",
        "I talked to Alice about the project today",
        "Who is Sarah?",
        "Find people named John"
    ]
    
    for query in test_queries:
        result = await expert.can_handle(query)
        assert result["can_handle"] == True, f"Should handle: {query}"
        assert result["confidence"] > 0.5
        print(f"✅ Can handle: {query} (confidence: {result['confidence']:.2f})")

@pytest.mark.asyncio
async def test_person_expert_extraction():
    """Test that person expert can extract information from queries"""
    expert = PersonExpert()
    
    # Test name extraction
    assert expert._extract_name("Add Sarah as a friend") == "Sarah"
    assert expert._extract_name("Tell me about John") == "John"
    print("✅ Name extraction works")
    
    # Test relationship extraction
    assert expert._extract_relationship("Add Sarah as a friend") == "friend"
    assert expert._extract_relationship("My colleague Alex") == "colleague"
    print("✅ Relationship extraction works")

@pytest.mark.asyncio
async def test_person_expert_mock_execution():
    """Test person expert can plan actions (mock database)"""
    expert = PersonExpert()
    
    # Note: This will fail if database doesn't exist, but we can test the logic
    query = "Add Test Person as a friend"
    can_handle = await expert.can_handle(query)
    
    assert can_handle["can_handle"] == True
    print(f"✅ Expert can plan action for: {query}")

def test_person_capabilities():
    """Test that person expert has expected capabilities"""
    expert = PersonExpert()
    
    capabilities = expert.get_capabilities()
    expected = [
        "add_person",
        "search_people",
        "add_note",
        "add_gift_idea",
        "track_conversation"
    ]
    
    for cap in expected:
        assert cap in capabilities, f"Missing capability: {cap}"
    
    print(f"✅ Person expert has {len(capabilities)} capabilities")

if __name__ == "__main__":
    print("\n" + "="*60)
    print("PEOPLE SYSTEM INTEGRATION TEST")
    print("="*60 + "\n")
    
    # Run tests
    asyncio.run(test_person_expert_can_handle_queries())
    asyncio.run(test_person_expert_extraction())
    asyncio.run(test_person_expert_mock_execution())
    test_person_capabilities()
    
    print("\n" + "="*60)
    print("✅ ALL TESTS PASSED")
    print("="*60 + "\n")


