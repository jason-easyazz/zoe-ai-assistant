#!/usr/bin/env python3
"""
Comprehensive Test Suite for Zoe Memory & Tool System
Tests all phases: Calendar Routing, Shopping Routing, Memory Storage & Recall, User Isolation
"""
import requests
import json
import time
import sqlite3
from datetime import datetime

BASE_URL = "http://localhost:8000/api/chat/"
DB_PATH = "/app/data/zoe.db"

class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    END = '\033[0m'

def test_api(user_id: str, message: str, test_name: str) -> dict:
    """Send a message to the API and return the response"""
    print(f"\n{Colors.BLUE}[TEST]{Colors.END} {test_name}")
    print(f"  User: {user_id}")
    print(f"  Message: {message}")
    
    try:
        response = requests.post(
            BASE_URL,
            params={"user_id": user_id, "stream": "false"},
            headers={"Content-Type": "application/json"},
            json={"message": message, "context": {}},
            timeout=15
        )
        
        if response.status_code == 200:
            data = response.json()
            routing = data.get("routing", "unknown")
            response_text = data.get("response", "")
            print(f"  Routing: {routing}")
            print(f"  Response: {response_text[:150]}...")
            return {"success": True, "data": data, "routing": routing, "response": response_text}
        else:
            print(f"  {Colors.RED}✗ API Error: {response.status_code}{Colors.END}")
            return {"success": False, "error": f"HTTP {response.status_code}"}
    except Exception as e:
        print(f"  {Colors.RED}✗ Exception: {str(e)}{Colors.END}")
        return {"success": False, "error": str(e)}

