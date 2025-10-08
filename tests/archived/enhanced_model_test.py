#!/usr/bin/env python3
"""
Enhanced Model Test with Better Tool Calling Analysis
"""

import asyncio
import httpx
import json
import time
import logging
import re

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def test_model_enhanced(model_name: str, test_scenarios: list) -> dict:
    """Enhanced test of a single model with multiple scenarios"""
    logger.info(f"🧪 Testing {model_name} with {len(test_scenarios)} scenarios...")
    
    results = []
    
    for i, scenario in enumerate(test_scenarios):
        logger.info(f"  📝 Scenario {i+1}: {scenario['name']}")
        
        start_time = time.time()
        
        try:
            async with httpx.AsyncClient(timeout=45.0) as client:
                response = await client.post(
                    "http://localhost:11434/api/generate",
                    json={
                        "model": model_name,
                        "prompt": scenario["prompt"],
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
                    results.append({
                        "scenario": scenario["name"],
                        "success": False,
                        "response_time": time.time() - start_time,
                        "error": f"HTTP {response.status_code}"
                    })
                    continue
                
                data = response.json()
                response_text = data.get("response", "")
                response_time = time.time() - start_time
                
                # Enhanced analysis
                tool_call_found = analyze_tool_calling(response_text, scenario)
                quality_score = analyze_response_quality(response_text, scenario)
                warmth_score = analyze_warmth(response_text)
                conciseness_score = analyze_conciseness(response_text)
                
                results.append({
                    "scenario": scenario["name"],
                    "success": True,
                    "response_time": response_time,
                    "response_length": len(response_text),
                    "tool_call_found": tool_call_found,
                    "quality_score": quality_score,
                    "warmth_score": warmth_score,
                    "conciseness_score": conciseness_score,
                    "response_preview": response_text[:150] + "..." if len(response_text) > 150 else response_text
                })
                
                logger.info(f"    ✅ {response_time:.2f}s, Quality: {quality_score}/10, "
                           f"Tool: {'✓' if tool_call_found else '✗'}, "
                           f"Warmth: {warmth_score}/10")
                
        except Exception as e:
            results.append({
                "scenario": scenario["name"],
                "success": False,
                "response_time": time.time() - start_time,
                "error": str(e)
            })
            logger.error(f"    ❌ Error: {e}")
        
        # Small delay between tests
        await asyncio.sleep(1)
    
    return {
        "model": model_name,
        "results": results,
        "summary": calculate_summary(results)
    }

def analyze_tool_calling(response: str, scenario: dict) -> bool:
    """Enhanced tool calling analysis"""
    if not scenario.get("expected_tool_call", False):
        return True  # No tool call expected
    
    # Check for proper tool call pattern
    tool_call_pattern = r'\[TOOL_CALL:([^:]+):(\{[^}]+\})\]'
    matches = re.findall(tool_call_pattern, response)
    
    if matches:
        return True
    
    # Check for partial tool calls (common issue)
    if "[TOOL_CALL:" in response:
        logger.warning(f"    ⚠️  Partial tool call found: {response}")
        return False
    
    # Check for action confirmation without tool call
    action_words = ["added", "created", "scheduled", "turned on", "turned off", "activated"]
    if any(word in response.lower() for word in action_words):
        logger.warning(f"    ⚠️  Action mentioned without tool call: {response[:50]}...")
        return False
    
    return False

def analyze_response_quality(response: str, scenario: dict) -> int:
    """Analyze response quality on 1-10 scale"""
    score = 5  # Base score
    
    # Length check
    if len(response) < 10:
        score -= 3
    elif len(response) > 500:
        score -= 1
    
    # Relevance check based on scenario type
    if scenario["category"] == "action":
        if any(word in response.lower() for word in ["added", "done", "completed", "success"]):
            score += 2
        if "[TOOL_CALL:" in response:
            score += 2
    elif scenario["category"] == "conversation":
        if any(word in response.lower() for word in ["good", "great", "wonderful", "happy"]):
            score += 2
        if len(response) > 20 and len(response) < 100:
            score += 1
    elif scenario["category"] == "reasoning":
        if len(response) > 100 and any(word in response.lower() for word in ["consider", "think", "plan", "suggest"]):
            score += 2
    
    # Coherence check
    if "error" in response.lower() or "sorry" in response.lower() or "cannot" in response.lower():
        score -= 2
    
    # Completeness check
    if response.endswith("...") or len(response.split()) < 3:
        score -= 1
    
    return max(1, min(10, score))

def analyze_warmth(response: str) -> int:
    """Analyze warmth on 1-10 scale"""
    score = 5  # Base score
    
    warm_words = ["great", "wonderful", "happy", "excited", "love", "amazing", "fantastic", "delighted", "pleased"]
    cold_words = ["error", "cannot", "unable", "sorry", "unfortunately", "failed", "problem"]
    
    response_lower = response.lower()
    
    for word in warm_words:
        if word in response_lower:
            score += 1
    
    for word in cold_words:
        if word in response_lower:
            score -= 1
    
    # Check for Samantha-like warmth
    if any(phrase in response_lower for phrase in ["i'm here", "happy to help", "glad to", "pleasure"]):
        score += 1
    
    return max(1, min(10, score))

def analyze_conciseness(response: str) -> int:
    """Analyze conciseness on 1-10 scale"""
    word_count = len(response.split())
    
    if word_count < 5:
        return 6  # Very concise
    elif word_count < 15:
        return 9  # Perfect conciseness
    elif word_count < 30:
        return 8  # Good conciseness
    elif word_count < 50:
        return 6  # Acceptable
    elif word_count < 100:
        return 4  # Getting verbose
    else:
        return 2  # Too verbose

def calculate_summary(results: list) -> dict:
    """Calculate summary statistics for a model"""
    successful_results = [r for r in results if r["success"]]
    
    if not successful_results:
        return {
            "success_rate": 0,
            "avg_response_time": 999,
            "avg_quality": 0,
            "avg_warmth": 0,
            "avg_conciseness": 0,
            "tool_success_rate": 0
        }
    
    return {
        "success_rate": len(successful_results) / len(results),
        "avg_response_time": sum(r["response_time"] for r in successful_results) / len(successful_results),
        "avg_quality": sum(r["quality_score"] for r in successful_results) / len(successful_results),
        "avg_warmth": sum(r["warmth_score"] for r in successful_results) / len(successful_results),
        "avg_conciseness": sum(r["conciseness_score"] for r in successful_results) / len(successful_results),
        "tool_success_rate": sum(r["tool_call_found"] for r in successful_results) / len(successful_results)
    }

async def main():
    """Enhanced test of available models"""
    
    # Available models
    available_models = [
        "gemma3:1b",
        "qwen2.5:1.5b", 
        "llama3.2:1b",
        "mistral:7b"
    ]
    
    # Test scenarios
    test_scenarios = [
        {
            "name": "Simple Action",
            "prompt": """You are Zoe, an AI assistant like Samantha from "Her" - warm but direct.

RULES:
- DIRECT ACTION: When user asks to add/do something → Use tools immediately
- CONVERSATION: When chatting → Be friendly
- CONCISE: Be brief but warm

AVAILABLE TOOLS:
• add_to_list: Add items to lists
• control_home_assistant_device: Control smart home devices

TOOL USAGE INSTRUCTIONS:
When you need to use a tool, respond with: [TOOL_CALL:tool_name:{"param1":"value1","param2":"value2"}]

User's message: Add bread to my shopping list
Zoe:""",
            "expected_tool_call": True,
            "category": "action"
        },
        {
            "name": "Casual Chat",
            "prompt": """You are Zoe, an AI assistant like Samantha from "Her" - warm but direct.

RULES:
- DIRECT ACTION: When user asks to add/do something → Use tools immediately
- CONVERSATION: When chatting → Be friendly
- CONCISE: Be brief but warm

User's message: How are you today?
Zoe:""",
            "expected_tool_call": False,
            "category": "conversation"
        },
        {
            "name": "Complex Reasoning",
            "prompt": """You are Zoe, an AI assistant like Samantha from "Her" - warm but direct.

RULES:
- DIRECT ACTION: When user asks to add/do something → Use tools immediately
- CONVERSATION: When chatting → Be friendly
- CONCISE: Be brief but warm

User's message: I need to plan a birthday party for my 8-year-old daughter. What should I consider?
Zoe:""",
            "expected_tool_call": False,
            "category": "reasoning"
        },
        {
            "name": "Smart Home Control",
            "prompt": """You are Zoe, an AI assistant like Samantha from "Her" - warm but direct.

RULES:
- DIRECT ACTION: When user asks to add/do something → Use tools immediately
- CONVERSATION: When chatting → Be friendly
- CONCISE: Be brief but warm

AVAILABLE TOOLS:
• control_home_assistant_device: Control smart home devices

TOOL USAGE INSTRUCTIONS:
When you need to use a tool, respond with: [TOOL_CALL:tool_name:{"param1":"value1","param2":"value2"}]

User's message: Turn on the living room light
Zoe:""",
            "expected_tool_call": True,
            "category": "action"
        }
    ]
    
    logger.info("🚀 Starting enhanced model test...")
    
    all_results = []
    
    for model in available_models:
        result = await test_model_enhanced(model, test_scenarios)
        all_results.append(result)
        
        summary = result["summary"]
        logger.info(f"\n📊 {model} Summary:")
        logger.info(f"   Success Rate: {summary['success_rate']:.1%}")
        logger.info(f"   Avg Response Time: {summary['avg_response_time']:.2f}s")
        logger.info(f"   Avg Quality: {summary['avg_quality']:.1f}/10")
        logger.info(f"   Avg Warmth: {summary['avg_warmth']:.1f}/10")
        logger.info(f"   Avg Conciseness: {summary['avg_conciseness']:.1f}/10")
        logger.info(f"   Tool Success Rate: {summary['tool_success_rate']:.1%}")
    
    # Overall comparison
    logger.info(f"\n🏆 Overall Comparison:")
    
    # Best performers
    best_speed = min(all_results, key=lambda x: x["summary"]["avg_response_time"])
    best_quality = max(all_results, key=lambda x: x["summary"]["avg_quality"])
    best_tools = max(all_results, key=lambda x: x["summary"]["tool_success_rate"])
    best_warmth = max(all_results, key=lambda x: x["summary"]["avg_warmth"])
    
    logger.info(f"   Fastest: {best_speed['model']} ({best_speed['summary']['avg_response_time']:.2f}s)")
    logger.info(f"   Best Quality: {best_quality['model']} ({best_quality['summary']['avg_quality']:.1f}/10)")
    logger.info(f"   Best Tool Calling: {best_tools['model']} ({best_tools['summary']['tool_success_rate']:.1%})")
    logger.info(f"   Warmest: {best_warmth['model']} ({best_warmth['summary']['avg_warmth']:.1f}/10)")
    
    # Save detailed results
    with open("/home/pi/zoe/enhanced_test_results.json", "w") as f:
        json.dump(all_results, f, indent=2)
    
    logger.info(f"\n📄 Detailed results saved to: /home/pi/zoe/enhanced_test_results.json")

if __name__ == "__main__":
    asyncio.run(main())

