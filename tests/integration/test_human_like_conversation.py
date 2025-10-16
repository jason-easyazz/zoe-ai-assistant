"""
Comprehensive test suite for human-like conversation capabilities
Tests natural conversation flow, temporal memory, orchestration, and edge cases
"""

import pytest
import httpx
import time
import asyncio
import sqlite3

BASE_URL = "http://localhost:8000"

# ============================================================================
# BASIC TEMPORAL MEMORY TESTS
# ============================================================================

@pytest.mark.asyncio
async def test_temporal_memory_recall():
    """Test conversational continuity - basic recall"""
    async with httpx.AsyncClient() as client:
        r1 = await client.post(f"{BASE_URL}/api/chat", json={
            "message": "My favorite color is blue",
            "user_id": "test_user"
        })
        
        r2 = await client.post(f"{BASE_URL}/api/chat", json={
            "message": "What color did I just tell you?",
            "user_id": "test_user"
        })
        
        assert "blue" in r2.json()["response"].lower()

# ============================================================================
# NATURAL CONVERSATION SCENARIOS
# ============================================================================

@pytest.mark.asyncio
async def test_conversation_scenario_shopping_corrections():
    """Scenario A: Natural corrections during shopping list building"""
    async with httpx.AsyncClient() as client:
        # Turn 1: Add item
        r1 = await client.post(f"{BASE_URL}/api/chat", json={
            "message": "Add milk to shopping list",
            "user_id": "conv_test_1"
        })
        assert r1.status_code == 200
        
        # Turn 2: Immediate correction (like real conversation)
        r2 = await client.post(f"{BASE_URL}/api/chat", json={
            "message": "Actually, make that almond milk",
            "user_id": "conv_test_1"
        })
        response2 = r2.json()["response"].lower()
        assert "almond" in response2 or "updated" in response2
        
        # Turn 3: Reference previous context
        r3 = await client.post(f"{BASE_URL}/api/chat", json={
            "message": "What did I just add?",
            "user_id": "conv_test_1"
        })
        response3 = r3.json()["response"].lower()
        assert "almond milk" in response3 or ("almond" in response3 and "milk" in response3)
        
        # Turn 4: Anaphoric reference (pronoun)
        r4 = await client.post(f"{BASE_URL}/api/chat", json={
            "message": "Remove it",
            "user_id": "conv_test_1"
        })
        response4 = r4.json()["response"].lower()
        assert "removed" in response4 or "deleted" in response4

@pytest.mark.asyncio
async def test_conversation_scenario_meeting_context():
    """Scenario B: Multi-turn conversation about same event"""
    async with httpx.AsyncClient() as client:
        # Turn 1: Query about meeting
        r1 = await client.post(f"{BASE_URL}/api/chat", json={
            "message": "When is my team meeting?",
            "user_id": "conv_test_2"
        })
        
        # Turn 2: Follow-up about same meeting (no re-specification)
        r2 = await client.post(f"{BASE_URL}/api/chat", json={
            "message": "Who's attending?",
            "user_id": "conv_test_2"
        })
        # Should understand "Who's attending [the team meeting]?"
        assert r2.status_code == 200
        
        # Turn 3: Action on same meeting
        r3 = await client.post(f"{BASE_URL}/api/chat", json={
            "message": "Send them a reminder",
            "user_id": "conv_test_2"
        })
        # Should understand "them" = attendees, reminder about meeting
        response3 = r3.json()["response"].lower()
        assert "reminder" in response3

@pytest.mark.asyncio
async def test_conversation_scenario_temporal_references():
    """Scenario C: Time-based conversational memory"""
    async with httpx.AsyncClient() as client:
        # Turn 1: Share information
        r1 = await client.post(f"{BASE_URL}/api/chat", json={
            "message": "My sister's birthday is May 5th",
            "user_id": "conv_test_3"
        })
        
        # Turn 2: Relative time reference
        r2 = await client.post(f"{BASE_URL}/api/chat", json={
            "message": "Remind me a week before that",
            "user_id": "conv_test_3"
        })
        response2 = r2.json()["response"].lower()
        assert "reminder" in response2 or "april" in response2  # Week before May 5 = late April
        
        # Turn 3: Verify what reminder is about
        r3 = await client.post(f"{BASE_URL}/api/chat", json={
            "message": "What am I being reminded about?",
            "user_id": "conv_test_3"
        })
        response3 = r3.json()["response"].lower()
        assert "sister" in response3 and "birthday" in response3

