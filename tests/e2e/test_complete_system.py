#!/usr/bin/env python3
"""Complete system test to verify all functionality works"""
import requests
import json

BASE_URL = "http://localhost:8000"

def test_chat(user_id, message, expected_in_response=None):
    """Test a chat message"""
    print(f"\n{'='*80}")
    print(f"USER ({user_id}): {message}")
    print(f"{'='*80}")
    
    response = requests.post(
        f"{BASE_URL}/api/chat/",
        params={"user_id": user_id, "stream": "false"},
        json={"message": message, "context": {}},
        timeout=30
    )
    
    if response.status_code != 200:
        print(f"❌ ERROR: {response.status_code}")
        print(response.text)
        return False
    
    data = response.json()
    response_text = data.get("response", "")
    
    print(f"ZOE: {response_text[:500]}")
    if len(response_text) > 500:
        print(f"... ({len(response_text)} chars total)")
    
    if expected_in_response:
        if expected_in_response.lower() in response_text.lower():
            print(f"✅ PASS: Response contains '{expected_in_response}'")
            return True
        else:
            print(f"❌ FAIL: Response missing '{expected_in_response}'")
            return False
    
    return True

def main():
    print("🚀 TESTING ZOE COMPLETE SYSTEM")
    print("="*80)
    
    tests_passed = 0
    tests_failed = 0
    
    # Test 1: Introduction with self-facts
    print("\n📋 TEST 1: User introduces themselves")
    if test_chat("test_user_1", "Hi, my name is Alice and my favorite food is pizza"):
        tests_passed += 1
    else:
        tests_failed += 1
    
    # Test 2: Greeting (should use name)
    print("\n📋 TEST 2: Greeting with name recall")
    if test_chat("test_user_1", "Hello!", expected_in_response="Alice"):
        tests_passed += 1
    else:
        tests_failed += 1
    
    # Test 3: Calendar event
    print("\n📋 TEST 3: Add calendar event")
    if test_chat("test_user_1", "I have a dentist appointment tomorrow at 3pm"):
        tests_passed += 1
    else:
        tests_failed += 1
    
    # Test 4: Shopping list
    print("\n📋 TEST 4: Add to shopping list")
    if test_chat("test_user_1", "Add milk and bread to my shopping list"):
        tests_passed += 1
    else:
        tests_failed += 1
    
    # Test 5: Check for fictional names
    print("\n📋 TEST 5: No fictional names in response")
    response = requests.post(
        f"{BASE_URL}/api/chat/",
        params={"user_id": "test_user_1", "stream": "false"},
        json={"message": "Who do you know?", "context": {}},
        timeout=30
    )
    response_text = response.json().get("response", "")
    fictional_names = ["Sarah", "John Smith", "Integration Test"]
    found_fictional = [name for name in fictional_names if name.lower() in response_text.lower()]
    
    if found_fictional:
        print(f"❌ FAIL: Found fictional names: {found_fictional}")
        print(f"Response: {response_text}")
        tests_failed += 1
    else:
        print(f"✅ PASS: No fictional names found")
        tests_passed += 1
    
    # Summary
    print(f"\n{'='*80}")
    print(f"RESULTS: {tests_passed} passed, {tests_failed} failed")
    print(f"{'='*80}")
    
    return tests_failed == 0

if __name__ == "__main__":
    import sys
    success = main()
    sys.exit(0 if success else 1)






