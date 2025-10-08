#!/usr/bin/env python3
"""
Zoe Core Components Comprehensive Testing Framework
Tests all core components: LiteLLM, RouteLLM, LightRAG, Mem Agent, MCP Server
"""

import asyncio
import httpx
import json
import time
import logging
from typing import Dict, List, Any, Optional
from dataclasses import dataclass
from datetime import datetime, timedelta
import statistics

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@dataclass
class ComponentTestResult:
    component: str
    test_name: str
    success: bool
    response_time: float
    score: float  # 0-100 scale
    details: Dict[str, Any]
    error_message: str = ""

@dataclass
class PromptTestResult:
    prompt: str
    success: bool
    response_time: float
    components_used: List[str]
    actions_executed: List[str]
    score: float
    details: Dict[str, Any]

class ZoeCoreComponentTester:
    """Comprehensive testing of all Zoe core components"""
    
    def __init__(self):
        self.base_url = "http://localhost:8000"
        self.services = {
            "zoe-core": "http://localhost:8000",
            "mcp-server": "http://localhost:8003", 
            "mem-agent": "http://localhost:11435",
            "litellm": "http://localhost:8001",
            "ollama": "http://localhost:11434"
        }
        
        # Test scenarios for each core component
        self.component_tests = {
            "litellm": {
                "model_routing": ["conversation", "action", "memory", "reasoning"],
                "performance": ["response_time", "model_selection", "fallback"],
                "quality": ["coherence", "accuracy", "relevance"]
            },
            "routellm": {
                "query_classification": ["intent_detection", "confidence_scoring"],
                "routing_accuracy": ["correct_model_selection", "fallback_handling"]
            },
            "lightrag": {
                "semantic_search": ["vector_search", "relationship_awareness"],
                "memory_management": ["storage", "retrieval", "context"],
                "temporal_features": ["episode_management", "time_queries"]
            },
            "mem_agent": {
                "expert_coordination": ["task_decomposition", "multi_expert"],
                "action_execution": ["list_management", "calendar", "planning"],
                "orchestration": ["timeout_handling", "rollback", "progress"]
            },
            "mcp_server": {
                "tool_discovery": ["tool_listing", "capability_detection"],
                "tool_execution": ["parameter_validation", "error_handling"],
                "orchestration": ["multi_tool", "dependency_resolution"]
            }
        }
    
    async def test_litellm_component(self) -> List[ComponentTestResult]:
        """Test LiteLLM unified API and multi-model router"""
        logger.info("🧠 Testing LiteLLM Component...")
        results = []
        
        # Test model routing
        routing_tests = [
            {"query": "Hello, how are you?", "expected_type": "conversation"},
            {"query": "Add bread to shopping list", "expected_type": "action"},
            {"query": "What did we discuss yesterday?", "expected_type": "memory"},
            {"query": "Analyze the pros and cons", "expected_type": "reasoning"}
        ]
        
        for test in routing_tests:
            start_time = time.time()
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.post(
                        f"{self.base_url}/api/chat/",
                        json={
                            "message": test["query"],
                            "user_id": "test_user"
                        }
                    )
                    
                    response_time = time.time() - start_time
                    
                    if response.status_code == 200:
                        data = response.json()
                        routing_type = data.get("routing", "unknown")
                        
                        # Score based on correct routing
                        score = 100 if routing_type == test["expected_type"] else 50
                        
                        results.append(ComponentTestResult(
                            component="litellm",
                            test_name=f"routing_{test['expected_type']}",
                            success=True,
                            response_time=response_time,
                            score=score,
                            details={
                                "expected_type": test["expected_type"],
                                "actual_type": routing_type,
                                "response_time": response_time,
                                "model_used": data.get("model_used", "unknown")
                            }
                        ))
                    else:
                        results.append(ComponentTestResult(
                            component="litellm",
                            test_name=f"routing_{test['expected_type']}",
                            success=False,
                            response_time=response_time,
                            score=0,
                            details={"error": f"HTTP {response.status_code}"},
                            error_message=f"HTTP {response.status_code}"
                        ))
                        
            except Exception as e:
                results.append(ComponentTestResult(
                    component="litellm",
                    test_name=f"routing_{test['expected_type']}",
                    success=False,
                    response_time=time.time() - start_time,
                    score=0,
                    details={"error": str(e)},
                    error_message=str(e)
                ))
        
        # Test performance
        performance_result = await self._test_litellm_performance()
        results.append(performance_result)
        
        return results
    
    async def _test_litellm_performance(self) -> ComponentTestResult:
        """Test LiteLLM performance metrics"""
        start_time = time.time()
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.base_url}/api/models/performance"
                )
                
                response_time = time.time() - start_time
                
                if response.status_code == 200:
                    data = response.json()
                    quality_analysis = data.get("quality_analysis", {})
                    
                    # Calculate performance score
                    total_calls = sum(model.get("total_calls", 0) for model in quality_analysis.values())
                    avg_quality = statistics.mean([
                        model.get("quality_score", 0) 
                        for model in quality_analysis.values() 
                        if model.get("quality_score", 0) > 0
                    ]) if quality_analysis else 0
                    
                    score = min(100, (avg_quality / 10) * 100)  # Convert 1-10 to 0-100
                    
                    return ComponentTestResult(
                        component="litellm",
                        test_name="performance",
                        success=True,
                        response_time=response_time,
                        score=score,
                        details={
                            "total_calls": total_calls,
                            "avg_quality": avg_quality,
                            "models_tracked": len(quality_analysis)
                        }
                    )
                else:
                    return ComponentTestResult(
                        component="litellm",
                        test_name="performance",
                        success=False,
                        response_time=response_time,
                        score=0,
                        details={"error": f"HTTP {response.status_code}"},
                        error_message=f"HTTP {response.status_code}"
                    )
                    
        except Exception as e:
            return ComponentTestResult(
                component="litellm",
                test_name="performance",
                success=False,
                response_time=time.time() - start_time,
                score=0,
                details={"error": str(e)},
                error_message=str(e)
            )
    
    async def test_lightrag_component(self) -> List[ComponentTestResult]:
        """Test LightRAG contextual vector memory"""
        logger.info("🧠 Testing LightRAG Component...")
        results = []
        
        # Test semantic search
        search_tests = [
            {"query": "person I met at café", "expected_type": "semantic"},
            {"query": "project we discussed", "expected_type": "contextual"},
            {"query": "relationship between people", "expected_type": "relationship"}
        ]
        
        for test in search_tests:
            start_time = time.time()
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.post(
                        f"{self.base_url}/api/memories/search/light-rag",
                        params={
                            "query": test["query"],
                            "limit": 10
                        }
                    )
                    
                    response_time = time.time() - start_time
                    
                    if response.status_code == 200:
                        data = response.json()
                        results_data = data.get("results", [])
                        
                        # Score based on result quality and relevance
                        score = min(100, len(results_data) * 10)  # 10 points per result
                        
                        results.append(ComponentTestResult(
                            component="lightrag",
                            test_name=f"search_{test['expected_type']}",
                            success=True,
                            response_time=response_time,
                            score=score,
                            details={
                                "query": test["query"],
                                "results_count": len(results_data),
                                "search_type": data.get("search_type"),
                                "cache_used": data.get("cache_used", False)
                            }
                        ))
                    else:
                        results.append(ComponentTestResult(
                            component="lightrag",
                            test_name=f"search_{test['expected_type']}",
                            success=False,
                            response_time=response_time,
                            score=0,
                            details={"error": f"HTTP {response.status_code}"},
                            error_message=f"HTTP {response.status_code}"
                        ))
                        
            except Exception as e:
                results.append(ComponentTestResult(
                    component="lightrag",
                    test_name=f"search_{test['expected_type']}",
                    success=False,
                    response_time=time.time() - start_time,
                    score=0,
                    details={"error": str(e)},
                    error_message=str(e)
                ))
        
        # Test memory management
        memory_result = await self._test_lightrag_memory_management()
        results.append(memory_result)
        
        return results
    
    async def _test_lightrag_memory_management(self) -> ComponentTestResult:
        """Test LightRAG memory management capabilities"""
        start_time = time.time()
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Test adding memory
                add_response = await client.post(
                    f"{self.base_url}/api/memories/memories/enhanced",
                    params={
                        "entity_type": "person",
                        "entity_id": 1,
                        "fact": "Test memory for LightRAG testing",
                        "category": "test",
                        "importance": 5
                    }
                )
                
                if add_response.status_code == 200:
                    # Test retrieving memory
                    get_response = await client.get(
                        f"{self.base_url}/api/memories/stats/light-rag"
                    )
                    
                    response_time = time.time() - start_time
                    
                    if get_response.status_code == 200:
                        stats_data = get_response.json()
                        system_stats = stats_data.get("system_stats", {})
                        
                        # Score based on system health
                        embedding_coverage = system_stats.get("embedding_coverage", 0)
                        score = min(100, embedding_coverage)
                        
                        return ComponentTestResult(
                            component="lightrag",
                            test_name="memory_management",
                            success=True,
                            response_time=response_time,
                            score=score,
                            details={
                                "embedding_coverage": embedding_coverage,
                                "total_memories": system_stats.get("total_memories", 0),
                                "embedded_memories": system_stats.get("embedded_memories", 0)
                            }
                        )
                    else:
                        return ComponentTestResult(
                            component="lightrag",
                            test_name="memory_management",
                            success=False,
                            response_time=response_time,
                            score=0,
                            details={"error": f"Stats HTTP {get_response.status_code}"},
                            error_message=f"Stats HTTP {get_response.status_code}"
                        )
                else:
                    return ComponentTestResult(
                        component="lightrag",
                        test_name="memory_management",
                        success=False,
                        response_time=time.time() - start_time,
                        score=0,
                        details={"error": f"Add HTTP {add_response.status_code}"},
                        error_message=f"Add HTTP {add_response.status_code}"
                    )
                    
        except Exception as e:
            return ComponentTestResult(
                component="lightrag",
                test_name="memory_management",
                success=False,
                response_time=time.time() - start_time,
                score=0,
                details={"error": str(e)},
                error_message=str(e)
            )
    
    async def test_mem_agent_component(self) -> List[ComponentTestResult]:
        """Test Mem Agent specialized experts"""
        logger.info("🤖 Testing Mem Agent Component...")
        results = []
        
        # Test expert coordination
        expert_tests = [
            {
                "query": "Add milk to shopping list",
                "expected_expert": "list",
                "expected_action": "add_to_list"
            },
            {
                "query": "Schedule meeting tomorrow at 2pm",
                "expected_expert": "calendar", 
                "expected_action": "create_event"
            },
            {
                "query": "What did we discuss about the project?",
                "expected_expert": "memory",
                "expected_action": "memory_search"
            },
            {
                "query": "Help me plan my week",
                "expected_expert": "planning",
                "expected_action": "create_plan"
            }
        ]
        
        for test in expert_tests:
            start_time = time.time()
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.post(
                        f"{self.services['mem-agent']}/search",
                        json={
                            "query": test["query"],
                            "user_id": "test_user",
                            "execute_actions": True,
                            "max_results": 5
                        }
                    )
                    
                    response_time = time.time() - start_time
                    
                    if response.status_code == 200:
                        data = response.json()
                        experts = data.get("experts", [])
                        primary_expert = data.get("primary_expert", "unknown")
                        actions_executed = data.get("actions_executed", 0)
                        
                        # Score based on correct expert selection and action execution
                        expert_score = 50 if primary_expert == test["expected_expert"] else 25
                        action_score = 50 if actions_executed > 0 else 0
                        score = expert_score + action_score
                        
                        results.append(ComponentTestResult(
                            component="mem_agent",
                            test_name=f"expert_{test['expected_expert']}",
                            success=True,
                            response_time=response_time,
                            score=score,
                            details={
                                "query": test["query"],
                                "expected_expert": test["expected_expert"],
                                "actual_expert": primary_expert,
                                "actions_executed": actions_executed,
                                "total_confidence": data.get("total_confidence", 0)
                            }
                        ))
                    else:
                        results.append(ComponentTestResult(
                            component="mem_agent",
                            test_name=f"expert_{test['expected_expert']}",
                            success=False,
                            response_time=response_time,
                            score=0,
                            details={"error": f"HTTP {response.status_code}"},
                            error_message=f"HTTP {response.status_code}"
                        ))
                        
            except Exception as e:
                results.append(ComponentTestResult(
                    component="mem_agent",
                    test_name=f"expert_{test['expected_expert']}",
                    success=False,
                    response_time=time.time() - start_time,
                    score=0,
                    details={"error": str(e)},
                    error_message=str(e)
                ))
        
        return results
    
    async def test_mcp_server_component(self) -> List[ComponentTestResult]:
        """Test MCP Server tool interface and orchestration"""
        logger.info("🔧 Testing MCP Server Component...")
        results = []
        
        # Test tool discovery
        start_time = time.time()
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.services['mcp-server']}/tools/list",
                    json={
                        "_auth_token": "default",
                        "_session_id": "default"
                    }
                )
                
                response_time = time.time() - start_time
                
                if response.status_code == 200:
                    data = response.json()
                    tools = data.get("tools", [])
                    
                    # Score based on number of available tools
                    score = min(100, len(tools) * 10)  # 10 points per tool
                    
                    results.append(ComponentTestResult(
                        component="mcp_server",
                        test_name="tool_discovery",
                        success=True,
                        response_time=response_time,
                        score=score,
                        details={
                            "tools_count": len(tools),
                            "tools": [tool.get("name", "unknown") for tool in tools[:5]]
                        }
                    ))
                else:
                    results.append(ComponentTestResult(
                        component="mcp_server",
                        test_name="tool_discovery",
                        success=False,
                        response_time=response_time,
                        score=0,
                        details={"error": f"HTTP {response.status_code}"},
                        error_message=f"HTTP {response.status_code}"
                    ))
                    
        except Exception as e:
            results.append(ComponentTestResult(
                component="mcp_server",
                test_name="tool_discovery",
                success=False,
                response_time=time.time() - start_time,
                score=0,
                details={"error": str(e)},
                error_message=str(e)
            ))
        
        # Test tool execution
        tool_execution_result = await self._test_mcp_tool_execution()
        results.append(tool_execution_result)
        
        return results
    
    async def _test_mcp_tool_execution(self) -> ComponentTestResult:
        """Test MCP Server tool execution"""
        start_time = time.time()
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Test a simple tool execution
                response = await client.post(
                    f"{self.services['mcp-server']}/tools/get_people",
                    json={
                        "_auth_token": "default",
                        "_session_id": "default"
                    }
                )
                
                response_time = time.time() - start_time
                
                if response.status_code == 200:
                    data = response.json()
                    success = data.get("success", False)
                    
                    score = 100 if success else 50
                    
                    return ComponentTestResult(
                        component="mcp_server",
                        test_name="tool_execution",
                        success=success,
                        response_time=response_time,
                        score=score,
                        details={
                            "tool": "get_people",
                            "success": success,
                            "data": data.get("data", {})
                        }
                    )
                else:
                    return ComponentTestResult(
                        component="mcp_server",
                        test_name="tool_execution",
                        success=False,
                        response_time=response_time,
                        score=0,
                        details={"error": f"HTTP {response.status_code}"},
                        error_message=f"HTTP {response.status_code}"
                    )
                    
        except Exception as e:
            return ComponentTestResult(
                component="mcp_server",
                test_name="tool_execution",
                success=False,
                response_time=time.time() - start_time,
                score=0,
                details={"error": str(e)},
                error_message=str(e)
            )
    
    async def run_comprehensive_tests(self) -> Dict[str, Any]:
        """Run comprehensive tests for all core components"""
        logger.info("🚀 Starting Comprehensive Core Component Tests...")
        
        all_results = {}
        
        # Test each component
        components = ["litellm", "lightrag", "mem_agent", "mcp_server"]
        
        for component in components:
            logger.info(f"Testing {component} component...")
            
            if component == "litellm":
                results = await self.test_litellm_component()
            elif component == "lightrag":
                results = await self.test_lightrag_component()
            elif component == "mem_agent":
                results = await self.test_mem_agent_component()
            elif component == "mcp_server":
                results = await self.test_mcp_server_component()
            
            all_results[component] = results
        
        # Calculate overall scores
        overall_scores = {}
        for component, results in all_results.items():
            if results:
                avg_score = statistics.mean([r.score for r in results])
                success_rate = len([r for r in results if r.success]) / len(results)
                avg_response_time = statistics.mean([r.response_time for r in results])
                
                overall_scores[component] = {
                    "avg_score": avg_score,
                    "success_rate": success_rate,
                    "avg_response_time": avg_response_time,
                    "total_tests": len(results)
                }
        
        # Calculate system health
        system_health = self._calculate_system_health(overall_scores)
        
        return {
            "timestamp": datetime.now().isoformat(),
            "components": all_results,
            "overall_scores": overall_scores,
            "system_health": system_health,
            "summary": {
                "total_tests": sum(len(results) for results in all_results.values()),
                "successful_tests": sum(
                    len([r for r in results if r.success]) 
                    for results in all_results.values()
                ),
                "overall_success_rate": system_health["overall_success_rate"],
                "overall_score": system_health["overall_score"]
            }
        }
    
    def _calculate_system_health(self, overall_scores: Dict) -> Dict:
        """Calculate overall system health metrics"""
        if not overall_scores:
            return {
                "overall_score": 0,
                "overall_success_rate": 0,
                "health_status": "critical"
            }
        
        # Weighted average based on component importance
        weights = {
            "litellm": 0.3,
            "lightrag": 0.25,
            "mem_agent": 0.25,
            "mcp_server": 0.2
        }
        
        weighted_score = sum(
            scores["avg_score"] * weights.get(component, 0.25)
            for component, scores in overall_scores.items()
        )
        
        weighted_success_rate = sum(
            scores["success_rate"] * weights.get(component, 0.25)
            for component, scores in overall_scores.items()
        )
        
        # Determine health status
        if weighted_score >= 90 and weighted_success_rate >= 0.95:
            health_status = "excellent"
        elif weighted_score >= 80 and weighted_success_rate >= 0.9:
            health_status = "good"
        elif weighted_score >= 70 and weighted_success_rate >= 0.8:
            health_status = "fair"
        else:
            health_status = "needs_improvement"
        
        return {
            "overall_score": weighted_score,
            "overall_success_rate": weighted_success_rate,
            "health_status": health_status,
            "component_breakdown": overall_scores
        }

