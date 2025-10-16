#!/usr/bin/env python3
"""
End-to-End Test: Chat → Feedback → Training Data Collection
Tests the complete feedback loop that feeds overnight training
"""

import sys
import asyncio
import json
sys.path.append('/home/pi/zoe/services/zoe-core')
sys.path.append('/app')

print("\n" + "="*70)
print("  TESTING COMPLETE FEEDBACK SYSTEM")
print("="*70 + "\n")

async def test_complete_feedback_workflow():
    """Test the full workflow from chat to training collection"""
    
    results = {}
    
    # Test 1: Verify feedback buttons exist in chat.html
    print("1️⃣  Testing Chat UI...")
    try:
        with open('/home/pi/zoe/services/zoe-ui/dist/chat.html', 'r') as f:
            chat_html = f.read()
            
        has_buttons = all([
            'feedbackPositive' in chat_html,
            'feedbackNegative' in chat_html,
            'correctResponse' in chat_html,
            '👍 Good' in chat_html,
            '👎 Bad' in chat_html,
            '✏️ Correct' in chat_html
        ])
        
        has_api_calls = all([
            '/api/chat/feedback/' in chat_html,
            'feedback_type=thumbs_up' in chat_html,
            'feedback_type=thumbs_down' in chat_html,
            'feedback_type=correction' in chat_html
        ])
        
        if has_buttons and has_api_calls:
            print("   ✅ Chat UI has feedback buttons connected to API")
            results['chat_ui'] = True
        else:
            print("   ❌ Chat UI missing buttons or API calls")
            results['chat_ui'] = False
    except Exception as e:
        print(f"   ❌ Chat UI test failed: {e}")
        results['chat_ui'] = False
    
    # Test 2: Verify backend feedback endpoint exists
    print("\n2️⃣  Testing Backend Endpoint...")
    try:
        with open('/home/pi/zoe/services/zoe-core/routers/chat.py', 'r') as f:
            chat_router = f.read()
        
        has_endpoint = '@router.post("/api/chat/feedback/{interaction_id}")' in chat_router
        has_function = 'async def provide_feedback' in chat_router
        
        if has_endpoint and has_function:
            print("   ✅ Backend feedback endpoint exists")
            results['backend_endpoint'] = True
        else:
            print("   ❌ Backend feedback endpoint missing")
            results['backend_endpoint'] = False
    except Exception as e:
        print(f"   ❌ Backend test failed: {e}")
        results['backend_endpoint'] = False
    
    # Test 3: Verify training collector is initialized
    print("\n3️⃣  Testing Training Collector...")
    try:
        from training_engine.data_collector import training_collector
        
        # Test database exists
        import os
        db_exists = os.path.exists('/app/data/training.db')
        
        if db_exists:
            print("   ✅ Training database exists")
            results['training_db'] = True
        else:
            print("   ⚠️  Training database not found (will be created on first use)")
            results['training_db'] = False
        
        # Test collector methods exist
        has_methods = all([
            hasattr(training_collector, 'log_interaction'),
            hasattr(training_collector, 'record_positive_feedback'),
            hasattr(training_collector, 'record_negative_feedback'),
            hasattr(training_collector, 'record_correction')
        ])
        
        if has_methods:
            print("   ✅ Training collector has all required methods")
            results['training_collector'] = True
        else:
            print("   ❌ Training collector missing methods")
            results['training_collector'] = False
            
    except Exception as e:
        print(f"   ❌ Training collector test failed: {e}")
        results['training_collector'] = False
    
    # Test 4: Test interaction logging
    print("\n4️⃣  Testing Interaction Logging...")
    try:
        from training_engine.data_collector import training_collector
        
        # Log a test interaction
        interaction_id = await training_collector.log_interaction({
            "message": "test feedback system",
            "response": "testing feedback collection",
            "context": {},
            "routing_type": "test",
            "model_used": "test-model",
            "user_id": "test-feedback"
        })
        
        print(f"   ✅ Logged interaction: {interaction_id}")
        results['interaction_logging'] = True
        
        # Test 5: Test positive feedback
        print("\n5️⃣  Testing Positive Feedback...")
        await training_collector.record_positive_feedback(interaction_id)
        print("   ✅ Positive feedback recorded")
        results['positive_feedback'] = True
        
        # Test 6: Test negative feedback
        print("\n6️⃣  Testing Negative Feedback...")
        test_id_2 = await training_collector.log_interaction({
            "message": "another test",
            "response": "another response",
            "context": {},
            "routing_type": "test",
            "model_used": "test-model",
            "user_id": "test-feedback"
        })
        await training_collector.record_negative_feedback(test_id_2)
        print("   ✅ Negative feedback recorded")
        results['negative_feedback'] = True
        
        # Test 7: Test correction
        print("\n7️⃣  Testing Correction Feedback...")
        test_id_3 = await training_collector.log_interaction({
            "message": "third test",
            "response": "wrong response",
            "context": {},
            "routing_type": "test",
            "model_used": "test-model",
            "user_id": "test-feedback"
        })
        await training_collector.record_correction(test_id_3, "correct response")
        print("   ✅ Correction recorded")
        results['correction_feedback'] = True
        
        # Test 8: Get stats
        print("\n8️⃣  Testing Training Stats...")
        stats = await training_collector.get_stats("test-feedback")
        print(f"   ✅ Stats retrieved: {stats}")
        results['training_stats'] = True
        
    except Exception as e:
        print(f"   ❌ Feedback testing failed: {e}")
        import traceback
        traceback.print_exc()
        results['interaction_logging'] = False
    
    # Test 9: Verify chat endpoint returns interaction_id
    print("\n9️⃣  Testing Chat Endpoint Returns Interaction ID...")
    try:
        # Check chat.py for interaction_id in response
        with open('/home/pi/zoe/services/zoe-core/routers/chat.py', 'r') as f:
            chat_code = f.read()
        
        has_interaction_id = '"interaction_id": interaction_id' in chat_code or \
                            "'interaction_id': interaction_id" in chat_code
        
        if has_interaction_id:
            print("   ✅ Chat endpoint returns interaction_id")
            results['interaction_id_return'] = True
        else:
            print("   ⚠️  Chat endpoint may not return interaction_id")
            results['interaction_id_return'] = False
    except Exception as e:
        print(f"   ❌ Chat endpoint test failed: {e}")
        results['interaction_id_return'] = False
    
    # Test 10: Check cron job for training
    print("\n🔟 Testing Nightly Training Schedule...")
    try:
        import subprocess
        result = subprocess.run(['crontab', '-l'], capture_output=True, text=True)
        cron_output = result.stdout
        
        has_training_cron = 'nightly_training' in cron_output
        has_consolidation_cron = 'daily_consolidation' in cron_output
        has_preference_cron = 'weekly_preference' in cron_output
        
        if has_training_cron:
            print("   ✅ Nightly training cron job scheduled")
            results['training_cron'] = True
        else:
            print("   ⚠️  Nightly training cron job not found")
            results['training_cron'] = False
            
        if has_consolidation_cron:
            print("   ✅ Memory consolidation scheduled")
        if has_preference_cron:
            print("   ✅ Preference updates scheduled")
            
    except Exception as e:
        print(f"   ❌ Cron test failed: {e}")
        results['training_cron'] = False
    
    # Summary
    print("\n" + "="*70)
    print("  TEST RESULTS")
    print("="*70)
    
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    
    for test_name, result in results.items():
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"  {test_name:30} {status}")
    
    print("\n" + "="*70)
    print(f"  FINAL SCORE: {passed}/{total} tests passed ({(passed/total)*100:.0f}%)")
    print("="*70 + "\n")
    
    if passed == total:
        print("🎉 PERFECT! Complete feedback system is operational!")
        print("\n✨ What this means:")
        print("   - Users can click 👍 👎 ✏️ buttons in chat")
        print("   - Feedback is sent to backend API")
        print("   - Data is collected in training database")
        print("   - Tonight at 10pm, Zoe will train on this data")
        print("   - Tomorrow morning, smarter Zoe!")
        return 0
    else:
        print(f"⚠️  {total - passed} test(s) failed")
        print("\nMost critical: interaction_id must be returned from chat endpoint")
        print("Check: routers/chat.py line ~500-600")
        return 1

if __name__ == "__main__":
    sys.exit(asyncio.run(test_complete_feedback_workflow()))












