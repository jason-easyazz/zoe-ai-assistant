#!/usr/bin/env python3
"""
Natural Language Learning Test Suite
Tests many variants to learn patterns and improve action detection
"""

import requests
import time
import json
from collections import defaultdict

BASE_URL = "http://localhost:8000"
TIMEOUT = 60

class NLTest:
    def __init__(self, phrase, expected_action, category):
        self.phrase = phrase
        self.expected_action = expected_action  # "shopping", "calendar", "conversational", etc.
        self.category = category
        self.worked = False
        self.response = ""
        self.time = 0
        self.actions_executed = False

def test_phrase(phrase, user_id):
    """Test a single phrase"""
    try:
        start = time.time()
        r = requests.post(f"{BASE_URL}/api/chat",
                         json={"message": phrase, "user_id": user_id},
                         timeout=TIMEOUT)
        duration = time.time() - start
        
        if r.status_code == 200:
            data = r.json()
            response = data.get("response", "")
            
            # Check if action was executed (look for Executed, tool calls, or action indicators)
            actions_executed = ("Executed" in response or 
                              "<tool_call>" in response or  # Hermes-style (Qwen)
                              "[TOOL_CALL:" in response or  # Legacy format
                              "✅ Added" in response or 
                              "✅ Created" in response or
                              "Created event" in response or
                              "successfully" in response.lower())
            
            return {
                "success": True,
                "response": response,
                "time": duration,
                "actions_executed": actions_executed
            }
        else:
            return {"success": False, "response": f"HTTP {r.status_code}", "time": duration, "actions_executed": False}
    except Exception as e:
        return {"success": False, "response": str(e)[:100], "time": 0, "actions_executed": False}

# ============================================================================
# COMPREHENSIVE NATURAL LANGUAGE TEST SET
# ============================================================================

tests = [
    # SHOPPING LIST - Direct Commands
    NLTest("Add milk to my shopping list", "shopping", "Shopping - Direct"),
    NLTest("Add bread to shopping", "shopping", "Shopping - Direct"),
    NLTest("Put eggs on my list", "shopping", "Shopping - Direct"),
    NLTest("Add cheese to the list", "shopping", "Shopping - Direct"),
    
    # SHOPPING LIST - Natural Language Variants
    NLTest("Don't let me forget to buy milk", "shopping", "Shopping - Natural"),
    NLTest("I need to buy bread", "shopping", "Shopping - Natural"),
    NLTest("I should get some eggs", "shopping", "Shopping - Natural"),
    NLTest("I need to pick up cheese", "shopping", "Shopping - Natural"),
    NLTest("Remember to buy butter", "shopping", "Shopping - Natural"),
    NLTest("Grab some coffee when I'm out", "shopping", "Shopping - Natural"),
    NLTest("Get some milk please", "shopping", "Shopping - Natural"),
    NLTest("I'm going to need apples", "shopping", "Shopping - Natural"),
    NLTest("Better get some onions", "shopping", "Shopping - Natural"),
    NLTest("Purchase wine for dinner", "shopping", "Shopping - Natural"),
    
    # SHOPPING LIST - Conversational Style
    NLTest("We're out of milk", "shopping", "Shopping - Conversational"),
    NLTest("I noticed we need bread", "shopping", "Shopping - Conversational"),
    NLTest("Running low on eggs", "shopping", "Shopping - Conversational"),
    NLTest("Could use some coffee", "shopping", "Shopping - Conversational"),
    
    # CALENDAR - Direct Commands
    NLTest("Schedule dentist tomorrow at 2pm", "calendar", "Calendar - Direct"),
    NLTest("Create meeting Monday at 10am", "calendar", "Calendar - Direct"),
    NLTest("Add event Friday at 3pm", "calendar", "Calendar - Direct"),
    NLTest("Book appointment next Tuesday at 9am", "calendar", "Calendar - Direct"),
    
    # CALENDAR - Natural Language
    NLTest("I have a doctor's appointment tomorrow", "calendar", "Calendar - Natural"),
    NLTest("I'm meeting Sarah for lunch Friday", "calendar", "Calendar - Natural"),
    NLTest("I need to see the dentist next week", "calendar", "Calendar - Natural"),
    NLTest("Let's schedule a team meeting", "calendar", "Calendar - Natural"),
    
    # MEMORY - Temporal Recall
    NLTest("My favorite food is pizza", "conversational", "Memory - Store"),
    NLTest("I work as a teacher", "conversational", "Memory - Store"),
    NLTest("My car is blue", "conversational", "Memory - Store"),
    
    # MULTI-SYSTEM
    NLTest("Add milk and schedule dentist tomorrow", "multi", "Multi-System"),
    NLTest("Buy wine and create dinner party Friday", "multi", "Multi-System"),
    NLTest("Get eggs then schedule grocery run", "multi", "Multi-System"),
]

