#!/usr/bin/env python3
"""
Test Script for Intelligent Model Management System
Tests the self-adapting model selection and quality monitoring
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

class IntelligentSystemTester:
    """Test the intelligent model management system"""
    
    def __init__(self):
        self.base_url = "http://localhost:8000"
        self.test_scenarios = [
            {
                "message": "Hello, how are you today?",
                "query_type": "conversation",
                "expected_quality": {"quality": 6, "warmth": 7, "intelligence": 5}
            },
            {
                "message": "Add bread to my shopping list",
                "query_type": "action",
                "expected_quality": {"quality": 7, "tool_usage": 8}
            },
            {
                "message": "What did I do last week?",
                "query_type": "memory",
                "expected_quality": {"quality": 6, "intelligence": 6}
            },
            {
                "message": "Analyze the pros and cons of different AI architectures",
                "query_type": "reasoning",
                "expected_quality": {"quality": 8, "intelligence": 8}
            },
            {
                "message": "Write a Python function to sort a list",
                "query_type": "coding",
                "expected_quality": {"quality": 7, "intelligence": 8, "tool_usage": 5}
            }
        ]
    
    async def test_enhanced_chat(self, scenario: Dict) -> Dict:
        """Test the enhanced chat endpoint"""
        logger.info(f"🧪 Testing scenario: {scenario['query_type']}")
        
        start_time = time.time()
        
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    f"{self.base_url}/api/chat/enhanced",
                    json={
                        "message": scenario["message"],
                        "query_type": scenario["query_type"],
                        "max_response_time": 30.0,
                        "user_id": "test_user"
                    }
                )
                
                response_time = time.time() - start_time
                
                if response.status_code == 200:
                    data = response.json()
                    return {
                        "success": True,
                        "response_time": response_time,
                        "model_used": data.get("model_used"),
                        "quality_scores": data.get("quality_scores", {}),
                        "response_text": data.get("response", "")[:100] + "..." if len(data.get("response", "")) > 100 else data.get("response", ""),
                        "query_type": scenario["query_type"]
                    }
                else:
                    return {
                        "success": False,
                        "response_time": response_time,
                        "error": f"HTTP {response.status_code}",
                        "query_type": scenario["query_type"]
                    }
                    
        except Exception as e:
            return {
                "success": False,
                "response_time": time.time() - start_time,
                "error": str(e),
                "query_type": scenario["query_type"]
            }
    
    async def test_performance_monitoring(self) -> Dict:
        """Test the performance monitoring endpoints"""
        logger.info("📊 Testing performance monitoring...")
        
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                # Test performance summary
                perf_response = await client.get(f"{self.base_url}/api/models/performance")
                
                # Test model rankings
                rankings_response = await client.get(f"{self.base_url}/api/models/rankings")
                
                if perf_response.status_code == 200 and rankings_response.status_code == 200:
                    return {
                        "success": True,
                        "performance_data": perf_response.json(),
                        "rankings_data": rankings_response.json()
                    }
                else:
                    return {
                        "success": False,
                        "error": f"Performance: {perf_response.status_code}, Rankings: {rankings_response.status_code}"
                    }
                    
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
    
    async def test_model_adaptation(self) -> Dict:
        """Test manual model adaptation"""
        logger.info("🔄 Testing model adaptation...")
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(f"{self.base_url}/api/models/adapt")
                
                if response.status_code == 200:
                    return {
                        "success": True,
                        "data": response.json()
                    }
                else:
                    return {
                        "success": False,
                        "error": f"HTTP {response.status_code}"
                    }
                    
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
    
    async def run_comprehensive_test(self) -> Dict:
        """Run comprehensive test of the intelligent system"""
        logger.info("🚀 Starting Comprehensive Intelligent System Test...")
        
        results = {
            "chat_tests": [],
            "performance_monitoring": {},
            "model_adaptation": {},
            "summary": {}
        }
        
        # Test enhanced chat with different scenarios
        for scenario in self.test_scenarios:
            result = await self.test_enhanced_chat(scenario)
            results["chat_tests"].append(result)
            
            if result["success"]:
                logger.info(f"  ✅ {scenario['query_type']}: {result['model_used']} "
                           f"({result['response_time']:.2f}s, Quality: {result['quality_scores']})")
            else:
                logger.error(f"  ❌ {scenario['query_type']}: {result.get('error', 'Unknown error')}")
            
            # Small delay between tests
            await asyncio.sleep(2)
        
        # Test performance monitoring
        results["performance_monitoring"] = await self.test_performance_monitoring()
        
        # Test model adaptation
        results["model_adaptation"] = await self.test_model_adaptation()
        
        # Calculate summary
        successful_tests = [t for t in results["chat_tests"] if t["success"]]
        results["summary"] = {
            "total_tests": len(results["chat_tests"]),
            "successful_tests": len(successful_tests),
            "success_rate": len(successful_tests) / len(results["chat_tests"]) if results["chat_tests"] else 0,
            "avg_response_time": sum(t["response_time"] for t in successful_tests) / len(successful_tests) if successful_tests else 0,
            "models_used": list(set(t["model_used"] for t in successful_tests if t.get("model_used"))),
            "performance_monitoring_working": results["performance_monitoring"]["success"],
            "model_adaptation_working": results["model_adaptation"]["success"]
        }
        
        return results
    
    def generate_test_report(self, results: Dict) -> str:
        """Generate comprehensive test report"""
        report = []
        report.append("# 🧠 Intelligent Model Management System Test Report")
        report.append(f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}")
        report.append("")
        
        # Summary
        summary = results["summary"]
        report.append("## 📊 Test Summary")
        report.append(f"**Total Tests**: {summary['total_tests']}")
        report.append(f"**Successful Tests**: {summary['successful_tests']}")
        report.append(f"**Success Rate**: {summary['success_rate']:.1%}")
        report.append(f"**Average Response Time**: {summary['avg_response_time']:.2f}s")
        report.append(f"**Models Used**: {', '.join(summary['models_used'])}")
        report.append(f"**Performance Monitoring**: {'✅ Working' if summary['performance_monitoring_working'] else '❌ Failed'}")
        report.append(f"**Model Adaptation**: {'✅ Working' if summary['model_adaptation_working'] else '❌ Failed'}")
        report.append("")
        
        # Chat test results
        report.append("## 💬 Chat Test Results")
        for i, test in enumerate(results["chat_tests"]):
            status = "✅" if test["success"] else "❌"
            report.append(f"### Test {i+1}: {test['query_type']}")
            report.append(f"**Status**: {status}")
            if test["success"]:
                report.append(f"**Model Used**: {test['model_used']}")
                report.append(f"**Response Time**: {test['response_time']:.2f}s")
                report.append(f"**Quality Scores**: {test['quality_scores']}")
                report.append(f"**Response Preview**: {test['response_text']}")
            else:
                report.append(f"**Error**: {test.get('error', 'Unknown error')}")
            report.append("")
        
        # Performance monitoring results
        report.append("## 📈 Performance Monitoring Results")
        if results["performance_monitoring"]["success"]:
            perf_data = results["performance_monitoring"]["performance_data"]
            report.append("**Performance Summary**:")
            for key, value in perf_data.items():
                report.append(f"- {key}: {value}")
            report.append("")
        else:
            report.append("❌ Performance monitoring failed")
            report.append("")
        
        # Model adaptation results
        report.append("## 🔄 Model Adaptation Results")
        if results["model_adaptation"]["success"]:
            report.append("✅ Model adaptation is working")
        else:
            report.append("❌ Model adaptation failed")
        report.append("")
        
        # Recommendations
        report.append("## 🎯 Recommendations")
        
        if summary["success_rate"] < 0.8:
            report.append("⚠️ **Low Success Rate**: Some tests are failing. Check model availability and configuration.")
        
        if summary["avg_response_time"] > 30:
            report.append("⚠️ **Slow Response Times**: Consider optimizing model parameters or using faster models.")
        
        if not summary["performance_monitoring_working"]:
            report.append("🔧 **Fix Performance Monitoring**: The performance tracking system needs attention.")
        
        if not summary["model_adaptation_working"]:
            report.append("🔧 **Fix Model Adaptation**: The self-adapting model selection needs attention.")
        
        if len(summary["models_used"]) < 2:
            report.append("📊 **Limited Model Diversity**: Only one model is being used. Check model selection logic.")
        
        report.append("")
        report.append("## 🚀 Next Steps")
        report.append("1. Monitor the system performance over time")
        report.append("2. Check model rankings and adaptation")
        report.append("3. Optimize model parameters based on quality scores")
        report.append("4. Implement additional quality metrics if needed")
        
        return "\n".join(report)

async def main():
    """Main test function"""
    tester = IntelligentSystemTester()
    
    # Check if the enhanced chat service is available
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{tester.base_url}/health", timeout=5.0)
            if response.status_code != 200:
                logger.error("❌ Enhanced chat service is not available")
                return
    except Exception as e:
        logger.error(f"❌ Cannot connect to enhanced chat service: {e}")
        return
    
    # Run the comprehensive test
    results = await tester.run_comprehensive_test()
    
    # Generate and save report
    report = tester.generate_test_report(results)
    
    with open(str(PROJECT_ROOT / "INTELLIGENT_SYSTEM_TEST_REPORT.md"), "w") as f:
        f.write(report)
    
    print("\n" + "="*80)
    print("🎉 INTELLIGENT MODEL MANAGEMENT SYSTEM TEST COMPLETE!")
    print("="*80)
    print(report)
    print("\n📄 Full report saved to: /home/zoe/assistant/INTELLIGENT_SYSTEM_TEST_REPORT.md")

if __name__ == "__main__":
    asyncio.run(main())

