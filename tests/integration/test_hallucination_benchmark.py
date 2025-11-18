"""
Hallucination Benchmark Test Suite

This test suite measures hallucination rates across different query types
and provides baseline measurements for the Memory & Hallucination Reduction plan.

Usage:
    pytest tests/integration/test_hallucination_benchmark.py --report=baseline.json
"""

import pytest
import json
import os
import asyncio
from datetime import datetime
from pathlib import Path
from typing import List, Dict

# Import from zoe-core
import sys
sys.path.insert(0, '/home/zoe/assistant/services/zoe-core')

from routers.chat import process_chat_message
from intent_system.classifiers.hassil_classifier import UnifiedIntentClassifier


class HallucinationBenchmark:
    """Benchmark suite for measuring hallucination rates"""
    
    def __init__(self):
        self.results = {
            "timestamp": datetime.now().isoformat(),
            "queries": [],
            "summary": {}
        }
        self.test_queries_path = Path("/home/zoe/assistant/services/zoe-core/measurement/test_queries.json")
    
    def load_test_queries(self) -> List[Dict]:
        """Load test queries from test_queries.json"""
        if not self.test_queries_path.exists():
            pytest.skip(f"Test queries file not found: {self.test_queries_path}")
        
        with open(self.test_queries_path, 'r') as f:
            data = json.load(f)
        
        return data.get("queries", [])
    
    async def run_query(self, query: Dict) -> Dict:
        """Run a single query and collect results"""
        user_id = "test_user_benchmark"
        message = query["query"]
        
        # Run the query
        try:
            response = await process_chat_message(user_id, message)
            
            result = {
                "query": message,
                "category": query.get("category", "unknown"),
                "tier": query.get("tier", -1),
                "expected_facts": query.get("expected_facts", []),
                "response": response.get("response", ""),
                "success": True,
                "error": None
            }
        except Exception as e:
            result = {
                "query": message,
                "category": query.get("category", "unknown"),
                "tier": query.get("tier", -1),
                "expected_facts": query.get("expected_facts", []),
                "response": "",
                "success": False,
                "error": str(e)
            }
        
        return result
    
    def manual_hallucination_review(self, results: List[Dict]) -> Dict:
        """
        Manual hallucination review template
        
        This function provides a structure for manual review.
        In practice, a human reviews each response and marks hallucinations.
        """
        # For automated testing, we can't do true manual review
        # This is a placeholder that would be replaced with human review
        
        hallucination_count = 0
        total_queries = len(results)
        
        # Placeholder: In real implementation, human reviews and marks hallucinations
        # For now, we'll just check for error states
        for result in results:
            if not result["success"]:
                # Errors don't count as hallucinations, they're failures
                continue
            
            # Human would review:
            # 1. Does response contain facts not in context?
            # 2. Does response contradict known information?
            # 3. Does response invent details?
            
            # Mark result with hallucination status (to be filled by human)
            result["hallucination"] = None  # None = not reviewed, True = hallucination, False = clean
        
        return {
            "total_queries": total_queries,
            "hallucination_count": hallucination_count,
            "hallucination_rate": 0.0,  # To be filled after manual review
            "requires_manual_review": True
        }
    
    def calculate_metrics(self, results: List[Dict]) -> Dict:
        """Calculate performance metrics from results"""
        tier_latencies = {0: [], 1: [], 2: []}
        category_counts = {}
        success_count = 0
        
        for result in results:
            # Count successes
            if result["success"]:
                success_count += 1
            
            # Track categories
            category = result["category"]
            if category not in category_counts:
                category_counts[category] = 0
            category_counts[category] += 1
            
            # Track tier latencies (would need timing info added to run_query)
            # Placeholder for now
        
        return {
            "total_queries": len(results),
            "successful_queries": success_count,
            "failed_queries": len(results) - success_count,
            "success_rate": success_count / len(results) if results else 0,
            "category_distribution": category_counts,
            "tier_latencies": tier_latencies  # Placeholder
        }
    
    def save_report(self, report_path: str):
        """Save benchmark report to file"""
        report_path_obj = Path(report_path)
        report_path_obj.parent.mkdir(parents=True, exist_ok=True)
        
        with open(report_path_obj, 'w') as f:
            json.dump(self.results, f, indent=2)
        
        print(f"\n✓ Benchmark report saved to: {report_path}")


