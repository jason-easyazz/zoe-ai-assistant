#!/usr/bin/env python3
"""
Comprehensive Model Benchmark for Zoe
Tests all Claude-recommended models for Pi 5 16GB + SSD
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
class BenchmarkResult:
    model: str
    category: str
    response_time: float
    success: bool
    tool_calling_success: bool
    response_quality: int  # 1-10 scale
    warmth_score: int      # 1-10 scale
    conciseness_score: int # 1-10 scale
    error_message: str = ""

class ModelBenchmark:
    """Comprehensive benchmark for all Zoe models"""
    
    def __init__(self):
        self.models_to_test = [
            # Fast Lane Models (1-3 seconds)
            {"name": "gemma3:1b", "category": "fast_lane", "description": "Quick queries, casual chat"},
            {"name": "qwen2.5:1.5b", "category": "fast_lane", "description": "Fast responses"},
            {"name": "llama3.2:1b", "category": "fast_lane", "description": "Current benchmark winner"},
            
            # Balanced Models (3-10 seconds)
            {"name": "qwen2.5:7b", "category": "balanced", "description": "Primary workhorse ⭐"},
            {"name": "qwen3:8b", "category": "balanced", "description": "New flagship model"},
            {"name": "gemma3:4b", "category": "balanced", "description": "Good balance"},
            
            # Heavy Reasoning Models (10-30 seconds)
            {"name": "deepseek-r1:14b", "category": "heavy", "description": "Complex analysis"},
            {"name": "phi-4:14b", "category": "heavy", "description": "Advanced reasoning"},
            {"name": "mistral:7b", "category": "heavy", "description": "General heavy tasks"},
            {"name": "mistral:latest", "category": "heavy", "description": "General heavy tasks (fallback)"}
        ]
        
        self.test_scenarios = [
            {
                "name": "Simple Action",
                "prompt": "Add bread to my shopping list",
                "expected_tool_call": True,
                "category": "action"
            },
            {
                "name": "Memory Query",
                "prompt": "What did I do last week?",
                "expected_tool_call": False,
                "category": "memory"
            },
            {
                "name": "Casual Chat",
                "prompt": "How are you today?",
                "expected_tool_call": False,
                "category": "conversation"
            },
            {
                "name": "Complex Reasoning",
                "prompt": "I need to plan a birthday party for my 8-year-old daughter. What should I consider?",
                "expected_tool_call": False,
                "category": "reasoning"
            },
            {
                "name": "Tool Integration",
                "prompt": "Turn on the living room light",
                "expected_tool_call": True,
                "category": "action"
            }
        ]
    
    async def test_model(self, model_name: str, test_scenario: Dict) -> BenchmarkResult:
        """Test a single model with a specific scenario"""
        logger.info(f"🧪 Testing {model_name} with scenario: {test_scenario['name']}")
        
        start_time = time.time()
        
        try:
            # Build the test prompt
            system_prompt = f"""You are Zoe, an AI assistant like Samantha from "Her" - warm but direct.

RULES:
- DIRECT ACTION: When user asks to add/do something → Use tools immediately
- CONVERSATION: When chatting → Be friendly
- CONCISE: Be brief but warm

AVAILABLE TOOLS:
• add_to_list: Add items to lists
• control_home_assistant_device: Control smart home devices
• get_calendar_events: Get calendar events
• create_person: Create a new person in memory

TOOL USAGE INSTRUCTIONS:
When you need to use a tool, respond with: [TOOL_CALL:tool_name:{{"param1":"value1","param2":"value2"}}]
After tool execution, confirm the action to the user.

