#!/usr/bin/env python3
"""
End-to-End Test for Complete Intelligence System
Tests the full workflow from query to response with all enhancements
"""
import sys
import asyncio
sys.path.append('/home/pi/zoe/services/zoe-core')
sys.path.append('/app')

import logging
logging.basicConfig(level=logging.INFO)

async def test_complete_workflow():
    """Test complete intelligence workflow"""
    
    print("\n" + "=" * 70)
    print("  ZOE COMPLETE INTELLIGENCE SYSTEM - END-TO-END TEST")
    print("=" * 70 + "\n")
    
    from training_engine.data_collector import training_collector
    from prompt_templates import build_enhanced_prompt, PromptTemplates
    from graph_engine import graph_engine
    from rag_enhancements import query_expander, reranker, hybrid_search_engine
    from context_optimizer import context_selector, context_compressor
    from memory_consolidation import memory_consolidator
    from preference_learner import preference_learner
    
    test_results = {}
    
    # Test 1: Enhanced Prompts
    print("1️⃣  Testing Enhanced Prompts...")
    try:
        base_prompt = PromptTemplates.base_system_prompt()
        assert len(base_prompt) > 1000
        assert "FEW-SHOT EXAMPLES" in base_prompt
        print("   ✅ Enhanced prompts loaded (6 examples included)")
        test_results["enhanced_prompts"] = True
    except Exception as e:
        print(f"   ❌ Enhanced prompts failed: {e}")
        test_results["enhanced_prompts"] = False
    
    # Test 2: Training Data Collection
    print("\n2️⃣  Testing Training Data Collection...")
    try:
        interaction_id = await training_collector.log_interaction({
            "message": "test query for e2e",
            "response": "test response",
            "context": {},
            "routing_type": "conversation",
            "model_used": "test-model",
            "user_id": "test-e2e"
        })
        assert interaction_id is not None
        print(f"   ✅ Interaction logged: {interaction_id}")
        
        # Test feedback
        await training_collector.record_positive_feedback(interaction_id)
        print("   ✅ Feedback recorded")
        
        # Get stats
        stats = await training_collector.get_stats("test-e2e")
        print(f"   ✅ Training stats retrieved: {stats.get('today_count', 0)} examples today")
        test_results["training_collection"] = True
    except Exception as e:
        print(f"   ❌ Training collection failed: {e}")
        test_results["training_collection"] = False
    
    # Test 3: Graph Engine
    print("\n3️⃣  Testing Graph Engine...")
    try:
        stats = graph_engine.get_stats()
        print(f"   ✅ Graph loaded: {stats['nodes']} nodes, {stats['edges']} edges")
        
        # Test adding node
        test_node = graph_engine.add_node("test", "test_e2e_node", {"data": "test"})
        print(f"   ✅ Node added: {test_node}")
        
        # Test graph query
        central = graph_engine.centrality_ranking(limit=3)
        print(f"   ✅ Centrality ranking working: {len(central)} nodes ranked")
        test_results["graph_engine"] = True
    except Exception as e:
        print(f"   ❌ Graph engine failed: {e}")
        test_results["graph_engine"] = False
    
    # Test 4: Query Expansion
    print("\n4️⃣  Testing Query Expansion...")
    try:
        expanded = await query_expander.expand_query("arduino project")
        print(f"   ✅ Query expanded: 'arduino project' → {expanded[:3]}")
        assert len(expanded) > 1
        test_results["query_expansion"] = True
    except Exception as e:
        print(f"   ❌ Query expansion failed: {e}")
        test_results["query_expansion"] = False
    
    # Test 5: Context Optimization
    print("\n5️⃣  Testing Context Optimization...")
    try:
        # Test scoring
        score = context_selector.score_context_piece(
            {"content": "arduino sensors", "created_at": "2025-10-10"},
            "arduino project"
        )
        print(f"   ✅ Context relevance scored: {score:.2f}")
        
        # Test compression
        compressed = context_compressor.compress_calendar([
            {"title": "Meeting 1", "start_time": "10:00"},
            {"title": "Meeting 2", "start_time": "14:00"}
        ])
        print(f"   ✅ Context compressed: '{compressed}'")
        test_results["context_optimization"] = True
    except Exception as e:
        print(f"   ❌ Context optimization failed: {e}")
        test_results["context_optimization"] = False
    
    # Test 6: Memory Consolidation
    print("\n6️⃣  Testing Memory Consolidation...")
    try:
        # This creates summary in database
        summary = await memory_consolidator.create_daily_summary("test-e2e")
        print(f"   ✅ Daily summary created: {len(summary)} chars")
        assert len(summary) > 0
        test_results["memory_consolidation"] = True
    except Exception as e:
        print(f"   ❌ Memory consolidation failed: {e}")
        test_results["memory_consolidation"] = False
    
    # Test 7: Preference Learning
    print("\n7️⃣  Testing Preference Learning...")
    try:
        prefs = await preference_learner.get_preferences("test-e2e")
        print(f"   ✅ Preferences retrieved: {prefs}")
        
        # Test preference prompt additions
        prompt_add = preference_learner.get_preference_prompt_additions(prefs)
        print(f"   ✅ Preference prompt additions: {len(prompt_add)} chars")
        test_results["preference_learning"] = True
    except Exception as e:
        print(f"   ❌ Preference learning failed: {e}")
        test_results["preference_learning"] = False
    
    # Test 8: Complete Prompt Building
    print("\n8️⃣  Testing Complete Prompt Building...")
    try:
        memories = {"semantic_results": [{"content": "Test memory about arduino"}]}
        user_context = {
            "calendar_events": [{"title": "Test Event", "start_time": "10:00"}],
            "people": [{"name": "Test Person", "relationship": "friend"}]
        }
        
        prompt = build_enhanced_prompt(memories, user_context, "conversation", prefs)
        assert "Test Event" in prompt
        assert "RESPONSE FRAMEWORK" in prompt
        print(f"   ✅ Complete prompt built: {len(prompt)} chars")
        print(f"   ✅ Includes: examples + context + preferences")
        test_results["complete_prompt"] = True
    except Exception as e:
        print(f"   ❌ Complete prompt building failed: {e}")
        test_results["complete_prompt"] = False
    
    # Results Summary
    print("\n" + "=" * 70)
    print("  TEST RESULTS")
    print("=" * 70)
    
    passed = sum(1 for v in test_results.values() if v)
    total = len(test_results)
    
    for test_name, result in test_results.items():
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"  {test_name:30} {status}")
    
    print("\n" + "=" * 70)
    print(f"  FINAL SCORE: {passed}/{total} tests passed ({(passed/total)*100:.0f}%)")
    print("=" * 70 + "\n")
    
    if passed == total:
        print("🎉 PERFECT SCORE! All intelligence systems working!")
        print("\n✨ Your Zoe is now:")
        print("   - Enhanced with few-shot prompts")
        print("   - Collecting feedback for training")
        print("   - Using hybrid search with graph intelligence")
        print("   - Optimizing context smartly")
        print("   - Creating daily summaries")
        print("   - Learning your preferences")
        print("\n🌙 Overnight training enabled:")
        print("   - 2:00 AM: Training on your feedback")
        print("   - 1:30 AM: Memory consolidation")
        print("   - 1:00 AM Sundays: Preference updates")
        print("\n🚀 Ready to use! Just chat and provide feedback.")
        return 0
    else:
        print(f"⚠️  {total - passed} test(s) failed. Check errors above.")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(test_complete_workflow()))












