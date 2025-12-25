#!/usr/bin/env python3
"""Simple but comprehensive test suite for Zoe"""
import requests
import json
import time
import sqlite3

BASE_URL = "http://localhost:8000/api/chat/"
DB_PATH = "/app/data/zoe.db"

def test(user_id, message, expected_keyword, test_name):
    """Simple test function"""
    print(f"\n{'='*60}")
    print(f"TEST: {test_name}")
    print(f"User: {user_id} | Message: {message}")
    
    try:
        resp = requests.post(
            BASE_URL,
            params={"user_id": user_id, "stream": "false"},
            json={"message": message, "context": {}},
            timeout=30
        )
        
        if resp.status_code == 200:
            data = resp.json()
            response_text = data.get("response", "")
            routing = data.get("routing", "unknown")
            
            print(f"Routing: {routing}")
            print(f"Response: {response_text[:120]}...")
            
            if expected_keyword:
                if expected_keyword.lower() in response_text.lower():
                    print(f"✅ PASS - Found '{expected_keyword}'")
                    return True
                else:
                    print(f"❌ FAIL - Expected '{expected_keyword}' not found")
                    return False
            else:
                print(f"✅ PASS - Request completed")
                return True
        else:
            print(f"❌ FAIL - HTTP {resp.status_code}")
            return False
    except Exception as e:
        print(f"❌ FAIL - {str(e)[:100]}")
        return False

print("\n" + "="*70)
print("COMPREHENSIVE TEST SUITE - ZOE")
print("="*70)

test_user = f"final_test_{int(time.time())}"
results = []

# Test 1: Calendar Event
results.append(test(test_user, "Add dentist appointment tomorrow at 2pm", None, "Calendar: Dentist appointment"))
time.sleep(3)

# Verify in database
conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()
cursor.execute("SELECT title FROM events WHERE user_id = ? ORDER BY created_at DESC LIMIT 1", (test_user,))
event = cursor.fetchone()
if event and "dentist" in event[0].lower():
    print(f"✅ DATABASE CHECK: Event found - {event[0]}")
    results.append(True)
else:
    print(f"❌ DATABASE CHECK: Event not found (got: {event})")
    results.append(False)
conn.close()

time.sleep(3)

# Test 2: Shopping List
results.append(test(test_user, "Add milk to my shopping list", None, "Shopping: Add milk"))
time.sleep(3)

# Verify in database
conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()
cursor.execute("""
    SELECT li.task_text 
    FROM list_items li 
    JOIN lists l ON li.list_id = l.id 
    WHERE l.user_id = ? 
    ORDER BY li.created_at DESC LIMIT 1
""", (test_user,))
item = cursor.fetchone()
if item and "milk" in item[0].lower():
    print(f"✅ DATABASE CHECK: Item found - {item[0]}")
    results.append(True)
else:
    print(f"❌ DATABASE CHECK: Item not found (got: {item})")
    results.append(False)
conn.close()

time.sleep(3)

# Test 3: Store Memory
results.append(test(test_user, "My favorite color is red", None, "Memory: Store favorite color"))
time.sleep(3)

# Verify in database
conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()
cursor.execute("SELECT fact_key, fact_value FROM self_facts WHERE user_id = ?", (test_user,))
facts = cursor.fetchall()
if facts and any("red" in f[1].lower() for f in facts):
    print(f"✅ DATABASE CHECK: Fact stored - {facts}")
    results.append(True)
else:
    print(f"❌ DATABASE CHECK: Fact not stored (got: {facts})")
    results.append(False)
conn.close()

time.sleep(3)

# Test 4: Recall Memory
results.append(test(test_user, "What is my favorite color?", "red", "Memory: Recall favorite color"))
time.sleep(3)

# Test 5: User Isolation
test_user_2 = f"final_test_2_{int(time.time())}"
results.append(test(test_user_2, "My favorite color is blue", None, "Isolation: User 2 stores blue"))
time.sleep(3)

results.append(test(test_user_2, "What is my favorite color?", "blue", "Isolation: User 2 recalls blue"))
time.sleep(3)

results.append(test(test_user, "What is my favorite color?", "red", "Isolation: User 1 still has red"))

# Final Results
print("\n" + "="*70)
print("FINAL RESULTS")
print("="*70)
passed = sum(results)
total = len(results)
print(f"Passed: {passed}/{total}")
print(f"Pass Rate: {passed/total*100:.1f}%")

if passed == total:
    print("\n🎉 ALL TESTS PASSED! System is production-ready!")
elif passed/total >= 0.8:
    print("\n✅ GOOD - Most tests passed")
else:
    print("\n⚠️  NEEDS WORK - Multiple failures")

print("="*70 + "\n")

