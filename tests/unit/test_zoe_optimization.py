#!/usr/bin/env python3
"""
Test script for Zoe's optimized brain responses
Tests the improved chat system with direct action execution
"""

import asyncio
import httpx
import json
import time

class ZoeOptimizationTester:
    def __init__(self):
        self.zoe_api_url = "http://localhost:8000"
        self.test_results = []
    
    async def test_direct_action(self, message: str, expected_action: str = None):
        """Test direct action execution"""
        print(f"\n🧪 Testing: '{message}'")
        
        start_time = time.time()
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.zoe_api_url}/api/chat",
                    json={
                        "message": message,
                        "user_id": "test_user",
                        "context": {"mode": "user"}
                    }
                )
                
                response_time = time.time() - start_time
                
                if response.status_code == 200:
                    data = response.json()
                    zoe_response = data.get("response", "")
                    routing = data.get("routing", "")
                    actions_executed = data.get("actions_executed", 0)
                    
                    print(f"   ⏱️  Response time: {response_time:.2f}s")
                    print(f"   🎯 Routing: {routing}")
                    print(f"   ⚡ Actions executed: {actions_executed}")
                    print(f"   💬 Zoe's response: {zoe_response}")
                    
                    # Check if it's a direct action response
                    is_direct = (
                        routing in ["action_executed", "mcp_action_executed"] or
                        actions_executed > 0 or
                        "added" in zoe_response.lower() or
                        "created" in zoe_response.lower() or
                        "scheduled" in zoe_response.lower()
                    )
                    
                    result = {
                        "message": message,
                        "response_time": response_time,
                        "routing": routing,
                        "actions_executed": actions_executed,
                        "is_direct": is_direct,
                        "response": zoe_response,
                        "success": True
                    }
                    
                    if is_direct:
                        print(f"   ✅ DIRECT ACTION: Zoe executed the action directly!")
                    else:
                        print(f"   ⚠️  CONVERSATIONAL: Zoe responded conversationally")
                    
                    self.test_results.append(result)
                    return result
                    
                else:
                    print(f"   ❌ HTTP Error: {response.status_code}")
                    return {"success": False, "error": f"HTTP {response.status_code}"}
                    
        except Exception as e:
            print(f"   ❌ Error: {str(e)}")
            return {"success": False, "error": str(e)}
    
    async def run_optimization_tests(self):
        """Run comprehensive optimization tests"""
        print("🚀 Testing Zoe's Optimized Brain Responses")
        print("=" * 60)
        
        # Test cases for direct actions
        test_cases = [
            ("Add bread to shopping list", "direct_action"),
            ("Add milk to the shopping list", "direct_action"),
            ("Show me my lists", "direct_action"),
            ("What's my schedule today?", "conversational"),
            ("How are you?", "conversational"),
            ("Add eggs to shopping list", "direct_action"),
            ("Create a new task", "direct_action"),
            ("Tell me about the weather", "conversational"),
        ]
        
        for message, expected_type in test_cases:
            await self.test_direct_action(message, expected_type)
            await asyncio.sleep(1)  # Brief pause between tests
        
        # Analyze results
        self.analyze_results()
    
    def analyze_results(self):
        """Analyze test results"""
        print("\n" + "=" * 60)
        print("📊 OPTIMIZATION ANALYSIS")
        print("=" * 60)
        
        if not self.test_results:
            print("❌ No test results to analyze")
            return
        
        total_tests = len(self.test_results)
        successful_tests = len([r for r in self.test_results if r.get("success", False)])
        direct_actions = len([r for r in self.test_results if r.get("is_direct", False)])
        
        avg_response_time = sum(r.get("response_time", 0) for r in self.test_results) / total_tests
        
        print(f"📈 Total Tests: {total_tests}")
        print(f"✅ Successful: {successful_tests}/{total_tests}")
        print(f"⚡ Direct Actions: {direct_actions}/{total_tests}")
        print(f"⏱️  Average Response Time: {avg_response_time:.2f}s")
        
        # Performance analysis
        if avg_response_time < 2.0:
            print("🚀 EXCELLENT: Response times are very fast!")
        elif avg_response_time < 5.0:
            print("✅ GOOD: Response times are acceptable")
        else:
            print("⚠️  SLOW: Response times need improvement")
        
        # Direct action analysis
        direct_action_rate = (direct_actions / total_tests) * 100
        if direct_action_rate > 70:
            print("🎯 EXCELLENT: High direct action execution rate!")
        elif direct_action_rate > 50:
            print("✅ GOOD: Good direct action execution rate")
        else:
            print("⚠️  NEEDS WORK: Low direct action execution rate")
        
        # Show detailed results
        print(f"\n📋 DETAILED RESULTS:")
        for i, result in enumerate(self.test_results, 1):
            status = "✅" if result.get("is_direct", False) else "💬"
            print(f"   {i}. {status} '{result['message']}' ({result.get('response_time', 0):.2f}s)")
        
        print(f"\n🎉 Optimization test completed!")

async def main():
    """Main test function"""
    tester = ZoeOptimizationTester()
    await tester.run_optimization_tests()

if __name__ == "__main__":
    asyncio.run(main())

