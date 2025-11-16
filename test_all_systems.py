#!/usr/bin/env python3
"""
Comprehensive Test Suite for Zoe AI Systems
Tests all models and advanced systems with natural language prompts
"""
import asyncio
import httpx
import json
import sys
import os
from datetime import datetime

# Add services to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'services', 'zoe-core'))

# Test configuration
OLLAMA_URL = "http://localhost:11434/api/generate"
CORE_API_URL = "http://localhost:8000"
USER_ID = "test_user"

# ✅ Enable dev mode for testing (allows localhost access without auth)
import os
os.environ["ZOE_DEV_MODE"] = "true"

# Test prompts by category
TEST_PROMPTS = {
    "conversation": [
        "Hi, how are you?",
        "What's the weather like?",
        "Tell me a joke",
        "What can you help me with?",
    ],
    "action": [
        "Add bread to my shopping list",
        "Schedule a meeting tomorrow at 2pm",
        "Create a reminder to call mom",
        "Add Sarah as a friend",
    ],
    "memory": [
        "Who is Sarah?",
        "What projects am I working on?",
        "Tell me about my recent conversations",
        "What do you remember about me?",
    ],
    "complex": [
        "Help me plan a birthday party for next week",
        "What's on my calendar today and add milk to shopping",
        "Tell me about Sarah and schedule coffee with her",
    ]
}

# Models to test
MODELS_TO_TEST = [
    "gemma3n-e2b-gpu-fixed",
    "gemma3n:e4b",
    "gemma3:27b",
    "gemma2:2b",
    "phi3:mini",
    "llama3.2:3b",
    "qwen2.5:7b",
]

