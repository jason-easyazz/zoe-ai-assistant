#!/usr/bin/env python3
"""
Comprehensive Integrated System Test
Tests all optimized tools working together for Samantha-level intelligence
"""

import asyncio
import httpx
import json
import time
import logging
from typing import Dict, List

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class ComprehensiveIntegratedTester:
    """Test all optimized tools working together"""
    
    def __init__(self):
        self.base_url = "http://localhost:8000"
        self.test_scenarios = [
            {
                "name": "Samantha Warmth Test",
                "message": "I'm feeling a bit overwhelmed with work today. Can you help me feel better?",
                "expected_qualities": ["warmth", "empathy", "support"],
                "query_type": "conversation"
            },
            {
                "name": "Tool Usage Test",
                "message": "Add 'buy groceries' to my shopping list with high priority",
                "expected_qualities": ["tool_usage", "action_execution"],
                "query_type": "action"
            },
            {
                "name": "Memory Integration Test",
                "message": "What do you remember about my last project meeting?",
                "expected_qualities": ["memory_retrieval", "context_awareness"],
                "query_type": "memory"
            },
            {
                "name": "Intelligence Test",
                "message": "I need to plan a complex project with multiple phases. Can you help me break it down?",
                "expected_qualities": ["intelligence", "planning", "structure"],
                "query_type": "reasoning"
            },
            {
                "name": "Technical Test",
                "message": "Write a Python function to calculate the Fibonacci sequence efficiently",
                "expected_qualities": ["technical_accuracy", "code_quality"],
                "query_type": "coding"
            }
        ]
    
    async def test_chat_with_quality_monitoring(self, scenario: Dict) -> Dict:
        """Test chat with quality monitoring"""
        logger.info(f"🧪 Testing: {scenario['name']}")
        
        start_time = time.time()
        
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    f"{self.base_url}/api/chat",
                    json={
                        "message": scenario["message"],
                        "user_id": "test_user",
                        "context": {"query_type": scenario["query_type"]}
                    }
                )
                
                response_time = time.time() - start_time
                
                if response.status_code == 200:
                    data = response.json()
                    return {
                        "success": True,
                        "scenario": scenario["name"],
                        "response_time": response_time,
                        "response": data.get("response", "")[:200] + "..." if len(data.get("response", "")) > 200 else data.get("response", ""),
                        "routing": data.get("routing"),
                        "memories_used": data.get("memories_used", 0),
                        "context_breakdown": data.get("context_breakdown", {})
                    }
                else:
                    return {
                        "success": False,
                        "scenario": scenario["name"],
                        "response_time": response_time,
                        "error": f"HTTP {response.status_code}"
                    }
                    
        except Exception as e:
            return {
                "success": False,
                "scenario": scenario["name"],
                "response_time": time.time() - start_time,
                "error": str(e)
            }
    
    async def test_quality_metrics(self) -> Dict:
        """Test quality metrics collection"""
        logger.info("📊 Testing quality metrics...")
        
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                # Test performance endpoint
                perf_response = await client.get(f"{self.base_url}/api/models/performance")
                
                # Test quality endpoint
                quality_response = await client.get(f"{self.base_url}/api/models/quality")
                
                if perf_response.status_code == 200 and quality_response.status_code == 200:
                    return {
                        "success": True,
                        "performance_data": perf_response.json(),
                        "quality_data": quality_response.json()
                    }
                else:
                    return {
                        "success": False,
                        "error": f"Performance: {perf_response.status_code}, Quality: {quality_response.status_code}"
                    }
                    
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
    
    async def test_mcp_integration(self) -> Dict:
        """Test MCP server integration"""
        logger.info("🔧 Testing MCP integration...")
        
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                # Test MCP server health
                mcp_response = await client.get("http://localhost:8003/health")
                
                if mcp_response.status_code == 200:
                    return {
                        "success": True,
                        "mcp_status": "healthy",
                        "mcp_data": mcp_response.json()
                    }
                else:
                    return {
                        "success": False,
                        "error": f"MCP server returned {mcp_response.status_code}"
                    }
                    
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
    
    async def test_mem_agent_integration(self) -> Dict:
        """Test memory agent integration"""
        logger.info("🧠 Testing memory agent integration...")
        
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                # Test memory agent health
                mem_response = await client.get("http://localhost:11435/health")
                
                if mem_response.status_code == 200:
                    return {
                        "success": True,
                        "mem_agent_status": "healthy",
                        "mem_agent_data": mem_response.json()
                    }
                else:
                    return {
                        "success": False,
                        "error": f"Memory agent returned {mem_response.status_code}"
                    }
                    
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
    
    async def test_litellm_integration(self) -> Dict:
        """Test LiteLLM integration"""
        logger.info("⚡ Testing LiteLLM integration...")
        
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                # Test LiteLLM health with proper authentication
                headers = {"Authorization": "Bearer sk-f3320300bb32df8f176495bb888ba7c8f87a0d01c2371b50f767b9ead154175f"}
                litellm_response = await client.get("http://localhost:8001/health", headers=headers)
                
                if litellm_response.status_code == 200:
                    return {
                        "success": True,
                        "litellm_status": "healthy",
                        "litellm_data": litellm_response.json()
                    }
                else:
                    return {
                        "success": False,
                        "error": f"LiteLLM returned {litellm_response.status_code}"
                    }
                    
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
    
    async def run_comprehensive_test(self) -> Dict:
        """Run comprehensive test of all integrated systems"""
        logger.info("🚀 Starting Comprehensive Integrated System Test...")
        
        results = {
            "chat_tests": [],
            "quality_metrics": {},
            "mcp_integration": {},
            "mem_agent_integration": {},
            "litellm_integration": {},
            "summary": {}
        }
        
        # Test chat scenarios
        for scenario in self.test_scenarios:
            result = await self.test_chat_with_quality_monitoring(scenario)
            results["chat_tests"].append(result)
            
            if result["success"]:
                logger.info(f"  ✅ {scenario['name']}: {result['response_time']:.2f}s")
            else:
                logger.error(f"  ❌ {scenario['name']}: {result.get('error', 'Unknown error')}")
            
            # Small delay between tests
            await asyncio.sleep(2)
        
        # Test quality metrics
        results["quality_metrics"] = await self.test_quality_metrics()
        
        # Test MCP integration
        results["mcp_integration"] = await self.test_mcp_integration()
        
        # Test memory agent integration
        results["mem_agent_integration"] = await self.test_mem_agent_integration()
        
        # Test LiteLLM integration
        results["litellm_integration"] = await self.test_litellm_integration()
        
        # Calculate summary
        successful_chat_tests = [t for t in results["chat_tests"] if t["success"]]
        results["summary"] = {
            "total_chat_tests": len(results["chat_tests"]),
            "successful_chat_tests": len(successful_chat_tests),
            "chat_success_rate": len(successful_chat_tests) / len(results["chat_tests"]) if results["chat_tests"] else 0,
            "avg_response_time": sum(t["response_time"] for t in successful_chat_tests) / len(successful_chat_tests) if successful_chat_tests else 0,
            "quality_metrics_working": results["quality_metrics"]["success"],
            "mcp_integration_working": results["mcp_integration"]["success"],
            "mem_agent_working": results["mem_agent_integration"]["success"],
            "litellm_working": results["litellm_integration"]["success"]
        }
        
        return results
    
    def generate_test_report(self, results: Dict) -> str:
        """Generate comprehensive test report"""
        report = []
        report.append("# 🧠 Comprehensive Integrated System Test Report")
        report.append(f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}")
        report.append("")
        
        # Summary
        summary = results["summary"]
        report.append("## 📊 System Summary")
        report.append(f"**Chat Tests**: {summary['successful_chat_tests']}/{summary['total_chat_tests']} ({summary['chat_success_rate']:.1%})")
        report.append(f"**Average Response Time**: {summary['avg_response_time']:.2f}s")
        report.append(f"**Quality Metrics**: {'✅ Working' if summary['quality_metrics_working'] else '❌ Failed'}")
        report.append(f"**MCP Integration**: {'✅ Working' if summary['mcp_integration_working'] else '❌ Failed'}")
        report.append(f"**Memory Agent**: {'✅ Working' if summary['mem_agent_working'] else '❌ Failed'}")
        report.append(f"**LiteLLM**: {'✅ Working' if summary['litellm_working'] else '❌ Failed'}")
        report.append("")
        
        # Chat test results
        report.append("## 💬 Chat Test Results")
        for test in results["chat_tests"]:
            status = "✅" if test["success"] else "❌"
            report.append(f"### {test['scenario']}")
            report.append(f"**Status**: {status}")
            if test["success"]:
                report.append(f"**Response Time**: {test['response_time']:.2f}s")
                report.append(f"**Routing**: {test.get('routing', 'N/A')}")
                report.append(f"**Memories Used**: {test.get('memories_used', 0)}")
                report.append(f"**Response Preview**: {test['response']}")
            else:
                report.append(f"**Error**: {test.get('error', 'Unknown error')}")
            report.append("")
        
        # Integration results
        report.append("## 🔧 Integration Results")
        
        # Quality metrics
        if results["quality_metrics"]["success"]:
            quality_data = results["quality_metrics"]["quality_data"]
            report.append("### Quality Metrics")
            report.append(f"**Total Calls**: {quality_data['summary']['total_calls']}")
            report.append(f"**Average Quality Score**: {quality_data['summary']['avg_quality_score']:.2f}")
            report.append(f"**Average Warmth Score**: {quality_data['summary']['avg_warmth_score']:.2f}")
            report.append(f"**Models Tracked**: {quality_data['summary']['models_tracked']}")
            report.append("")
        else:
            report.append("### Quality Metrics: ❌ Failed")
            report.append("")
        
        # MCP Integration
        if results["mcp_integration"]["success"]:
            report.append("### MCP Integration: ✅ Working")
        else:
            report.append("### MCP Integration: ❌ Failed")
        report.append("")
        
        # Memory Agent
        if results["mem_agent_integration"]["success"]:
            report.append("### Memory Agent: ✅ Working")
        else:
            report.append("### Memory Agent: ❌ Failed")
        report.append("")
        
        # LiteLLM
        if results["litellm_integration"]["success"]:
            report.append("### LiteLLM: ✅ Working")
        else:
            report.append("### LiteLLM: ❌ Failed")
        report.append("")
        
        # Overall assessment
        report.append("## 🎯 Overall Assessment")
        
        working_components = sum([
            summary['quality_metrics_working'],
            summary['mcp_integration_working'],
            summary['mem_agent_working'],
            summary['litellm_working']
        ])
        
        total_components = 4
        system_health = working_components / total_components
        
        if system_health >= 0.8:
            report.append("🟢 **System Status**: EXCELLENT - All major components working")
        elif system_health >= 0.6:
            report.append("🟡 **System Status**: GOOD - Most components working")
        elif system_health >= 0.4:
            report.append("🟠 **System Status**: FAIR - Some components need attention")
        else:
            report.append("🔴 **System Status**: POOR - Multiple components failing")
        
        report.append(f"**Component Health**: {working_components}/{total_components} ({system_health:.1%})")
        report.append(f"**Chat Success Rate**: {summary['chat_success_rate']:.1%}")
        report.append("")
        
        # Recommendations
        report.append("## 🚀 Recommendations")
        
        if not summary['quality_metrics_working']:
            report.append("🔧 **Fix Quality Metrics**: The quality monitoring system needs attention.")
        
        if not summary['mcp_integration_working']:
            report.append("🔧 **Fix MCP Integration**: The MCP server needs to be configured and started.")
        
        if not summary['mem_agent_working']:
            report.append("🔧 **Fix Memory Agent**: The memory system needs attention.")
        
        if not summary['litellm_working']:
            report.append("🔧 **Fix LiteLLM**: The model routing system needs attention.")
        
        if summary['chat_success_rate'] < 0.8:
            report.append("⚠️ **Improve Chat Success Rate**: Some chat tests are failing.")
        
        if summary['avg_response_time'] > 30:
            report.append("⚡ **Optimize Response Times**: Consider using faster models or optimizing parameters.")
        
        report.append("")
        report.append("## 🎉 Next Steps")
        report.append("1. Monitor system performance over time")
        report.append("2. Optimize based on quality metrics")
        report.append("3. Expand tool integrations")
        report.append("4. Implement additional Samantha-level features")
        
        return "\n".join(report)

async def main():
    """Main test function"""
    tester = ComprehensiveIntegratedTester()
    
    # Check if the core service is available
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{tester.base_url}/health", timeout=5.0)
            if response.status_code != 200:
                logger.error("❌ Core service is not available")
                return
    except Exception as e:
        logger.error(f"❌ Cannot connect to core service: {e}")
        return
    
    # Run the comprehensive test
    results = await tester.run_comprehensive_test()
    
    # Generate and save report
    report = tester.generate_test_report(results)
    
    with open("/home/pi/zoe/COMPREHENSIVE_INTEGRATED_TEST_REPORT.md", "w") as f:
        f.write(report)
    
    print("\n" + "="*80)
    print("🎉 COMPREHENSIVE INTEGRATED SYSTEM TEST COMPLETE!")
    print("="*80)
    print(report)
    print("\n📄 Full report saved to: /home/pi/zoe/COMPREHENSIVE_INTEGRATED_TEST_REPORT.md")

if __name__ == "__main__":
    asyncio.run(main())
