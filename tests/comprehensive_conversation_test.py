#!/usr/bin/env python3
"""
Comprehensive Real-Time Conversation Test Suite
Tests all experts, tools, and conversational scenarios
"""

import requests
import time
import json
from datetime import datetime
from typing import List, Dict, Tuple

BASE_URL = "http://localhost:8000"
TIMEOUT = 60  # Increased timeout for Pi 5

class ConversationTest:
    def __init__(self, name: str, turns: List[Dict], expected_keywords: List[str], category: str):
        self.name = name
        self.turns = turns
        self.expected_keywords = expected_keywords
        self.category = category
        self.passed = False
        self.response_times = []
        self.responses = []
        self.error = None

def run_test(test: ConversationTest) -> Tuple[bool, str]:
    """Run a single conversation test"""
    user_id = f"test_{test.name.replace(' ', '_').lower()}"
    
    try:
        for i, turn in enumerate(test.turns):
            start = time.time()
            
            response = requests.post(
                f"{BASE_URL}/api/chat",
                json={"message": turn["message"], "user_id": user_id},
                timeout=TIMEOUT
            )
            
            duration = time.time() - start
            test.response_times.append(duration)
            
            if response.status_code != 200:
                return False, f"HTTP {response.status_code}"
            
            data = response.json()
            test.responses.append(data.get("response", ""))
            
            # Small delay between turns
            if i < len(test.turns) - 1:
                time.sleep(1)
        
        # Check if expected keywords appear in ANY response
        all_responses = " ".join(test.responses).lower()
        
        if test.expected_keywords:
            for keyword in test.expected_keywords:
                if keyword.lower() in all_responses:
                    return True, f"✓ Found '{keyword}'"
            return False, f"Missing keywords: {test.expected_keywords}"
        
        return True, "✓ Completed"
        
    except requests.Timeout:
        return False, "Timeout"
    except Exception as e:
        return False, str(e)[:50]

# ============================================================================
# TEST SCENARIOS
# ============================================================================

