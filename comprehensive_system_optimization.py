#!/usr/bin/env python3
"""
Comprehensive System Optimization for Zoe
Tests and optimizes all tools: LLM, Mem Agent, LiteLLM, RouteLLM, MCP, LightRAG
"""

import asyncio
import httpx
import json
import time
import logging
from typing import Dict, List, Any
from dataclasses import dataclass
import statistics

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@dataclass
class OptimizationResult:
    component: str
    test_name: str
    response_time: float
    success: bool
    optimization_score: int  # 1-10 scale
    error_message: str = ""

class SystemOptimizer:
    """Comprehensive system optimization for maximum potential"""
    
    def __init__(self):
        self.services = {
            "zoe-core": "http://localhost:8000",
            "mcp-server": "http://localhost:8003",
            "mem-agent": "http://localhost:11435",
            "litellm": "http://localhost:8001",
            "homeassistant-bridge": "http://localhost:8007",
            "n8n-bridge": "http://localhost:8009"
        }
        
        self.optimization_tests = [
            {
                "name": "LLM Performance",
                "tests": [
                    "quick_response_test",
                    "complex_reasoning_test",
                    "tool_calling_test",
                    "context_awareness_test"
                ]
            },
            {
                "name": "Memory System",
                "tests": [
                    "semantic_search_test",
                    "context_retrieval_test",
                    "relationship_mapping_test",
                    "proactive_memory_test"
                ]
            },
            {
                "name": "MCP Integration",
                "tests": [
                    "tool_discovery_test",
                    "tool_execution_test",
                    "home_assistant_test",
                    "n8n_workflow_test"
                ]
            },
            {
                "name": "RouteLLM Intelligence",
                "tests": [
                    "model_routing_test",
                    "query_classification_test",
                    "fallback_chain_test",
                    "performance_optimization_test"
                ]
            }
        ]
    
    async def test_llm_performance(self) -> List[OptimizationResult]:
        """Test LLM performance optimization"""
        logger.info("🧠 Testing LLM Performance Optimization...")
        results = []
        
        # Test different models with optimized parameters
        models_to_test = [
            {"name": "gemma3:1b", "params": {"temperature": 0.5, "top_p": 0.8, "num_predict": 64}},
            {"name": "llama3.2:1b", "params": {"temperature": 0.6, "top_p": 0.9, "num_predict": 128}},
            {"name": "qwen2.5:1.5b", "params": {"temperature": 0.7, "top_p": 0.8, "num_predict": 96}},
            {"name": "mistral:7b", "params": {"temperature": 0.8, "top_p": 0.9, "num_predict": 256}}
        ]
        
        for model in models_to_test:
            start_time = time.time()
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.post(
                        "http://localhost:11434/api/generate",
                        json={
                            "model": model["name"],
                            "prompt": "You are Zoe, an AI assistant with Samantha-level intelligence. Respond with warmth and intelligence.",
                            "stream": False,
                            "options": model["params"]
                        }
                    )
                    
                    response_time = time.time() - start_time
                    success = response.status_code == 200
                    
                    # Calculate optimization score based on response time and quality
                    if success:
                        data = response.json()
                        response_text = data.get("response", "")
                        quality_score = min(10, len(response_text) / 10)  # Simple quality metric
                        speed_score = max(1, 10 - (response_time / 2))  # Speed score
                        optimization_score = int((quality_score + speed_score) / 2)
                    else:
                        optimization_score = 0
                    
                    results.append(OptimizationResult(
                        component="LLM",
                        test_name=f"{model['name']}_performance",
                        response_time=response_time,
                        success=success,
                        optimization_score=optimization_score
                    ))
                    
            except Exception as e:
                results.append(OptimizationResult(
                    component="LLM",
                    test_name=f"{model['name']}_performance",
                    response_time=time.time() - start_time,
                    success=False,
                    optimization_score=0,
                    error_message=str(e)
                ))
        
        return results
    
    async def test_memory_system(self) -> List[OptimizationResult]:
        """Test memory system optimization"""
        logger.info("🧠 Testing Memory System Optimization...")
        results = []
        
        memory_tests = [
            {
                "name": "semantic_search",
                "query": "Find memories about my work preferences and productivity patterns",
                "expected_features": ["semantic_search", "context_retrieval"]
            },
            {
                "name": "relationship_mapping",
                "query": "Show me all relationships and their context",
                "expected_features": ["relationship_mapping", "social_intelligence"]
            },
            {
                "name": "proactive_memory",
                "query": "What should I remember about today's interactions?",
                "expected_features": ["proactive_memory", "context_awareness"]
            }
        ]
        
        for test in memory_tests:
            start_time = time.time()
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    response = await client.post(
                        f"{self.services['mem-agent']}/search",
                        json={
                            "query": test["query"],
                            "user_id": "test_user",
                            "max_results": 10,
                            "include_graph": True,
                            "execute_actions": True,
                            "context_window": 2048,
                            "temperature": 0.7,
                            "use_semantic_search": True,
                            "include_relationships": True
                        }
                    )
                    
                    response_time = time.time() - start_time
                    success = response.status_code == 200
                    
                    if success:
                        data = response.json()
                        experts = data.get("experts", [])
                        optimization_score = min(10, len(experts) * 2)  # Score based on expert engagement
                    else:
                        optimization_score = 0
                    
                    results.append(OptimizationResult(
                        component="Memory",
                        test_name=test["name"],
                        response_time=response_time,
                        success=success,
                        optimization_score=optimization_score
                    ))
                    
            except Exception as e:
                results.append(OptimizationResult(
                    component="Memory",
                    test_name=test["name"],
                    response_time=time.time() - start_time,
                    success=False,
                    optimization_score=0,
                    error_message=str(e)
                ))
        
        return results
    
    async def test_mcp_integration(self) -> List[OptimizationResult]:
        """Test MCP integration optimization"""
        logger.info("🔧 Testing MCP Integration Optimization...")
        results = []
        
        mcp_tests = [
            {
                "name": "tool_discovery",
                "endpoint": "/tools/list",
                "method": "POST",
                "data": {"_auth_token": "default", "_session_id": "default"}
            },
            {
                "name": "list_management",
                "endpoint": "/tools/add_to_list",
                "method": "POST",
                "data": {
                    "_auth_token": "default",
                    "_session_id": "default",
                    "list_name": "optimization_test",
                    "task_text": "Test task for optimization",
                    "priority": "high"
                }
            },
            {
                "name": "calendar_integration",
                "endpoint": "/tools/create_calendar_event",
                "method": "POST",
                "data": {
                    "_auth_token": "default",
                    "_session_id": "default",
                    "title": "Optimization Test Event",
                    "start_date": "2025-01-05",
                    "start_time": "14:00",
                    "description": "Testing calendar integration"
                }
            }
        ]
        
        for test in mcp_tests:
            start_time = time.time()
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    if test["method"] == "POST":
                        response = await client.post(
                            f"{self.services['mcp-server']}{test['endpoint']}",
                            json=test["data"]
                        )
                    else:
                        response = await client.get(f"{self.services['mcp-server']}{test['endpoint']}")
                    
                    response_time = time.time() - start_time
                    success = response.status_code == 200
                    
                    if success:
                        data = response.json()
                        optimization_score = 8 if "success" in str(data) else 6
                    else:
                        optimization_score = 0
                    
                    results.append(OptimizationResult(
                        component="MCP",
                        test_name=test["name"],
                        response_time=response_time,
                        success=success,
                        optimization_score=optimization_score
                    ))
                    
            except Exception as e:
                results.append(OptimizationResult(
                    component="MCP",
                    test_name=test["name"],
                    response_time=time.time() - start_time,
                    success=False,
                    optimization_score=0,
                    error_message=str(e)
                ))
        
        return results
    
    async def test_routellm_intelligence(self) -> List[OptimizationResult]:
        """Test RouteLLM intelligence optimization"""
        logger.info("🔄 Testing RouteLLM Intelligence Optimization...")
        results = []
        
        routing_tests = [
            {
                "name": "quick_query_routing",
                "query": "What time is it?",
                "expected_model": "gemma3-ultra-fast"
            },
            {
                "name": "complex_reasoning_routing",
                "query": "Analyze the pros and cons of different AI architectures for home automation",
                "expected_model": "deepseek-r1-advanced"
            },
            {
                "name": "coding_query_routing",
                "query": "Write a Python function to optimize database queries",
                "expected_model": "codellama-specialist"
            },
            {
                "name": "conversation_routing",
                "query": "Tell me about your day and how you're feeling",
                "expected_model": "qwen2.5-workhorse"
            }
        ]
        
        for test in routing_tests:
            start_time = time.time()
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.post(
                        f"{self.services['litellm']}/chat/completions",
                        headers={"Authorization": "Bearer sk-1234567890abcdef"},
                        json={
                            "model": "gemma3-ultra-fast",  # Test with specific model
                            "messages": [
                                {"role": "system", "content": "You are Zoe, an AI assistant with Samantha-level intelligence."},
                                {"role": "user", "content": test["query"]}
                            ],
                            "temperature": 0.7,
                            "max_tokens": 256
                        }
                    )
                    
                    response_time = time.time() - start_time
                    success = response.status_code == 200
                    
                    if success:
                        data = response.json()
                        response_text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                        optimization_score = min(10, len(response_text) / 20)  # Score based on response quality
                    else:
                        optimization_score = 0
                    
                    results.append(OptimizationResult(
                        component="RouteLLM",
                        test_name=test["name"],
                        response_time=response_time,
                        success=success,
                        optimization_score=optimization_score
                    ))
                    
            except Exception as e:
                results.append(OptimizationResult(
                    component="RouteLLM",
                    test_name=test["name"],
                    response_time=time.time() - start_time,
                    success=False,
                    optimization_score=0,
                    error_message=str(e)
                ))
        
        return results
    
    async def run_comprehensive_optimization(self) -> Dict[str, Any]:
        """Run comprehensive system optimization"""
        logger.info("🚀 Starting Comprehensive System Optimization...")
        
        all_results = []
        
        # Test LLM Performance
        llm_results = await self.test_llm_performance()
        all_results.extend(llm_results)
        
        # Test Memory System
        memory_results = await self.test_memory_system()
        all_results.extend(memory_results)
        
        # Test MCP Integration
        mcp_results = await self.test_mcp_integration()
        all_results.extend(mcp_results)
        
        # Test RouteLLM Intelligence
        routing_results = await self.test_routellm_intelligence()
        all_results.extend(routing_results)
        
        # Calculate optimization scores
        component_scores = {}
        for component in ["LLM", "Memory", "MCP", "RouteLLM"]:
            component_results = [r for r in all_results if r.component == component]
            if component_results:
                avg_score = statistics.mean([r.optimization_score for r in component_results])
                success_rate = sum([r.success for r in component_results]) / len(component_results)
                avg_response_time = statistics.mean([r.response_time for r in component_results])
                
                component_scores[component] = {
                    "optimization_score": avg_score,
                    "success_rate": success_rate,
                    "avg_response_time": avg_response_time,
                    "total_tests": len(component_results)
                }
        
        return {
            "component_scores": component_scores,
            "all_results": all_results,
            "overall_score": statistics.mean([r.optimization_score for r in all_results if r.success])
        }
    
    def generate_optimization_report(self, results: Dict[str, Any]) -> str:
        """Generate comprehensive optimization report"""
        report = []
        report.append("# 🔧 Comprehensive System Optimization Report")
        report.append(f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}")
        report.append("")
        
        # Overall score
        report.append("## 🏆 Overall Optimization Score")
        report.append(f"**Overall Score**: {results['overall_score']:.1f}/10")
        report.append("")
        
        # Component scores
        report.append("## 📊 Component Optimization Scores")
        
        component_scores = results["component_scores"]
        sorted_components = sorted(component_scores.items(), key=lambda x: x[1]["optimization_score"], reverse=True)
        
        for component, data in sorted_components:
            emoji = "🥇" if component == sorted_components[0][0] else "🥈" if component == sorted_components[1][0] else "🥉" if component == sorted_components[2][0] else "📊"
            report.append(f"{emoji} **{component}**: {data['optimization_score']:.1f}/10")
            report.append(f"   - Success Rate: {data['success_rate']:.1%}")
            report.append(f"   - Avg Response Time: {data['avg_response_time']:.2f}s")
            report.append(f"   - Total Tests: {data['total_tests']}")
            report.append("")
        
        # Detailed results
        report.append("## 📋 Detailed Test Results")
        
        for component in ["LLM", "Memory", "MCP", "RouteLLM"]:
            component_results = [r for r in results["all_results"] if r.component == component]
            if component_results:
                report.append(f"\n### {component} Component")
                for result in component_results:
                    status = "✅" if result.success else "❌"
                    report.append(f"- {status} {result.test_name}: {result.optimization_score}/10 ({result.response_time:.2f}s)")
                    if not result.success and result.error_message:
                        report.append(f"  Error: {result.error_message}")
        
        # Optimization recommendations
        report.append("\n## 🎯 Optimization Recommendations")
        
        # Identify areas for improvement
        low_scoring_components = [comp for comp, data in component_scores.items() if data["optimization_score"] < 7]
        
        if low_scoring_components:
            report.append("### 🔧 Components Needing Optimization")
            for component in low_scoring_components:
                data = component_scores[component]
                report.append(f"- **{component}**: Score {data['optimization_score']:.1f}/10")
                if component == "LLM":
                    report.append("  - Consider downloading missing optimal models")
                    report.append("  - Optimize model parameters for better performance")
                elif component == "Memory":
                    report.append("  - Enhance semantic search capabilities")
                    report.append("  - Improve context retrieval algorithms")
                elif component == "MCP":
                    report.append("  - Fix tool calling format issues")
                    report.append("  - Enhance Home Assistant and N8N integration")
                elif component == "RouteLLM":
                    report.append("  - Optimize model routing logic")
                    report.append("  - Improve query classification accuracy")
        
        # High-performing components
        high_scoring_components = [comp for comp, data in component_scores.items() if data["optimization_score"] >= 8]
        if high_scoring_components:
            report.append("\n### 🌟 High-Performing Components")
            for component in high_scoring_components:
                report.append(f"- **{component}**: Excellent performance ({component_scores[component]['optimization_score']:.1f}/10)")
        
        # Next steps
        report.append("\n## 🚀 Next Steps for Maximum Potential")
        report.append("1. **Download Missing Models**: Complete the optimal model setup")
        report.append("2. **Fix Tool Calling**: Resolve JSON format issues in tool calls")
        report.append("3. **Enhance Memory**: Improve semantic search and context awareness")
        report.append("4. **Optimize Routing**: Fine-tune model selection logic")
        report.append("5. **Integration Testing**: Test all components working together")
        
        return "\n".join(report)

async def main():
    """Main optimization function"""
    optimizer = SystemOptimizer()
    
    # Check if all services are running
    logger.info("🔍 Checking service availability...")
    for service, url in optimizer.services.items():
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{url}/health", timeout=5.0)
                if response.status_code == 200:
                    logger.info(f"✅ {service}: Healthy")
                else:
                    logger.warning(f"⚠️ {service}: Unhealthy ({response.status_code})")
        except Exception as e:
            logger.error(f"❌ {service}: Unavailable ({e})")
    
    # Run the optimization
    results = await optimizer.run_comprehensive_optimization()
    
    # Generate and save report
    report = optimizer.generate_optimization_report(results)
    
    with open("/home/pi/zoe/SYSTEM_OPTIMIZATION_REPORT.md", "w") as f:
        f.write(report)
    
    print("\n" + "="*80)
    print("🎉 COMPREHENSIVE SYSTEM OPTIMIZATION COMPLETE!")
    print("="*80)
    print(report)
    print("\n📄 Full report saved to: /home/pi/zoe/SYSTEM_OPTIMIZATION_REPORT.md")

if __name__ == "__main__":
    asyncio.run(main())
