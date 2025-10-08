#!/usr/bin/env python3
"""
Complete Web UI Testing for Enhancement Systems
===============================================

Test all enhancement systems as a real user would through the web interface.
"""

import requests
import json
import time
from datetime import datetime, timedelta

def test_as_real_user():
    """Test enhancement systems as a real user would"""
    print("👤 TESTING AS REAL USER - WEB UI ENHANCEMENT SYSTEMS")
    print("=" * 65)
    
    base_url = "http://localhost:8000"
    test_user = "real_user_demo"
    
    # Simulate a real user session
    session_results = {}
    
    print(f"\n🎬 Starting user session as: {test_user}")
    print(f"🕐 Session time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Scenario 1: User asks about temporal memory
    print("\n" + "="*50)
    print("📅 SCENARIO 1: Testing Temporal Memory")
    print("User asks: 'Can you remember our conversation from earlier?'")
    print("="*50)
    
    try:
        start_time = time.time()
        response = requests.post(f"{base_url}/api/chat",
            json={"message": "Can you remember our conversation from earlier? What enhancement systems are we testing?"},
            params={"user_id": test_user},
            timeout=10
        )
        response_time = time.time() - start_time
        
        if response.status_code == 200:
            data = response.json()
            response_text = data.get('response', '')
            
            print(f"✅ Zoe Response ({response_time:.2f}s):")
            print(f"   {response_text}")
            
            # Check if response shows temporal awareness
            temporal_keywords = ["earlier", "conversation", "remember", "discussed", "before"]
            temporal_awareness = sum(1 for keyword in temporal_keywords if keyword.lower() in response_text.lower())
            
            session_results["temporal_memory_query"] = {
                "success": True,
                "response_time": response_time,
                "temporal_awareness_score": temporal_awareness,
                "response_length": len(response_text)
            }
            
            print(f"📊 Temporal Awareness Score: {temporal_awareness}/5")
        else:
            print(f"❌ Request failed: {response.status_code}")
            session_results["temporal_memory_query"] = {"success": False, "error": response.text}
    except Exception as e:
        print(f"❌ Error: {e}")
        session_results["temporal_memory_query"] = {"success": False, "error": str(e)}
    
    # Scenario 2: User requests complex multi-step task
    print("\n" + "="*50)
    print("🤝 SCENARIO 2: Testing Cross-Agent Orchestration")
    print("User asks: 'Help me plan my day tomorrow'")
    print("="*50)
    
    try:
        start_time = time.time()
        response = requests.post(f"{base_url}/api/chat",
            json={"message": "Help me plan my day tomorrow. I need to schedule a team meeting at 10am, add 'review quarterly reports' to my task list, and remember that Q4 planning is my top priority this week."},
            params={"user_id": test_user},
            timeout=15
        )
        response_time = time.time() - start_time
        
        if response.status_code == 200:
            data = response.json()
            response_text = data.get('response', '')
            
            print(f"✅ Zoe Response ({response_time:.2f}s):")
            print(f"   {response_text}")
            
            # Check if response handles multiple tasks
            task_keywords = ["schedule", "meeting", "task", "list", "remember", "priority"]
            multi_task_handling = sum(1 for keyword in task_keywords if keyword.lower() in response_text.lower())
            
            session_results["multi_step_orchestration"] = {
                "success": True,
                "response_time": response_time,
                "multi_task_score": multi_task_handling,
                "response_length": len(response_text)
            }
            
            print(f"📊 Multi-Task Handling Score: {multi_task_handling}/6")
        else:
            print(f"❌ Request failed: {response.status_code}")
            session_results["multi_step_orchestration"] = {"success": False, "error": response.text}
    except Exception as e:
        print(f"❌ Error: {e}")
        session_results["multi_step_orchestration"] = {"success": False, "error": str(e)}
    
    # Scenario 3: User provides feedback
    print("\n" + "="*50)
    print("😊 SCENARIO 3: Testing User Satisfaction")
    print("User provides feedback on Zoe's helpfulness")
    print("="*50)
    
    try:
        # Simulate user providing feedback
        interaction_id = f'demo_interaction_{int(time.time())}'
        
        response = requests.post(f'{base_url}/api/satisfaction/feedback',
            json={
                'interaction_id': interaction_id,
                'rating': 5,
                'feedback_text': 'Zoe is incredibly helpful with the new enhancement systems!',
                'context': {'scenario': 'user_demo', 'feature_test': True}
            },
            params={'user_id': test_user},
            timeout=10
        )
        
        if response.status_code == 200:
            feedback_data = response.json()
            print(f'✅ Feedback recorded: {feedback_data[\"feedback_id\"]}')
            print(f'😊 Satisfaction Level: {feedback_data[\"satisfaction_level\"]}')
            
            # Get user's satisfaction metrics
            response = requests.get(f'{base_url}/api/satisfaction/metrics',
                params={'user_id': test_user},
                timeout=10
            )
            
            if response.status_code == 200:
                metrics = response.json()
                print(f'📊 User Metrics:')
                print(f'   Total Interactions: {metrics[\"total_interactions\"]}')
                print(f'   Average Satisfaction: {metrics[\"average_satisfaction\"]:.2f}/5.0')
                print(f'   Explicit Feedback Count: {metrics[\"explicit_feedback_count\"]}')
                
                session_results["satisfaction_feedback"] = {
                    "success": True,
                    "satisfaction_level": feedback_data["satisfaction_level"],
                    "avg_satisfaction": metrics["average_satisfaction"],
                    "total_interactions": metrics["total_interactions"]
                }
            else:
                print(f'❌ Metrics retrieval failed: {response.status_code}')
                session_results["satisfaction_feedback"] = {"success": False, "error": "Metrics failed"}
        else:
            print(f'❌ Feedback submission failed: {response.status_code}')
            session_results["satisfaction_feedback"] = {"success": False, "error": response.text}
    except Exception as e:
        print(f'❌ Error: {e}')
        session_results["satisfaction_feedback"] = {"success": False, "error": str(e)}
    
    # Scenario 4: Test Self-Awareness Integration
    print('\n' + '='*50)
    print('🧠 SCENARIO 4: Testing Self-Awareness Integration')
    print("User asks about Zoe's capabilities and feelings")
    print('='*50)
    
    try:
        start_time = time.time()
        response = requests.post(f'{base_url}/api/chat',
            json={'message': 'Tell me about yourself and your new capabilities. How do you feel about helping users with these enhancement systems?'},
            params={'user_id': test_user},
            timeout=15
        )
        response_time = time.time() - start_time
        
        if response.status_code == 200:
            data = response.json()
            response_text = data.get('response', '')
            
            print(f'✅ Zoe Response ({response_time:.2f}s):')
            print(f'   {response_text[:400]}...')
            
            # Check self-awareness indicators
            awareness_keywords = ['feel', 'help', 'capabilities', 'enhancement', 'systems', 'users']
            awareness_score = sum(1 for keyword in awareness_keywords if keyword.lower() in response_text.lower())
            
            session_results['self_awareness_integration'] = {
                'success': True,
                'response_time': response_time,
                'awareness_score': awareness_score,
                'response_length': len(response_text)
            }
            
            print(f'📊 Self-Awareness Score: {awareness_score}/6')
        else:
            print(f'❌ Request failed: {response.status_code}')
            session_results['self_awareness_integration'] = {'success': False, 'error': response.text}
    except Exception as e:
        print(f'❌ Error: {e}')
        session_results['self_awareness_integration'] = {'success': False, 'error': str(e)}
    
    # Final Assessment
    print('\n' + '='*65)
    print('🎯 REAL USER EXPERIENCE TEST RESULTS')
    print('='*65)
    
    total_scenarios = len(session_results)
    successful_scenarios = sum(1 for result in session_results.values() if result.get('success', False))
    success_rate = (successful_scenarios / total_scenarios) * 100
    
    for scenario_name, result in session_results.items():
        status = '✅ EXCELLENT' if result.get('success', False) else '❌ FAILED'
        print(f'{scenario_name.replace(\"_\", \" \").title():<35} {status}')
        
        if result.get('success', False):
            if 'response_time' in result:
                print(f'  ⏱️ Response Time: {result[\"response_time\"]:.2f}s')
            if 'awareness_score' in result:
                print(f'  🧠 Awareness Score: {result[\"awareness_score\"]}/6')
            if 'temporal_awareness_score' in result:
                print(f'  🕐 Temporal Score: {result[\"temporal_awareness_score\"]}/5')
            if 'multi_task_score' in result:
                print(f'  🤝 Multi-Task Score: {result[\"multi_task_score\"]}/6')
        else:
            error_msg = result.get('error', 'Unknown error')
            if len(error_msg) > 100:
                error_msg = error_msg[:100] + '...'
            print(f'  ❌ Error: {error_msg}')
    
    print('-' * 65)
    print(f'User Experience Success Rate: {success_rate:.1f}% ({successful_scenarios}/{total_scenarios})')
    
    # Overall assessment
    if success_rate >= 90:
        print('🎉 USER EXPERIENCE: OUTSTANDING - FULLY FUNCTIONAL')
        ux_status = 'OUTSTANDING'
    elif success_rate >= 75:
        print('✅ USER EXPERIENCE: EXCELLENT - READY FOR USERS')
        ux_status = 'EXCELLENT'
    elif success_rate >= 50:
        print('⚠️  USER EXPERIENCE: GOOD - MINOR ISSUES')
        ux_status = 'GOOD'
    else:
        print('❌ USER EXPERIENCE: NEEDS IMPROVEMENT')
        ux_status = 'NEEDS_IMPROVEMENT'
    
    return {
        'session_results': session_results,
        'success_rate': success_rate,
        'ux_status': ux_status,
        'ready_for_users': success_rate >= 75
    }

if __name__ == '__main__':
    results = test_as_real_user()
    
    # Save results
    with open('/home/pi/web_ui_test_results.json', 'w') as f:
        json.dump(results, f, indent=2)
    
    print(f'\\n📊 Complete results saved to: web_ui_test_results.json')
    
    if results['ready_for_users']:
        print('\\n🚀 FINAL STATUS: ENHANCEMENT SYSTEMS READY FOR REAL USERS!')
    else:
        print('\\n⚠️  FINAL STATUS: NEEDS MORE WORK BEFORE USER RELEASE')
"
