#!/usr/bin/env python3
"""
Test Web Chat Integration
=========================

Test the enhancement systems through the existing web chat interface
to verify they improve the user experience.
"""

import requests
import json
import time
from datetime import datetime

def test_web_chat_integration():
    """Test enhancement systems through web chat"""
    print("💬 Testing Enhancement Systems Through Web Chat")
    print("=" * 60)
    
    base_url = "http://localhost:8000"
    test_user = "enhancement_test_user"
    
    results = {}
    
    # Test 1: Basic Chat Functionality
    print("\n1️⃣ Testing Basic Chat...")
    try:
        response = requests.post(f"{base_url}/api/chat", 
            json={"message": "Hello, I'm testing the enhancement systems"},
            params={"user_id": test_user},
            timeout=10
        )
        
        if response.status_code == 200:
            data = response.json()
            print(f"  ✅ Chat Response: {data.get('response', 'No response')[:100]}...")
            print(f"  ⏱️ Response Time: {data.get('response_time', 0):.2f}s")
            results["basic_chat"] = {"success": True, "response_time": data.get('response_time', 0)}
        else:
            print(f"  ❌ Chat failed: {response.status_code} - {response.text}")
            results["basic_chat"] = {"success": False, "error": response.text}
    except Exception as e:
        print(f"  ❌ Chat error: {e}")
        results["basic_chat"] = {"success": False, "error": str(e)}
    
    # Test 2: Memory System Integration
    print("\n2️⃣ Testing Memory System...")
    try:
        # Add a memory
        response = requests.post(f"{base_url}/api/memories", 
            json={
                "fact": "The user is testing enhancement systems on October 6, 2025",
                "category": "testing",
                "importance": 8
            },
            params={"user_id": test_user},
            timeout=10
        )
        
        if response.status_code == 200:
            print("  ✅ Memory added successfully")
            
            # Search memories
            response = requests.get(f"{base_url}/api/memories/search",
                params={"query": "enhancement systems", "user_id": test_user},
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                memories = data.get("memories", [])
                print(f"  ✅ Found {len(memories)} memories")
                results["memory_system"] = {"success": True, "memories_found": len(memories)}
            else:
                print(f"  ❌ Memory search failed: {response.status_code}")
                results["memory_system"] = {"success": False, "error": "Search failed"}
        else:
            print(f"  ❌ Memory add failed: {response.status_code}")
            results["memory_system"] = {"success": False, "error": "Add failed"}
    except Exception as e:
        print(f"  ❌ Memory system error: {e}")
        results["memory_system"] = {"success": False, "error": str(e)}
    
    # Test 3: Calendar Integration
    print("\n3️⃣ Testing Calendar System...")
    try:
        # Create an event
        tomorrow = datetime.now().strftime("%Y-%m-%d")
        response = requests.post(f"{base_url}/api/calendar/events",
            json={
                "title": "Enhancement System Test Meeting",
                "description": "Testing the enhancement systems",
                "start_date": tomorrow,
                "start_time": "14:00",
                "end_time": "15:00",
                "category": "testing"
            },
            params={"user_id": test_user},
            timeout=10
        )
        
        if response.status_code == 200:
            print("  ✅ Calendar event created")
            
            # Get events
            response = requests.get(f"{base_url}/api/calendar/events",
                params={"user_id": test_user},
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                events = data.get("events", [])
                print(f"  ✅ Found {len(events)} events")
                results["calendar_system"] = {"success": True, "events_found": len(events)}
            else:
                print(f"  ❌ Calendar get failed: {response.status_code}")
                results["calendar_system"] = {"success": False, "error": "Get failed"}
        else:
            print(f"  ❌ Calendar create failed: {response.status_code}")
            results["calendar_system"] = {"success": False, "error": "Create failed"}
    except Exception as e:
        print(f"  ❌ Calendar system error: {e}")
        results["calendar_system"] = {"success": False, "error": str(e)}
    
    # Test 4: Lists Integration
    print("\n4️⃣ Testing Lists System...")
    try:
        # Create a list
        response = requests.post(f"{base_url}/api/lists",
            json={
                "name": "Enhancement Testing List",
                "description": "Testing the enhancement systems",
                "category": "testing"
            },
            params={"user_id": test_user},
            timeout=10
        )
        
        if response.status_code == 200:
            data = response.json()
            list_id = data.get("list", {}).get("id")
            print(f"  ✅ List created with ID: {list_id}")
            
            if list_id:
                # Add items to the list
                response = requests.post(f"{base_url}/api/lists/{list_id}/items",
                    json={
                        "content": "Test temporal memory system",
                        "priority": "high"
                    },
                    params={"user_id": test_user},
                    timeout=10
                )
                
                if response.status_code == 200:
                    print("  ✅ List item added")
                    results["lists_system"] = {"success": True, "list_id": list_id}
                else:
                    print(f"  ❌ List item add failed: {response.status_code}")
                    results["lists_system"] = {"success": False, "error": "Item add failed"}
            else:
                results["lists_system"] = {"success": False, "error": "No list ID returned"}
        else:
            print(f"  ❌ List create failed: {response.status_code}")
            results["lists_system"] = {"success": False, "error": "Create failed"}
    except Exception as e:
        print(f"  ❌ Lists system error: {e}")
        results["lists_system"] = {"success": False, "error": str(e)}
    
    # Test 5: Complex Multi-Step Request (What orchestration would handle)
    print("\n5️⃣ Testing Complex Multi-Step Request...")
    try:
        complex_request = "I need to schedule a meeting for tomorrow at 3pm about the enhancement systems, add it to my testing list, and remember that we completed the integration testing"
        
        response = requests.post(f"{base_url}/api/chat",
            json={"message": complex_request},
            params={"user_id": test_user},
            timeout=30  # Longer timeout for complex request
        )
        
        if response.status_code == 200:
            data = response.json()
            response_text = data.get('response', '')
            response_time = data.get('response_time', 0)
            
            print(f"  ✅ Complex request handled")
            print(f"  ⏱️ Response Time: {response_time:.2f}s")
            print(f"  📝 Response: {response_text[:200]}...")
            
            # Check if the response mentions handling multiple tasks
            multi_task_indicators = ['calendar', 'list', 'remember', 'schedule', 'add']
            mentions = sum(1 for indicator in multi_task_indicators if indicator.lower() in response_text.lower())
            
            results["complex_request"] = {
                "success": True, 
                "response_time": response_time,
                "multi_task_mentions": mentions
            }
        else:
            print(f"  ❌ Complex request failed: {response.status_code}")
            results["complex_request"] = {"success": False, "error": response.text}
    except Exception as e:
        print(f"  ❌ Complex request error: {e}")
        results["complex_request"] = {"success": False, "error": str(e)}
    
    # Test 6: Self-Awareness and Reflection
    print("\n6️⃣ Testing Self-Awareness...")
    try:
        response = requests.post(f"{base_url}/api/chat",
            json={"message": "How do you feel about helping me test these enhancement systems? What have you learned?"},
            params={"user_id": test_user},
            timeout=15
        )
        
        if response.status_code == 200:
            data = response.json()
            response_text = data.get('response', '')
            
            # Check for self-awareness indicators
            awareness_indicators = ['feel', 'learn', 'help', 'experience', 'understand']
            mentions = sum(1 for indicator in awareness_indicators if indicator.lower() in response_text.lower())
            
            print(f"  ✅ Self-awareness response received")
            print(f"  🧠 Awareness indicators: {mentions}")
            print(f"  💭 Response: {response_text[:200]}...")
            
            results["self_awareness"] = {
                "success": True,
                "awareness_indicators": mentions,
                "response_length": len(response_text)
            }
        else:
            print(f"  ❌ Self-awareness test failed: {response.status_code}")
            results["self_awareness"] = {"success": False, "error": response.text}
    except Exception as e:
        print(f"  ❌ Self-awareness error: {e}")
        results["self_awareness"] = {"success": False, "error": str(e)}
    
    # Summary
    print("\n" + "=" * 60)
    print("📊 WEB CHAT INTEGRATION TEST RESULTS")
    print("=" * 60)
    
    total_tests = len(results)
    successful_tests = sum(1 for result in results.values() if result.get("success", False))
    success_rate = (successful_tests / total_tests) * 100
    
    for test_name, result in results.items():
        status = "✅ PASS" if result.get("success", False) else "❌ FAIL"
        print(f"{test_name.replace('_', ' ').title():<25} {status}")
        
        if not result.get("success", False):
            print(f"  Error: {result.get('error', 'Unknown error')}")
    
    print("-" * 60)
    print(f"Success Rate: {success_rate:.1f}% ({successful_tests}/{total_tests})")
    
    if success_rate >= 80:
        print("🎉 WEB CHAT INTEGRATION: EXCELLENT")
    elif success_rate >= 60:
        print("✅ WEB CHAT INTEGRATION: GOOD")
    else:
        print("⚠️  WEB CHAT INTEGRATION: NEEDS IMPROVEMENT")
    
    return results

if __name__ == "__main__":
    results = test_web_chat_integration()
    
    # Save results
    with open('/home/pi/web_chat_test_results.json', 'w') as f:
        json.dump(results, f, indent=2)
    
    print(f"\n📊 Results saved to: web_chat_test_results.json")