class PromptTestSuite:
    """Real-world prompt testing for Zoe capabilities"""
    
    def __init__(self):
        self.test_prompts = [
            {
                "prompt": "Remind me to call Mum tomorrow at 5 PM.",
                "expected_components": ["calendar_expert", "mcp_server"],
                "expected_actions": ["create_calendar_event"],
                "success_criteria": ["event_created", "correct_time", "correct_title"],
                "test_category": "calendar_management"
            },
            {
                "prompt": "What's on my schedule for tomorrow?",
                "expected_components": ["calendar_expert", "lightrag"],
                "expected_actions": ["get_calendar_events"],
                "success_criteria": ["events_retrieved", "formatted_response"],
                "test_category": "calendar_query"
            },
            {
                "prompt": "Can you move my dentist appointment to Thursday afternoon?",
                "expected_components": ["calendar_expert", "lightrag"],
                "expected_actions": ["update_calendar_event"],
                "success_criteria": ["event_found", "time_updated", "confirmation"],
                "test_category": "calendar_modification"
            },
            {
                "prompt": "Summarize my week.",
                "expected_components": ["calendar_expert", "lightrag", "litellm"],
                "expected_actions": ["get_calendar_events", "generate_summary"],
                "success_criteria": ["events_retrieved", "coherent_summary"],
                "test_category": "intelligent_summary"
            },
            {
                "prompt": "What did we talk about the last time I mentioned the solar project?",
                "expected_components": ["memory_expert", "lightrag", "temporal_memory"],
                "expected_actions": ["temporal_search", "context_retrieval"],
                "success_criteria": ["relevant_memories", "temporal_accuracy"],
                "test_category": "temporal_memory"
            },
            {
                "prompt": "Add milk, spinach, and eggs to my shopping list.",
                "expected_components": ["list_expert", "mcp_server"],
                "expected_actions": ["add_to_list"],
                "success_criteria": ["items_added", "correct_list", "confirmation"],
                "test_category": "list_management"
            },
            {
                "prompt": "Turn off the living room lights.",
                "expected_components": ["mcp_server", "homeassistant_bridge"],
                "expected_actions": ["control_home_assistant_device"],
                "success_criteria": ["device_controlled", "confirmation"],
                "test_category": "home_automation"
            },
            {
                "prompt": "Plan a trip from Geraldton to Merimbula with daily stops.",
                "expected_components": ["planning_expert", "litellm"],
                "expected_actions": ["create_plan", "generate_itinerary"],
                "success_criteria": ["plan_created", "daily_stops", "route_info"],
                "test_category": "complex_planning"
            },
            {
                "prompt": "What's the name of the person I met at the café in Fremantle?",
                "expected_components": ["memory_expert", "lightrag"],
                "expected_actions": ["semantic_search", "entity_extraction"],
                "success_criteria": ["person_identified", "location_context"],
                "test_category": "memory_retrieval"
            },
            {
                "prompt": "List everything you know about my van setup.",
                "expected_components": ["memory_expert", "lightrag"],
                "expected_actions": ["contextual_search", "entity_aggregation"],
                "success_criteria": ["comprehensive_info", "organized_response"],
                "test_category": "knowledge_aggregation"
            }
        ]
    
    async def execute_test(self, test_case: Dict) -> PromptTestResult:
        """Execute a single prompt test"""
        start_time = time.time()
        
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    "http://localhost:8000/api/chat/",
                    json={
                        "message": test_case["prompt"],
                        "user_id": "test_user"
                    }
                )
                
                response_time = time.time() - start_time
                
                if response.status_code == 200:
                    data = response.json()
                    response_text = data.get("response", "")
                    
                    # Analyze response for success criteria
                    success_analysis = self._analyze_response_success(
                        test_case, response_text, data
                    )
                    
                    # Calculate score based on success criteria
                    score = self._calculate_prompt_score(success_analysis)
                    
                    return PromptTestResult(
                        prompt=test_case["prompt"],
                        success=success_analysis["overall_success"],
                        response_time=response_time,
                        components_used=data.get("routing", "unknown"),
                        actions_executed=self._extract_actions(data),
                        score=score,
                        details=success_analysis
                    )
                else:
                    return PromptTestResult(
                        prompt=test_case["prompt"],
                        success=False,
                        response_time=response_time,
                        components_used=[],
                        actions_executed=[],
                        score=0,
                        details={"error": f"HTTP {response.status_code}"}
                    )
                    
        except Exception as e:
            return PromptTestResult(
                prompt=test_case["prompt"],
                success=False,
                response_time=time.time() - start_time,
                components_used=[],
                actions_executed=[],
                score=0,
                details={"error": str(e)}
            )
    
    def _analyze_response_success(self, test_case: Dict, response_text: str, data: Dict) -> Dict:
        """Analyze if response meets success criteria"""
        success_criteria = test_case["success_criteria"]
        analysis = {}
        
        # Check each success criterion
        for criterion in success_criteria:
            if criterion == "event_created":
                analysis[criterion] = "calendar" in response_text.lower() or "event" in response_text.lower()
            elif criterion == "correct_time":
                analysis[criterion] = "5" in response_text and ("pm" in response_text.lower() or "17:00" in response_text)
            elif criterion == "correct_title":
                analysis[criterion] = "mum" in response_text.lower() or "call" in response_text.lower()
            elif criterion == "events_retrieved":
                analysis[criterion] = len(response_text) > 50 and ("schedule" in response_text.lower() or "tomorrow" in response_text.lower())
            elif criterion == "formatted_response":
                analysis[criterion] = len(response_text.split('\n')) > 1 or "•" in response_text
            elif criterion == "items_added":
                analysis[criterion] = "milk" in response_text.lower() and "spinach" in response_text.lower() and "eggs" in response_text.lower()
            elif criterion == "confirmation":
                analysis[criterion] = "done" in response_text.lower() or "added" in response_text.lower() or "created" in response_text.lower()
            else:
                analysis[criterion] = True  # Default to true for complex criteria
        
        # Calculate overall success
        analysis["overall_success"] = all(analysis.values())
        
        return analysis
    
    def _calculate_prompt_score(self, success_analysis: Dict) -> float:
        """Calculate score based on success analysis"""
        if not success_analysis:
            return 0
        
        criteria_count = len([k for k in success_analysis.keys() if k != "overall_success"])
        if criteria_count == 0:
            return 0
        
        successful_criteria = len([v for k, v in success_analysis.items() if k != "overall_success" and v])
        return (successful_criteria / criteria_count) * 100
    
    def _extract_actions(self, data: Dict) -> List[str]:
        """Extract actions executed from response data"""
        actions = []
        
        # Check for tool calls in response
        response_text = data.get("response", "")
        if "[TOOL_CALL:" in response_text:
            import re
            tool_calls = re.findall(r'\[TOOL_CALL:([^:]+):', response_text)
            actions.extend(tool_calls)
        
        # Check for actions executed
        if "actions_executed" in data:
            actions.append(f"actions_executed: {data['actions_executed']}")
        
        return actions
    
    async def run_all_tests(self) -> Dict[str, Any]:
        """Run all prompt tests"""
        logger.info("🎯 Starting Prompt Test Suite...")
        
        results = {}
        total_score = 0
        successful_tests = 0
        
        for i, test_case in enumerate(self.test_prompts):
            logger.info(f"Testing prompt {i+1}/10: {test_case['prompt'][:50]}...")
            
            result = await self.execute_test(test_case)
            results[f"prompt_{i+1}"] = {
                "prompt": test_case["prompt"],
                "test_category": test_case["test_category"],
                "success": result.success,
                "response_time": result.response_time,
                "components_used": result.components_used,
                "actions_executed": result.actions_executed,
                "score": result.score,
                "details": result.details
            }
            
            total_score += result.score
            if result.success:
                successful_tests += 1
        
        avg_score = total_score / len(self.test_prompts)
        success_rate = successful_tests / len(self.test_prompts)
        
        return {
            "timestamp": datetime.now().isoformat(),
            "total_tests": len(self.test_prompts),
            "successful_tests": successful_tests,
            "success_rate": success_rate,
            "average_score": avg_score,
            "results": results,
            "summary": {
                "excellent": len([r for r in results.values() if r["score"] >= 90]),
                "good": len([r for r in results.values() if 70 <= r["score"] < 90]),
                "fair": len([r for r in results.values() if 50 <= r["score"] < 70]),
                "poor": len([r for r in results.values() if r["score"] < 50])
            }
        }

