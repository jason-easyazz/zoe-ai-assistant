"""
Comprehensive Test Suite for Zoe Performance Optimization
Tests 100+ natural language prompts across all functionality
"""
import asyncio
import httpx
import time
import json
import logging
import os
from typing import List, Dict, Tuple
from datetime import datetime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Test prompts organized by category
TEST_PROMPTS = {
    "simple_queries": [
        "What's the weather?",
        "Tell me a joke",
        "What time is it?",
        "Hello",
        "Hi there",
        "How are you?",
        "What can you do?",
        "Help me",
        "Thanks",
        "Good morning"
    ],
    "action_requests": [
        "Add bread to shopping list",
        "Add milk to my shopping list",
        "Create a calendar event for tomorrow at 2pm",
        "Schedule a meeting next Monday at 10am",
        "Add eggs to shopping list",
        "Remind me to call John tomorrow",
        "Add apples to shopping list",
        "Create event: Doctor appointment on Friday",
        "Add bananas to shopping list",
        "Schedule lunch meeting next week",
        "Add to shopping list: chicken",
        "Create reminder: Pick up dry cleaning",
        "Add tomatoes to my list",
        "Schedule a call with Sarah",
        "Add coffee to shopping list"
    ],
    "memory_queries": [
        "Who is John?",
        "What did we talk about last week?",
        "Remember that I like coffee",
        "Who are my contacts?",
        "What's my schedule?",
        "Tell me about my projects",
        "What events do I have coming up?",
        "Who did I meet last month?",
        "What are my active lists?",
        "Show me my calendar"
    ],
    "complex_multi_step": [
        "Find all events this week and add them to a reminder list",
        "Check my calendar and tell me what's coming up",
        "Add bread and milk to shopping list",
        "Create a meeting tomorrow at 2pm and remind me 30 minutes before",
        "Show me my shopping list and calendar events",
        "What do I have scheduled today and tomorrow?",
        "Add eggs, milk, and bread to shopping list",
        "Check my calendar for next week and create reminders",
        "Show me my lists and upcoming events",
        "What's on my shopping list and what events are coming up?"
    ],
    "edge_cases": [
        "",  # Empty message
        "a",  # Single character
        "add",  # Incomplete command
        "add to",  # Incomplete
        "asdfghjkl",  # Gibberish
        "123456789",  # Numbers only
        "!@#$%^&*()",  # Special characters
        "add add add add",  # Repetitive
        "what what what",  # Repetitive query
        "aaaaaaaaaaaaaaaaaaaa",  # Long single character
    ],
    "natural_conversation": [
        "Hey, can you help me plan my day?",
        "I need to buy some groceries, can you add them to my list?",
        "What's happening this week?",
        "I have a doctor's appointment coming up, can you remind me?",
        "Show me what I need to do today",
        "Can you tell me about my upcoming events?",
        "I want to schedule something for next week",
        "What's on my shopping list?",
        "Help me organize my tasks",
        "What did we discuss yesterday?"
    ],
    "capability_questions": [
        "What can you do?",
        "What are your capabilities?",
        "Tell me what you can help with",
        "What things can you do?",
        "How can you help me?",
        "What features do you have?",
        "What are your functions?",
        "What can I ask you?",
        "What do you support?",
        "What are you capable of?"
    ],
    "shopping_list_operations": [
        "Show my shopping list",
        "What's on my shopping list?",
        "Get my shopping list",
        "Display shopping list",
        "List my shopping items",
        "What do I need to buy?",
        "Show shopping list items",
        "What's in my shopping list?",
        "Display my shopping list",
        "Get shopping list"
    ],
    "calendar_operations": [
        "What's on my calendar today?",
        "Show me my calendar",
        "What events do I have?",
        "What's scheduled for this week?",
        "Show upcoming events",
        "What's on my schedule?",
        "Display my calendar",
        "What appointments do I have?",
        "Show my events",
        "What's coming up?"
    ],
    "mixed_operations": [
        "Add bread to shopping list and show my calendar",
        "Create an event and add items to shopping list",
        "Show my shopping list and calendar",
        "What's on my list and what's coming up?",
        "Add milk and check my schedule",
        "Create event and show shopping list",
        "Add items and check calendar",
        "Show list and schedule",
        "Add to list and show events",
        "Create reminder and show calendar"
    ]
}

