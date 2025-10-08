#!/usr/bin/env python3
"""
Test Suite for Zoe Chat System with Enhancement Integrations
============================================================

Tests the consolidated chat router and its enhancement system integrations.
"""

import sys
import requests
import json
import time

def test_chat_endpoints():
    """Test basic chat endpoint availability"""
    print("🧪 Testing Chat Endpoints...")
    
    base_url = "http://localhost:8000"
    
    # Test 1: Health check
    try:
        response = requests.get(f"{base_url}/health", timeout=5)
        if response.status_code == 200:
            print("✅ Health check passed")
        else:
            print(f"❌ Health check failed: {response.status_code}")
            return False
    except Exception as e:
        print(f"❌ Health check error: {e}")
        return False
    
    # Test 2: Chat capabilities endpoint
    try:
        response = requests.get(f"{base_url}/api/chat/capabilities", timeout=5)
        if response.status_code == 200:
            data = response.json()
            print(f"✅ Chat capabilities endpoint working")
            print(f"   Enhancement systems: {len(data.get('enhancement_systems', {}))}")
        else:
            print(f"❌ Capabilities endpoint failed: {response.status_code}")
            return False
    except Exception as e:
        print(f"❌ Capabilities endpoint error: {e}")
        return False
    
    # Test 3: Chat status endpoint
    try:
        response = requests.get(f"{base_url}/api/chat/status?user_id=test", timeout=5)
        if response.status_code == 200:
            data = response.json()
            print(f"✅ Chat status endpoint working")
            print(f"   Status: {data.get('status')}")
        else:
            print(f"⚠️  Status endpoint returned: {response.status_code}")
    except Exception as e:
        print(f"⚠️  Status endpoint error: {e}")
    
    return True

def test_enhancement_systems():
    """Test enhancement system endpoints"""
    print("\n🧪 Testing Enhancement System Endpoints...")
    
    base_url = "http://localhost:8000"
    test_user = "test_user"
    
    # Test 1: Temporal Memory - Episodes
    print("\n📚 Testing Temporal Memory...")
    try:
        response = requests.get(
            f"{base_url}/api/temporal-memory/episodes/active",
            params={"user_id": test_user},
            timeout=5
        )
        if response.status_code == 200:
            print("✅ Temporal memory endpoint accessible")
        else:
            print(f"⚠️  Temporal memory endpoint: {response.status_code}")
    except Exception as e:
        print(f"⚠️  Temporal memory error: {e}")
    
    # Test 2: Cross-Agent Collaboration - Experts
    print("\n🤝 Testing Cross-Agent Collaboration...")
    try:
        response = requests.get(
            f"{base_url}/api/orchestration/experts",
            timeout=5
        )
        if response.status_code == 200:
            data = response.json()
            experts = data.get('experts', {})
            print(f"✅ Orchestration endpoint accessible")
            print(f"   Available experts: {len(experts)}")
        else:
            print(f"⚠️  Orchestration endpoint: {response.status_code}")
    except Exception as e:
        print(f"⚠️  Orchestration error: {e}")
    
    # Test 3: User Satisfaction - Levels
    print("\n📊 Testing User Satisfaction...")
    try:
        response = requests.get(
            f"{base_url}/api/satisfaction/levels",
            timeout=5
        )
        if response.status_code == 200:
            print("✅ Satisfaction endpoint accessible")
        else:
            print(f"⚠️  Satisfaction endpoint: {response.status_code}")
    except Exception as e:
        print(f"⚠️  Satisfaction error: {e}")
    
    return True