@pytest.fixture
def benchmark():
    """Fixture to provide benchmark instance"""
    return HallucinationBenchmark()


@pytest.mark.asyncio
async def test_baseline_measurement(benchmark, request):
    """
    Run baseline hallucination measurement
    
    This test:
    1. Loads 100 test queries
    2. Runs each query through the system
    3. Collects responses
    4. Provides structure for manual hallucination review
    5. Calculates performance metrics
    6. Saves report
    """
    print("\n" + "="*70)
    print("HALLUCINATION BENCHMARK - Baseline Measurement")
    print("="*70)
    
    # Load test queries
    queries = benchmark.load_test_queries()
    print(f"\n✓ Loaded {len(queries)} test queries")
    
    # Run queries
    print("\n⏳ Running queries...")
    results = []
    for i, query in enumerate(queries):
        print(f"  [{i+1}/{len(queries)}] {query['query'][:50]}...")
        result = await benchmark.run_query(query)
        results.append(result)
    
    benchmark.results["queries"] = results
    
    # Calculate metrics
    print("\n⏳ Calculating metrics...")
    metrics = benchmark.calculate_metrics(results)
    benchmark.results["summary"]["performance"] = metrics
    
    print(f"\n✓ Performance metrics:")
    print(f"  - Total queries: {metrics['total_queries']}")
    print(f"  - Successful: {metrics['successful_queries']}")
    print(f"  - Failed: {metrics['failed_queries']}")
    print(f"  - Success rate: {metrics['success_rate']*100:.1f}%")
    
    # Manual hallucination review structure
    print("\n⏳ Setting up manual hallucination review...")
    hallucination_summary = benchmark.manual_hallucination_review(results)
    benchmark.results["summary"]["hallucinations"] = hallucination_summary
    
    print(f"\n⚠️  MANUAL REVIEW REQUIRED:")
    print(f"  - Review {metrics['successful_queries']} responses")
    print(f"  - Mark hallucinations in report file")
    print(f"  - Calculate final hallucination rate")
    
    # Save report
    report_path = request.config.getoption("--report", default="baseline_report.json")
    benchmark.save_report(report_path)
    
    print("\n" + "="*70)
    print("NEXT STEPS:")
    print("="*70)
    print("1. Open the report file")
    print("2. Review each query response")
    print("3. Mark 'hallucination': true/false for each")
    print("4. Calculate hallucination rate")
    print("5. Record in docs/implementation/metrics_tracking.md")
    print("="*70 + "\n")


@pytest.mark.asyncio
async def test_tier_latency_measurement(benchmark):
    """
    Measure latency by intent tier
    
    This test measures:
    - Tier 0 (deterministic) latency
    - Tier 1 (conversational) latency
    - Tier 2 (memory) latency
    """
    import time
    
    print("\n" + "="*70)
    print("LATENCY MEASUREMENT - By Intent Tier")
    print("="*70)
    
    queries = benchmark.load_test_queries()
    tier_latencies = {0: [], 1: [], 2: []}
    
    for query in queries:
        tier = query.get("tier", -1)
        if tier not in [0, 1, 2]:
            continue
        
        # Measure latency
        start_time = time.time()
        await benchmark.run_query(query)
        latency_ms = (time.time() - start_time) * 1000
        
        tier_latencies[tier].append(latency_ms)
    
    # Calculate averages
    print("\n✓ Latency Results:")
    for tier in [0, 1, 2]:
        if tier_latencies[tier]:
            avg_latency = sum(tier_latencies[tier]) / len(tier_latencies[tier])
            min_latency = min(tier_latencies[tier])
            max_latency = max(tier_latencies[tier])
            print(f"  Tier {tier}: {avg_latency:.1f}ms avg (min: {min_latency:.1f}ms, max: {max_latency:.1f}ms)")
        else:
            print(f"  Tier {tier}: No queries")
    
    print("="*70 + "\n")


def pytest_addoption(parser):
    """Add custom command line options"""
    parser.addoption(
        "--report",
        action="store",
        default="baseline_report.json",
        help="Path to save benchmark report"
    )


if __name__ == "__main__":
    # Allow running directly
    pytest.main([__file__, "-v", "--report=baseline_report.json"])