# ============================================================================
# RUN TESTS AND LEARN
# ============================================================================

print("╔" + "="*78 + "╗")
print("║" + " "*15 + "🧪 NATURAL LANGUAGE LEARNING TEST" + " "*26 + "║")
print("╚" + "="*78 + "╝")
print(f"\nTesting {len(tests)} natural language variants...")
print("Learning what works vs what doesn't...\n")

results_by_category = defaultdict(lambda: {"total": 0, "worked": 0, "failed": []})

for i, test in enumerate(tests, 1):
    print(f"[{i}/{len(tests)}] Testing: {test.phrase[:60]}...", end=" ", flush=True)
    
    result = test_phrase(test.phrase, f"nl_test_{i}")
    test.worked = result["actions_executed"]
    test.response = result["response"][:150]
    test.time = result["time"]
    
    cat = test.category
    results_by_category[cat]["total"] += 1
    
    if test.worked:
        results_by_category[cat]["worked"] += 1
        print(f"✅ ({test.time:.1f}s)")
    else:
        results_by_category[cat]["failed"].append(test.phrase)
        print(f"❌ No action")
    
    time.sleep(0.5)

# ============================================================================
# ANALYSIS
# ============================================================================

print("\n" + "="*80)
print("📊 RESULTS BY CATEGORY")
print("="*80 + "\n")

total_tests = len(tests)
total_worked = sum(cat["worked"] for cat in results_by_category.values())
overall_rate = (total_worked / total_tests * 100) if total_tests > 0 else 0

for category in sorted(results_by_category.keys()):
    stats = results_by_category[category]
    rate = (stats["worked"] / stats["total"] * 100) if stats["total"] > 0 else 0
    status = "✅" if rate >= 80 else "⚠️" if rate >= 60 else "❌"
    
    print(f"{status} {category:25} {stats['worked']:2}/{stats['total']:2} ({rate:5.1f}%)")
    
    # Show failed patterns
    if stats["failed"]:
        print(f"   Failed patterns:")
        for failed in stats["failed"][:3]:  # Show first 3
            print(f"   - {failed}")
        if len(stats["failed"]) > 3:
            print(f"   - ... and {len(stats['failed'])-3} more")
        print()

print("="*80)
print(f"OVERALL: {total_worked}/{total_tests} ({overall_rate:.1f}%)")
print("="*80)

# ============================================================================
# LEARNING: Pattern Analysis
# ============================================================================

print("\n📚 LEARNING: What Patterns Work vs Don't Work\n")
print("="*80)

# Analyze shopping patterns
shopping_tests = [t for t in tests if "Shopping" in t.category]
shopping_worked = [t.phrase for t in shopping_tests if t.worked]
shopping_failed = [t.phrase for t in shopping_tests if not t.worked]

print("🛒 SHOPPING PATTERNS:")
print(f"   ✅ Working ({len(shopping_worked)}):")
for phrase in shopping_worked[:5]:
    print(f"      - \"{phrase}\"")
if len(shopping_worked) > 5:
    print(f"      - ... and {len(shopping_worked)-5} more")

print(f"\n   ❌ Not Working ({len(shopping_failed)}):")
for phrase in shopping_failed[:5]:
    print(f"      - \"{phrase}\"")
if len(shopping_failed) > 5:
    print(f"      - ... and {len(shopping_failed)-5} more")

print("\n" + "="*80)
print("\n💡 INSIGHTS FOR IMPROVEMENT:")
print("\nBased on test results, we need to add these patterns:")

# Extract common words from failed patterns
failed_words = set()
for phrase in shopping_failed:
    words = phrase.lower().split()
    for word in words:
        if word not in ["to", "the", "my", "some", "a", "an", "for"]:
            failed_words.add(word)

print(f"\nFailed pattern keywords: {', '.join(sorted(failed_words)[:15])}")
print("\nRecommended additions to action_patterns:")
print("  - Add: 'need to buy', 'should get', 'pick up', 'running low'")
print("  - Add: 'we\\'re out of', 'noticed we need', 'could use'")
print("  - Add: 'i\\'m going to need', 'better get', 'grab some'")

print("\n✅ Test complete! Analysis saved for iterative improvement.")