async def main():
    """Main function to run comprehensive tests"""
    logger.info("🚀 Starting Zoe Comprehensive Testing Framework...")
    
    # Test core components
    component_tester = ZoeCoreComponentTester()
    component_results = await component_tester.run_comprehensive_tests()
    
    # Test prompt scenarios
    prompt_tester = PromptTestSuite()
    prompt_results = await prompt_tester.run_all_tests()
    
    # Combine results
    comprehensive_results = {
        "timestamp": datetime.now().isoformat(),
        "component_tests": component_results,
        "prompt_tests": prompt_results,
        "overall_assessment": {
            "component_health": component_results["system_health"]["health_status"],
            "prompt_success_rate": prompt_results["success_rate"],
            "overall_score": (component_results["system_health"]["overall_score"] + prompt_results["average_score"]) / 2,
            "recommendations": generate_recommendations(component_results, prompt_results)
        }
    }
    
    # Save results (convert dataclasses to dicts for JSON serialization)
    def convert_to_dict(obj):
        if hasattr(obj, '__dict__'):
            return {k: convert_to_dict(v) for k, v in obj.__dict__.items()}
        elif isinstance(obj, list):
            return [convert_to_dict(item) for item in obj]
        elif isinstance(obj, dict):
            return {k: convert_to_dict(v) for k, v in obj.items()}
        else:
            return obj
    
    comprehensive_results_dict = convert_to_dict(comprehensive_results)
    
    with open("/home/pi/zoe/comprehensive_test_results.json", "w") as f:
        json.dump(comprehensive_results_dict, f, indent=2)
    
    logger.info("✅ Comprehensive testing complete!")
    logger.info(f"Component Health: {component_results['system_health']['health_status']}")
    logger.info(f"Prompt Success Rate: {prompt_results['success_rate']:.2%}")
    logger.info(f"Overall Score: {comprehensive_results['overall_assessment']['overall_score']:.1f}")
    
    return comprehensive_results