class SystemTester:
    def __init__(self):
        self.results = {
            "models": {},
            "systems": {},
            "errors": []
        }
    
    async def test_model(self, model_name: str, prompt: str = "Hello, test") -> dict:
        """Test a single model"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    OLLAMA_URL,
                    json={
                        "model": model_name,
                        "prompt": prompt,
                        "stream": False,
                        "options": {
                            "num_gpu": 1 if "gpu" in model_name.lower() else 0,
                            "num_predict": 100
                        }
                    }
                )
                
                if response.status_code == 200:
                    data = response.json()
                    return {
                        "status": "success",
                        "response": data.get("response", "")[:100],
                        "model": model_name,
                        "response_time": data.get("total_duration", 0) / 1e9
                    }
                else:
                    return {
                        "status": "error",
                        "error": f"HTTP {response.status_code}",
                        "model": model_name
                    }
        except Exception as e:
            return {
                "status": "error",
                "error": str(e),
                "model": model_name
            }
    
    async def test_route_llm(self, prompt: str) -> dict:
        """Test RouteLLM classification"""
        try:
            from route_llm import router as route_llm_router
            
            routing = await route_llm_router.route_query(prompt, {})
            return {
                "status": "success",
                "routing_type": routing.get("model", "unknown"),  # RouteLLM returns "model" not "type"
                "model": routing.get("model"),
                "confidence": routing.get("confidence", 0.0),
                "reasoning": routing.get("reasoning", "")
            }
        except Exception as e:
            return {
                "status": "error",
                "error": str(e)
            }
    
    async def test_enhanced_mem_agent(self, prompt: str) -> dict:
        """Test Enhanced MemAgent"""
        try:
            from enhanced_mem_agent_client import EnhancedMemAgentClient
            
            client = EnhancedMemAgentClient()
            await client.initialize()
            
            result = await client.enhanced_search(prompt, USER_ID, max_results=3, execute_actions=False)
            
            await client.close()
            
            return {
                "status": "success",
                "experts": result.get("experts", []),
                "primary_expert": result.get("primary_expert"),
                "has_results": len(result.get("experts", [])) > 0
            }
        except Exception as e:
            return {
                "status": "error",
                "error": str(e)
            }
    
    async def test_rag_enhancements(self, query: str) -> dict:
        """Test RAG enhancements"""
        try:
            from rag_enhancements import query_expander, reranker
            
            # Test query expansion
            expanded = await query_expander.expand_query(query)
            
            return {
                "status": "success",
                "expanded_queries": expanded,
                "original_query": query,
                "expansion_count": len(expanded)
            }
        except Exception as e:
            return {
                "status": "error",
                "error": str(e)
            }
    
    async def test_chat_api(self, prompt: str, query_type: str = "conversation") -> dict:
        """Test full chat API endpoint"""
        try:
            session_id = f"test_{datetime.now().timestamp()}"
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    f"{CORE_API_URL}/api/chat/",
                    json={
                        "message": prompt,
                        "user_id": USER_ID,
                        "session_id": session_id
                    },
                    headers={
                        "Content-Type": "application/json",
                        "X-Session-ID": session_id  # ✅ Required for authentication
                    }
                )
                
                if response.status_code == 200:
                    return {
                        "status": "success",
                        "response_received": True,
                        "status_code": response.status_code
                    }
                elif response.status_code == 401:
                    # Try with dev mode bypass (if running from localhost)
                    return {
                        "status": "error",
                        "error": f"HTTP {response.status_code} - Authentication required",
                        "note": "Set ZOE_DEV_MODE=true or provide valid X-Session-ID"
                    }
                else:
                    return {
                        "status": "error",
                        "error": f"HTTP {response.status_code}",
                        "response": response.text[:200]
                    }
        except Exception as e:
            return {
                "status": "error",
                "error": str(e)
            }
    
    async def run_all_tests(self):
        """Run comprehensive test suite"""
        print("=" * 80)
        print("ZOE AI SYSTEMS COMPREHENSIVE TEST SUITE")
        print("=" * 80)
        print()
        
        # Test 1: Individual Models
        print("📊 TEST 1: Testing Individual Models")
        print("-" * 80)
        for model in MODELS_TO_TEST:
            print(f"Testing {model}...", end=" ", flush=True)
            result = await self.test_model(model)
            self.results["models"][model] = result
            
            if result["status"] == "success":
                print(f"✅ SUCCESS ({result.get('response_time', 0):.2f}s)")
                print(f"   Response: {result.get('response', '')[:60]}...")
            else:
                print(f"❌ FAILED: {result.get('error', 'Unknown error')}")
            print()
        
        # Test 2: RouteLLM Classification
        print("📊 TEST 2: Testing RouteLLM Classification")
        print("-" * 80)
        for category, prompts in TEST_PROMPTS.items():
            if category == "complex":
                continue
            prompt = prompts[0]
            print(f"Testing '{prompt}'...", end=" ", flush=True)
            result = await self.test_route_llm(prompt)
            self.results["systems"][f"route_llm_{category}"] = result
            
            if result["status"] == "success":
                routing_type = result.get('routing_type', 'unknown')
                confidence = result.get('confidence', 0)
                reasoning = result.get('reasoning', '')
                print(f"✅ Classified as: {routing_type} (confidence: {confidence:.2f})")
                if reasoning:
                    print(f"   Reasoning: {reasoning}")
            else:
                print(f"❌ FAILED: {result.get('error', 'Unknown error')}")
            print()
        
        # Test 3: Enhanced MemAgent
        print("📊 TEST 3: Testing Enhanced MemAgent")
        print("-" * 80)
        test_queries = [
            "add bread to shopping list",
            "what's on my calendar",
            "who is Sarah"
        ]
        for query in test_queries:
            print(f"Testing '{query}'...", end=" ", flush=True)
            result = await self.test_enhanced_mem_agent(query)
            self.results["systems"][f"mem_agent_{query[:20]}"] = result
            
            if result["status"] == "success":
                expert = result.get("primary_expert", "none")
                experts_count = len(result.get("experts", []))
                print(f"✅ Primary Expert: {expert}, Found {experts_count} experts")
            else:
                print(f"❌ FAILED: {result.get('error', 'Unknown error')}")
            print()
        
        # Test 4: RAG Enhancements
        print("📊 TEST 4: Testing RAG Enhancements")
        print("-" * 80)
        test_queries = [
            "arduino project",
            "garden automation",
            "shopping list"
        ]
        for query in test_queries:
            print(f"Testing query expansion for '{query}'...", end=" ", flush=True)
            result = await self.test_rag_enhancements(query)
            self.results["systems"][f"rag_{query[:20]}"] = result
            
            if result["status"] == "success":
                expanded = result.get("expanded_queries", [])
                print(f"✅ Expanded to {len(expanded)} queries: {expanded[:3]}")
            else:
                print(f"❌ FAILED: {result.get('error', 'Unknown error')}")
            print()
        
        # Test 5: Full Chat API
        print("📊 TEST 5: Testing Full Chat API")
        print("-" * 80)
        for category, prompts in TEST_PROMPTS.items():
            prompt = prompts[0]
            print(f"Testing '{prompt}'...", end=" ", flush=True)
            result = await self.test_chat_api(prompt, category)
            self.results["systems"][f"chat_api_{category}"] = result
            
            if result["status"] == "success":
                print(f"✅ SUCCESS")
            else:
                print(f"❌ FAILED: {result.get('error', 'Unknown error')}")
            print()
        
        # Print Summary
        self.print_summary()
    
    def print_summary(self):
        """Print test summary"""
        print("=" * 80)
        print("TEST SUMMARY")
        print("=" * 80)
        print()
        
        # Model Tests
        print("MODELS:")
        success_count = sum(1 for r in self.results["models"].values() if r["status"] == "success")
        total_count = len(self.results["models"])
        print(f"  ✅ {success_count}/{total_count} models working")
        
        for model, result in self.results["models"].items():
            status_icon = "✅" if result["status"] == "success" else "❌"
            print(f"    {status_icon} {model}")
            if result["status"] == "error":
                print(f"       Error: {result.get('error', 'Unknown')}")
        print()
        
        # System Tests
        print("SYSTEMS:")
        system_success = sum(1 for r in self.results["systems"].values() if r["status"] == "success")
        system_total = len(self.results["systems"])
        print(f"  ✅ {system_success}/{system_total} system tests passed")
        print()
        
        # Overall Status
        total_tests = total_count + system_total
        total_success = success_count + system_success
        success_rate = (total_success / total_tests * 100) if total_tests > 0 else 0
        
        print(f"OVERALL: {total_success}/{total_tests} tests passed ({success_rate:.1f}%)")
        print()
        
        if success_rate >= 90:
            print("🎉 EXCELLENT - All systems operational!")
        elif success_rate >= 70:
            print("✅ GOOD - Most systems working")
        else:
            print("⚠️  NEEDS ATTENTION - Some systems need fixes")

async def main():
    tester = SystemTester()
    await tester.run_all_tests()

if __name__ == "__main__":
    asyncio.run(main())

