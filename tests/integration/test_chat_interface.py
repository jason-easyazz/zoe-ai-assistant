#!/usr/bin/env python3
"""
Test script to verify chat interface and prompt structure
"""
import requests
import json
import time

BASE_URL = "http://localhost:8000"
TEST_USER = "chat_test_user"

def test_chat_interface():
    print("=" * 60)
    print("TESTING CHAT INTERFACE")
    print("=" * 60)
    
    # Test 1: Store a fact
    print("\n1. Storing fact: 'My name is Alice'")
    response1 = requests.post(
        f"{BASE_URL}/api/chat",
        json={"message": "My name is Alice", "user_id": TEST_USER},
        timeout=10
    )
    print(f"   Status: {response1.status_code}")
    if response1.status_code == 200:
        data1 = response1.json()
        print(f"   Response time: {data1.get('response_time', 0):.2f}s")
        print(f"   Response preview: {data1.get('response', '')[:100]}...")
    
    time.sleep(2)
    
    # Test 2: Retrieve the fact
    print("\n2. Retrieving fact: 'What is my name?'")
    response2 = requests.post(
        f"{BASE_URL}/api/chat",
        json={"message": "What is my name?", "user_id": TEST_USER},
        timeout=10
    )
    print(f"   Status: {response2.status_code}")
    if response2.status_code == 200:
        data2 = response2.json()
        resp_text = data2.get('response', '')
        print(f"   Response time: {data2.get('response_time', 0):.2f}s")
        print(f"   Full response: {resp_text}")
        
        # Check if name was found
        if 'Alice' in resp_text:
            print("   ✅ SUCCESS: Name was found in response!")
        else:
            print("   ❌ FAIL: Name was NOT found in response")
            print(f"   Routing: {data2.get('routing', 'unknown')}")
            print(f"   Memories used: {data2.get('memories_used', 0)}")
    
    # Test 3: Check chat.html accessibility
    print("\n3. Testing chat.html accessibility")
    try:
        ui_response = requests.get("http://localhost/chat.html", timeout=5)
        if ui_response.status_code == 200:
            print(f"   ✅ SUCCESS: chat.html is accessible (Status: {ui_response.status_code})")
            if "Zoe - AI Chat" in ui_response.text:
                print("   ✅ Page title found")
        else:
            print(f"   ❌ FAIL: chat.html returned status {ui_response.status_code}")
    except Exception as e:
        print(f"   ❌ ERROR: Could not access chat.html: {e}")
    
    print("\n" + "=" * 60)

if __name__ == "__main__":
    test_chat_interface()



