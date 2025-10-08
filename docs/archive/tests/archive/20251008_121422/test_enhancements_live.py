#!/usr/bin/env python3
"""
Live Enhancement Systems Test
============================

Test the enhancement systems in the running container without breaking the main app.
"""

import asyncio
import sys
import tempfile
import os
from datetime import datetime

async def test_enhancement_systems():
    """Test all enhancement systems in isolation"""
    print("🧪 Testing Enhancement Systems in Live Container")
    print("=" * 60)
    
    results = {}
    
    # Test 1: Temporal Memory System
    print("\n📅 Testing Temporal Memory System...")
    try:
        # Use a temporary database to avoid conflicts
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            temp_db = f.name
        
        sys.path.append('/app')
        from temporal_memory import TemporalMemorySystem
        
        temporal_system = TemporalMemorySystem(temp_db)
        
        # Test episode creation
        episode = temporal_system.create_episode("test_user", "development")
        assert episode.user_id == "test_user"
        assert episode.context_type == "development"
        
        # Test message addition
        success = temporal_system.add_message_to_episode(episode.id, "Testing temporal memory", "user")
        assert success
        
        # Test episode closure
        success = temporal_system.close_episode(episode.id)
        assert success
        
        results["temporal_memory"] = {"success": True, "message": "All tests passed"}
        print("  ✅ Temporal Memory System: WORKING")
        
        # Cleanup
        os.unlink(temp_db)
        
    except Exception as e:
        results["temporal_memory"] = {"success": False, "error": str(e)}
        print(f"  ❌ Temporal Memory System: FAILED - {e}")
    
    # Test 2: Cross-Agent Collaboration
    print("\n🤝 Testing Cross-Agent Collaboration System...")
    try:
        from cross_agent_collaboration import ExpertOrchestrator, ExpertType
        
        orchestrator = ExpertOrchestrator()
        
        # Test task decomposition
        tasks = orchestrator._simple_keyword_decomposition("Schedule a meeting and create a list")
        assert len(tasks) >= 2
        assert any("calendar" in task["expert_type"] for task in tasks)
        assert any("lists" in task["expert_type"] for task in tasks)
        
        # Test expert endpoints
        assert ExpertType.CALENDAR in orchestrator.expert_endpoints
        assert ExpertType.LISTS in orchestrator.expert_endpoints
        
        results["cross_agent_collaboration"] = {"success": True, "message": "All tests passed"}
        print("  ✅ Cross-Agent Collaboration: WORKING")
        
    except Exception as e:
        results["cross_agent_collaboration"] = {"success": False, "error": str(e)}
        print(f"  ❌ Cross-Agent Collaboration: FAILED - {e}")
    
    # Test 3: User Satisfaction System
    print("\n😊 Testing User Satisfaction System...")
    try:
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            temp_db = f.name
        
        from user_satisfaction import UserSatisfactionSystem, SatisfactionLevel
        
        satisfaction_system = UserSatisfactionSystem(temp_db)
        
        # Test explicit feedback
        feedback_id = satisfaction_system.record_explicit_feedback(
            "test_user", "interaction_123", 4, "Great response!"
        )
        assert feedback_id is not None
        
        # Test implicit analysis
        success = satisfaction_system.record_interaction(
            "interaction_456", "test_user", "Test request", "Test response", 2.5
        )
        assert success
        
        # Test metrics
        metrics = satisfaction_system.get_satisfaction_metrics("test_user")
        assert metrics is not None
        assert metrics.total_interactions > 0
        
        results["user_satisfaction"] = {"success": True, "message": "All tests passed"}
        print("  ✅ User Satisfaction System: WORKING")
        
        # Cleanup
        os.unlink(temp_db)
        
    except Exception as e:
        results["user_satisfaction"] = {"success": False, "error": str(e)}
        print(f"  ❌ User Satisfaction System: FAILED - {e}")
    
    # Test 4: Context Cache System
    print("\n🚀 Testing Context Cache System...")
    try:
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            temp_db = f.name
        
        from context_cache import ContextCacheSystem, ContextType
        
        cache_system = ContextCacheSystem(temp_db)
        
        # Test cache key generation
        key = cache_system._generate_context_key("user1", ContextType.MEMORY, {"test": "data"})
        assert len(key) == 64  # SHA256 hash length
        
        # Test summarization
        summary, confidence = cache_system._summarize_memory_context({
            "memories": [{"fact": "Test memory 1"}, {"fact": "Test memory 2"}]
        })
        assert len(summary) > 0
        assert 0.0 <= confidence <= 1.0
        
        results["context_cache"] = {"success": True, "message": "All tests passed"}
        print("  ✅ Context Cache System: WORKING")
        
        # Cleanup
        os.unlink(temp_db)
        
    except Exception as e:
        results["context_cache"] = {"success": False, "error": str(e)}
        print(f"  ❌ Context Cache System: FAILED - {e}")
    
    # Summary
    print("\n" + "=" * 60)
    print("📊 LIVE TEST RESULTS")
    print("=" * 60)
    
    total_systems = len(results)
    successful_systems = sum(1 for result in results.values() if result["success"])
    success_rate = (successful_systems / total_systems) * 100
    
    for system_name, result in results.items():
        status = "✅ PASS" if result["success"] else "❌ FAIL"
        print(f"{system_name.replace('_', ' ').title():<30} {status}")
        if not result["success"]:
            print(f"  Error: {result['error']}")
    
    print("-" * 60)
    print(f"Success Rate: {success_rate:.1f}% ({successful_systems}/{total_systems})")
    
    if success_rate == 100:
        print("🎉 ALL ENHANCEMENT SYSTEMS WORKING IN LIVE CONTAINER!")
        return True
    else:
        print("⚠️  SOME SYSTEMS NEED FIXES")
        return False

if __name__ == "__main__":
    success = asyncio.run(test_enhancement_systems())
    exit(0 if success else 1)