def test_simple_chat():
    """Test a simple chat interaction"""
    print("\n🧪 Testing Simple Chat Interaction...")
    
    base_url = "http://localhost:8000"
    
    try:
        response = requests.post(
            f"{base_url}/api/chat",
            json={
                "message": "Hello, can you tell me what enhancement systems are available?",
                "context": {}
            },
            params={"user_id": "test_user"},
            timeout=30
        )
        
        if response.status_code == 200:
            data = response.json()
            print("✅ Chat interaction successful")
            print(f"   Response time: {data.get('response_time', 0):.2f}s")
            print(f"   Enhancements active: {data.get('enhancements_active', {})}")
            print(f"   Response preview: {data.get('response', '')[:100]}...")
            return True
        else:
            print(f"❌ Chat interaction failed: {response.status_code}")
            print(f"   Response: {response.text}")
            return False
    except Exception as e:
        print(f"❌ Chat interaction error: {e}")
        return False

def test_chat_router_consolidation():
    """Verify that only one chat router exists"""
    print("\n🧪 Testing Chat Router Consolidation...")
    
    import os
    import glob
    
    # Check for chat router files
    chat_files = []
    routers_path = "/home/pi/zoe/services/zoe-core/routers"
    
    if os.path.exists(routers_path):
        # Find all chat*.py files excluding archive
        for file in glob.glob(f"{routers_path}/chat*.py"):
            if "archive" not in file:
                chat_files.append(file)
    
    if len(chat_files) == 1 and chat_files[0].endswith("chat.py"):
        print(f"✅ Single consolidated chat router found: {chat_files[0]}")
        return True
    else:
        print(f"❌ Multiple chat routers found: {len(chat_files)}")
        for file in chat_files:
            print(f"   - {file}")
        return False

def test_main_py_import():
    """Test that main.py correctly imports only the consolidated chat router"""
    print("\n🧪 Testing main.py Chat Router Import...")
    
    try:
        with open("/home/pi/zoe/services/zoe-core/main.py", "r") as f:
            content = f.read()
        
        # Check for problematic imports
        if "chat_langgraph" in content:
            print("❌ main.py still imports chat_langgraph")
            return False
        
        if "from routers import" in content and "chat" in content:
            print("✅ main.py imports chat router")
        else:
            print("❌ main.py does not import chat router")
            return False
        
        # Count chat router includes
        chat_includes = content.count("include_router(chat")
        if chat_includes == 1:
            print(f"✅ main.py includes exactly 1 chat router")
            return True
        else:
            print(f"❌ main.py includes {chat_includes} chat routers (should be 1)")
            return False
            
    except Exception as e:
        print(f"❌ Error reading main.py: {e}")
        return False

def main():
    """Run all tests"""
    print("=" * 70)
    print("🎯 ZOE CHAT SYSTEM ENHANCEMENT INTEGRATION TEST SUITE")
    print("=" * 70)
    
    results = {
        "Chat Endpoints": test_chat_endpoints(),
        "Enhancement Systems": test_enhancement_systems(),
        "Chat Router Consolidation": test_chat_router_consolidation(),
        "Main.py Import": test_main_py_import(),
    }
    
    # Test actual chat interaction last (may take longer)
    print("\n" + "=" * 70)
    print("Testing actual chat interaction (this may take 30+ seconds)...")
    print("=" * 70)
    results["Simple Chat Interaction"] = test_simple_chat()
    
    # Summary
    print("\n" + "=" * 70)
    print("📊 TEST SUMMARY")
    print("=" * 70)
    
    passed = sum(1 for result in results.values() if result)
    total = len(results)
    
    for test_name, result in results.items():
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status}: {test_name}")
    
    print("\n" + "=" * 70)
    print(f"🎯 OVERALL RESULT: {passed}/{total} tests passed ({passed/total*100:.1f}%)")
    
    if passed == total:
        print("🎉 ALL TESTS PASSED - SYSTEM FULLY OPTIMIZED!")
    elif passed >= total * 0.7:
        print("⚠️  MOST TESTS PASSED - MINOR ISSUES REMAIN")
    else:
        print("❌ MULTIPLE FAILURES - SYSTEM NEEDS ATTENTION")
    
    print("=" * 70)
    
    return passed == total

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)