tests = [
    # TEMPORAL MEMORY TESTS (10)
    ConversationTest(
        "Temporal: Basic recall",
        [
            {"message": "My name is Alice"},
            {"message": "What's my name?"}
        ],
        ["alice"],
        "Temporal Memory"
    ),
    ConversationTest(
        "Temporal: Multiple facts",
        [
            {"message": "I work at Google"},
            {"message": "I live in Seattle"},
            {"message": "Where do I work and live?"}
        ],
        ["google", "seattle"],
        "Temporal Memory"
    ),
    ConversationTest(
        "Temporal: Pet name",
        [
            {"message": "My dog is named Buddy"},
            {"message": "What's my dog's name?"}
        ],
        ["buddy"],
        "Temporal Memory"
    ),
    ConversationTest(
        "Temporal: Food preference",
        [
            {"message": "I hate broccoli"},
            {"message": "What food do I hate?"}
        ],
        ["broccoli"],
        "Temporal Memory"
    ),
    ConversationTest(
        "Temporal: Number recall",
        [
            {"message": "My birthday is June 15th"},
            {"message": "When's my birthday?"}
        ],
        ["june", "15"],
        "Temporal Memory"
    ),
    ConversationTest(
        "Temporal: Color preference",
        [
            {"message": "Blue is my favorite color"},
            {"message": "What color do I like?"}
        ],
        ["blue"],
        "Temporal Memory"
    ),
    ConversationTest(
        "Temporal: Hobby recall",
        [
            {"message": "I love playing guitar"},
            {"message": "What do I love doing?"}
        ],
        ["guitar"],
        "Temporal Memory"
    ),
    ConversationTest(
        "Temporal: Three-turn memory",
        [
            {"message": "I drive a Honda"},
            {"message": "It's red"},
            {"message": "What car do I drive and what color?"}
        ],
        ["honda", "red"],
        "Temporal Memory"
    ),
    ConversationTest(
        "Temporal: Job title",
        [
            {"message": "I'm a software engineer"},
            {"message": "What's my job?"}
        ],
        ["software", "engineer"],
        "Temporal Memory"
    ),
    ConversationTest(
        "Temporal: Pronoun resolution",
        [
            {"message": "My sister lives in Boston"},
            {"message": "Where does she live?"}
        ],
        ["boston"],
        "Temporal Memory"
    ),
    
    # LIST OPERATIONS (10)
    ConversationTest(
        "Lists: Add single item",
        [{"message": "Add milk to shopping list"}],
        ["milk", "shopping", "added"],
        "Lists"
    ),
    ConversationTest(
        "Lists: Add multiple items",
        [{"message": "Add eggs, bread, and butter to shopping"}],
        ["added"],
        "Lists"
    ),
    ConversationTest(
        "Lists: Todo task",
        [{"message": "Add call dentist to my todo list"}],
        ["dentist", "added"],
        "Lists"
    ),
    ConversationTest(
        "Lists: Priority item",
        [{"message": "Add urgent task: finish report"}],
        ["report", "added"],
        "Lists"
    ),
    ConversationTest(
        "Lists: Correction",
        [
            {"message": "Add bananas to shopping"},
            {"message": "Actually change that to apples"}
        ],
        ["apple"],
        "Lists"
    ),
    ConversationTest(
        "Lists: Remove item",
        [
            {"message": "Add cookies to shopping"},
            {"message": "Remove it"}
        ],
        ["removed"],
        "Lists"
    ),
    ConversationTest(
        "Lists: Query list",
        [{"message": "What's on my shopping list?"}],
        ["shopping", "list"],
        "Lists"
    ),
    ConversationTest(
        "Lists: Add with context",
        [
            {"message": "I need to buy groceries"},
            {"message": "Add pasta to the list"}
        ],
        ["pasta", "added"],
        "Lists"
    ),
    ConversationTest(
        "Lists: Multiple lists",
        [
            {"message": "Add milk to shopping"},
            {"message": "Add read book to todo"}
        ],
        ["added"],
        "Lists"
    ),
    ConversationTest(
        "Lists: Natural language",
        [{"message": "Don't let me forget to buy cheese"}],
        ["cheese", "added"],
        "Lists"
    ),
    
    # CALENDAR OPERATIONS (10)
    ConversationTest(
        "Calendar: Create event",
        [{"message": "Schedule dentist appointment tomorrow at 2pm"}],
        ["dentist", "scheduled", "2pm"],
        "Calendar"
    ),
    ConversationTest(
        "Calendar: Event with details",
        [{"message": "Create meeting with John next Monday at 10am"}],
        ["john", "monday", "10am"],
        "Calendar"
    ),
    ConversationTest(
        "Calendar: Event correction",
        [
            {"message": "Schedule lunch tomorrow at 12pm"},
            {"message": "Actually make that 1pm"}
        ],
        ["1pm", "moved"],
        "Calendar"
    ),
    ConversationTest(
        "Calendar: Query events",
        [{"message": "What's on my calendar today?"}],
        ["calendar", "today"],
        "Calendar"
    ),
    ConversationTest(
        "Calendar: Event context",
        [
            {"message": "Schedule team standup tomorrow"},
            {"message": "Make it at 9am"}
        ],
        ["9am"],
        "Calendar"
    ),
    ConversationTest(
        "Calendar: All-day event",
        [{"message": "Mark tomorrow as vacation"}],
        ["vacation", "tomorrow"],
        "Calendar"
    ),
    ConversationTest(
        "Calendar: Recurring hint",
        [{"message": "Schedule weekly team meeting on Mondays"}],
        ["monday", "meeting"],
        "Calendar"
    ),
    ConversationTest(
        "Calendar: Event with person",
        [{"message": "Schedule call with Sarah on Friday"}],
        ["sarah", "friday"],
        "Calendar"
    ),
    ConversationTest(
        "Calendar: Time change",
        [
            {"message": "Create event tomorrow at 3pm"},
            {"message": "Move that to 4pm"}
        ],
        ["4pm"],
        "Calendar"
    ),
    ConversationTest(
        "Calendar: Event question",
        [
            {"message": "Schedule doctor visit next week"},
            {"message": "When is that appointment?"}
        ],
        ["next week", "doctor"],
        "Calendar"
    ),
    
    # ORCHESTRATION (10)
    ConversationTest(
        "Orchestration: Multi-system",
        [{"message": "Add milk to shopping and schedule dentist tomorrow"}],
        ["milk", "dentist"],
        "Orchestration"
    ),
    ConversationTest(
        "Orchestration: Plan my day",
        [{"message": "Plan my day tomorrow"}],
        ["plan", "tomorrow"],
        "Orchestration"
    ),
    ConversationTest(
        "Orchestration: Sequential tasks",
        [{"message": "Add eggs to shopping then schedule grocery run"}],
        ["eggs"],
        "Orchestration"
    ),
    ConversationTest(
        "Orchestration: Complex request",
        [{"message": "Help me plan a dinner party next Friday"}],
        ["dinner", "friday"],
        "Orchestration"
    ),
    ConversationTest(
        "Orchestration: Multi-step",
        [
            {"message": "I need to organize my week"},
            {"message": "Add important tasks and show my calendar"}
        ],
        ["calendar", "task"],
        "Orchestration"
    ),
    ConversationTest(
        "Orchestration: Event + list",
        [{"message": "Schedule birthday party and add decorations to shopping"}],
        ["birthday", "decorations"],
        "Orchestration"
    ),
    ConversationTest(
        "Orchestration: Plan morning",
        [{"message": "Plan my morning tomorrow"}],
        ["morning", "tomorrow"],
        "Orchestration"
    ),
    ConversationTest(
        "Orchestration: Multiple actions",
        [{"message": "Add coffee to shopping, schedule meeting, and remind me to call mom"}],
        ["coffee"],
        "Orchestration"
    ),
    ConversationTest(
        "Orchestration: Context chain",
        [
            {"message": "I have a presentation Friday"},
            {"message": "Add practice time and buy supplies"}
        ],
        ["practice", "supplies"],
        "Orchestration"
    ),
    ConversationTest(
        "Orchestration: Show everything",
        [{"message": "Show me all my tasks and events"}],
        ["task", "event"],
        "Orchestration"
    ),
    
    # EDGE CASES & NATURAL LANGUAGE (10)
    ConversationTest(
        "Edge: Ambiguous pronoun",
        [
            {"message": "Add bread to shopping"},
            {"message": "Also cookies"},
            {"message": "Remove the first one"}
        ],
        ["bread", "removed"],
        "Edge Cases"
    ),
    ConversationTest(
        "Edge: Implicit reference",
        [
            {"message": "Schedule meeting tomorrow"},
            {"message": "With who?"}
        ],
        ["meeting"],
        "Edge Cases"
    ),
    ConversationTest(
        "Edge: Negative statement",
        [
            {"message": "I don't like spinach"},
            {"message": "What vegetable don't I like?"}
        ],
        ["spinach"],
        "Edge Cases"
    ),
    ConversationTest(
        "Edge: Conversational repair",
        [
            {"message": "Add oranges to shopping"},
            {"message": "Wait, I meant apples"}
        ],
        ["apple"],
        "Edge Cases"
    ),
    ConversationTest(
        "Edge: Context switch",
        [
            {"message": "Schedule dentist tomorrow"},
            {"message": "Add milk to shopping"},
            {"message": "When's that dentist appointment?"}
        ],
        ["tomorrow", "dentist"],
        "Edge Cases"
    ),
    ConversationTest(
        "Edge: Elliptical speech",
        [
            {"message": "Do I have meetings today?"},
            {"message": "Tomorrow?"}
        ],
        ["tomorrow"],
        "Edge Cases"
    ),
    ConversationTest(
        "Edge: Follow-up action",
        [
            {"message": "What's on my shopping list?"},
            {"message": "Add wine to it"}
        ],
        ["wine", "added"],
        "Edge Cases"
    ),
    ConversationTest(
        "Edge: Clarification",
        [
            {"message": "Add that to my list"},
            {"message": "I mean add cheese"}
        ],
        ["cheese"],
        "Edge Cases"
    ),
    ConversationTest(
        "Edge: Time ambiguity",
        [
            {"message": "Schedule call"},
            {"message": "Tomorrow at 3"}
        ],
        ["3", "tomorrow"],
        "Edge Cases"
    ),
    ConversationTest(
        "Edge: Compound correction",
        [
            {"message": "Add bananas and oranges"},
            {"message": "Actually forget the bananas"}
        ],
        ["orange"],
        "Edge Cases"
    ),
]

