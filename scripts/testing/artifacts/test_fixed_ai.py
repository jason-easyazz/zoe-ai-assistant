#!/usr/bin/env python3
import requests
import json
import time

def test_ai_system():
    """Test the fixed AI system"""
    print("🧪 Testing Fixed Multi-Model AI System\n")
    
    # Test 1: Check AI usage stats
    print("1️⃣ Checking AI usage stats...")
    try:
        response = requests.get("http://localhost:8000/api/developer/ai/usage")
        if response.status_code == 200:
            usage = response.json()
            print(f"✅ AI Usage Stats:")
            print(f"   Models available: {usage.get('models_available', {})}")
            print(f"   Daily budget: ${usage.get('daily_budget', 0):.2f}")
            print(f"   Used today: ${usage.get('used_today', 0):.2f}")
        else:
            print(f"❌ Failed to get usage stats: {response.status_code}")
    except Exception as e:
        print(f"❌ Error: {e}")
    
    # Test 2: Simple query
    print("\n2️⃣ Testing simple query...")
    try:
        response = requests.post(
            "http://localhost:8000/api/developer/chat",
            json={"message": "Say hello"},
            timeout=30
        )
        if response.status_code == 200:
            data = response.json()
            print(f"✅ Response received")
            print(f"   Model: {data.get('model_used', 'Unknown')}")
            print(f"   Response: {data.get('response', '')[:100]}...")
        else:
            print(f"❌ Error: {response.status_code}")
            print(f"   {response.text}")
    except Exception as e:
        print(f"❌ Error: {e}")
    
    # Test 3: Developer query
    print("\n3️⃣ Testing developer query...")
    try:
        response = requests.post(
            "http://localhost:8000/api/developer/chat",
            json={"message": "Generate a backup script"},
            timeout=30
        )
        if response.status_code == 200:
            data = response.json()
            print(f"✅ Response received")
            print(f"   Model: {data.get('model_used', 'Unknown')}")
            print(f"   Complexity: {data.get('complexity', 'Unknown')}")
            print(f"   Response length: {len(data.get('response', ''))} chars")
        else:
            print(f"❌ Error: {response.status_code}")
    except Exception as e:
        print(f"❌ Error: {e}")
    
    print("\n✨ Test complete!")

if __name__ == "__main__":
    test_ai_system()