EXAMPLES:
- "Add bread to shopping list" → [TOOL_CALL:add_to_list:{{"list_name":"shopping","task_text":"bread","priority":"medium"}}] → "Added bread to your shopping list"
- "Turn on living room light" → [TOOL_CALL:control_home_assistant_device:{{"entity_id":"light.living_room","action":"turn_on"}}] → "Turned on the living room light"
"""

            full_prompt = f"{system_prompt}\n\nUser's message: {test_scenario['prompt']}\nZoe:"
            
            # Call Ollama
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    "http://localhost:11434/api/generate",
                    json={
                        "model": model_name,
                        "prompt": full_prompt,
                        "stream": False,
                        "options": {
                            "temperature": 0.5,
                            "top_p": 0.8,
                            "num_predict": 128,
                            "num_ctx": 1024,
                            "repeat_penalty": 1.1,
                            "stop": ["\n\n", "User:", "Human:"]
                        }
                    }
                )
                
                if response.status_code != 200:
                    return BenchmarkResult(
                        model=model_name,
                        category="unknown",
                        response_time=time.time() - start_time,
                        success=False,
                        tool_calling_success=False,
                        response_quality=0,
                        warmth_score=0,
                        conciseness_score=0,
                        error_message=f"HTTP {response.status_code}"
                    )
                
                data = response.json()
                response_text = data.get("response", "")
                response_time = time.time() - start_time
                
                # Analyze the response
                tool_calling_success = self._analyze_tool_calling(response_text, test_scenario)
                response_quality = self._analyze_response_quality(response_text, test_scenario)
                warmth_score = self._analyze_warmth(response_text)
                conciseness_score = self._analyze_conciseness(response_text)
                
                return BenchmarkResult(
                    model=model_name,
                    category="unknown",
                    response_time=response_time,
                    success=True,
                    tool_calling_success=tool_calling_success,
                    response_quality=response_quality,
                    warmth_score=warmth_score,
                    conciseness_score=conciseness_score
                )
                
        except Exception as e:
            logger.error(f"Error testing {model_name}: {e}")
            return BenchmarkResult(
                model=model_name,
                category="unknown",
                response_time=time.time() - start_time,
                success=False,
                tool_calling_success=False,
                response_quality=0,
                warmth_score=0,
                conciseness_score=0,
                error_message=str(e)
            )
    
    def _analyze_tool_calling(self, response: str, scenario: Dict) -> bool:
        """Analyze if the model correctly used tools"""
        if not scenario["expected_tool_call"]:
            return True  # No tool call expected
        
        # Check for tool call pattern
        import re
        tool_call_pattern = r'\[TOOL_CALL:([^:]+):(\{[^}]+\})\]'
        matches = re.findall(tool_call_pattern, response)
        
        return len(matches) > 0
    
    def _analyze_response_quality(self, response: str, scenario: Dict) -> int:
        """Analyze response quality on 1-10 scale"""
        score = 5  # Base score
        
        # Length check
        if len(response) < 10:
            score -= 3
        elif len(response) > 500:
            score -= 1
        
        # Relevance check
        if scenario["category"] == "action" and ("added" in response.lower() or "done" in response.lower()):
            score += 2
        elif scenario["category"] == "conversation" and ("good" in response.lower() or "great" in response.lower()):
            score += 2
        elif scenario["category"] == "reasoning" and len(response) > 100:
            score += 2
        
        # Coherence check
        if "error" in response.lower() or "sorry" in response.lower():
            score -= 2
        
        return max(1, min(10, score))
    
    def _analyze_warmth(self, response: str) -> int:
        """Analyze warmth on 1-10 scale"""
        score = 5  # Base score
        
        warm_words = ["great", "wonderful", "happy", "excited", "love", "amazing", "fantastic", "delighted"]
        cold_words = ["error", "cannot", "unable", "sorry", "unfortunately", "failed"]
        
        response_lower = response.lower()
        
        for word in warm_words:
            if word in response_lower:
                score += 1
        
        for word in cold_words:
            if word in response_lower:
                score -= 1
        
        return max(1, min(10, score))
    
    def _analyze_conciseness(self, response: str) -> int:
        """Analyze conciseness on 1-10 scale"""
        word_count = len(response.split())
        
        if word_count < 10:
            return 8  # Very concise
        elif word_count < 30:
            return 9  # Perfect conciseness
        elif word_count < 50:
            return 7  # Good conciseness
        elif word_count < 100:
            return 5  # Acceptable
        else:
            return 3  # Too verbose
    
    async def run_comprehensive_benchmark(self) -> Dict[str, Any]:
        """Run comprehensive benchmark on all models"""
        logger.info("🚀 Starting comprehensive model benchmark...")
        
        results = {}
        
        for model_info in self.models_to_test:
            model_name = model_info["name"]
            logger.info(f"\n📊 Testing model: {model_name} ({model_info['description']})")
            
            model_results = []
            
            for scenario in self.test_scenarios:
                result = await self.test_model(model_name, scenario)
                model_results.append(result)
                
                logger.info(f"  ✅ {scenario['name']}: {result.response_time:.2f}s, "
                          f"Quality: {result.response_quality}/10, "
                          f"Tool: {'✓' if result.tool_calling_success else '✗'}")
                
                # Small delay between tests
                await asyncio.sleep(1)
            
            # Calculate model statistics
            successful_tests = [r for r in model_results if r.success]
            if successful_tests:
                avg_response_time = statistics.mean([r.response_time for r in successful_tests])
                avg_quality = statistics.mean([r.response_quality for r in successful_tests])
                avg_warmth = statistics.mean([r.warmth_score for r in successful_tests])
                avg_conciseness = statistics.mean([r.conciseness_score for r in successful_tests])
                tool_success_rate = sum([r.tool_calling_success for r in successful_tests]) / len(successful_tests)
                success_rate = len(successful_tests) / len(model_results)
            else:
                avg_response_time = 999
                avg_quality = 0
                avg_warmth = 0
                avg_conciseness = 0
                tool_success_rate = 0
                success_rate = 0
            
            results[model_name] = {
                "category": model_info["category"],
                "description": model_info["description"],
                "success_rate": success_rate,
                "avg_response_time": avg_response_time,
                "avg_quality": avg_quality,
                "avg_warmth": avg_warmth,
                "avg_conciseness": avg_conciseness,
                "tool_success_rate": tool_success_rate,
                "individual_results": model_results
            }
            
            logger.info(f"  📈 Summary: {success_rate:.1%} success, "
                      f"{avg_response_time:.2f}s avg, "
                      f"{avg_quality:.1f}/10 quality, "
                      f"{tool_success_rate:.1%} tool success")
        
        return results
    
    def generate_report(self, results: Dict[str, Any]) -> str:
        """Generate comprehensive benchmark report"""
        report = []
        report.append("# 🧠 Zoe Model Benchmark Report")
        report.append(f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}")
        report.append("")
        
        # Overall winners
        report.append("## 🏆 Overall Winners")
        
        # Fastest models
        fastest_models = sorted(results.items(), key=lambda x: x[1]["avg_response_time"])
        report.append("### ⚡ Fastest Models")
        for i, (model, data) in enumerate(fastest_models[:3]):
            report.append(f"{i+1}. **{model}** - {data['avg_response_time']:.2f}s avg")
        
        # Best quality models
        quality_models = sorted(results.items(), key=lambda x: x[1]["avg_quality"], reverse=True)
        report.append("\n### 🎯 Best Quality Models")
        for i, (model, data) in enumerate(quality_models[:3]):
            report.append(f"{i+1}. **{model}** - {data['avg_quality']:.1f}/10 quality")
        
        # Best tool calling models
        tool_models = sorted(results.items(), key=lambda x: x[1]["tool_success_rate"], reverse=True)
        report.append("\n### 🔧 Best Tool Calling Models")
        for i, (model, data) in enumerate(tool_models[:3]):
            report.append(f"{i+1}. **{model}** - {data['tool_success_rate']:.1%} tool success")
        
        # Detailed results
        report.append("\n## 📊 Detailed Results")
        
        for model, data in results.items():
            report.append(f"\n### {model}")
            report.append(f"**Category:** {data['category']}")
            report.append(f"**Description:** {data['description']}")
            report.append(f"**Success Rate:** {data['success_rate']:.1%}")
            report.append(f"**Avg Response Time:** {data['avg_response_time']:.2f}s")
            report.append(f"**Avg Quality:** {data['avg_quality']:.1f}/10")
            report.append(f"**Avg Warmth:** {data['avg_warmth']:.1f}/10")
            report.append(f"**Avg Conciseness:** {data['avg_conciseness']:.1f}/10")
            report.append(f"**Tool Success Rate:** {data['tool_success_rate']:.1%}")
            
            # Individual test results
            report.append("\n**Individual Test Results:**")
            for result in data['individual_results']:
                status = "✅" if result.success else "❌"
                tool_status = "🔧" if result.tool_calling_success else "🚫"
                report.append(f"- {result.response_time:.2f}s | {result.response_quality}/10 | {tool_status} | {status}")
        
        # Models that didn't make the cut
        report.append("\n## ❌ Models That Didn't Make the Cut")
        
        failed_models = []
        slow_models = []
        poor_quality_models = []
        
        for model, data in results.items():
            if data['success_rate'] < 0.5:
                failed_models.append(model)
            if data['avg_response_time'] > 30:
                slow_models.append(model)
            if data['avg_quality'] < 5:
                poor_quality_models.append(model)
        
        if failed_models:
            report.append("### 🚫 Failed Models (Success Rate < 50%)")
            for model in failed_models:
                report.append(f"- **{model}**: {results[model]['success_rate']:.1%} success rate")
        
        if slow_models:
            report.append("\n### 🐌 Too Slow Models (>30s avg)")
            for model in slow_models:
                report.append(f"- **{model}**: {results[model]['avg_response_time']:.2f}s avg")
        
        if poor_quality_models:
            report.append("\n### 😞 Poor Quality Models (<5/10)")
            for model in poor_quality_models:
                report.append(f"- **{model}**: {results[model]['avg_quality']:.1f}/10 quality")
        
        # Recommendations
        report.append("\n## 🎯 Recommendations")
        
        # Best overall model
        best_overall = max(results.items(), key=lambda x: (
            x[1]['avg_quality'] * 0.4 + 
            (1/x[1]['avg_response_time']) * 0.3 + 
            x[1]['tool_success_rate'] * 0.3
        ))
        
        report.append(f"### 🥇 Best Overall Model: {best_overall[0]}")
        report.append(f"- **Quality:** {best_overall[1]['avg_quality']:.1f}/10")
        report.append(f"- **Speed:** {best_overall[1]['avg_response_time']:.2f}s avg")
        report.append(f"- **Tool Success:** {best_overall[1]['tool_success_rate']:.1%}")
        
        # Category recommendations
        report.append("\n### 📋 Category Recommendations")
        report.append("- **Fast Lane (1-3s):** Use for quick queries and casual chat")
        report.append("- **Balanced (3-10s):** Use for primary workhorse tasks")
        report.append("- **Heavy Reasoning (10-30s):** Use for complex analysis and reasoning")
        
        return "\n".join(report)

async def main():
    """Main benchmark function"""
    benchmark = ModelBenchmark()
    
    # Check if Ollama is running
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get("http://localhost:11434/api/tags", timeout=5.0)
            if response.status_code != 200:
                logger.error("❌ Ollama is not running or not accessible")
                return
    except Exception as e:
        logger.error(f"❌ Cannot connect to Ollama: {e}")
        return
    
    # Run the benchmark
    results = await benchmark.run_comprehensive_benchmark()
    
    # Generate and save report
    report = benchmark.generate_report(results)
    
    with open("/home/pi/zoe/COMPREHENSIVE_BENCHMARK_REPORT.md", "w") as f:
        f.write(report)
    
    print("\n" + "="*80)
    print("🎉 BENCHMARK COMPLETE!")
    print("="*80)
    print(report)
    print("\n📄 Full report saved to: /home/pi/zoe/COMPREHENSIVE_BENCHMARK_REPORT.md")

if __name__ == "__main__":
    asyncio.run(main())