def check_database(query: str, params: tuple = ()) -> list:
    """Query the database directly"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(query, params)
    results = cursor.fetchall()
    conn.close()
    return results

def verify_result(condition: bool, success_msg: str, fail_msg: str) -> bool:
    """Print test result"""
    if condition:
        print(f"  {Colors.GREEN}✓ {success_msg}{Colors.END}")
        return True
    else:
        print(f"  {Colors.RED}✗ {fail_msg}{Colors.END}")
        return False

print(f"\n{'='*80}")
print(f"{Colors.BLUE}COMPREHENSIVE TEST SUITE - ZOE MEMORY & TOOL SYSTEM{Colors.END}")
print(f"{'='*80}\n")

test_results = {"passed": 0, "failed": 0, "total": 0}

# =============================================================================
# PHASE 2: CALENDAR ROUTING TESTS
# =============================================================================
print(f"\n{Colors.YELLOW}═══ PHASE 2: CALENDAR ROUTING TESTS ═══{Colors.END}")

test_user = f"test_calendar_{int(time.time())}"

# Test 1: Appointment with time
result = test_api(test_user, "Add dentist appointment tomorrow at 3pm", "Calendar: Appointment with time")
test_results["total"] += 1
if result["success"]:
    events = check_database("SELECT title, start_time FROM events WHERE user_id = ? ORDER BY created_at DESC LIMIT 1", (test_user,))
    if verify_result(len(events) > 0 and "dentist" in events[0][0].lower(), 
                     f"Event created: {events[0] if events else 'None'}", 
                     "Event not found in database"):
        test_results["passed"] += 1
    else:
        test_results["failed"] += 1
else:
    test_results["failed"] += 1

time.sleep(2)

# Test 2: Meeting with person
result = test_api(test_user, "Schedule meeting with John on Friday at 2pm", "Calendar: Meeting with person")
test_results["total"] += 1
if result["success"]:
    events = check_database("SELECT title FROM events WHERE user_id = ? ORDER BY created_at DESC LIMIT 1", (test_user,))
    if verify_result(len(events) > 0 and "meeting" in events[0][0].lower(), 
                     f"Meeting created: {events[0][0]}", 
                     "Meeting not found"):
        test_results["passed"] += 1
    else:
        test_results["failed"] += 1
else:
    test_results["failed"] += 1

time.sleep(2)

# Test 3: Doctor visit
result = test_api(test_user, "Book doctor visit next Monday morning", "Calendar: Doctor visit")
test_results["total"] += 1
if result["success"]:
    events = check_database("SELECT title FROM events WHERE user_id = ? ORDER BY created_at DESC LIMIT 1", (test_user,))
    if verify_result(len(events) > 0 and "doctor" in events[0][0].lower(), 
                     f"Doctor visit created: {events[0][0]}", 
                     "Doctor visit not found"):
        test_results["passed"] += 1
    else:
        test_results["failed"] += 1
else:
    test_results["failed"] += 1

# =============================================================================
# PHASE 2: SHOPPING LIST ROUTING TESTS
# =============================================================================
print(f"\n{Colors.YELLOW}═══ PHASE 2: SHOPPING LIST ROUTING TESTS ═══{Colors.END}")

test_user_shop = f"test_shopping_{int(time.time())}"

time.sleep(2)

# Test 4: Add to shopping list
result = test_api(test_user_shop, "Add milk to my shopping list", "Shopping: Add item to list")
test_results["total"] += 1
if result["success"]:
    # Give it a moment to process
    time.sleep(1)
    items = check_database(
        "SELECT li.task_text FROM list_items li JOIN lists l ON li.list_id = l.id WHERE l.user_id = ? ORDER BY li.created_at DESC LIMIT 1", 
        (test_user_shop,)
    )
    if verify_result(len(items) > 0 and "milk" in items[0][0].lower(), 
                     f"Item added: {items[0][0]}", 
                     "Item not found in shopping list"):
        test_results["passed"] += 1
    else:
        test_results["failed"] += 1
else:
    test_results["failed"] += 1

time.sleep(2)

# Test 5: Need to buy
result = test_api(test_user_shop, "I need to buy bread and eggs", "Shopping: Need to buy")
test_results["total"] += 1
if result["success"]:
    time.sleep(1)
    items = check_database(
        "SELECT li.task_text FROM list_items li JOIN lists l ON li.list_id = l.id WHERE l.user_id = ? ORDER BY li.created_at DESC LIMIT 2", 
        (test_user_shop,)
    )
    if verify_result(len(items) > 0, 
                     f"Items added: {[i[0] for i in items]}", 
                     "No items found"):
        test_results["passed"] += 1
    else:
        test_results["failed"] += 1
else:
    test_results["failed"] += 1

# =============================================================================
# PHASE 3: MEMORY STORAGE TESTS
# =============================================================================
print(f"\n{Colors.YELLOW}═══ PHASE 3: MEMORY STORAGE TESTS ═══{Colors.END}")

test_user_mem = f"test_memory_{int(time.time())}"

time.sleep(2)

# Test 6: Store favorite color
result = test_api(test_user_mem, "My favorite color is blue", "Memory: Store favorite")
test_results["total"] += 1
if result["success"]:
    time.sleep(1)
    facts = check_database("SELECT fact_key, fact_value FROM self_facts WHERE user_id = ? AND fact_key LIKE '%color%'", (test_user_mem,))
    if verify_result(len(facts) > 0 and "blue" in facts[0][1].lower(), 
                     f"Fact stored: {facts[0]}", 
                     "Fact not stored"):
        test_results["passed"] += 1
    else:
        test_results["failed"] += 1
else:
    test_results["failed"] += 1

time.sleep(2)

# Test 7: Store hobby
result = test_api(test_user_mem, "I love playing guitar", "Memory: Store hobby")
test_results["total"] += 1
if result["success"]:
    time.sleep(1)
    facts = check_database("SELECT fact_key, fact_value FROM self_facts WHERE user_id = ?", (test_user_mem,))
    if verify_result(len(facts) > 1, 
                     f"Facts stored: {[(f[0], f[1]) for f in facts]}", 
                     "Second fact not stored"):
        test_results["passed"] += 1
    else:
        test_results["failed"] += 1
else:
    test_results["failed"] += 1

# =============================================================================
# PHASE 3: MEMORY RECALL TESTS
# =============================================================================
print(f"\n{Colors.YELLOW}═══ PHASE 3: MEMORY RECALL TESTS ═══{Colors.END}")

time.sleep(2)

# Test 8: Recall favorite color
result = test_api(test_user_mem, "What is my favorite color?", "Memory: Recall favorite color")
test_results["total"] += 1
if result["success"]:
    if verify_result("blue" in result["response"].lower(), 
                     f"Correctly recalled: blue", 
                     f"Failed to recall blue (got: {result['response'][:100]})"):
        test_results["passed"] += 1
    else:
        test_results["failed"] += 1
else:
    test_results["failed"] += 1

time.sleep(2)

# Test 9: Recall hobby
result = test_api(test_user_mem, "What do I like to do?", "Memory: Recall hobby")
test_results["total"] += 1
if result["success"]:
    if verify_result("guitar" in result["response"].lower(), 
                     f"Correctly recalled: guitar", 
                     f"Failed to recall guitar (got: {result['response'][:100]})"):
        test_results["passed"] += 1
    else:
        test_results["failed"] += 1
else:
    test_results["failed"] += 1

# =============================================================================
# USER ISOLATION TEST
# =============================================================================
print(f"\n{Colors.YELLOW}═══ USER ISOLATION TEST ═══{Colors.END}")

test_user_a = f"test_isolation_a_{int(time.time())}"
test_user_b = f"test_isolation_b_{int(time.time())}"

time.sleep(2)

# Store fact for user A
result_a = test_api(test_user_a, "My favorite food is pizza", "Isolation: User A stores fact")
time.sleep(2)

# Store different fact for user B
result_b = test_api(test_user_b, "My favorite food is sushi", "Isolation: User B stores fact")
time.sleep(2)

# User A recalls - should get pizza
result_a_recall = test_api(test_user_a, "What is my favorite food?", "Isolation: User A recalls")
test_results["total"] += 1
if result_a_recall["success"]:
    if verify_result("pizza" in result_a_recall["response"].lower() and "sushi" not in result_a_recall["response"].lower(), 
                     f"User A correctly isolated (got pizza, not sushi)", 
                     f"User isolation failed for A (got: {result_a_recall['response'][:100]})"):
        test_results["passed"] += 1
    else:
        test_results["failed"] += 1
else:
    test_results["failed"] += 1

time.sleep(2)

# User B recalls - should get sushi
result_b_recall = test_api(test_user_b, "What is my favorite food?", "Isolation: User B recalls")
test_results["total"] += 1
if result_b_recall["success"]:
    if verify_result("sushi" in result_b_recall["response"].lower() and "pizza" not in result_b_recall["response"].lower(), 
                     f"User B correctly isolated (got sushi, not pizza)", 
                     f"User isolation failed for B (got: {result_b_recall['response'][:100]})"):
        test_results["passed"] += 1
    else:
        test_results["failed"] += 1
else:
    test_results["failed"] += 1

# =============================================================================
# FINAL RESULTS
# =============================================================================
print(f"\n{'='*80}")
print(f"{Colors.YELLOW}FINAL TEST RESULTS{Colors.END}")
print(f"{'='*80}")
print(f"Total Tests: {test_results['total']}")
print(f"{Colors.GREEN}Passed: {test_results['passed']}{Colors.END}")
print(f"{Colors.RED}Failed: {test_results['failed']}{Colors.END}")

pass_rate = (test_results['passed'] / test_results['total'] * 100) if test_results['total'] > 0 else 0
print(f"\nPass Rate: {pass_rate:.1f}%")

if pass_rate >= 90:
    print(f"\n{Colors.GREEN}🎉 EXCELLENT! System is production-ready!{Colors.END}")
elif pass_rate >= 70:
    print(f"\n{Colors.YELLOW}⚠️  GOOD but needs improvement{Colors.END}")
else:
    print(f"\n{Colors.RED}❌ FAILED - Critical issues found{Colors.END}")

print(f"{'='*80}\n")