# ============================================================================
# RUN TESTS
# ============================================================================

def main():
    print("=" * 80)
    print("🧪 COMPREHENSIVE CONVERSATION TEST SUITE")
    print("=" * 80)
    print(f"Total Tests: {len(tests)}")
    print(f"Timeout: {TIMEOUT}s per request")
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)
    print()
    
    results_by_category = {}
    total_passed = 0
    total_failed = 0
    total_time = 0
    
    start_time = time.time()
    
    for i, test in enumerate(tests, 1):
        print(f"[{i}/{len(tests)}] {test.category}: {test.name}...", end=" ", flush=True)
        
        test_start = time.time()
        passed, message = run_test(test)
        test_duration = time.time() - test_start
        total_time += test_duration
        
        test.passed = passed
        
        # Track by category
        if test.category not in results_by_category:
            results_by_category[test.category] = {"passed": 0, "failed": 0, "tests": []}
        
        if passed:
            total_passed += 1
            results_by_category[test.category]["passed"] += 1
            avg_response = sum(test.response_times) / len(test.response_times) if test.response_times else 0
            print(f"✅ PASS ({avg_response:.1f}s avg) - {message}")
        else:
            total_failed += 1
            results_by_category[test.category]["failed"] += 1
            print(f"❌ FAIL - {message}")
        
        results_by_category[test.category]["tests"].append(test)
    
    # ========================================================================
    # SUMMARY
    # ========================================================================
    
    total_duration = time.time() - start_time
    
    print()
    print("=" * 80)
    print("📊 TEST RESULTS SUMMARY")
    print("=" * 80)
    print()
    
    # Overall stats
    pass_rate = (total_passed / len(tests) * 100) if tests else 0
    print(f"✅ Passed: {total_passed}/{len(tests)} ({pass_rate:.1f}%)")
    print(f"❌ Failed: {total_failed}/{len(tests)}")
    print(f"⏱️  Total Time: {total_duration:.1f}s")
    print(f"📈 Avg Time/Test: {total_time/len(tests):.1f}s")
    print()
    
    # Category breakdown
    print("📂 Results by Category:")
    print("-" * 80)
    
    for category in sorted(results_by_category.keys()):
        stats = results_by_category[category]
        total_cat = stats["passed"] + stats["failed"]
        cat_pass_rate = (stats["passed"] / total_cat * 100) if total_cat > 0 else 0
        
        status = "✅" if cat_pass_rate >= 80 else "⚠️" if cat_pass_rate >= 60 else "❌"
        print(f"{status} {category:20} {stats['passed']:2}/{total_cat:2} ({cat_pass_rate:5.1f}%)")
    
    print()
    print("-" * 80)
    
    # Failed tests details
    if total_failed > 0:
        print()
        print("❌ FAILED TESTS:")
        print("-" * 80)
        
        for category, stats in sorted(results_by_category.items()):
            failed_tests = [t for t in stats["tests"] if not t.passed]
            if failed_tests:
                print(f"\n{category}:")
                for test in failed_tests:
                    print(f"  • {test.name}")
                    if test.responses:
                        print(f"    Last response: {test.responses[-1][:100]}...")
    
    # Performance stats
    print()
    print("⚡ PERFORMANCE STATS:")
    print("-" * 80)
    
    all_response_times = []
    for test in tests:
        all_response_times.extend(test.response_times)
    
    if all_response_times:
        avg_response = sum(all_response_times) / len(all_response_times)
        max_response = max(all_response_times)
        min_response = min(all_response_times)
        
        print(f"Avg Response Time: {avg_response:.2f}s")
        print(f"Min Response Time: {min_response:.2f}s")
        print(f"Max Response Time: {max_response:.2f}s")
        print(f"Timeouts: {sum(1 for t in tests if 'Timeout' in str(t.error))}")
    
    print()
    print("=" * 80)
    print(f"✨ Test suite completed at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)
    
    # Exit code based on pass rate
    if pass_rate >= 80:
        print("\n🎉 EXCELLENT: >80% pass rate - System ready for production!")
        return 0
    elif pass_rate >= 60:
        print("\n⚠️  GOOD: >60% pass rate - Minor improvements needed")
        return 0
    else:
        print("\n❌ NEEDS WORK: <60% pass rate - Significant issues detected")
        return 1

if __name__ == "__main__":
    exit(main())