@pytest.mark.asyncio
async def test_conversation_scenario_context_switching():
    """Scenario D: Natural topic switching with memory"""
    async with httpx.AsyncClient() as client:
        # Topic 1: Calendar
        r1 = await client.post(f"{BASE_URL}/api/chat", json={
            "message": "Schedule dentist appointment tomorrow at 3pm",
            "user_id": "conv_test_4"
        })
        
        # Topic 2: Shopping (switch topics)
        r2 = await client.post(f"{BASE_URL}/api/chat", json={
            "message": "Add toothpaste to shopping list",
            "user_id": "conv_test_4"
        })
        
        # Return to Topic 1 (implicitly)
        r3 = await client.post(f"{BASE_URL}/api/chat", json={
            "message": "What time was that appointment?",
            "user_id": "conv_test_4"
        })
        response3 = r3.json()["response"].lower()
        assert "3" in response3 and ("pm" in response3 or "15" in response3)

@pytest.mark.asyncio
async def test_conversation_scenario_clarification_repair():
    """Scenario E: Conversational repair (wait, I meant...)"""
    async with httpx.AsyncClient() as client:
        # Turn 1: Initial request
        r1 = await client.post(f"{BASE_URL}/api/chat", json={
            "message": "Add bananas to my list",
            "user_id": "conv_test_5"
        })
        
        # Turn 2: Repair/correction
        r2 = await client.post(f"{BASE_URL}/api/chat", json={
            "message": "Wait, I meant apples not bananas",
            "user_id": "conv_test_5"
        })
        response2 = r2.json()["response"].lower()
        assert "apple" in response2
        
        # Turn 3: Verify final state
        r3 = await client.post(f"{BASE_URL}/api/chat", json={
            "message": "What's on my list?",
            "user_id": "conv_test_5"
        })
        response3 = r3.json()["response"].lower()
        assert "apple" in response3
        assert "banana" not in response3  # Should be removed/replaced

@pytest.mark.asyncio
async def test_conversation_scenario_multi_turn_planning():
    """Scenario F: Extended multi-turn planning conversation"""
    async with httpx.AsyncClient() as client:
        # Turn 1: Initial planning request
        r1 = await client.post(f"{BASE_URL}/api/chat", json={
            "message": "I need to plan a dinner party",
            "user_id": "conv_test_6"
        })
        
        # Turn 2: Add details
        r2 = await client.post(f"{BASE_URL}/api/chat", json={
            "message": "It's next Friday at 7pm",
            "user_id": "conv_test_6"
        })
        
        # Turn 3: Add more context
        r3 = await client.post(f"{BASE_URL}/api/chat", json={
            "message": "There will be 6 people",
            "user_id": "conv_test_6"
        })
        
        # Turn 4: Request action based on all context
        r4 = await client.post(f"{BASE_URL}/api/chat", json={
            "message": "Add wine to shopping list",
            "user_id": "conv_test_6"
        })
        response4 = r4.json()["response"].lower()
        assert "wine" in response4
        
        # Turn 5: Summary recall
        r5 = await client.post(f"{BASE_URL}/api/chat", json={
            "message": "What have we planned so far?",
            "user_id": "conv_test_6"
        })
        response5 = r5.json()["response"].lower()
        assert "dinner" in response5 and "friday" in response5

# ============================================================================
# PRONOUN & REFERENCE RESOLUTION TESTS
# ============================================================================

@pytest.mark.asyncio
async def test_pronoun_resolution_it():
    """Test 'it' pronoun resolution"""
    async with httpx.AsyncClient() as client:
        r1 = await client.post(f"{BASE_URL}/api/chat", json={
            "message": "Add bread to shopping list",
            "user_id": "pronoun_test_1"
        })
        
        r2 = await client.post(f"{BASE_URL}/api/chat", json={
            "message": "Remove it",
            "user_id": "pronoun_test_1"
        })
        response = r2.json()["response"].lower()
        assert "bread" in response or "removed" in response

@pytest.mark.asyncio
async def test_pronoun_resolution_that():
    """Test 'that' demonstrative resolution"""
    async with httpx.AsyncClient() as client:
        r1 = await client.post(f"{BASE_URL}/api/chat", json={
            "message": "Schedule team standup tomorrow at 9am",
            "user_id": "pronoun_test_2"
        })
        
        r2 = await client.post(f"{BASE_URL}/api/chat", json={
            "message": "Move that to 10am",
            "user_id": "pronoun_test_2"
        })
        response = r2.json()["response"].lower()
        assert "10" in response

