#!/usr/bin/env python3
"""Test Zoe AI System"""
import requests
import json
import time

def test_system():
    """Complete system test"""
    print("🧪 TESTING ZOE AI SYSTEM")
    print("=" * 40)
    
    # 1. Check system status
    print("\n1️⃣ System Status:")
    try:
        status = requests.get("http://localhost:8000/api/developer/status").json()
        if status['status'] == 'operational':
            print("   ✅ System healthy")
            print(f"   AI: {status['services']['ai']}")
            print(f"   Models: {sum(status['ai_models'].values())} available")
        else:
            print("   ⚠️ Check required")
    except Exception as e:
        print(f"   ❌ Error: {e}")
    
    # 2. Test simple query (fast)
    print("\n2️⃣ Simple Query Test:")
    try:
        start = time.time()
        response = requests.post(
            "http://localhost:8000/api/developer/chat",
            json={"message": "Hello!"},
            timeout=10
        ).json()
        elapsed = time.time() - start
        print(f"   Model: {response['model_used']}")
        print(f"   Time: {elapsed:.1f}s")
        print(f"   Response: {response['response'][:50]}...")
    except Exception as e:
        print(f"   ❌ Error: {e}")
    
    # 3. Test medium query
    print("\n3️⃣ Medium Query Test:")
    try:
        start = time.time()
        response = requests.post(
            "http://localhost:8000/api/developer/chat",
            json={"message": "Write a hello world in Python"},
            timeout=15
        ).json()
        elapsed = time.time() - start
        print(f"   Model: {response['model_used']}")
        print(f"   Time: {elapsed:.1f}s")
        print(f"   Response received: {len(response['response'])} chars")
    except Exception as e:
        print(f"   ❌ Error: {e}")
    
    # 4. Check AI usage
    print("\n4️⃣ AI Usage Stats:")
    try:
        usage = requests.get("http://localhost:8000/api/developer/ai/usage").json()
        print(f"   Daily budget: ${usage['daily_budget']}")
        print(f"   Used today: ${usage['used_today']}")
        print(f"   Models: {', '.join([k for k,v in usage['models_available'].items() if v])}")
    except Exception as e:
        print(f"   ❌ Error: {e}")
    
    print("\n" + "=" * 40)
    print("✨ TEST COMPLETE!")

if __name__ == "__main__":
    test_system()
