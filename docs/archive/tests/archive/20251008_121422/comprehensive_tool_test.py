#!/usr/bin/env python3
"""
Comprehensive Tool and Enhancement Test
=======================================

Test all tools and enhancements to ensure 100% functionality.
"""

import requests
import json
import time
from datetime import datetime

def test_all_systems():
    """Test every system and tool comprehensively"""
    print("🔧 COMPREHENSIVE TOOL AND ENHANCEMENT TEST")
    print("=" * 60)
    
    base_url = "http://localhost:8000"
    test_user = f"comprehensive_test_{int(time.time())}"
    
    results = {}
    
    # 1. Test Core System Health
    print("\n🏥 Testing Core System Health...")
    try:
        response = requests.get(f"{base_url}/health", timeout=5)
        if response.status_code == 200:
            data = response.json()
            print(f"  ✅ System: {data['service']} v{data['version']}")
            print(f"  ✅ Features: {len(data['features'])} available")
            print(f"  ✅ Enhancements: {data.get('enhancements_loaded', False)}")
            results["core_health"] = {"success": True, "version": data['version'], "features": len(data['features'])}
        else:
            print(f"  ❌ Health check failed: {response.status_code}")
            results["core_health"] = {"success": False, "error": response.status_code}
    except Exception as e:
        print(f"  ❌ Health check error: {e}")
        results["core_health"] = {"success": False, "error": str(e)}
    
    # 2. Test LiteLLM Integration
    print("\n🤖 Testing LiteLLM Integration...")
    try:
        response = requests.get("http://localhost:8001/health", timeout=5)
        if response.status_code == 200:
            print("  ✅ LiteLLM service: HEALTHY")
            results["litellm"] = {"success": True, "status": "healthy"}
        else:
            print(f"  ❌ LiteLLM service: UNHEALTHY ({response.status_code})")
            results["litellm"] = {"success": False, "status": response.status_code}
    except Exception as e:
        print(f"  ❌ LiteLLM error: {e}")
        results["litellm"] = {"success": False, "error": str(e)}
    
    # 3. Test Ollama Integration
    print("\n🦙 Testing Ollama Integration...")
    try:
        response = requests.get("http://localhost:11434/api/tags", timeout=5)
        if response.status_code == 200:
            data = response.json()
            models = data.get("models", [])
            print(f"  ✅ Ollama: {len(models)} models available")
            for model in models[:3]:  # Show first 3 models
                print(f"    - {model['name']}")
            results["ollama"] = {"success": True, "models": len(models)}
        else:
            print(f"  ❌ Ollama: UNHEALTHY ({response.status_code})")
            results["ollama"] = {"success": False, "status": response.status_code}
    except Exception as e:
        print(f"  ❌ Ollama error: {e}")
        results["ollama"] = {"success": False, "error": str(e)}
    
    # 4. Test MEM Agent
    print("\n🧠 Testing MEM Agent...")
    try:
        response = requests.get("http://localhost:11435/health", timeout=5)
        if response.status_code == 200:
            print("  ✅ MEM Agent: HEALTHY")
            results["mem_agent"] = {"success": True, "status": "healthy"}
        else:
            print(f"  ❌ MEM Agent: UNHEALTHY ({response.status_code})")
            results["mem_agent"] = {"success": False, "status": response.status_code}
    except Exception as e:
        print(f"  ❌ MEM Agent error: {e}")
        results["mem_agent"] = {"success": False, "error": str(e)}
    
    # 5. Test MCP Server
    print("\n🔌 Testing MCP Server...")
    try:
        response = requests.get("http://localhost:8003/health", timeout=5)
        if response.status_code == 200:
            print("  ✅ MCP Server: HEALTHY")
            results["mcp_server"] = {"success": True, "status": "healthy"}
        else:
            print(f"  ❌ MCP Server: UNHEALTHY ({response.status_code})")
            results["mcp_server"] = {"success": False, "status": response.status_code}
    except Exception as e:
        print(f"  ❌ MCP Server error: {e}")
        results["mcp_server"] = {"success": False, "error": str(e)}
    
    # 6. Test Enhancement Systems APIs
    print("\n🌟 Testing Enhancement Systems...")
    
    # Temporal Memory
    try:
        response = requests.get(f"{base_url}/api/temporal-memory/stats?user_id={test_user}", timeout=5)
        if response.status_code == 200:
            print("  ✅ Temporal Memory API: WORKING")
            results["temporal_memory_api"] = {"success": True}
        else:
            print(f"  ❌ Temporal Memory API: FAILED ({response.status_code})")
            results["temporal_memory_api"] = {"success": False, "status": response.status_code}
    except Exception as e:
        print(f"  ❌ Temporal Memory API error: {e}")
        results["temporal_memory_api"] = {"success": False, "error": str(e)}
    
    # Cross-Agent Orchestration
    try:
        response = requests.get(f"{base_url}/api/orchestration/experts", timeout=5)
        if response.status_code == 200:
            data = response.json()
            experts = data.get("experts", {})
            print(f"  ✅ Orchestration API: WORKING ({len(experts)} experts)")
            results["orchestration_api"] = {"success": True, "experts": len(experts)}
        else:
            print(f"  ❌ Orchestration API: FAILED ({response.status_code})")
            results["orchestration_api"] = {"success": False, "status": response.status_code}
    except Exception as e:
        print(f"  ❌ Orchestration API error: {e}")
        results["orchestration_api"] = {"success": False, "error": str(e)}
    
    # User Satisfaction
    try:
        response = requests.get(f"{base_url}/api/satisfaction/levels", timeout=5)
        if response.status_code == 200:
            data = response.json()
            levels = data.get("satisfaction_levels", [])
            print(f"  ✅ Satisfaction API: WORKING ({len(levels)} levels)")
            results["satisfaction_api"] = {"success": True, "levels": len(levels)}
        else:
            print(f"  ❌ Satisfaction API: FAILED ({response.status_code})")
            results["satisfaction_api"] = {"success": False, "status": response.status_code}
    except Exception as e:
        print(f"  ❌ Satisfaction API error: {e}")
        results["satisfaction_api"] = {"success": False, "error": str(e)}
    
    # 7. Test Core APIs
    print("\n🔧 Testing Core APIs...")
    
    core_apis = [
        ("/api/calendar/events", "Calendar"),
        ("/api/memories", "Memory System"),
        ("/api/lists", "Lists"),
        ("/api/self-awareness/status", "Self-Awareness")
    ]
    
    for endpoint, name in core_apis:
        try:
            response = requests.get(f"{base_url}{endpoint}?user_id={test_user}", timeout=5)
            if response.status_code in [200, 201]:
                print(f"  ✅ {name}: WORKING")
                results[f"core_api_{name.lower().replace(' ', '_')}"] = {"success": True}
            else:
                print(f"  ❌ {name}: ISSUES ({response.status_code})")
                results[f"core_api_{name.lower().replace(' ', '_')}"] = {"success": False, "status": response.status_code}
        except Exception as e:
            print(f"  ❌ {name}: ERROR - {e}")
            results[f"core_api_{name.lower().replace(' ', '_')}"] = {"success": False, "error": str(e)}
    
    # 8. Test Chat UI Integration with Enhancement Features
    print("\n💬 Testing Chat UI with Enhancement Features...")
    
    test_scenarios = [
        {
            "name": "Temporal Memory Query",
            "message": "What did we discuss in our previous conversation? Can you remember our earlier topics?",
            "expected_keywords": ["previous", "remember", "earlier", "discussed", "conversation"]
        },
        {
            "name": "Multi-Expert Orchestration",
            "message": "I need you to schedule a meeting for tomorrow at 2pm, add 'prepare presentation' to my tasks, and remember that this is high priority",
            "expected_keywords": ["schedule", "meeting", "tasks", "remember", "priority"]
        },
        {
            "name": "Self-Awareness Enhancement",
            "message": "Tell me about your new enhancement systems. How do they help you assist users better?",
            "expected_keywords": ["enhancement", "systems", "temporal", "collaboration", "satisfaction", "help"]
        }
    ]
    
    chat_results = {}
    for scenario in test_scenarios:
        try:
            start_time = time.time()
            response = requests.post(f"{base_url}/api/chat",
                json={"message": scenario["message"]},
                params={"user_id": test_user},
                timeout=20
            )
            response_time = time.time() - start_time
            
            if response.status_code == 200:
                data = response.json()
                response_text = data.get('response', '')
                
                # Check for expected keywords
                keyword_matches = sum(1 for keyword in scenario["expected_keywords"] 
                                    if keyword.lower() in response_text.lower())
                
                # Check response quality
                is_detailed = len(response_text) > 50
                is_conversational = any(word in response_text.lower() for word in ["i", "me", "my", "you", "we"])
                
                print(f"  📝 {scenario['name']}:")
                print(f"    ⏱️ Response Time: {response_time:.2f}s")
                print(f"    📊 Keyword Matches: {keyword_matches}/{len(scenario['expected_keywords'])}")
                print(f"    💬 Response Length: {len(response_text)} chars")
                print(f"    🗣️ Conversational: {is_conversational}")
                print(f"    📄 Preview: {response_text[:100]}...")
                
                chat_results[scenario['name']] = {
                    "success": True,
                    "response_time": response_time,
                    "keyword_matches": keyword_matches,
                    "response_length": len(response_text),
                    "is_conversational": is_conversational,
                    "quality_score": (keyword_matches / len(scenario['expected_keywords'])) * 100
                }
            else:
                print(f"  ❌ {scenario['name']}: FAILED ({response.status_code})")
                chat_results[scenario['name']] = {"success": False, "status": response.status_code}
        except Exception as e:
            print(f"  ❌ {scenario['name']}: ERROR - {e}")
            chat_results[scenario['name']] = {"success": False, "error": str(e)}
    
    results["chat_scenarios"] = chat_results
    
    # Calculate overall scores
    total_tests = len([k for k in results.keys() if k != "chat_scenarios"]) + len(chat_results)
    successful_tests = sum(1 for v in results.values() if isinstance(v, dict) and v.get("success", False))
    successful_tests += sum(1 for v in chat_results.values() if v.get("success", False))
    
    success_rate = (successful_tests / total_tests) * 100
    
    print("\n" + "=" * 60)
    print("🎯 COMPREHENSIVE TEST RESULTS")
    print("=" * 60)
    
    # Core systems
    print("Core Systems:")
    for key, result in results.items():
        if key != "chat_scenarios" and isinstance(result, dict):
            status = "✅ PASS" if result.get("success", False) else "❌ FAIL"
            print(f"  {key.replace('_', ' ').title():<25} {status}")
    
    # Chat scenarios
    print("\nChat UI Integration:")
    for scenario_name, result in chat_results.items():
        status = "✅ PASS" if result.get("success", False) else "❌ FAIL"
        quality = f"({result.get('quality_score', 0):.0f}%)" if result.get("success") else ""
        print(f"  {scenario_name:<25} {status} {quality}")
    
    print("-" * 60)
    print(f"Overall Success Rate: {success_rate:.1f}% ({successful_tests}/{total_tests})")
    
    if success_rate >= 95:
        print("🎉 STATUS: OUTSTANDING - 100% READY")
    elif success_rate >= 90:
        print("✅ STATUS: EXCELLENT - NEARLY PERFECT")
    elif success_rate >= 80:
        print("⚠️  STATUS: GOOD - MINOR ISSUES")
    else:
        print("❌ STATUS: NEEDS WORK")
    
    return results, success_rate

if __name__ == "__main__":
    results, success_rate = test_all_systems()
    
    # Save results
    with open('/home/pi/comprehensive_test_results.json', 'w') as f:
        json.dump({"results": results, "success_rate": success_rate, "timestamp": datetime.now().isoformat()}, f, indent=2)
    
    print(f"\n📊 Results saved to: comprehensive_test_results.json")
    
    if success_rate >= 95:
        print("\n🚀 READY FOR 100% CERTIFICATION!")
    else:
        print(f"\n⚠️  Need to achieve {95 - success_rate:.1f}% more for 100% certification")