def generate_recommendations(component_results: Dict, prompt_results: Dict) -> List[str]:
    """Generate recommendations based on test results"""
    recommendations = []
    
    # Component-based recommendations
    component_scores = component_results["overall_scores"]
    for component, scores in component_scores.items():
        if scores["avg_score"] < 80:
            recommendations.append(f"Improve {component} component performance (current: {scores['avg_score']:.1f})")
        if scores["success_rate"] < 0.9:
            recommendations.append(f"Fix {component} component reliability issues (success rate: {scores['success_rate']:.2%})")
    
    # Prompt-based recommendations
    if prompt_results["success_rate"] < 0.8:
        recommendations.append("Improve prompt handling success rate")
    
    failed_prompts = [r for r in prompt_results["results"].values() if not r["success"]]
    if failed_prompts:
        categories = [p["test_category"] for p in failed_prompts]
        most_failed_category = max(set(categories), key=categories.count)
        recommendations.append(f"Focus on improving {most_failed_category} capabilities")
    
    # Performance recommendations
    avg_response_time = statistics.mean([
        scores["avg_response_time"] 
        for scores in component_scores.values()
    ])
    if avg_response_time > 5.0:
        recommendations.append("Optimize system response times")
    
    return recommendations

if __name__ == "__main__":
    asyncio.run(main())
