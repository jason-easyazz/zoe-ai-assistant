#!/usr/bin/env python3
"""
Full Conversation Display - Shows Complete Q&A for Quality Assessment
"""

import requests
import time
import json
from datetime import datetime

BASE_URL = "http://localhost:8000"
TIMEOUT = 60

def chat(message, user_id):
    """Send chat message and return response with timing"""
    start = time.time()
    try:
        response = requests.post(
            f"{BASE_URL}/api/chat",
            json={"message": message, "user_id": user_id},
            timeout=TIMEOUT
        )
        duration = time.time() - start
        
        if response.status_code == 200:
            data = response.json()
            return {
                "response": data.get("response", ""),
                "time": duration,
                "success": True
            }
        else:
            return {
                "response": f"[ERROR: HTTP {response.status_code}]",
                "time": duration,
                "success": False
            }
    except Exception as e:
        return {
            "response": f"[ERROR: {str(e)[:100]}]",
            "time": time.time() - start,
            "success": False
        }

def run_conversation_test(title, user_id, turns):
    """Run a conversation and display full Q&A"""
    print("═" * 80)
    print(f"🧪 {title}")
    print("═" * 80)
    print()
    
    for i, turn in enumerate(turns, 1):
        print(f"👤 User (Turn {i}): {turn}")
        
        result = chat(turn, user_id)
        
        print(f"🤖 Zoe ({result['time']:.1f}s): {result['response']}")
        print()
        
        if i < len(turns):
            time.sleep(2)  # Pause between turns
    
    print()

# ============================================================================
# RUN 20 COMPREHENSIVE CONVERSATION TESTS
# ============================================================================

print("╔" + "═" * 78 + "╗")
print("║" + " " * 15 + "🎯 FULL CONVERSATION QUALITY ASSESSMENT" + " " * 23 + "║")
print("╚" + "═" * 78 + "╝")
print()
print("Showing COMPLETE conversations for you to judge quality...")
print()
print()

# TEST 1: Temporal Memory - Perfect Recall
run_conversation_test(
    "TEST 1: Temporal Memory - Perfect Recall",
    "full_test_1",
    [
        "My favorite food is sushi",
        "What food do I love?"
    ]
)

# TEST 2: Temporal Memory - Multiple Facts
run_conversation_test(
    "TEST 2: Temporal Memory - Multiple Facts in Sequence",
    "full_test_2",
    [
        "I live in Seattle",
        "I work as a nurse",
        "My car is a Toyota",
        "Tell me everything I just told you"
    ]
)

# TEST 3: Shopping List - Single Item
run_conversation_test(
    "TEST 3: Shopping List - Add Single Item",
    "full_test_3",
    [
        "Add bread to my shopping list"
    ]
)

# TEST 4: Shopping List - Multiple Items
run_conversation_test(
    "TEST 4: Shopping List - Multiple Items (eggs and bacon)",
    "full_test_4",
    [
        "Add eggs and bacon to shopping"
    ]
)

# TEST 5: Shopping List - Natural Language
run_conversation_test(
    "TEST 5: Shopping List - Natural Language Variant",
    "full_test_5",
    [
        "Don't let me forget to buy milk"
    ]
)

# TEST 6: Calendar - Create Event
run_conversation_test(
    "TEST 6: Calendar - Create Specific Event",
    "full_test_6",
    [
        "Schedule dentist appointment tomorrow at 2pm"
    ]
)

# TEST 7: Calendar - Multi-turn Context
run_conversation_test(
    "TEST 7: Calendar - Multi-turn with Context",
    "full_test_7",
    [
        "I need to schedule a meeting",
        "Make it tomorrow at 10am",
        "With Sarah"
    ]
)

# TEST 8: Correction
run_conversation_test(
    "TEST 8: Conversational Correction (Actually, I meant...)",
    "full_test_8",
    [
        "Add oranges to my list",
        "Actually, I meant apples not oranges",
        "What's on my shopping list?"
    ]
)

# TEST 9: Pronoun Resolution
run_conversation_test(
    "TEST 9: Pronoun Resolution (it, that)",
    "full_test_9",
    [
        "Add cookies to shopping",
        "Remove it"
    ]
)

# TEST 10: Multi-System Orchestration
run_conversation_test(
    "TEST 10: Multi-System - Shopping AND Calendar",
    "full_test_10",
    [
        "Add wine to shopping and schedule dinner party Friday at 7pm"
    ]
)

# TEST 11: Context Chain
run_conversation_test(
    "TEST 11: Context Chain - Maintain Topic Across Turns",
    "full_test_11",
    [
        "I'm planning a road trip",
        "It's in two weeks",
        "To California",
        "Add snacks to my shopping list for the trip"
    ]
)

# TEST 12: Emotional Intelligence
run_conversation_test(
    "TEST 12: Emotional Intelligence - Stress Response",
    "full_test_12",
    [
        "I'm really overwhelmed with work",
        "I have three deadlines this week"
    ]
)

# TEST 13: Planning
run_conversation_test(
    "TEST 13: Planning - Organize My Day",
    "full_test_13",
    [
        "Plan my day tomorrow"
    ]
)

# TEST 14: Query Then Action
run_conversation_test(
    "TEST 14: Query Then Action - List Then Add",
    "full_test_14",
    [
        "What's on my shopping list?",
        "Add cheese to it"
    ]
)

# TEST 15: Complex Natural Language
run_conversation_test(
    "TEST 15: Complex Natural Language Request",
    "full_test_15",
    [
        "I need to get some groceries for dinner tonight"
    ]
)

# TEST 16: Person Information
run_conversation_test(
    "TEST 16: Person Information - Store and Recall",
    "full_test_16",
    [
        "My sister Sarah lives in Boston",
        "Where does Sarah live?",
        "What's my sister's name?"
    ]
)

# TEST 17: Time-based Memory
run_conversation_test(
    "TEST 17: Time-based Memory - What Did I Just Say",
    "full_test_17",
    [
        "My birthday is June 15th",
        "What did I just tell you about my birthday?"
    ]
)

# TEST 18: Sequential Actions
run_conversation_test(
    "TEST 18: Sequential Actions - Then/After",
    "full_test_18",
    [
        "Add milk to shopping then schedule grocery run tomorrow"
    ]
)

# TEST 19: Conversational Repair
run_conversation_test(
    "TEST 19: Conversational Repair - Wait, I Meant...",
    "full_test_19",
    [
        "Add bananas to my list",
        "Wait, make that strawberries instead"
    ]
)

# TEST 20: Natural Greeting & Follow-up
run_conversation_test(
    "TEST 20: Natural Greeting and Engagement",
    "full_test_20",
    [
        "Hey Zoe!",
        "How are you doing?",
        "Can you help me plan my week?"
    ]
)

print("╔" + "═" * 78 + "╗")
print("║" + " " * 25 + "✅ ALL 20 TESTS COMPLETE" + " " * 27 + "║")
print("╚" + "═" * 78 + "╝")
print()
print("Review the conversations above to judge:")
print("  • Response quality and naturalness")
print("  • Memory recall accuracy")
print("  • Action execution")
print("  • Personality and warmth")
print("  • Overall human-like feel")
print()


