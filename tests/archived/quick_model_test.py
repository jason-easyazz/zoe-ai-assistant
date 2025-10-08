#!/usr/bin/env python3
"""
Quick Model Test for Currently Available Models
"""

import asyncio
import httpx
import json
import time
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def test_model_quick(model_name: str, prompt: str) -> dict:
    """Quick test of a single model"""
    logger.info(f"🧪 Testing {model_name}...")
    
    start_time = time.time()
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "http://localhost:11434/api/generate",
                json={
                    "model": model_name,
                    "prompt": prompt,
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
            
            if response.status_code != 200:
                return {
                    "model": model_name,
                    "success": False,
                    "response_time": time.time() - start_time,
                    "error": f"HTTP {response.status_code}"
                }
            
            data = response.json()
            response_text = data.get("response", "")
            response_time = time.time() - start_time
            
            # Check for tool calling
            tool_call_found = "[TOOL_CALL:" in response_text
            
            return {
                "model": model_name,
                "success": True,
                "response_time": response_time,
                "response_length": len(response_text),
                "tool_call_found": tool_call_found,
                "response_preview": response_text[:100] + "..." if len(response_text) > 100 else response_text
            }
            
    except Exception as e:
        return {
            "model": model_name,
            "success": False,
            "response_time": time.time() - start_time,
            "error": str(e)
        }

async def main():
    """Quick test of available models"""
    
    # Available models from the list
    available_models = [
        "gemma3:1b",
        "qwen2.5:1.5b", 
        "llama3.2:1b",
        "mistral:7b",
        "mistral:latest"
    ]
    
    test_prompt = """You are Zoe, an AI assistant like Samantha from "Her" - warm but direct.

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
Zoe:"""
    
    logger.info("🚀 Starting quick model test...")
    
    results = []
    
    for model in available_models:
        result = await test_model_quick(model, test_prompt)
        results.append(result)
        
        if result["success"]:
            logger.info(f"✅ {model}: {result['response_time']:.2f}s, "
                       f"{result['response_length']} chars, "
                       f"Tool: {'✓' if result['tool_call_found'] else '✗'}")
            logger.info(f"   Preview: {result['response_preview']}")
        else:
            logger.error(f"❌ {model}: {result.get('error', 'Unknown error')}")
        
        # Small delay between tests
        await asyncio.sleep(1)
    
    # Summary
    successful_tests = [r for r in results if r["success"]]
    if successful_tests:
        avg_time = sum(r["response_time"] for r in successful_tests) / len(successful_tests)
        tool_success = sum(r["tool_call_found"] for r in successful_tests) / len(successful_tests)
        
        logger.info(f"\n📊 Summary:")
        logger.info(f"   Successful tests: {len(successful_tests)}/{len(results)}")
        logger.info(f"   Average response time: {avg_time:.2f}s")
        logger.info(f"   Tool calling success rate: {tool_success:.1%}")
        
        # Best performers
        fastest = min(successful_tests, key=lambda x: x["response_time"])
        best_tool = max(successful_tests, key=lambda x: x["tool_call_found"])
        
        logger.info(f"\n🏆 Best Performers:")
        logger.info(f"   Fastest: {fastest['model']} ({fastest['response_time']:.2f}s)")
        logger.info(f"   Best Tool Calling: {best_tool['model']} ({'✓' if best_tool['tool_call_found'] else '✗'})")

if __name__ == "__main__":
    asyncio.run(main())

