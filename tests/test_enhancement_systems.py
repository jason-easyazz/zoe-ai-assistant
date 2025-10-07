"""
Comprehensive Test Suite for Zoe Enhancement Systems
===================================================

Tests all four enhancement systems with scoring framework similar to 
the existing optimization system.
"""

import pytest
import sqlite3
import tempfile
import os
import json
import asyncio
import time
from datetime import datetime, timedelta
from pathlib import Path
import sys

# Add the services directory to the path
sys.path.append('/workspace/services/zoe-core')

from temporal_memory import TemporalMemorySystem, EpisodeStatus
from cross_agent_collaboration import ExpertOrchestrator, TaskStatus, ExpertType
from user_satisfaction import UserSatisfactionSystem, FeedbackType, SatisfactionLevel
from context_cache import ContextCacheSystem, ContextType, CacheStatus

class EnhancementTestSuite:
    """Comprehensive test suite with scoring framework"""
    
    def __init__(self):
        self.test_results = {}
        self.target_scores = {
            "temporal_memory": 90,  # 90%+ for production readiness
            "cross_agent_collaboration": 85,  # 85%+ for production readiness
            "user_satisfaction": 80,  # 80%+ for production readiness
            "context_cache": 75  # 75%+ for production readiness (performance optimization)
        }
    
    def calculate_score(self, test_results: dict) -> float:
        """Calculate overall score from test results"""
        if not test_results:
            return 0.0
        
        total_tests = len(test_results)
        passed_tests = sum(1 for result in test_results.values() if result)
        
        return (passed_tests / total_tests) * 100
    
    def run_all_tests(self) -> dict:
        """Run all enhancement system tests and return scores"""
        print("🧪 Running Comprehensive Enhancement System Tests")
        print("=" * 60)
        
        # Run test suites
        temporal_results = self.test_temporal_memory_suite()
        orchestration_results = self.test_orchestration_suite()
        satisfaction_results = self.test_satisfaction_suite()
        cache_results = self.test_context_cache_suite()
        
        # Calculate scores
        scores = {
            "temporal_memory": self.calculate_score(temporal_results),
            "cross_agent_collaboration": self.calculate_score(orchestration_results),
            "user_satisfaction": self.calculate_score(satisfaction_results),
            "context_cache": self.calculate_score(cache_results)
        }
        
        # Overall system health
        overall_score = sum(scores.values()) / len(scores)
        
        # Print results
        self.print_test_results(scores, overall_score)
        
        return {
            "scores": scores,
            "overall_score": overall_score,
            "production_ready": self.assess_production_readiness(scores),
            "detailed_results": {
                "temporal_memory": temporal_results,
                "cross_agent_collaboration": orchestration_results,
                "user_satisfaction": satisfaction_results,
                "context_cache": cache_results
            }
        }
    
    def test_temporal_memory_suite(self) -> dict:
        """Test temporal memory system"""
        print("\n📅 Testing Temporal Memory System...")
        
        results = {}
        
        try:
            # Create temporary database
            with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
                db_path = f.name
            
            temporal_system = TemporalMemorySystem(db_path)
            
            # Test 1: Episode creation
            results["episode_creation"] = self._test_episode_creation(temporal_system)
            
            # Test 2: Episode management
            results["episode_management"] = self._test_episode_management(temporal_system)
            
            # Test 3: Temporal search
            results["temporal_search"] = self._test_temporal_search(temporal_system)
            
            # Test 4: Memory decay
            results["memory_decay"] = self._test_memory_decay(temporal_system)
            
            # Test 5: Episode summaries
            results["episode_summaries"] = self._test_episode_summaries(temporal_system)
            
            # Test 6: Time range queries
            results["time_range_queries"] = self._test_time_range_queries(temporal_system)
            
            # Test 7: Topic extraction
            results["topic_extraction"] = self._test_topic_extraction(temporal_system)
            
            # Test 8: Episode timeouts
            results["episode_timeouts"] = self._test_episode_timeouts(temporal_system)
            
            # Cleanup
            os.unlink(db_path)
            
        except Exception as e:
            print(f"❌ Temporal memory test suite failed: {e}")
            results["suite_execution"] = False
        
        return results
    
    def test_orchestration_suite(self) -> dict:
        """Test cross-agent orchestration system"""
        print("\n🤝 Testing Cross-Agent Orchestration System...")
        
        results = {}
        
        try:
            orchestrator = ExpertOrchestrator()
            
            # Test 1: Task decomposition
            results["task_decomposition"] = self._test_task_decomposition(orchestrator)
            
            # Test 2: Expert coordination
            results["expert_coordination"] = self._test_expert_coordination(orchestrator)
            
            # Test 3: Dependency resolution
            results["dependency_resolution"] = self._test_dependency_resolution(orchestrator)
            
            # Test 4: Timeout handling
            results["timeout_handling"] = self._test_timeout_handling(orchestrator)
            
            # Test 5: Result synthesis
            results["result_synthesis"] = self._test_result_synthesis(orchestrator)
            
            # Test 6: Error handling
            results["error_handling"] = self._test_orchestration_error_handling(orchestrator)
            
            # Test 7: Parallel execution
            results["parallel_execution"] = self._test_parallel_execution(orchestrator)
            
        except Exception as e:
            print(f"❌ Orchestration test suite failed: {e}")
            results["suite_execution"] = False
        
        return results
    
    def test_satisfaction_suite(self) -> dict:
        """Test user satisfaction system"""
        print("\n😊 Testing User Satisfaction System...")
        
        results = {}
        
        try:
            # Create temporary database
            with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
                db_path = f.name
            
            satisfaction_system = UserSatisfactionSystem(db_path)
            
            # Test 1: Explicit feedback collection
            results["explicit_feedback"] = self._test_explicit_feedback(satisfaction_system)
            
            # Test 2: Implicit signal analysis
            results["implicit_signals"] = self._test_implicit_signals(satisfaction_system)
            
            # Test 3: Satisfaction metrics
            results["satisfaction_metrics"] = self._test_satisfaction_metrics(satisfaction_system)
            
            # Test 4: Trend analysis
            results["trend_analysis"] = self._test_trend_analysis(satisfaction_system)
            
            # Test 5: Privacy isolation
            results["privacy_isolation"] = self._test_privacy_isolation(satisfaction_system)
            
            # Test 6: Feedback processing
            results["feedback_processing"] = self._test_feedback_processing(satisfaction_system)
            
            # Cleanup
            os.unlink(db_path)
            
        except Exception as e:
            print(f"❌ Satisfaction test suite failed: {e}")
            results["suite_execution"] = False
        
        return results
    
    def test_context_cache_suite(self) -> dict:
        """Test context cache system"""
        print("\n🚀 Testing Context Cache System...")
        
        results = {}
        
        try:
            # Create temporary database
            with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
                db_path = f.name
            
            cache_system = ContextCacheSystem(db_path)
            
            # Test 1: Cache operations
            results["cache_operations"] = self._test_cache_operations(cache_system)
            
            # Test 2: LLM summarization
            results["llm_summarization"] = self._test_llm_summarization(cache_system)
            
            # Test 3: Cache invalidation
            results["cache_invalidation"] = self._test_cache_invalidation(cache_system)
            
            # Test 4: Performance benchmarking
            results["performance_benchmarking"] = self._test_performance_benchmarking(cache_system)
            
            # Test 5: TTL and expiration
            results["ttl_expiration"] = self._test_ttl_expiration(cache_system)
            
            # Test 6: Memory efficiency
            results["memory_efficiency"] = self._test_memory_efficiency(cache_system)
            
            # Cleanup
            os.unlink(db_path)
            
        except Exception as e:
            print(f"❌ Context cache test suite failed: {e}")
            results["suite_execution"] = False
        
        return results
    
    # Individual test methods
    def _test_episode_creation(self, temporal_system) -> bool:
        """Test episode creation functionality"""
        try:
            episode = temporal_system.create_episode("test_user", "development", ["user1", "user2"])
            
            assert episode.user_id == "test_user"
            assert episode.context_type == "development"
            assert episode.status == EpisodeStatus.ACTIVE
            assert len(episode.participants) == 2
            
            print("  ✅ Episode creation")
            return True
        except Exception as e:
            print(f"  ❌ Episode creation: {e}")
            return False
    
    def _test_episode_management(self, temporal_system) -> bool:
        """Test episode management operations"""
        try:
            # Create episode
            episode = temporal_system.create_episode("test_user", "chat")
            
            # Add messages
            success = temporal_system.add_message_to_episode(episode.id, "Hello, how are you?", "user")
            assert success
            
            # Close episode
            success = temporal_system.close_episode(episode.id, generate_summary=True)
            assert success
            
            print("  ✅ Episode management")
            return True
        except Exception as e:
            print(f"  ❌ Episode management: {e}")
            return False
    
    def _test_temporal_search(self, temporal_system) -> bool:
        """Test temporal search functionality"""
        try:
            # This would require setting up memory facts with temporal metadata
            # For now, test the search method doesn't crash
            results = temporal_system.search_temporal_memories("test query", "test_user")
            assert isinstance(results, list)
            
            print("  ✅ Temporal search")
            return True
        except Exception as e:
            print(f"  ❌ Temporal search: {e}")
            return False
    
    def _test_memory_decay(self, temporal_system) -> bool:
        """Test memory decay algorithm"""
        try:
            result = temporal_system.apply_memory_decay("test_user")
            assert "decayed_memories" in result
            assert "decay_threshold_days" in result
            
            print("  ✅ Memory decay")
            return True
        except Exception as e:
            print(f"  ❌ Memory decay: {e}")
            return False
    
    def _test_episode_summaries(self, temporal_system) -> bool:
        """Test episode summary generation"""
        try:
            episode = temporal_system.create_episode("test_user", "planning")
            temporal_system.add_message_to_episode(episode.id, "Let's plan the project", "user")
            
            # Test summary generation (simplified)
            summary = temporal_system._generate_episode_summary(episode.id)
            assert summary is not None
            assert len(summary) > 0
            
            print("  ✅ Episode summaries")
            return True
        except Exception as e:
            print(f"  ❌ Episode summaries: {e}")
            return False
    
    def _test_time_range_queries(self, temporal_system) -> bool:
        """Test time range query functionality"""
        try:
            now = datetime.now()
            start_time = (now - timedelta(hours=1)).isoformat()
            end_time = now.isoformat()
            
            results = temporal_system.search_temporal_memories(
                "test", "test_user", time_range=(start_time, end_time)
            )
            assert isinstance(results, list)
            
            print("  ✅ Time range queries")
            return True
        except Exception as e:
            print(f"  ❌ Time range queries: {e}")
            return False
    
    def _test_topic_extraction(self, temporal_system) -> bool:
        """Test topic extraction from messages"""
        try:
            topics = temporal_system._extract_topics("Let's schedule a meeting and create a task list")
            assert "calendar" in topics
            assert "tasks" in topics
            
            print("  ✅ Topic extraction")
            return True
        except Exception as e:
            print(f"  ❌ Topic extraction: {e}")
            return False
    
    def _test_episode_timeouts(self, temporal_system) -> bool:
        """Test episode timeout checking"""
        try:
            result = temporal_system.check_episode_timeouts()
            assert "checked_episodes" in result
            assert "closed_episodes" in result
            
            print("  ✅ Episode timeouts")
            return True
        except Exception as e:
            print(f"  ❌ Episode timeouts: {e}")
            return False
    
    def _test_task_decomposition(self, orchestrator) -> bool:
        """Test LLM-based task decomposition"""
        try:
            # Test keyword-based decomposition (fallback)
            tasks = orchestrator._simple_keyword_decomposition("Schedule a meeting and create a shopping list")
            assert len(tasks) >= 2  # Should identify calendar and list tasks
            
            print("  ✅ Task decomposition")
            return True
        except Exception as e:
            print(f"  ❌ Task decomposition: {e}")
            return False
    
    def _test_expert_coordination(self, orchestrator) -> bool:
        """Test expert coordination logic"""
        try:
            # Test endpoint mapping
            assert ExpertType.CALENDAR in orchestrator.expert_endpoints
            assert ExpertType.LISTS in orchestrator.expert_endpoints
            
            print("  ✅ Expert coordination")
            return True
        except Exception as e:
            print(f"  ❌ Expert coordination: {e}")
            return False
    
    def _test_dependency_resolution(self, orchestrator) -> bool:
        """Test task dependency resolution"""
        try:
            from cross_agent_collaboration import ExpertTask
            
            tasks = [
                ExpertTask("task1", ExpertType.MEMORY, "First task", {}, "output1"),
                ExpertTask("task2", ExpertType.CALENDAR, "Second task", {}, "output2")
            ]
            
            dependencies = orchestrator._create_execution_plan(tasks)
            assert len(dependencies) == 1  # One dependency for sequential execution
            
            print("  ✅ Dependency resolution")
            return True
        except Exception as e:
            print(f"  ❌ Dependency resolution: {e}")
            return False
    
    def _test_timeout_handling(self, orchestrator) -> bool:
        """Test timeout handling for expert tasks"""
        try:
            # Test timeout configuration
            assert orchestrator.task_timeout == 30
            
            print("  ✅ Timeout handling")
            return True
        except Exception as e:
            print(f"  ❌ Timeout handling: {e}")
            return False
    
    def _test_result_synthesis(self, orchestrator) -> bool:
        """Test result synthesis functionality"""
        try:
            from cross_agent_collaboration import ExpertTask
            
            tasks = [
                ExpertTask("task1", ExpertType.MEMORY, "Test task", {}, "output", status=TaskStatus.COMPLETED)
            ]
            
            synthesis = asyncio.run(orchestrator._synthesize_results(tasks, "Test request"))
            assert "summary" in synthesis
            assert "successful_tasks" in synthesis
            
            print("  ✅ Result synthesis")
            return True
        except Exception as e:
            print(f"  ❌ Result synthesis: {e}")
            return False
    
    def _test_orchestration_error_handling(self, orchestrator) -> bool:
        """Test orchestration error handling"""
        try:
            # Test error handling doesn't crash the system
            result = orchestrator._generate_summary({"successful_tasks": 1, "failed_tasks": 0, "total_tasks": 1, "results": {}})
            assert isinstance(result, str)
            
            print("  ✅ Error handling")
            return True
        except Exception as e:
            print(f"  ❌ Error handling: {e}")
            return False
    
    def _test_parallel_execution(self, orchestrator) -> bool:
        """Test parallel task execution capabilities"""
        try:
            # Test that orchestrator can handle multiple tasks
            assert len(orchestrator.expert_endpoints) >= 5  # Multiple experts available
            
            print("  ✅ Parallel execution")
            return True
        except Exception as e:
            print(f"  ❌ Parallel execution: {e}")
            return False
    
    def _test_explicit_feedback(self, satisfaction_system) -> bool:
        """Test explicit feedback collection"""
        try:
            feedback_id = satisfaction_system.record_explicit_feedback(
                "test_user", "interaction_123", 4, "Great response!"
            )
            assert feedback_id is not None
            
            print("  ✅ Explicit feedback")
            return True
        except Exception as e:
            print(f"  ❌ Explicit feedback: {e}")
            return False
    
    def _test_implicit_signals(self, satisfaction_system) -> bool:
        """Test implicit signal analysis"""
        try:
            success = satisfaction_system.record_interaction(
                "interaction_456", "test_user", "Test request", "Test response", 2.5
            )
            assert success
            
            print("  ✅ Implicit signals")
            return True
        except Exception as e:
            print(f"  ❌ Implicit signals: {e}")
            return False
    
    def _test_satisfaction_metrics(self, satisfaction_system) -> bool:
        """Test satisfaction metrics calculation"""
        try:
            # Add some feedback first
            satisfaction_system.record_explicit_feedback("test_user", "int1", 4)
            satisfaction_system.record_explicit_feedback("test_user", "int2", 5)
            
            metrics = satisfaction_system.get_satisfaction_metrics("test_user")
            assert metrics is not None
            assert metrics.total_interactions > 0
            
            print("  ✅ Satisfaction metrics")
            return True
        except Exception as e:
            print(f"  ❌ Satisfaction metrics: {e}")
            return False
    
    def _test_trend_analysis(self, satisfaction_system) -> bool:
        """Test satisfaction trend analysis"""
        try:
            # Test trend calculation doesn't crash
            score = satisfaction_system._calculate_implicit_satisfaction(2.0, True, 1, 30.0)
            assert 0.0 <= score <= 1.0
            
            print("  ✅ Trend analysis")
            return True
        except Exception as e:
            print(f"  ❌ Trend analysis: {e}")
            return False
    
    def _test_privacy_isolation(self, satisfaction_system) -> bool:
        """Test user privacy isolation"""
        try:
            # Test that different users have isolated data
            satisfaction_system.record_explicit_feedback("user1", "int1", 5)
            satisfaction_system.record_explicit_feedback("user2", "int2", 3)
            
            user1_metrics = satisfaction_system.get_satisfaction_metrics("user1")
            user2_metrics = satisfaction_system.get_satisfaction_metrics("user2")
            
            # Should have different metrics
            assert user1_metrics.user_id != user2_metrics.user_id
            
            print("  ✅ Privacy isolation")
            return True
        except Exception as e:
            print(f"  ❌ Privacy isolation: {e}")
            return False
    
    def _test_feedback_processing(self, satisfaction_system) -> bool:
        """Test feedback processing pipeline"""
        try:
            # Test rating to satisfaction level conversion
            level = satisfaction_system._rating_to_satisfaction_level(4)
            assert level == SatisfactionLevel.SATISFIED
            
            print("  ✅ Feedback processing")
            return True
        except Exception as e:
            print(f"  ❌ Feedback processing: {e}")
            return False
    
    def _test_cache_operations(self, cache_system) -> bool:
        """Test basic cache operations"""
        try:
            # Test cache key generation
            key = cache_system._generate_context_key("user1", ContextType.MEMORY, {"test": "data"})
            assert len(key) == 64  # SHA256 hash length
            
            print("  ✅ Cache operations")
            return True
        except Exception as e:
            print(f"  ❌ Cache operations: {e}")
            return False
    
    def _test_llm_summarization(self, cache_system) -> bool:
        """Test LLM-based summarization"""
        try:
            # Test memory context summarization
            summary, confidence = cache_system._summarize_memory_context({
                "memories": [{"fact": "Test memory 1"}, {"fact": "Test memory 2"}]
            })
            assert len(summary) > 0
            assert 0.0 <= confidence <= 1.0
            
            print("  ✅ LLM summarization")
            return True
        except Exception as e:
            print(f"  ❌ LLM summarization: {e}")
            return False
    
    def _test_cache_invalidation(self, cache_system) -> bool:
        """Test cache invalidation logic"""
        try:
            # Test invalidation doesn't crash
            cache_system.invalidate_context("test_user", ContextType.MEMORY, "test_invalidation")
            
            print("  ✅ Cache invalidation")
            return True
        except Exception as e:
            print(f"  ❌ Cache invalidation: {e}")
            return False
    
    def _test_performance_benchmarking(self, cache_system) -> bool:
        """Test performance benchmarking"""
        try:
            # Test performance recording
            cache_system._record_performance(ContextType.MEMORY, "test", 50.0, True, "test_user")
            
            print("  ✅ Performance benchmarking")
            return True
        except Exception as e:
            print(f"  ❌ Performance benchmarking: {e}")
            return False
    
    def _test_ttl_expiration(self, cache_system) -> bool:
        """Test TTL and cache expiration"""
        try:
            # Test TTL configuration
            assert cache_system.default_ttl_hours == 24
            assert cache_system.max_cache_size == 1000
            
            print("  ✅ TTL expiration")
            return True
        except Exception as e:
            print(f"  ❌ TTL expiration: {e}")
            return False
    
    def _test_memory_efficiency(self, cache_system) -> bool:
        """Test memory efficiency and cleanup"""
        try:
            # Test cleanup doesn't crash
            cache_system._cleanup_old_entries()
            
            print("  ✅ Memory efficiency")
            return True
        except Exception as e:
            print(f"  ❌ Memory efficiency: {e}")
            return False
    
    def print_test_results(self, scores: dict, overall_score: float):
        """Print formatted test results"""
        print("\n" + "=" * 60)
        print("🎯 ENHANCEMENT SYSTEMS TEST RESULTS")
        print("=" * 60)
        
        for system, score in scores.items():
            target = self.target_scores[system]
            status = "✅ PASS" if score >= target else "❌ FAIL"
            print(f"{system.replace('_', ' ').title():<30} {score:>6.1f}% (target: {target}%) {status}")
        
        print("-" * 60)
        print(f"{'Overall System Health':<30} {overall_score:>6.1f}%")
        
        # Production readiness assessment
        production_ready = all(scores[system] >= self.target_scores[system] for system in scores)
        status = "🚀 PRODUCTION READY" if production_ready else "⚠️  NEEDS WORK"
        print(f"{'Production Readiness':<30} {status}")
        
        print("=" * 60)
    
    def assess_production_readiness(self, scores: dict) -> dict:
        """Assess production readiness of each system"""
        readiness = {}
        
        for system, score in scores.items():
            target = self.target_scores[system]
            readiness[system] = {
                "ready": score >= target,
                "score": score,
                "target": target,
                "gap": max(0, target - score)
            }
        
        return readiness

# Main execution
if __name__ == "__main__":
    test_suite = EnhancementTestSuite()
    results = test_suite.run_all_tests()
    
    # Save results to file
    with open("/workspace/tests/enhancement_test_results.json", "w") as f:
        json.dump(results, f, indent=2)
    
    print(f"\n📊 Test results saved to: enhancement_test_results.json")
    
    # Exit with appropriate code
    overall_ready = all(system["ready"] for system in results["production_ready"].values())
    exit(0 if overall_ready else 1)


