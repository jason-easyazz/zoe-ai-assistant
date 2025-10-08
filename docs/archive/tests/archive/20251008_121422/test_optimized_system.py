#!/usr/bin/env python3
"""
Comprehensive System Test for Optimized Zoe AI
Tests all enhancement systems and chat functionality
"""
import asyncio
import httpx
import json
import time
from typing import Dict, List, Any

class ZoeSystemTester:
    def __init__(self):
        self.base_url = "http://localhost:8000"
        self.results = {}
        self.start_time = time.time()
    
    async def test_health_check(self) -> bool:
        """Test system health check"""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"{self.base_url}/health")
                if response.status_code == 200:
                    data = response.json()
                    print(f"✅ Health Check: {data.get('status', 'unknown')}")
                    print(f"   Version: {data.get('version', 'unknown')}")
                    print(f"   Enhancements: {data.get('enhancements_loaded', False)}")
                    return True
                else:
                    print(f"❌ Health Check failed: {response.status_code}")
                    return False
        except Exception as e:
            print(f"❌ Health Check error: {e}")
            return False
    
    async def test_chat_system(self) -> Dict[str, Any]:
        """Test chat system functionality"""
        print("\n🔍 Testing Chat System...")
        
        test_cases = [
            {
                "name": "Basic Greeting",
                "message": "Hello Zoe! How are you today?",
                "expected_keywords": ["hello", "good", "great", "wonderful", "amazing"]
            },
            {
                "name": "Enhancement Awareness",
                "message": "What enhancement systems do you have?",
                "expected_keywords": ["temporal", "memory", "collaboration", "satisfaction", "light rag"]
            },
            {
                "name": "Temporal Memory",
                "message": "Can you help me remember something from our conversation?",
                "expected_keywords": ["remember", "memory", "temporal", "conversation"]
            },
            {
                "name": "Multi-Expert Coordination",
                "message": "I need to schedule a meeting and add it to my task list",
                "expected_keywords": ["schedule", "meeting", "task", "list", "calendar"]
            }
        ]
        
        results = {"total": len(test_cases), "passed": 0, "failed": 0, "details": []}
        
        for test_case in test_cases:
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.post(
                        f"{self.base_url}/api/chat",
                        json={
                            "message": test_case["message"],
                            "user_id": "test_user"
                        }
                    )
                    
                    if response.status_code == 200:
                        data = response.json()
                        response_text = data.get("response", "").lower()
                        response_time = data.get("response_time", 0)
                        
                        # Check for expected keywords
                        keyword_matches = sum(1 for keyword in test_case["expected_keywords"] 
                                           if keyword.lower() in response_text)
                        
                        success = keyword_matches > 0 and response_time < 30.0
                        
                        if success:
                            results["passed"] += 1
                            print(f"✅ {test_case['name']}: {response_time:.2f}s")
                        else:
                            results["failed"] += 1
                            print(f"❌ {test_case['name']}: {response_time:.2f}s (keywords: {keyword_matches}/{len(test_case['expected_keywords'])})")
                        
                        results["details"].append({
                            "name": test_case["name"],
                            "success": success,
                            "response_time": response_time,
                            "keyword_matches": keyword_matches,
                            "response_length": len(data.get("response", "")),
                            "enhancement_used": data.get("enhancement_used"),
                            "confidence": data.get("confidence")
                        })
                    else:
                        results["failed"] += 1
                        print(f"❌ {test_case['name']}: HTTP {response.status_code}")
                        results["details"].append({
                            "name": test_case["name"],
                            "success": False,
                            "error": f"HTTP {response.status_code}"
                        })
                        
            except Exception as e:
                results["failed"] += 1
                print(f"❌ {test_case['name']}: {e}")
                results["details"].append({
                    "name": test_case["name"],
                    "success": False,
                    "error": str(e)
                })
        
        return results
    
    async def test_enhanced_chat(self) -> Dict[str, Any]:
        """Test enhanced chat functionality"""
        print("\n🔍 Testing Enhanced Chat System...")
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.base_url}/api/chat/enhanced",
                    json={
                        "message": "Show me all your capabilities and enhancement systems",
                        "user_id": "test_user"
                    }
                )
                
                if response.status_code == 200:
                    data = response.json()
                    response_text = data.get("response", "").lower()
                    response_time = data.get("response_time", 0)
                    
                    # Check for enhancement system mentions
                    enhancement_keywords = [
                        "temporal", "memory", "collaboration", "satisfaction", 
                        "light rag", "enhancement", "expert", "coordination"
                    ]
                    
                    keyword_matches = sum(1 for keyword in enhancement_keywords 
                                       if keyword in response_text)
                    
                    success = keyword_matches >= 3 and response_time < 30.0
                    
                    if success:
                        print(f"✅ Enhanced Chat: {response_time:.2f}s ({keyword_matches} keywords)")
                    else:
                        print(f"❌ Enhanced Chat: {response_time:.2f}s ({keyword_matches} keywords)")
                    
                    return {
                        "success": success,
                        "response_time": response_time,
                        "keyword_matches": keyword_matches,
                        "response_length": len(data.get("response", "")),
                        "enhancement_used": data.get("enhancement_used"),
                        "confidence": data.get("confidence")
                    }
                else:
                    print(f"❌ Enhanced Chat failed: HTTP {response.status_code}")
                    return {"success": False, "error": f"HTTP {response.status_code}"}
                    
        except Exception as e:
            print(f"❌ Enhanced Chat error: {e}")
            return {"success": False, "error": str(e)}
    
    async def test_enhancement_systems(self) -> Dict[str, Any]:
        """Test individual enhancement systems"""
        print("\n🔍 Testing Enhancement Systems...")
        
        systems = {
            "temporal_memory": "/api/temporal-memory/status",
            "cross_agent_collaboration": "/api/orchestration/status", 
            "user_satisfaction": "/api/satisfaction/status",
            "light_rag": "/api/memories/stats/light-rag"
        }
        
        results = {"total": len(systems), "active": 0, "inactive": 0, "details": []}
        
        for system_name, endpoint in systems.items():
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.get(f"{self.base_url}{endpoint}")
                    
                    if response.status_code == 200:
                        results["active"] += 1
                        print(f"✅ {system_name}: Active")
                        results["details"].append({
                            "system": system_name,
                            "status": "active",
                            "response_code": response.status_code
                        })
                    else:
                        results["inactive"] += 1
                        print(f"❌ {system_name}: Inactive (HTTP {response.status_code})")
                        results["details"].append({
                            "system": system_name,
                            "status": "inactive",
                            "response_code": response.status_code
                        })
                        
            except Exception as e:
                results["inactive"] += 1
                print(f"❌ {system_name}: Error - {e}")
                results["details"].append({
                    "system": system_name,
                    "status": "error",
                    "error": str(e)
                })
        
        return results
    
    async def test_chat_status(self) -> Dict[str, Any]:
        """Test chat system status endpoint"""
        print("\n🔍 Testing Chat Status...")
        
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"{self.base_url}/api/chat/status")
                
                if response.status_code == 200:
                    data = response.json()
                    print(f"✅ Chat Status: {data.get('status', 'unknown')}")
                    print(f"   Active Enhancements: {data.get('active_count', 0)}/{data.get('total_enhancements', 0)}")
                    print(f"   System Health: {data.get('system_health', 'unknown')}")
                    return data
                else:
                    print(f"❌ Chat Status failed: HTTP {response.status_code}")
                    return {"success": False, "error": f"HTTP {response.status_code}"}
                    
        except Exception as e:
            print(f"❌ Chat Status error: {e}")
            return {"success": False, "error": str(e)}
    
    async def test_chat_capabilities(self) -> Dict[str, Any]:
        """Test chat capabilities endpoint"""
        print("\n🔍 Testing Chat Capabilities...")
        
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"{self.base_url}/api/chat/capabilities")
                
                if response.status_code == 200:
                    data = response.json()
                    print(f"✅ Chat Capabilities: Available")
                    print(f"   AI Capabilities: {len(data.get('ai_capabilities', {}))}")
                    print(f"   Enhancement Systems: {len(data.get('enhancement_systems', {}))}")
                    return data
                else:
                    print(f"❌ Chat Capabilities failed: HTTP {response.status_code}")
                    return {"success": False, "error": f"HTTP {response.status_code}"}
                    
        except Exception as e:
            print(f"❌ Chat Capabilities error: {e}")
            return {"success": False, "error": str(e)}
    
    async def run_all_tests(self) -> Dict[str, Any]:
        """Run all system tests"""
        print("🚀 Starting Comprehensive System Test...")
        print("=" * 60)
        
        # Test 1: Health Check
        health_ok = await self.test_health_check()
        
        # Test 2: Chat System
        chat_results = await self.test_chat_system()
        
        # Test 3: Enhanced Chat
        enhanced_chat_results = await self.test_enhanced_chat()
        
        # Test 4: Enhancement Systems
        enhancement_results = await self.test_enhancement_systems()
        
        # Test 5: Chat Status
        chat_status_results = await self.test_chat_status()
        
        # Test 6: Chat Capabilities
        chat_capabilities_results = await self.test_chat_capabilities()
        
        # Calculate overall results
        total_time = time.time() - self.start_time
        
        # Calculate success rates
        chat_success_rate = (chat_results["passed"] / chat_results["total"]) * 100 if chat_results["total"] > 0 else 0
        enhancement_success_rate = (enhancement_results["active"] / enhancement_results["total"]) * 100 if enhancement_results["total"] > 0 else 0
        
        # Overall system health
        overall_health = "excellent" if chat_success_rate >= 80 and enhancement_success_rate >= 75 else "good" if chat_success_rate >= 60 and enhancement_success_rate >= 50 else "needs_improvement"
        
        results = {
            "overall_health": overall_health,
            "total_test_time": total_time,
            "health_check": health_ok,
            "chat_system": chat_results,
            "enhanced_chat": enhanced_chat_results,
            "enhancement_systems": enhancement_results,
            "chat_status": chat_status_results,
            "chat_capabilities": chat_capabilities_results,
            "success_rates": {
                "chat_system": chat_success_rate,
                "enhancement_systems": enhancement_success_rate
            },
            "summary": {
                "total_tests": chat_results["total"] + 4,  # chat tests + 4 other tests
                "passed_tests": chat_results["passed"] + (1 if health_ok else 0) + (1 if enhanced_chat_results.get("success") else 0) + enhancement_results["active"] + (1 if chat_status_results.get("status") == "operational" else 0) + (1 if chat_capabilities_results.get("ai_capabilities") else 0),
                "failed_tests": chat_results["failed"] + (0 if health_ok else 1) + (0 if enhanced_chat_results.get("success") else 1) + enhancement_results["inactive"] + (0 if chat_status_results.get("status") == "operational" else 1) + (0 if chat_capabilities_results.get("ai_capabilities") else 1)
            }
        }
        
        return results
    
    def print_summary(self, results: Dict[str, Any]):
        """Print test summary"""
        print("\n" + "=" * 60)
        print("📊 TEST SUMMARY")
        print("=" * 60)
        
        print(f"Overall Health: {results['overall_health'].upper()}")
        print(f"Total Test Time: {results['total_test_time']:.2f} seconds")
        print()
        
        print("Chat System Results:")
        print(f"  Success Rate: {results['success_rates']['chat_system']:.1f}%")
        print(f"  Passed: {results['chat_system']['passed']}/{results['chat_system']['total']}")
        print(f"  Failed: {results['chat_system']['failed']}/{results['chat_system']['total']}")
        print()
        
        print("Enhancement Systems Results:")
        print(f"  Success Rate: {results['success_rates']['enhancement_systems']:.1f}%")
        print(f"  Active: {results['enhancement_systems']['active']}/{results['enhancement_systems']['total']}")
        print(f"  Inactive: {results['enhancement_systems']['inactive']}/{results['enhancement_systems']['total']}")
        print()
        
        print("Overall Test Results:")
        print(f"  Total Tests: {results['summary']['total_tests']}")
        print(f"  Passed: {results['summary']['passed_tests']}")
        print(f"  Failed: {results['summary']['failed_tests']}")
        
        overall_success_rate = (results['summary']['passed_tests'] / results['summary']['total_tests']) * 100
        print(f"  Overall Success Rate: {overall_success_rate:.1f}%")
        print()
        
        if results['overall_health'] == 'excellent':
            print("🎉 SYSTEM IS FULLY OPTIMIZED AND READY!")
        elif results['overall_health'] == 'good':
            print("✅ SYSTEM IS WORKING WELL WITH MINOR ISSUES")
        else:
            print("⚠️  SYSTEM NEEDS ATTENTION - SOME ISSUES DETECTED")

async def main():
    """Main test function"""
    tester = ZoeSystemTester()
    results = await tester.run_all_tests()
    tester.print_summary(results)
    
    # Save results to file
    with open("/workspace/optimized_system_test_results.json", "w") as f:
        json.dump(results, f, indent=2)
    
    print(f"\n📄 Detailed results saved to: optimized_system_test_results.json")

if __name__ == "__main__":
    asyncio.run(main())
