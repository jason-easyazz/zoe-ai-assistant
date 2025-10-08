#!/usr/bin/env python3
"""
Comprehensive UI Chat Test for Enhancement Systems
==================================================

Test all enhancement systems through the web chat interface.
"""

import requests
import json
import time
import uuid
from datetime import datetime, timedelta

def test_comprehensive_ui_integration():
    """Test all enhancement systems through UI chat"""
    print("🌟 COMPREHENSIVE UI CHAT TESTING - ENHANCEMENT SYSTEMS")
    print("=" * 70)
    
    base_url = "http://localhost:8000"
    test_user = f"ui_test_user_{int(time.time())}"
    
    results = {}
    
    # Test 1: Create Temporal Episode
    print("\n1️⃣ Testing Temporal Memory - Episode Creation...")
    try:
        response = requests.post(f"{base_url}/api/temporal-memory/episodes",
            json={"context_type": "testing", "participants": [test_user]},
            params={"user_id": test_user},
            timeout=10
        )
        
        if response.status_code == 200:
            episode_data = response.json()
            episode_id = episode_data["episode"]["id"]
            print(f"  ✅ Episode created: {episode_id}")
            print(f"  📝 Context: {episode_data['episode']['context_type']}")
            results["temporal_episode"] = {"success": True, "episode_id": episode_id}
        else:
            print(f"  ❌ Episode creation failed: {response.status_code} - {response.text}")
            results["temporal_episode"] = {"success": False, "error": response.text}
    except Exception as e:
        print(f"  ❌ Episode creation error: {e}")
        results["temporal_episode"] = {"success": False, "error": str(e)}
    
    # Test 2: Test Orchestration System
    print("\n2️⃣ Testing Cross-Agent Orchestration...")
    try:
        complex_request = {
            "request": "I need to schedule a meeting for tomorrow at 2pm about project planning, create a task list for the meeting preparation, and remember that this is part of our enhancement testing",
            "context": {"priority": "high", "testing": True}
        }
        
        response = requests.post(f"{base_url}/api/orchestration/orchestrate",
            json=complex_request,
            params={"user_id": test_user},
            timeout=30
        )
        
        if response.status_code == 200:
            orchestration_data = response.json()
            print(f"  ✅ Orchestration completed: {orchestration_data['success']}")
            print(f"  ⏱️ Duration: {orchestration_data['total_duration']:.2f}s")
            print(f"  📊 Tasks: {len(orchestration_data.get('decomposed_tasks', []))}")
            print(f"  📝 Summary: {orchestration_data.get('summary', 'No summary')}")
            results["orchestration"] = {
                "success": orchestration_data['success'], 
                "duration": orchestration_data['total_duration'],
                "tasks": len(orchestration_data.get('decomposed_tasks', []))
            }
        else:
            print(f"  ❌ Orchestration failed: {response.status_code} - {response.text}")
            results["orchestration"] = {"success": False, "error": response.text}
    except Exception as e:
        print(f"  ❌ Orchestration error: {e}")
        results["orchestration"] = {"success": False, "error": str(e)}
    
    # Test 3: User Satisfaction Feedback
    print("\n3️⃣ Testing User Satisfaction System...")
    try:
        interaction_id = str(uuid.uuid4())
        
        # Record an interaction first
        response = requests.post(f"{base_url}/api/satisfaction/interaction",
            json={
                "interaction_id": interaction_id,
                "request_text": "Testing satisfaction system",
                "response_text": "System is working well",
                "response_time": 1.5,
                "context": {"test": True}
            },
            params={"user_id": test_user},
            timeout=10
        )
        
        if response.status_code == 200:
            print("  ✅ Interaction recorded")
            
            # Submit explicit feedback
            response = requests.post(f"{base_url}/api/satisfaction/feedback",
                json={
                    "interaction_id": interaction_id,
                    "rating": 5,
                    "feedback_text": "Enhancement systems are working great!",
                    "context": {"test_feedback": True}
                },
                params={"user_id": test_user},
                timeout=10
            )
            
            if response.status_code == 200:
                feedback_data = response.json()
                print(f"  ✅ Feedback recorded: {feedback_data['feedback_id']}")
                print(f"  😊 Satisfaction: {feedback_data['satisfaction_level']}")
                
                # Get satisfaction metrics
                response = requests.get(f"{base_url}/api/satisfaction/metrics",
                    params={"user_id": test_user},
                    timeout=10
                )
                
                if response.status_code == 200:
                    metrics = response.json()
                    print(f"  📊 Total interactions: {metrics['total_interactions']}")
                    print(f"  📈 Average satisfaction: {metrics['average_satisfaction']:.2f}")
                    results["satisfaction"] = {
                        "success": True, 
                        "interactions": metrics['total_interactions'],
                        "avg_satisfaction": metrics['average_satisfaction']
                    }
                else:
                    print(f"  ❌ Metrics retrieval failed: {response.status_code}")
                    results["satisfaction"] = {"success": False, "error": "Metrics failed"}
            else:
                print(f"  ❌ Feedback submission failed: {response.status_code}")
                results["satisfaction"] = {"success": False, "error": "Feedback failed"}
        else:
            print(f"  ❌ Interaction recording failed: {response.status_code}")
            results["satisfaction"] = {"success": False, "error": "Interaction failed"}
    except Exception as e:
        print(f"  ❌ Satisfaction system error: {e}")
        results["satisfaction"] = {"success": False, "error": str(e)}
    
    # Test 4: Enhanced Chat with Temporal Context
    print("\n4️⃣ Testing Enhanced Chat with Temporal Context...")
    try:
        # First, create some temporal context
        if results.get("temporal_episode", {}).get("success"):
            episode_id = results["temporal_episode"]["episode_id"]
            
            # Add a message to the episode
            response = requests.post(f"{base_url}/api/temporal-memory/episodes/{episode_id}/messages",
                params={
                    "message": "We are testing the enhancement systems comprehensively",
                    "message_type": "user",
                    "user_id": test_user
                },
                timeout=10
            )
            
            if response.status_code == 200:
                print("  ✅ Message added to episode")
                
                # Now test temporal search
                response = requests.post(f"{base_url}/api/temporal-memory/search",
                    json={"query": "enhancement systems", "episode_id": episode_id},
                    params={"user_id": test_user},
                    timeout=10
                )
                
                if response.status_code == 200:
                    search_data = response.json()
                    memories = search_data.get("memories", [])
                    print(f"  ✅ Temporal search found {len(memories)} memories")
                    results["temporal_search"] = {"success": True, "memories_found": len(memories)}
                else:
                    print(f"  ❌ Temporal search failed: {response.status_code}")
                    results["temporal_search"] = {"success": False, "error": "Search failed"}
            else:
                print(f"  ❌ Message addition failed: {response.status_code}")
                results["temporal_search"] = {"success": False, "error": "Message add failed"}
        else:
            print("  ⚠️ Skipping temporal search (no episode available)")
            results["temporal_search"] = {"success": False, "error": "No episode available"}
    except Exception as e:
        print(f"  ❌ Temporal context error: {e}")
        results["temporal_search"] = {"success": False, "error": str(e)}
    
    # Test 5: Full Web Chat Integration Test
    print("\n5️⃣ Testing Full Web Chat Integration...")
    try:
        # Test a complex request that should trigger multiple enhancement systems
        chat_request = {
            "message": "Hi Zoe! I want to test all your new enhancement features. Can you help me schedule a meeting for tomorrow, remember that we're testing the temporal memory system, and tell me how you feel about these new capabilities?",
            "context": {"test_mode": True, "user_id": test_user}
        }
        
        start_time = time.time()
        response = requests.post(f"{base_url}/api/chat",
            json=chat_request,
            params={"user_id": test_user},
            timeout=30
        )
        response_time = time.time() - start_time
        
        if response.status_code == 200:
            data = response.json()
            response_text = data.get('response', '')
            
            print(f"  ✅ Full integration test completed")
            print(f"  ⏱️ Response Time: {response_time:.2f}s")
            print(f"  📝 Response Length: {len(response_text)} characters")
            print(f"  💬 Response Preview: {response_text[:300]}...")
            
            # Check for enhancement system mentions
            enhancement_mentions = {
                "temporal": any(word in response_text.lower() for word in ["temporal", "episode", "remember", "memory"]),
                "orchestration": any(word in response_text.lower() for word in ["schedule", "meeting", "calendar"]),
                "satisfaction": any(word in response_text.lower() for word in ["feel", "capabilities", "enhancement"]),
                "self_awareness": any(word in response_text.lower() for word in ["i", "me", "my", "feel"])
            }
            
            print(f"  🧠 Enhancement mentions: {sum(enhancement_mentions.values())}/4")
            
            results["full_integration"] = {
                "success": True,
                "response_time": response_time,
                "response_length": len(response_text),
                "enhancement_mentions": enhancement_mentions,
                "mentions_count": sum(enhancement_mentions.values())
            }
        else:
            print(f"  ❌ Full integration test failed: {response.status_code}")
            results["full_integration"] = {"success": False, "error": response.text}
    except Exception as e:
        print(f"  ❌ Full integration error: {e}")
        results["full_integration"] = {"success": False, "error": str(e)}
    
    # Test 6: Test Temporal Memory Query
    print("\n6️⃣ Testing Temporal Memory Query...")
    try:
        # Ask a temporal question
        temporal_question = {
            "message": "What did we discuss in our testing episode? Can you recall our conversation about enhancement systems?",
            "context": {"temporal_query": True, "user_id": test_user}
        }
        
        response = requests.post(f"{base_url}/api/chat",
            json=temporal_question,
            params={"user_id": test_user},
            timeout=20
        )
        
        if response.status_code == 200:
            data = response.json()
            response_text = data.get('response', '')
            
            # Check if response shows temporal awareness
            temporal_indicators = ["episode", "earlier", "discussed", "conversation", "remember", "recall"]
            temporal_mentions = sum(1 for indicator in temporal_indicators if indicator.lower() in response_text.lower())
            
            print(f"  ✅ Temporal query responded")
            print(f"  🕐 Temporal indicators: {temporal_mentions}")
            print(f"  💭 Response: {response_text[:200]}...")
            
            results["temporal_query"] = {
                "success": True,
                "temporal_mentions": temporal_mentions,
                "shows_temporal_awareness": temporal_mentions >= 2
            }
        else:
            print(f"  ❌ Temporal query failed: {response.status_code}")
            results["temporal_query"] = {"success": False, "error": response.text}
    except Exception as e:
        print(f"  ❌ Temporal query error: {e}")
        results["temporal_query"] = {"success": False, "error": str(e)}
    
    # Final Summary
    print("\n" + "=" * 70)
    print("🎯 COMPREHENSIVE UI CHAT TEST RESULTS")
    print("=" * 70)
    
    total_tests = len(results)
    successful_tests = sum(1 for result in results.values() if result.get("success", False))
    success_rate = (successful_tests / total_tests) * 100
    
    for test_name, result in results.items():
        status = "✅ PASS" if result.get("success", False) else "❌ FAIL"
        print(f"{test_name.replace('_', ' ').title():<30} {status}")
        
        if result.get("success", False):
            # Show additional success metrics
            if "response_time" in result:
                print(f"  ⏱️ Response Time: {result['response_time']:.2f}s")
            if "memories_found" in result:
                print(f"  🧠 Memories Found: {result['memories_found']}")
            if "avg_satisfaction" in result:
                print(f"  😊 Avg Satisfaction: {result['avg_satisfaction']:.2f}")
        else:
            print(f"  ❌ Error: {result.get('error', 'Unknown error')[:100]}")
    
    print("-" * 70)
    print(f"Overall Success Rate: {success_rate:.1f}% ({successful_tests}/{total_tests})")
    
    # Assessment
    if success_rate >= 90:
        print("🎉 UI INTEGRATION: EXCELLENT - PRODUCTION READY")
        assessment = "EXCELLENT"
    elif success_rate >= 75:
        print("✅ UI INTEGRATION: VERY GOOD - MINOR ISSUES")
        assessment = "VERY_GOOD"
    elif success_rate >= 50:
        print("⚠️  UI INTEGRATION: GOOD - SOME ISSUES")
        assessment = "GOOD"
    else:
        print("❌ UI INTEGRATION: NEEDS WORK")
        assessment = "NEEDS_WORK"
    
    # Feature-specific assessment
    print("\n🔍 FEATURE-SPECIFIC ASSESSMENT:")
    
    temporal_working = results.get("temporal_episode", {}).get("success", False) and results.get("temporal_query", {}).get("success", False)
    orchestration_working = results.get("orchestration", {}).get("success", False)
    satisfaction_working = results.get("satisfaction", {}).get("success", False)
    integration_working = results.get("full_integration", {}).get("success", False)
    
    print(f"  📅 Temporal Memory System:     {'✅ WORKING' if temporal_working else '❌ ISSUES'}")
    print(f"  🤝 Cross-Agent Orchestration:  {'✅ WORKING' if orchestration_working else '❌ ISSUES'}")
    print(f"  😊 User Satisfaction System:   {'✅ WORKING' if satisfaction_working else '❌ ISSUES'}")
    print(f"  💬 Full Chat Integration:      {'✅ WORKING' if integration_working else '❌ ISSUES'}")
    
    # User experience summary
    print(f"\n🌟 USER EXPERIENCE SUMMARY:")
    print(f"  Users can now experience:")
    
    if temporal_working:
        print(f"    ✅ Time-based memory queries and episode tracking")
    if orchestration_working:
        print(f"    ✅ Complex multi-step task coordination")
    if satisfaction_working:
        print(f"    ✅ Feedback collection and satisfaction tracking")
    if integration_working:
        print(f"    ✅ Enhanced conversational AI with multiple capabilities")
    
    working_features = sum([temporal_working, orchestration_working, satisfaction_working, integration_working])
    print(f"\n  📊 Enhancement Features Available: {working_features}/4 ({working_features/4*100:.0f}%)")
    
    return {
        "results": results,
        "success_rate": success_rate,
        "assessment": assessment,
        "features_working": {
            "temporal_memory": temporal_working,
            "orchestration": orchestration_working,
            "satisfaction": satisfaction_working,
            "integration": integration_working
        },
        "user_experience_ready": working_features >= 3
    }

if __name__ == "__main__":
    test_results = test_comprehensive_ui_integration()
    
    # Save detailed results
    with open('/home/pi/comprehensive_ui_test_results.json', 'w') as f:
        json.dump(test_results, f, indent=2)
    
    print(f"\n📊 Detailed results saved to: comprehensive_ui_test_results.json")
    
    # Final status
    if test_results["user_experience_ready"]:
        print("\n🎉 FINAL STATUS: ENHANCEMENT SYSTEMS READY FOR USER EXPERIENCE!")
    else:
        print("\n⚠️  FINAL STATUS: ENHANCEMENT SYSTEMS NEED MORE WORK")
    
    exit(0 if test_results["success_rate"] >= 75 else 1)


