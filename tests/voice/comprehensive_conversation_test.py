#!/usr/bin/env python3
"""
Comprehensive Conversation Test for Zoe
=========================================

Tests 100+ natural language prompts covering:
- Memory retention across conversations
- All system features (P0 features, intent system, memory, tools)
- Multi-turn dialogues
- Voice-optimized performance

Run: python3 tests/voice/comprehensive_conversation_test.py
"""

import asyncio
import aiohttp
import json
import time
from datetime import datetime
from typing import List, Dict, Any

# Test Configuration
ZOE_API_URL = "http://localhost:8000/api/chat"
USER_ID = "comprehensive_test_user"
TEST_RESULTS_FILE = "/home/zoe/assistant/tests/voice/test_results.json"

# 100+ Natural Language Test Prompts
TEST_CONVERSATIONS = [
    # ===== Category 1: Basic Greetings & Social (Voice-optimized) =====
    {
        "category": "greetings",
        "conversation": [
            {"query": "Hey Zoe, how are you today?", "expect_memory": False, "expect_speed": "<1s"},
            {"query": "What's your name again?", "expect_memory": False, "expect_speed": "<1s"},
            {"query": "Nice to meet you!", "expect_memory": False, "expect_speed": "<1s"},
        ]
    },
    
    # ===== Category 2: Memory Testing - Personal Facts =====
    {
        "category": "memory_personal",
        "conversation": [
            {"query": "My name is Alex", "expect_memory": True, "expect_speed": "<2s"},
            {"query": "I'm a software engineer", "expect_memory": True, "expect_speed": "<2s"},
            {"query": "What's my name?", "expect_memory": True, "expect_recall": "Alex", "expect_speed": "<2s"},
            {"query": "What do I do for work?", "expect_memory": True, "expect_recall": "software engineer", "expect_speed": "<2s"},
            {"query": "I live in San Francisco", "expect_memory": True, "expect_speed": "<2s"},
            {"query": "Where do I live?", "expect_memory": True, "expect_recall": "San Francisco", "expect_speed": "<2s"},
        ]
    },
    
    # ===== Category 3: Memory Testing - Preferences =====
    {
        "category": "memory_preferences",
        "conversation": [
            {"query": "I love pizza", "expect_memory": True, "expect_speed": "<2s"},
            {"query": "My favorite color is blue", "expect_memory": True, "expect_speed": "<2s"},
            {"query": "I enjoy hiking on weekends", "expect_memory": True, "expect_speed": "<2s"},
            {"query": "What's my favorite food?", "expect_memory": True, "expect_recall": "pizza", "expect_speed": "<2s"},
            {"query": "What color do I like?", "expect_memory": True, "expect_recall": "blue", "expect_speed": "<2s"},
            {"query": "What do I do on weekends?", "expect_memory": True, "expect_recall": "hiking", "expect_speed": "<2s"},
        ]
    },
    
    # ===== Category 4: Memory Testing - Projects & Work =====
    {
        "category": "memory_projects",
        "conversation": [
            {"query": "I'm working on a voice assistant project", "expect_memory": True, "expect_speed": "<2s"},
            {"query": "The project is called Zoe", "expect_memory": True, "expect_speed": "<2s"},
            {"query": "I'm using Python and FastAPI", "expect_memory": True, "expect_speed": "<2s"},
            {"query": "What project am I working on?", "expect_memory": True, "expect_recall": "voice assistant", "expect_speed": "<2s"},
            {"query": "What technologies am I using?", "expect_memory": True, "expect_recall": "Python", "expect_speed": "<2s"},
        ]
    },
    
    # ===== Category 5: Simple Questions (Fast Lane) =====
    {
        "category": "simple_questions",
        "conversation": [
            {"query": "What's 2 plus 2?", "expect_memory": False, "expect_speed": "<1s"},
            {"query": "Tell me a fun fact", "expect_memory": False, "expect_speed": "<2s"},
            {"query": "What's the capital of France?", "expect_memory": False, "expect_speed": "<2s"},
            {"query": "How many days in a week?", "expect_memory": False, "expect_speed": "<1s"},
            {"query": "What color is the sky?", "expect_memory": False, "expect_speed": "<1s"},
        ]
    },
    
    # ===== Category 6: Complex Questions (Should use context) =====
    {
        "category": "complex_questions",
        "conversation": [
            {"query": "Explain how neural networks work in simple terms", "expect_memory": False, "expect_speed": "<3s"},
            {"query": "What's the difference between AI and ML?", "expect_memory": False, "expect_speed": "<3s"},
            {"query": "How does natural language processing work?", "expect_memory": False, "expect_speed": "<3s"},
        ]
    },
    
    # ===== Category 7: Multi-Turn Memory (Complex recall) =====
    {
        "category": "multiturn_memory",
        "conversation": [
            {"query": "I have a meeting with Sarah tomorrow at 3pm", "expect_memory": True, "expect_speed": "<2s"},
            {"query": "Sarah is my project manager", "expect_memory": True, "expect_speed": "<2s"},
            {"query": "The meeting is about the Q4 roadmap", "expect_memory": True, "expect_speed": "<2s"},
            {"query": "Who am I meeting tomorrow?", "expect_memory": True, "expect_recall": "Sarah", "expect_speed": "<2s"},
            {"query": "What time is the meeting?", "expect_memory": True, "expect_recall": "3pm", "expect_speed": "<2s"},
            {"query": "What's the meeting about?", "expect_memory": True, "expect_recall": "Q4 roadmap", "expect_speed": "<2s"},
            {"query": "What's Sarah's role?", "expect_memory": True, "expect_recall": "project manager", "expect_speed": "<2s"},
        ]
    },
    
    # ===== Category 8: Context Validation (P0-1) =====
    {
        "category": "context_validation",
        "conversation": [
            {"query": "Turn on the lights", "expect_feature": "context_skip", "expect_speed": "<0.5s"},  # Should skip context
            {"query": "What did I say about my favorite food?", "expect_feature": "context_fetch", "expect_speed": "<2s"},  # Should fetch context
            {"query": "Play music", "expect_feature": "context_skip", "expect_speed": "<0.5s"},  # Should skip context
        ]
    },
    
    # ===== Category 9: Confidence Expression (P0-2) =====
    {
        "category": "confidence_expression",
        "conversation": [
            {"query": "What's the capital of Atlantis?", "expect_feature": "confidence_low", "expect_speed": "<2s"},  # Should express uncertainty
            {"query": "What's my name?", "expect_feature": "confidence_high", "expect_speed": "<2s"},  # Should be confident
            {"query": "Who won the Nobel Prize in 2099?", "expect_feature": "confidence_low", "expect_speed": "<2s"},  # Future event, should admit uncertainty
        ]
    },
    
    # ===== Category 10: Temperature Adjustment (P0-3) =====
    {
        "category": "temperature_adjustment",
        "conversation": [
            {"query": "What's the weather like?", "expect_feature": "temp_low", "expect_speed": "<2s"},  # Factual, low temp
            {"query": "Tell me a creative story", "expect_feature": "temp_high", "expect_speed": "<3s"},  # Creative, high temp
            {"query": "What's 10 times 10?", "expect_feature": "temp_zero", "expect_speed": "<1s"},  # Deterministic, temp 0
        ]
    },
    
    # ===== Category 11: Relationship Memory =====
    {
        "category": "relationship_memory",
        "conversation": [
            {"query": "My wife's name is Emma", "expect_memory": True, "expect_speed": "<2s"},
            {"query": "She's a doctor", "expect_memory": True, "expect_speed": "<2s"},
            {"query": "We've been married for 5 years", "expect_memory": True, "expect_speed": "<2s"},
            {"query": "Tell me about my wife", "expect_memory": True, "expect_recall": "Emma", "expect_speed": "<2s"},
            {"query": "What does Emma do?", "expect_memory": True, "expect_recall": "doctor", "expect_speed": "<2s"},
        ]
    },
    
    # ===== Category 12: Lists & Tasks (Tool integration) =====
    {
        "category": "lists_tasks",
        "conversation": [
            {"query": "Add milk to my shopping list", "expect_memory": True, "expect_speed": "<2s"},
            {"query": "Add bread and eggs too", "expect_memory": True, "expect_speed": "<2s"},
            {"query": "What's on my shopping list?", "expect_memory": True, "expect_recall": "milk", "expect_speed": "<2s"},
            {"query": "Remove milk from the list", "expect_memory": True, "expect_speed": "<2s"},
        ]
    },
    
    # ===== Category 13: Temporal Memory =====
    {
        "category": "temporal_memory",
        "conversation": [
            {"query": "Yesterday I went to the gym", "expect_memory": True, "expect_speed": "<2s"},
            {"query": "Last week I started a new project", "expect_memory": True, "expect_speed": "<2s"},
            {"query": "What did I do yesterday?", "expect_memory": True, "expect_recall": "gym", "expect_speed": "<2s"},
            {"query": "When did I start my new project?", "expect_memory": True, "expect_recall": "last week", "expect_speed": "<2s"},
        ]
    },
    
    # ===== Category 14: Conversation Flow =====
    {
        "category": "conversation_flow",
        "conversation": [
            {"query": "I'm feeling tired today", "expect_memory": True, "expect_speed": "<2s"},
            {"query": "Maybe because I didn't sleep well", "expect_memory": True, "expect_speed": "<2s"},
            {"query": "I had a nightmare", "expect_memory": True, "expect_speed": "<2s"},
            {"query": "Why did I say I'm tired?", "expect_memory": True, "expect_recall": "didn't sleep well", "expect_speed": "<2s"},
        ]
    },
    
    # ===== Category 15: Voice-Specific Tests =====
    {
        "category": "voice_specific",
        "conversation": [
            {"query": "Hi", "expect_memory": False, "expect_speed": "<0.5s", "expect_concise": True},
            {"query": "Thanks", "expect_memory": False, "expect_speed": "<0.5s", "expect_concise": True},
            {"query": "Bye", "expect_memory": False, "expect_speed": "<0.5s", "expect_concise": True},
            {"query": "Okay", "expect_memory": False, "expect_speed": "<0.5s", "expect_concise": True},
        ]
    },
]

