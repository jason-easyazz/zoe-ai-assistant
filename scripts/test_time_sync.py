#!/usr/bin/env python3
"""
Test script for Zoe Time Sync System
"""

import requests
import json
import time

def test_api_endpoint(url, method="GET", data=None):
    """Test an API endpoint"""
    try:
        if method == "GET":
            response = requests.get(url, timeout=10)
        elif method == "POST":
            response = requests.post(url, json=data, timeout=10)
        
        if response.status_code == 200:
            return True, response.json()
        else:
            return False, f"HTTP {response.status_code}: {response.text}"
    except Exception as e:
        return False, str(e)

def main():
    print("🕒 Testing Zoe Time Sync System")
    print("=" * 50)
    
    base_url = "http://localhost:8000"
    
    # Test endpoints
    tests = [
        ("Time Status", f"{base_url}/api/time/status", "GET"),
        ("Time Location Settings", f"{base_url}/api/settings/time-location", "GET"),
        ("Available Timezones", f"{base_url}/api/settings/time-location/timezones", "GET"),
        ("Time Sync", f"{base_url}/api/settings/time-location/sync", "POST"),
    ]
    
    results = []
    
    for test_name, url, method in tests:
        print(f"\n🧪 Testing {test_name}...")
        success, result = test_api_endpoint(url, method)
        
        if success:
            print(f"✅ {test_name}: PASSED")
            if isinstance(result, dict) and len(result) < 10:
                print(f"   Response: {json.dumps(result, indent=2)}")
            else:
                print(f"   Response: {type(result).__name__} (truncated)")
        else:
            print(f"❌ {test_name}: FAILED")
            print(f"   Error: {result}")
        
        results.append((test_name, success))
    
    # Summary
    print("\n" + "=" * 50)
    print("📊 Test Summary:")
    passed = sum(1 for _, success in results if success)
    total = len(results)
    
    for test_name, success in results:
        status = "✅ PASS" if success else "❌ FAIL"
        print(f"  {status} {test_name}")
    
    print(f"\n🎯 Overall: {passed}/{total} tests passed")
    
    if passed == total:
        print("🎉 All tests passed! Time sync system is working correctly.")
        return True
    else:
        print("⚠️  Some tests failed. Check the errors above.")
        return False

if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
