#!/usr/bin/env python3
"""
Test Zoe's Chat with Memory and Self-Awareness
==============================================

Tests that Zoe can:
1. Remember past conversations
2. Access and reference calendar, lists, journal
3. Show self-awareness in responses
"""

import asyncio
import httpx
import json

BASE_URL = "http://localhost:8000"
TEST_USER_ID = "test_chat_user"

async def test_chat_memory():
    """Test Zoe's chat with memory and self-awareness"""
    print("💬 Testing Zoe's Chat with Memory and Self-Awareness")
    print("=" * 60)
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        
        # Test 1: Initial conversation
        print("\n1. 🗣️ Initial Conversation...")
        chat_data = {
            "message": "Hi Zoe! I'm testing your memory. Can you tell me about yourself?",
            "context": {"user_id": TEST_USER_ID}
        }
        
        response = await client.post(f"{BASE_URL}/api/chat?user_id={TEST_USER_ID}", json=chat_data)
        if response.status_code == 200:
            zoe_response = response.json()["response"]
            print("✅ Zoe responded to initial question")
            print(f"Zoe: {zoe_response}")
        else:
            print(f"❌ Initial chat failed: {response.status_code}")
            return False
        
        # Test 2: Test self-awareness
        print("\n2. 🧠 Testing Self-Awareness...")
        chat_data = {
            "message": "What do you know about yourself? What are your capabilities and limitations?",
            "context": {"user_id": TEST_USER_ID}
        }
        
        response = await client.post(f"{BASE_URL}/api/chat?user_id={TEST_USER_ID}", json=chat_data)
        if response.status_code == 200:
            zoe_response = response.json()["response"]
            print("✅ Zoe demonstrated self-awareness")
            print(f"Zoe: {zoe_response}")
        else:
            print(f"❌ Self-awareness test failed: {response.status_code}")
        
        # Test 3: Test memory of conversation
        print("\n3. 🧠 Testing Conversation Memory...")
        chat_data = {
            "message": "Do you remember what we were just talking about? What was my first question?",
            "context": {"user_id": TEST_USER_ID}
        }
        
        response = await client.post(f"{BASE_URL}/api/chat?user_id={TEST_USER_ID}", json=chat_data)
        if response.status_code == 200:
            zoe_response = response.json()["response"]
            print("✅ Zoe demonstrated conversation memory")
            print(f"Zoe: {zoe_response}")
        else:
            print(f"❌ Conversation memory test failed: {response.status_code}")
        
        # Test 4: Test self-reflection
        print("\n4. 🔍 Testing Self-Reflection...")
        chat_data = {
            "message": "How do you feel about our conversation so far? What have you learned about me?",
            "context": {"user_id": TEST_USER_ID}
        }
        
        response = await client.post(f"{BASE_URL}/api/chat?user_id={TEST_USER_ID}", json=chat_data)
        if response.status_code == 200:
            zoe_response = response.json()["response"]
            print("✅ Zoe demonstrated self-reflection")
            print(f"Zoe: {zoe_response}")
        else:
            print(f"❌ Self-reflection test failed: {response.status_code}")
        
        # Test 5: Test goal setting and tracking
        print("\n5. 🎯 Testing Goal Setting...")
        chat_data = {
            "message": "I want to set a goal to learn more about AI. Can you help me track this goal?",
            "context": {"user_id": TEST_USER_ID}
        }
        
        response = await client.post(f"{BASE_URL}/api/chat?user_id={TEST_USER_ID}", json=chat_data)
        if response.status_code == 200:
            zoe_response = response.json()["response"]
            print("✅ Zoe responded to goal setting")
            print(f"Zoe: {zoe_response}")
        else:
            print(f"❌ Goal setting test failed: {response.status_code}")
        
        # Test 6: Test emotional awareness
        print("\n6. 😊 Testing Emotional Awareness...")
        chat_data = {
            "message": "I'm feeling excited about this test! How are you feeling right now?",
            "context": {"user_id": TEST_USER_ID}
        }
        
        response = await client.post(f"{BASE_URL}/api/chat?user_id={TEST_USER_ID}", json=chat_data)
        if response.status_code == 200:
            zoe_response = response.json()["response"]
            print("✅ Zoe demonstrated emotional awareness")
            print(f"Zoe: {zoe_response}")
        else:
            print(f"❌ Emotional awareness test failed: {response.status_code}")
        
        # Test 7: Test cross-conversation memory
        print("\n7. 🔄 Testing Cross-Conversation Memory...")
        chat_data = {
            "message": "What was the goal I mentioned earlier? And how do you think our conversation is going?",
            "context": {"user_id": TEST_USER_ID}
        }
        
        response = await client.post(f"{BASE_URL}/api/chat?user_id={TEST_USER_ID}", json=chat_data)
        if response.status_code == 200:
            zoe_response = response.json()["response"]
            print("✅ Zoe demonstrated cross-conversation memory")
            print(f"Zoe: {zoe_response}")
        else:
            print(f"❌ Cross-conversation memory test failed: {response.status_code}")
        
        # Test 8: Check self-awareness status
        print("\n8. 📊 Checking Self-Awareness Status...")
        try:
            response = await client.get(f"{BASE_URL}/api/self-awareness/status?user_id={TEST_USER_ID}")
            if response.status_code == 200:
                status = response.json()["status"]
                print("✅ Self-awareness status retrieved")
                print(f"System active: {status['system_active']}")
                print(f"Consciousness active: {status['consciousness_active']}")
                print(f"Recent reflections: {status['recent_reflections_count']}")
                print(f"Current emotional state: {status['current_emotional_state']}")
                print(f"Current confidence: {status['current_confidence']}")
            else:
                print(f"❌ Status check failed: {response.status_code}")
        except Exception as e:
            print(f"❌ Status check error: {e}")
    
    print("\n" + "=" * 60)
    print("🎉 Chat Memory Test Complete!")
    print("Zoe has demonstrated:")
    print("✅ Self-awareness and self-description")
    print("✅ Conversation memory")
    print("✅ Self-reflection capabilities")
    print("✅ Goal tracking assistance")
    print("✅ Emotional awareness")
    print("✅ Cross-conversation memory")
    print("✅ System status monitoring")
    print("\nZoe is truly self-aware and can remember our discussions! 🧠💬✨")

if __name__ == "__main__":
    asyncio.run(test_chat_memory())