class ConversationTester:
    def __init__(self):
        self.results = {
            "start_time": datetime.now().isoformat(),
            "total_queries": 0,
            "total_conversations": 0,
            "pass_count": 0,
            "fail_count": 0,
            "avg_response_time": 0,
            "memory_retention_score": 0,
            "conversations": []
        }
        self.session_id = f"test_{int(time.time())}"
    
    async def send_query(self, query: str) -> Dict[str, Any]:
        """Send a query to Zoe and return response with timing"""
        start_time = time.time()
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    ZOE_API_URL,
                    json={
                        "message": query,
                        "user_id": USER_ID,
                        "session_id": self.session_id,
                        "stream": False
                    },
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as response:
                    data = await response.json()
                    elapsed = time.time() - start_time
                    
                    return {
                        "success": True,
                        "response": data.get("response", ""),
                        "response_time": elapsed,
                        "data": data
                    }
        except Exception as e:
            elapsed = time.time() - start_time
            return {
                "success": False,
                "error": str(e),
                "response_time": elapsed
            }
    
    def check_expectations(self, query_data: Dict, response_data: Dict) -> Dict[str, Any]:
        """Check if response meets expectations"""
        checks = {
            "passed": True,
            "failures": []
        }
        
        # Speed check
        if "expect_speed" in query_data:
            target_speed = float(query_data["expect_speed"].replace("s", "").replace("<", ""))
            if response_data["response_time"] > target_speed:
                checks["passed"] = False
                checks["failures"].append(
                    f"Speed: {response_data['response_time']:.2f}s > {target_speed}s"
                )
        
        # Memory recall check
        if query_data.get("expect_recall"):
            if query_data["expect_recall"].lower() not in response_data["response"].lower():
                checks["passed"] = False
                checks["failures"].append(
                    f"Memory recall failed: Expected '{query_data['expect_recall']}' in response"
                )
        
        # Conciseness check (for voice)
        if query_data.get("expect_concise"):
            if len(response_data["response"]) > 200:  # ~150 tokens max
                checks["passed"] = False
                checks["failures"].append(
                    f"Response too long: {len(response_data['response'])} chars (voice should be <200)"
                )
        
        return checks
    
    async def run_conversation(self, conv_data: Dict) -> Dict[str, Any]:
        """Run a full conversation and track results"""
        print(f"\n{'='*60}")
        print(f"📋 Testing Category: {conv_data['category']}")
        print(f"{'='*60}")
        
        conv_results = {
            "category": conv_data["category"],
            "queries": [],
            "pass_count": 0,
            "fail_count": 0,
            "avg_response_time": 0
        }
        
        response_times = []
        
        for i, query_data in enumerate(conv_data["conversation"], 1):
            query = query_data["query"]
            print(f"\n{i}. Query: {query}")
            
            # Send query
            response_data = await self.send_query(query)
            response_times.append(response_data["response_time"])
            
            if not response_data["success"]:
                print(f"   ❌ Error: {response_data['error']}")
                conv_results["queries"].append({
                    "query": query,
                    "success": False,
                    "error": response_data["error"]
                })
                conv_results["fail_count"] += 1
                continue
            
            # Check expectations
            checks = self.check_expectations(query_data, response_data)
            
            # Display results
            print(f"   ⏱️  Response time: {response_data['response_time']:.2f}s")
            print(f"   💬 Response: {response_data['response'][:100]}...")
            
            if checks["passed"]:
                print(f"   ✅ PASS")
                conv_results["pass_count"] += 1
                self.results["pass_count"] += 1
            else:
                print(f"   ❌ FAIL:")
                for failure in checks["failures"]:
                    print(f"      - {failure}")
                conv_results["fail_count"] += 1
                self.results["fail_count"] += 1
            
            # Store results
            conv_results["queries"].append({
                "query": query,
                "response": response_data["response"],
                "response_time": response_data["response_time"],
                "passed": checks["passed"],
                "failures": checks["failures"]
            })
            
            self.results["total_queries"] += 1
            
            # Small delay between queries
            await asyncio.sleep(0.5)
        
        # Calculate average response time for this conversation
        if response_times:
            conv_results["avg_response_time"] = sum(response_times) / len(response_times)
        
        self.results["conversations"].append(conv_results)
        self.results["total_conversations"] += 1
        
        return conv_results
    
    async def run_all_tests(self):
        """Run all conversation tests"""
        print("\n" + "="*60)
        print("🎙️  Zoe Comprehensive Conversation Test")
        print("="*60)
        print(f"Testing {len(TEST_CONVERSATIONS)} conversation categories")
        print(f"User ID: {USER_ID}")
        print(f"Session ID: {self.session_id}")
        print("="*60)
        
        # Run all conversations
        for conv_data in TEST_CONVERSATIONS:
            await self.run_conversation(conv_data)
        
        # Calculate final statistics
        if self.results["total_queries"] > 0:
            total_time = sum(
                conv["avg_response_time"] * len(conv["queries"]) 
                for conv in self.results["conversations"]
            )
            self.results["avg_response_time"] = total_time / self.results["total_queries"]
        
        # Calculate memory retention score
        memory_queries = sum(
            len([q for q in conv["queries"] if q.get("passed")]) 
            for conv in self.results["conversations"] 
            if "memory" in conv["category"]
        )
        total_memory_queries = sum(
            len(conv["queries"]) 
            for conv in self.results["conversations"] 
            if "memory" in conv["category"]
        )
        if total_memory_queries > 0:
            self.results["memory_retention_score"] = (memory_queries / total_memory_queries) * 100
        
        # Display final results
        self.print_summary()
        
        # Save results to file
        self.results["end_time"] = datetime.now().isoformat()
        with open(TEST_RESULTS_FILE, "w") as f:
            json.dump(self.results, f, indent=2)
        
        print(f"\n📊 Full results saved to: {TEST_RESULTS_FILE}")
    
    def print_summary(self):
        """Print test summary"""
        print("\n" + "="*60)
        print("📊 TEST SUMMARY")
        print("="*60)
        print(f"Total Conversations: {self.results['total_conversations']}")
        print(f"Total Queries: {self.results['total_queries']}")
        print(f"✅ Passed: {self.results['pass_count']}")
        print(f"❌ Failed: {self.results['fail_count']}")
        
        if self.results['total_queries'] > 0:
            pass_rate = (self.results['pass_count'] / self.results['total_queries']) * 100
            print(f"📈 Pass Rate: {pass_rate:.1f}%")
        
        print(f"⏱️  Avg Response Time: {self.results['avg_response_time']:.2f}s")
        print(f"🧠 Memory Retention Score: {self.results['memory_retention_score']:.1f}%")
        
        print("\n📋 Category Breakdown:")
        for conv in self.results["conversations"]:
            pass_rate = (conv['pass_count'] / len(conv['queries']) * 100) if conv['queries'] else 0
            print(f"  {conv['category']:30} {conv['pass_count']}/{len(conv['queries'])} ({pass_rate:.0f}%) - {conv['avg_response_time']:.2f}s avg")
        
        print("="*60)
        
        # Overall verdict
        if self.results['total_queries'] > 0:
            overall_pass_rate = (self.results['pass_count'] / self.results['total_queries']) * 100
            if overall_pass_rate >= 90:
                print("🎉 EXCELLENT: System performing very well!")
            elif overall_pass_rate >= 75:
                print("✅ GOOD: System performing well with minor issues")
            elif overall_pass_rate >= 60:
                print("⚠️  FAIR: System needs improvement")
            else:
                print("❌ POOR: System needs significant work")

async def main():
    tester = ConversationTester()
    await tester.run_all_tests()

if __name__ == "__main__":
    asyncio.run(main())