class ZoeTester:
    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url
        self.results: List[Dict] = []
        self.failures: List[Dict] = []
        self.performance_metrics: Dict = {
            "total_tests": 0,
            "passed": 0,
            "failed": 0,
            "avg_first_token_latency": 0,
            "avg_total_latency": 0,
            "avg_tokens_per_second": 0,
            "min_latency": float('inf'),
            "max_latency": 0
        }
    
    async def get_test_session(self) -> str:
        """Get or create a test session"""
        # Use dev-localhost session directly (bypasses auth for testing)
        logger.info("Using dev-localhost session for testing")
        return "dev-localhost"
    
    async def test_prompt(self, prompt: str, category: str, user_id: str = "test_user", session_id: str = None) -> Dict:
        """Test a single prompt and measure performance"""
        start_time = time.time()
        first_token_time = None
        total_tokens = 0
        
        if not session_id:
            session_id = await self.get_test_session()
        
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                # Use X-Session-ID header for authentication
                headers = {
                    "Content-Type": "application/json",
                    "X-Session-ID": session_id
                }
                
                response = await client.post(
                    f"{self.base_url}/api/chat",
                    params={"stream": True},
                    json={
                        "message": prompt,
                        "user_id": user_id,
                    },
                    headers=headers
                )
                
                if response.status_code != 200:
                    return {
                        "prompt": prompt,
                        "category": category,
                        "status": "failed",
                        "error": f"HTTP {response.status_code}",
                        "latency": time.time() - start_time,
                        "first_token_latency": None
                    }
                
                # Parse streaming response
                full_response = ""
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        try:
                            data = json.loads(line[6:])
                            if data.get("type") == "message_delta":
                                if first_token_time is None:
                                    first_token_time = time.time() - start_time
                                delta = data.get("delta", "")
                                full_response += delta
                                total_tokens += len(delta.split())
                            elif data.get("type") == "error":
                                return {
                                    "prompt": prompt,
                                    "category": category,
                                    "status": "failed",
                                    "error": data.get("error", {}).get("message", "Unknown error"),
                                    "latency": time.time() - start_time,
                                    "first_token_latency": first_token_time
                                }
                        except json.JSONDecodeError:
                            continue
                
                total_latency = time.time() - start_time
                tokens_per_second = total_tokens / total_latency if total_latency > 0 else 0
                
                # Validate response
                is_valid = len(full_response.strip()) > 0
                
                result = {
                    "prompt": prompt,
                    "category": category,
                    "status": "passed" if is_valid else "failed",
                    "response_length": len(full_response),
                    "latency": total_latency,
                    "first_token_latency": first_token_time,
                    "tokens_per_second": tokens_per_second,
                    "response_preview": full_response[:100] if full_response else ""
                }
                
                if not is_valid:
                    result["error"] = "Empty response"
                
                return result
                
        except Exception as e:
            return {
                "prompt": prompt,
                "category": category,
                "status": "failed",
                "error": str(e),
                "latency": time.time() - start_time,
                "first_token_latency": first_token_time
            }
    
    async def run_test_suite(self):
        """Run all test prompts"""
        logger.info("🧪 Starting comprehensive test suite...")
        
        all_prompts = []
        for category, prompts in TEST_PROMPTS.items():
            for prompt in prompts:
                all_prompts.append((prompt, category))
        
        self.performance_metrics["total_tests"] = len(all_prompts)
        logger.info(f"📊 Testing {len(all_prompts)} prompts across {len(TEST_PROMPTS)} categories")
        
        # Run tests with concurrency limit
        semaphore = asyncio.Semaphore(5)  # Max 5 concurrent tests
        
        async def test_with_semaphore(prompt, category):
            async with semaphore:
                return await self.test_prompt(prompt, category)
        
        # Run all tests
        session_id = await self.get_test_session()
        tasks = [self.test_prompt(prompt, category, session_id=session_id) for prompt, category in all_prompts]
        results = await asyncio.gather(*tasks)
        
        # Process results
        for result in results:
            self.results.append(result)
            if result["status"] == "failed":
                self.failures.append(result)
            else:
                self.performance_metrics["passed"] += 1
                # Update performance metrics
                if result.get("first_token_latency"):
                    self.performance_metrics["min_latency"] = min(
                        self.performance_metrics["min_latency"],
                        result["first_token_latency"]
                    )
                    self.performance_metrics["max_latency"] = max(
                        self.performance_metrics["max_latency"],
                        result["first_token_latency"]
                    )
        
        self.performance_metrics["failed"] = len(self.failures)
        
        # Calculate averages
        latencies = [r["first_token_latency"] for r in self.results if r.get("first_token_latency")]
        total_latencies = [r["latency"] for r in self.results]
        tokens_per_sec = [r.get("tokens_per_second", 0) for r in self.results if r.get("tokens_per_second", 0) > 0]
        
        if latencies:
            self.performance_metrics["avg_first_token_latency"] = sum(latencies) / len(latencies)
        if total_latencies:
            self.performance_metrics["avg_total_latency"] = sum(total_latencies) / len(total_latencies)
        if tokens_per_sec:
            self.performance_metrics["avg_tokens_per_second"] = sum(tokens_per_sec) / len(tokens_per_sec)
    
    def generate_report(self) -> str:
        """Generate test report"""
        report = []
        report.append("=" * 80)
        report.append("ZOE PERFORMANCE TEST REPORT")
        report.append("=" * 80)
        report.append(f"Test Date: {datetime.now().isoformat()}")
        report.append(f"Total Tests: {self.performance_metrics['total_tests']}")
        report.append(f"Passed: {self.performance_metrics['passed']}")
        report.append(f"Failed: {self.performance_metrics['failed']}")
        report.append(f"Pass Rate: {(self.performance_metrics['passed'] / self.performance_metrics['total_tests'] * 100):.1f}%")
        report.append("")
        report.append("PERFORMANCE METRICS:")
        report.append(f"  Average First Token Latency: {self.performance_metrics['avg_first_token_latency']:.3f}s")
        report.append(f"  Average Total Latency: {self.performance_metrics['avg_total_latency']:.3f}s")
        report.append(f"  Average Tokens/Second: {self.performance_metrics['avg_tokens_per_second']:.2f}")
        report.append(f"  Min First Token Latency: {self.performance_metrics['min_latency']:.3f}s")
        report.append(f"  Max First Token Latency: {self.performance_metrics['max_latency']:.3f}s")
        report.append("")
        
        if self.failures:
            report.append("FAILURES:")
            for failure in self.failures[:20]:  # Show first 20 failures
                report.append(f"  - [{failure['category']}] {failure['prompt'][:50]}")
                report.append(f"    Error: {failure.get('error', 'Unknown')}")
            if len(self.failures) > 20:
                report.append(f"  ... and {len(self.failures) - 20} more failures")
            report.append("")
        
        # Category breakdown
        report.append("CATEGORY BREAKDOWN:")
        for category in TEST_PROMPTS.keys():
            category_results = [r for r in self.results if r["category"] == category]
            passed = sum(1 for r in category_results if r["status"] == "passed")
            total = len(category_results)
            report.append(f"  {category}: {passed}/{total} ({passed/total*100:.1f}%)")
        
        report.append("")
        report.append("=" * 80)
        
        return "\n".join(report)

async def main():
    """Main test execution"""
    tester = ZoeTester()
    
    try:
        await tester.run_test_suite()
        report = tester.generate_report()
        print(report)
        
        # Save detailed results
        with open("/tmp/zoe_test_results.json", "w") as f:
            json.dump({
                "results": tester.results,
                "metrics": tester.performance_metrics,
                "timestamp": datetime.now().isoformat()
            }, f, indent=2)
        
        logger.info("✅ Test suite completed. Results saved to /tmp/zoe_test_results.json")
        
        # Return exit code based on pass rate
        pass_rate = tester.performance_metrics["passed"] / tester.performance_metrics["total_tests"]
        if pass_rate >= 0.95:  # 95% pass rate required
            logger.info("✅ Tests passed! Pass rate >= 95%")
            return 0
        else:
            logger.warning(f"⚠️ Tests failed. Pass rate: {pass_rate*100:.1f}% (required: 95%)")
            return 1
            
    except Exception as e:
        logger.error(f"❌ Test suite failed: {e}", exc_info=True)
        return 1

if __name__ == "__main__":
    exit_code = asyncio.run(main())
    exit(exit_code)