@pytest.mark.asyncio
async def test_temporal_reference_earlier():
    """Test temporal reference 'earlier'"""
    async with httpx.AsyncClient() as client:
        r1 = await client.post(f"{BASE_URL}/api/chat", json={
            "message": "John's phone number is 555-1234",
            "user_id": "temporal_ref_test"
        })
        
        await asyncio.sleep(1)  # Small delay
        
        r2 = await client.post(f"{BASE_URL}/api/chat", json={
            "message": "What number did I tell you earlier?",
            "user_id": "temporal_ref_test"
        })
        response = r2.json()["response"].lower()
        assert "555-1234" in response or "555" in response

# ============================================================================
# ORCHESTRATION & COMPLEX TASKS
# ============================================================================

@pytest.mark.asyncio
async def test_multi_expert_orchestration():
    """Test complex multi-step tasks"""
    async with httpx.AsyncClient() as client:
        r = await client.post(f"{BASE_URL}/api/chat", json={
            "message": "Add milk to shopping list and create event tomorrow at 2pm",
            "user_id": "orchestration_test"
        })
        
        assert r.status_code == 200
        response = r.json()["response"]
        assert "milk" in response.lower()
        assert "event" in response.lower() or "tomorrow" in response.lower()

@pytest.mark.asyncio
async def test_orchestration_then_sequence():
    """Test 'and then' sequential task detection"""
    async with httpx.AsyncClient() as client:
        r = await client.post(f"{BASE_URL}/api/chat", json={
            "message": "Check my calendar and then tell me when I have free time",
            "user_id": "sequence_test"
        })
        
        response = r.json()["response"].lower()
        # Should show calendar AND free time (not just one)
        assert "free" in response or "available" in response

# ============================================================================
# PERFORMANCE & RELIABILITY TESTS
# ============================================================================

@pytest.mark.asyncio
async def test_no_timeouts():
    """Test response times are acceptable"""
    async with httpx.AsyncClient(timeout=30.0) as client:
        start = time.time()
        r = await client.post(f"{BASE_URL}/api/chat", json={
            "message": "What events do I have this week?",
            "user_id": "perf_test"
        })
        duration = time.time() - start
        
        assert r.status_code == 200
        assert duration < 10.0  # Should respond in <10s

@pytest.mark.asyncio
async def test_satisfaction_tracking():
    """Test satisfaction data is recorded"""
    async with httpx.AsyncClient() as client:
        r = await client.post(f"{BASE_URL}/api/chat", json={
            "message": "Hello Zoe",
            "user_id": "satisfaction_test"
        })
        
        # Give async task time to complete
        await asyncio.sleep(0.5)
        
        # Check satisfaction was recorded
        conn = sqlite3.connect("/app/data/zoe.db")
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM interaction_tracking WHERE user_id = ?", ("satisfaction_test",))
        count = cursor.fetchone()[0]
        conn.close()
        
        assert count > 0

# ============================================================================
# EDGE CASES & NATURAL LANGUAGE VARIATIONS
# ============================================================================

@pytest.mark.asyncio
async def test_implicit_references():
    """Test implicit subject references"""
    async with httpx.AsyncClient() as client:
        r1 = await client.post(f"{BASE_URL}/api/chat", json={
            "message": "Create a meeting with Sarah tomorrow",
            "user_id": "implicit_test"
        })
        
        # Implicit subject (meeting is implied)
        r2 = await client.post(f"{BASE_URL}/api/chat", json={
            "message": "Make it at 3pm",
            "user_id": "implicit_test"
        })
        response = r2.json()["response"].lower()
        assert "3" in response or "15" in response

@pytest.mark.asyncio
async def test_conversational_ellipsis():
    """Test elliptical constructions (partial sentences)"""
    async with httpx.AsyncClient() as client:
        r1 = await client.post(f"{BASE_URL}/api/chat", json={
            "message": "Do I have any meetings today?",
            "user_id": "ellipsis_test"
        })
        
        # Elliptical response (omitted subject/verb)
        r2 = await client.post(f"{BASE_URL}/api/chat", json={
            "message": "Tomorrow?",  # Means "Do I have any meetings tomorrow?"
            "user_id": "ellipsis_test"
        })
        assert r2.status_code == 200

@pytest.mark.asyncio
async def test_question_answer_continuity():
    """Test question-answer-followup chains"""
    async with httpx.AsyncClient() as client:
        r1 = await client.post(f"{BASE_URL}/api/chat", json={
            "message": "What's on my shopping list?",
            "user_id": "qa_test"
        })
        
        # Follow-up action based on answer
        r2 = await client.post(f"{BASE_URL}/api/chat", json={
            "message": "Add eggs to it",  # 'it' = the shopping list we just discussed
            "user_id": "qa_test"
        })
        response = r2.json()["response"].lower()
        assert "egg" in response



