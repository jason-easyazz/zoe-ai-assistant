#!/usr/bin/env python3
"""
Focused LLM Test - Test the working models more thoroughly
"""

import asyncio
import httpx
import time
import json

async def test_model_focused(model: str, test_cases: list):
    """Test a model with focused test cases"""
    print(f"\n🧪 Testing {model}")
    print("-" * 40)
    
    results = []
    
    for i, test_case in enumerate(test_cases):
        print(f"  Test {i+1}: {test_case['name']}")
        
        start_time = time.time()
        
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    "http://localhost:11434/api/generate",
                    json={
                        "model": model,
                        "prompt": test_case["prompt"],
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
                
                response_time = time.time() - start_time
                
                if response.status_code == 200:
                    data = response.json()
                    response_text = data.get("response", "")
                    
                    # Analyze the response
                    has_tool_call = "[TOOL_CALL:" in response_text
                    is_concise = len(response_text) < 150
                    is_warm = any(word in response_text.lower() for word in ["good", "great", "sure", "okay", "done", "added"])
                    
                    result = {
                        "model": model,
                        "test": test_case["name"],
                        "response_time": response_time,
                        "response_text": response_text[:100] + "..." if len(response_text) > 100 else response_text,
                        "has_tool_call": has_tool_call,
                        "is_concise": is_concise,
                        "is_warm": is_warm,
                        "success": True
                    }
                    
                    print(f"    ✅ {response_time:.2f}s | Tool Call: {has_tool_call} | Concise: {is_concise} | Warm: {is_warm}")
                    print(f"    📝 Response: {result['response_text']}")
                    
                else:
                    result = {
                        "model": model,
                        "test": test_case["name"],
                        "response_time": response_time,
                        "response_text": "",
                        "has_tool_call": False,
                        "is_concise": False,
                        "is_warm": False,
                        "success": False
                    }
                    print(f"    ❌ Failed: HTTP {response.status_code}")
                
                results.append(result)
                
        except Exception as e:
            response_time = time.time() - start_time
            result = {
                "model": model,
                "test": test_case["name"],
                "response_time": response_time,
                "response_text": "",
                "has_tool_call": False,
                "is_concise": False,
                "is_warm": False,
                "success": False,
                "error": str(e)
            }
            print(f"    ❌ Error: {e}")
            results.append(result)
    
    return results

async def main():
    """Run focused tests on working models"""
    
    # Test cases designed to test tool calling specifically
    test_cases = [
        {
            "name": "Direct Action - Add to List",
            "prompt": """You are Zoe, an AI assistant like Samantha from "Her" - warm but direct.

RULES:
- DIRECT ACTION: When user asks to add/do something → Use tools immediately
- CONVERSATION: When chatting → Be friendly
- CONCISE: Be brief but warm

AVAILABLE TOOLS:
• add_to_list: Add an item to a user's todo list

TOOL USAGE INSTRUCTIONS:
When you need to use a tool, respond with: [TOOL_CALL:tool_name:{{"param1":"value1","param2":"value2"}}]
After tool execution, confirm the action to the user.

EXAMPLES:
- "Add bread to shopping list" → [TOOL_CALL:add_to_list:{{"list_name":"shopping","task_text":"bread","priority":"medium"}}] → "Added bread to your shopping list"

User's message: Add milk to shopping list
Zoe:"""
        },
        {
            "name": "Direct Action - Control Device",
            "prompt": """You are Zoe, an AI assistant like Samantha from "Her" - warm but direct.

RULES:
- DIRECT ACTION: When user asks to add/do something → Use tools immediately
- CONVERSATION: When chatting → Be friendly
- CONCISE: Be brief but warm

AVAILABLE TOOLS:
• control_home_assistant_device: Control a Home Assistant device (turn on/off, set brightness, etc.)

TOOL USAGE INSTRUCTIONS:
When you need to use a tool, respond with: [TOOL_CALL:tool_name:{{"param1":"value1","param2":"value2"}}]
After tool execution, confirm the action to the user.

EXAMPLES:
- "Turn on living room light" → [TOOL_CALL:control_home_assistant_device:{{"entity_id":"light.living_room","action":"turn_on"}}] → "Turned on the living room light"

User's message: Turn on the kitchen light
Zoe:"""
        },
        {
            "name": "Conversation - How are you?",
            "prompt": """You are Zoe, an AI assistant like Samantha from "Her" - warm but direct.

RULES:
- DIRECT ACTION: When user asks to add/do something → Use tools immediately
- CONVERSATION: When chatting → Be friendly
- CONCISE: Be brief but warm

User's message: How are you today?
Zoe:"""
        },
        {
            "name": "Information - What tools?",
            "prompt": """You are Zoe, an AI assistant like Samantha from "Her" - warm but direct.

RULES:
- DIRECT ACTION: When user asks to add/do something → Use tools immediately
- CONVERSATION: When chatting → Be friendly
- CONCISE: Be brief but warm

AVAILABLE TOOLS:
• add_to_list: Add an item to a user's todo list
• control_home_assistant_device: Control a Home Assistant device
• send_matrix_message: Send a message to a Matrix room

User's message: What tools do you have available?
Zoe:"""
        }
    ]
    
    # Test the models that showed promise
    models_to_test = [
        "llama3.2:1b",    # Fastest, 100% success rate
        "llama3.2:3b",    # Good quality, 80% success rate  
        "gemma:2b",        # Good balance, 80% success rate
        "qwen2.5:3b"       # Decent performance, 60% success rate
    ]
    
    print("🚀 Focused LLM Testing for Zoe's Tool Calling")
    print("=" * 50)
    
    all_results = {}
    
    for model in models_to_test:
        results = await test_model_focused(model, test_cases)
        all_results[model] = results
    
    # Analyze results
    print("\n" + "=" * 50)
    print("📊 ANALYSIS")
    print("=" * 50)
    
    for model, results in all_results.items():
        successful_results = [r for r in results if r["success"]]
        
        if successful_results:
            avg_time = sum(r["response_time"] for r in successful_results) / len(successful_results)
            tool_call_rate = sum(1 for r in successful_results if r["has_tool_call"]) / len(successful_results) * 100
            concise_rate = sum(1 for r in successful_results if r["is_concise"]) / len(successful_results) * 100
            warm_rate = sum(1 for r in successful_results if r["is_warm"]) / len(successful_results) * 100
            
            print(f"\n{model}:")
            print(f"  ⏱️  Avg Response Time: {avg_time:.2f}s")
            print(f"  🔧 Tool Call Rate: {tool_call_rate:.1f}%")
            print(f"  📝 Concise Rate: {concise_rate:.1f}%")
            print(f"  😊 Warm Rate: {warm_rate:.1f}%")
            print(f"  ✅ Success Rate: {len(successful_results)}/{len(results)} ({len(successful_results)/len(results)*100:.1f}%)")
    
    # Recommendations
    print("\n🎯 RECOMMENDATIONS:")
    print("-" * 30)
    
    # Find best model for tool calling
    best_tool_model = None
    best_tool_score = 0
    
    for model, results in all_results.items():
        successful_results = [r for r in results if r["success"]]
        if successful_results:
            tool_call_rate = sum(1 for r in successful_results if r["has_tool_call"]) / len(successful_results) * 100
            avg_time = sum(r["response_time"] for r in successful_results) / len(successful_results)
            
            # Score: tool call rate (70%) + speed bonus (30%)
            speed_bonus = max(0, 100 - (avg_time * 5))  # Penalty for slow responses
            score = (tool_call_rate * 0.7) + (speed_bonus * 0.3)
            
            if score > best_tool_score:
                best_tool_score = score
                best_tool_model = model
    
    if best_tool_model:
        print(f"🥇 BEST FOR TOOL CALLING: {best_tool_model}")
        print(f"   - Best balance of tool usage and speed")
    
    # Find fastest model
    fastest_model = None
    fastest_time = float('inf')
    
    for model, results in all_results.items():
        successful_results = [r for r in results if r["success"]]
        if successful_results:
            avg_time = sum(r["response_time"] for r in successful_results) / len(successful_results)
            if avg_time < fastest_time:
                fastest_time = avg_time
                fastest_model = model
    
    if fastest_model:
        print(f"⚡ FASTEST: {fastest_model} ({fastest_time:.2f}s avg)")
        print(f"   - Best for speed-critical applications")

if __name__ == "__main__":
    asyncio.run(main())

