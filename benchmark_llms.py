#!/usr/bin/env python3
"""
LLM Benchmarking Script for Zoe's Tool Calling Performance
Tests different models for response quality and speed
"""

import asyncio
import httpx
import time
import json
from typing import Dict, List, Tuple

class LLMBenchmark:
    def __init__(self):
        self.models = [
            "llama3.2:1b",    # Current model - smallest/fastest
            "llama3.2:3b",    # Larger Llama - better quality
            "qwen2.5:3b",     # Qwen - good balance
            "gemma:2b",        # Google Gemma - efficient
            "phi3:mini",       # Microsoft Phi - compact
            "mistral:latest",  # Mistral - high quality
            "codellama:7b"     # Code-focused - largest
        ]
        
        self.test_prompts = [
            {
                "message": "Add bread to shopping list",
                "expected_tool": "add_to_list",
                "type": "direct_action"
            },
            {
                "message": "Turn on the living room light",
                "expected_tool": "control_home_assistant_device", 
                "type": "direct_action"
            },
            {
                "message": "What tools do you have available?",
                "expected_tool": None,
                "type": "information"
            },
            {
                "message": "How are you today?",
                "expected_tool": None,
                "type": "conversation"
            },
            {
                "message": "Send a message to the Matrix room",
                "expected_tool": "send_matrix_message",
                "type": "direct_action"
            }
        ]
    
    async def test_model(self, model: str, prompt: Dict) -> Dict:
        """Test a single model with a single prompt"""
        start_time = time.time()
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    "http://localhost:11434/api/generate",
                    json={
                        "model": model,
                        "prompt": f"""You are Zoe, an AI assistant like Samantha from "Her" - warm but direct.

RULES:
- DIRECT ACTION: When user asks to add/do something → Use tools immediately
- CONVERSATION: When chatting → Be friendly
- CONCISE: Be brief but warm

AVAILABLE TOOLS:
• add_to_list: Add an item to a user's todo list
• control_home_assistant_device: Control a Home Assistant device (turn on/off, set brightness, etc.)
• send_matrix_message: Send a message to a Matrix room
• get_home_assistant_devices: Get all devices from Home Assistant (lights, switches, sensors)
• create_calendar_event: Create a new calendar event
• search_memories: Search through Zoe's memory system for people, projects, facts, and collections

TOOL USAGE INSTRUCTIONS:
When you need to use a tool, respond with: [TOOL_CALL:tool_name:{{"param1":"value1","param2":"value2"}}]
After tool execution, confirm the action to the user.

EXAMPLES:
- "Add bread to shopping list" → [TOOL_CALL:add_to_list:{{"list_name":"shopping","task_text":"bread","priority":"medium"}}] → "Added bread to your shopping list"
- "Turn on living room light" → [TOOL_CALL:control_home_assistant_device:{{"entity_id":"light.living_room","action":"turn_on"}}] → "Turned on the living room light"
- "Send message to Matrix" → [TOOL_CALL:send_matrix_message:{{"room_id":"!room:matrix.org","message":"Hello!"}}] → "Message sent to Matrix room"

User's message: {prompt['message']}
Zoe:""",
                        "stream": False,
                        "options": {
                            "temperature": 0.5,
                            "top_p": 0.8,
                            "num_predict": 64,
                            "num_ctx": 512,
                            "repeat_penalty": 1.1,
                            "stop": ["\n\n", "User:", "Human:"]
                        }
                    }
                )
                
                response_time = time.time() - start_time
                
                if response.status_code == 200:
                    data = response.json()
                    response_text = data.get("response", "")
                    
                    # Analyze response quality
                    quality_score = self.analyze_response_quality(response_text, prompt)
                    
                    return {
                        "model": model,
                        "prompt": prompt["message"],
                        "response_time": response_time,
                        "response_text": response_text,
                        "quality_score": quality_score,
                        "success": True,
                        "error": None
                    }
                else:
                    return {
                        "model": model,
                        "prompt": prompt["message"],
                        "response_time": response_time,
                        "response_text": "",
                        "quality_score": 0,
                        "success": False,
                        "error": f"HTTP {response.status_code}"
                    }
                    
        except Exception as e:
            response_time = time.time() - start_time
            return {
                "model": model,
                "prompt": prompt["message"],
                "response_time": response_time,
                "response_text": "",
                "quality_score": 0,
                "success": False,
                "error": str(e)
            }
    
    def analyze_response_quality(self, response_text: str, prompt: Dict) -> float:
        """Analyze response quality based on prompt type and expected behavior"""
        score = 0.0
        
        # Check for tool calls in direct action prompts
        if prompt["type"] == "direct_action" and prompt["expected_tool"]:
            if f"[TOOL_CALL:{prompt['expected_tool']}" in response_text:
                score += 50  # Major points for correct tool usage
            elif "TOOL_CALL" in response_text:
                score += 25  # Partial points for any tool call
        
        # Check response length (should be concise)
        if len(response_text) < 100:
            score += 20  # Concise responses get points
        elif len(response_text) < 200:
            score += 10  # Medium length OK
        
        # Check for Zoe-like personality
        if any(word in response_text.lower() for word in ["added", "done", "completed", "sure", "okay"]):
            score += 15  # Direct action confirmation
        
        # Check for warmth in conversation
        if prompt["type"] == "conversation" and any(word in response_text.lower() for word in ["good", "great", "wonderful", "happy", "excited"]):
            score += 20  # Warm conversational tone
        
        # Check for information accuracy
        if prompt["type"] == "information" and "tool" in response_text.lower():
            score += 25  # Mentions tools when asked
        
        # Penalty for generic responses
        if "moment of clarity" in response_text.lower() or "try that again" in response_text.lower():
            score -= 30  # Penalty for generic fallback responses
        
        return min(100, max(0, score))  # Clamp between 0-100
    
    async def benchmark_all_models(self) -> Dict:
        """Benchmark all models with all test prompts"""
        print("🚀 Starting LLM Benchmark for Zoe's Tool Calling")
        print("=" * 60)
        
        results = {}
        
        for model in self.models:
            print(f"\n🧪 Testing Model: {model}")
            print("-" * 40)
            
            model_results = []
            
            for i, prompt in enumerate(self.test_prompts):
                print(f"  Test {i+1}/5: {prompt['message'][:30]}...")
                
                result = await self.test_model(model, prompt)
                model_results.append(result)
                
                if result["success"]:
                    print(f"    ✅ {result['response_time']:.2f}s | Quality: {result['quality_score']}/100")
                else:
                    print(f"    ❌ Failed: {result['error']}")
            
            # Calculate model averages
            successful_results = [r for r in model_results if r["success"]]
            if successful_results:
                avg_time = sum(r["response_time"] for r in successful_results) / len(successful_results)
                avg_quality = sum(r["quality_score"] for r in successful_results) / len(successful_results)
                success_rate = len(successful_results) / len(model_results) * 100
                
                results[model] = {
                    "avg_response_time": avg_time,
                    "avg_quality_score": avg_quality,
                    "success_rate": success_rate,
                    "results": model_results
                }
                
                print(f"  📊 Avg Time: {avg_time:.2f}s | Avg Quality: {avg_quality:.1f}/100 | Success: {success_rate:.1f}%")
            else:
                results[model] = {
                    "avg_response_time": float('inf'),
                    "avg_quality_score": 0,
                    "success_rate": 0,
                    "results": model_results
                }
                print(f"  ❌ Model failed all tests")
        
        return results
    
    def generate_report(self, results: Dict) -> str:
        """Generate a comprehensive benchmark report"""
        print("\n" + "=" * 60)
        print("📊 LLM BENCHMARK RESULTS")
        print("=" * 60)
        
        # Sort models by combined score (quality + speed)
        model_scores = []
        for model, data in results.items():
            if data["success_rate"] > 0:
                # Combined score: quality (70%) + speed (30%)
                speed_score = max(0, 100 - (data["avg_response_time"] * 10))  # Penalty for slow responses
                combined_score = (data["avg_quality_score"] * 0.7) + (speed_score * 0.3)
                model_scores.append((model, combined_score, data))
        
        model_scores.sort(key=lambda x: x[1], reverse=True)
        
        print("\n🏆 RANKING (Quality + Speed Combined):")
        print("-" * 50)
        
        for i, (model, score, data) in enumerate(model_scores):
            print(f"{i+1}. {model}")
            print(f"   Combined Score: {score:.1f}/100")
            print(f"   Avg Response Time: {data['avg_response_time']:.2f}s")
            print(f"   Avg Quality Score: {data['avg_quality_score']:.1f}/100")
            print(f"   Success Rate: {data['success_rate']:.1f}%")
            print()
        
        # Recommendations
        print("🎯 RECOMMENDATIONS:")
        print("-" * 30)
        
        if model_scores:
            best_model = model_scores[0][0]
            print(f"🥇 BEST OVERALL: {best_model}")
            print(f"   - Best balance of quality and speed")
            print(f"   - Recommended for Zoe's tool calling")
            
            # Find fastest model
            fastest_model = min(model_scores, key=lambda x: x[2]["avg_response_time"])
            if fastest_model[0] != best_model:
                print(f"⚡ FASTEST: {fastest_model[0]}")
                print(f"   - Best for speed-critical applications")
            
            # Find highest quality model
            quality_model = max(model_scores, key=lambda x: x[2]["avg_quality_score"])
            if quality_model[0] != best_model:
                print(f"🎨 HIGHEST QUALITY: {quality_model[0]}")
                print(f"   - Best for complex reasoning tasks")
        
        return best_model if model_scores else None

async def main():
    """Run the complete LLM benchmark"""
    benchmark = LLMBenchmark()
    
    try:
        results = await benchmark.benchmark_all_models()
        best_model = benchmark.generate_report(results)
        
        if best_model:
            print(f"\n🎉 RECOMMENDATION: Use '{best_model}' for Zoe's tool calling!")
        else:
            print("\n❌ No models performed well enough to recommend")
            
    except KeyboardInterrupt:
        print("\n⏹️  Benchmark interrupted by user")
    except Exception as e:
        print(f"\n❌ Benchmark failed: {e}")

if __name__ == "__main__":
    asyncio.run(main())
