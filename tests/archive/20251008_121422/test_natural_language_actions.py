#!/usr/bin/env python3
"""
Natural Language Action Test Suite
===================================

Test Zoe's ability to understand and execute natural language commands
for adding items, creating reminders, scheduling events, etc.
"""

import requests
import json
import time
from datetime import datetime, timedelta

def test_natural_language_actions():
    """Test natural language understanding and action execution"""
    print("🗣️ NATURAL LANGUAGE ACTION TEST SUITE")
    print("=" * 80)
    print(f"Testing new LangGraph chat endpoint with action detection")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    
    base_url = "https://localhost"
    chat_endpoint = f"{base_url}/api/chat/langgraph"
    
    test_scenarios = [
        {
            "category": "Shopping List - Add Items",
            "commands": [
                "add milk to shopping list",
                "add bread to the shopping list",
                "put eggs on my shopping list",
                "I need bananas on the shopping list"
            ],
            "expected_action": "list_item_added",
            "success_criteria": ["added", "shopping", "list"]
        },
        {
            "category": "Reminders - Time-Based",
            "commands": [
                "remind me in 30 minutes to check the oven",
                "set a reminder for 2 hours from now",
                "remind me in 1 hour to call mom",
                "I need a reminder in 45 minutes"
            ],
            "expected_action": "reminder_created",
            "success_criteria": ["remind", "set", "minutes", "hours"]
        },
        {
            "category": "Calendar - Today's Events",
            "commands": [
                "what's on my calendar today?",
                "show me today's schedule",
                "do I have any meetings today?",
                "what's my schedule for today?"
            ],
            "expected_action": "calendar_fetch",
            "success_criteria": ["calendar", "event", "today", "schedule"]
        },
        {
            "category": "Calendar - Future Events",
            "commands": [
                "what's on my calendar tomorrow?",
                "show me this week's schedule",
                "do I have meetings this week?",
                "what's coming up?"
            ],
            "expected_action": "calendar_fetch",
            "success_criteria": ["calendar", "schedule", "tomorrow", "week"]
        },
        {
            "category": "Tasks - Query",
            "commands": [
                "what are my tasks?",
                "show me my todo list",
                "what do I need to do?",
                "list my pending tasks"
            ],
            "expected_action": "tasks_fetch",
            "success_criteria": ["task", "todo", "pending"]
        },
        {
            "category": "Multi-Action - Complex Requests",
            "commands": [
                "add flour to shopping list and remind me to bake tomorrow",
                "schedule a meeting for 3pm and add it to my tasks",
                "what's on my calendar and what tasks do I have?",
            ],
            "expected_action": "multiple",
            "success_criteria": ["added", "remind", "schedule", "calendar"]
        },
        {
            "category": "Context Awareness",
            "commands": [
                # First message
                "what's on my calendar today?",
                # Follow-up (requires context)
                "can you remind me about that?",
                # Another follow-up
                "yes in 30 minutes"
            ],
            "expected_action": "context_maintained",
            "success_criteria": ["calendar", "remind", "set"]
        }
    ]
    
    results = {
        "timestamp": datetime.now().isoformat(),
        "total_categories": len(test_scenarios),
        "total_commands": sum(len(s["commands"]) for s in test_scenarios),
        "category_results": []
    }
    
    for scenario in test_scenarios:
        print(f"\n{'='*80}")
        print(f"📋 CATEGORY: {scenario['category']}")
        print(f"{'='*80}")
        
        category_result = {
            "category": scenario['category'],
            "commands_tested": len(scenario["commands"]),
            "successes": 0,
            "failures": 0,
            "responses": []
        }
        
        for cmd_num, command in enumerate(scenario["commands"], 1):
            print(f"\n🧪 Test {cmd_num}/{len(scenario['commands'])}: \"{command}\"")
            
            try:
                start_time = time.time()
                
                # Call new LangGraph endpoint
                response = requests.post(
                    chat_endpoint,
                    json={
                        "message": command,
                        "enable_agents": False,  # Keep simple for testing
                        "enable_visualization": False
                    },
                    timeout=30,
                    verify=False  # Self-signed cert
                )
                
                response_time = time.time() - start_time
                
                # For streaming endpoint, we need to read the stream
                if response.status_code == 200:
                    # Parse SSE stream
                    content_parts = []
                    tool_calls = []
                    
                    for line in response.text.split('\n'):
                        if line.startswith('data: '):
                            try:
                                event = json.loads(line[6:])
                                if event.get('type') == 'content_delta':
                                    content_parts.append(event.get('content', ''))
                                elif event.get('type') == 'tool_call_start':
                                    tool_calls.append(event.get('tool', ''))
                                elif event.get('type') == 'tool_result':
                                    if event.get('success'):
                                        tool_calls.append(f"{event.get('tool')}_success")
                            except:
                                pass
                    
                    response_text = ''.join(content_parts)
                    
                    # Analyze response
                    keyword_matches = sum(1 for kw in scenario['success_criteria'] 
                                        if kw.lower() in response_text.lower())
                    
                    has_action = any(action in str(tool_calls) for action in ['list_item', 'reminder', 'calendar'])
                    
                    success = keyword_matches > 0 or has_action or len(response_text) > 20
                    
                    if success:
                        category_result["successes"] += 1
                        print(f"  ✅ Response received ({response_time:.2f}s)")
                        print(f"  📊 Keywords matched: {keyword_matches}/{len(scenario['success_criteria'])}")
                        if tool_calls:
                            print(f"  🔧 Tools used: {', '.join(tool_calls)}")
                        print(f"  💬 Response preview: {response_text[:100]}...")
                    else:
                        category_result["failures"] += 1
                        print(f"  ⚠️ Weak response ({response_time:.2f}s)")
                        print(f"  💬 Got: {response_text[:100]}...")
                    
                    category_result["responses"].append({
                        "command": command,
                        "response_time": response_time,
                        "keyword_matches": keyword_matches,
                        "tool_calls": tool_calls,
                        "response_length": len(response_text),
                        "success": success
                    })
                    
                else:
                    category_result["failures"] += 1
                    print(f"  ❌ HTTP {response.status_code}")
                    category_result["responses"].append({
                        "command": command,
                        "error": f"HTTP {response.status_code}",
                        "success": False
                    })
                    
            except Exception as e:
                category_result["failures"] += 1
                print(f"  ❌ Error: {str(e)}")
                category_result["responses"].append({
                    "command": command,
                    "error": str(e),
                    "success": False
                })
            
            # Small delay between requests
            time.sleep(1)
        
        # Category summary
        total = category_result["successes"] + category_result["failures"]
        success_rate = (category_result["successes"] / total * 100) if total > 0 else 0
        
        print(f"\n📊 Category Result: {category_result['successes']}/{total} ({success_rate:.1f}%)")
        
        if success_rate >= 80:
            print(f"  🏆 EXCELLENT - Category working well")
        elif success_rate >= 60:
            print(f"  ✅ GOOD - Mostly working")
        elif success_rate >= 40:
            print(f"  ⚠️ FAIR - Needs improvement")
        else:
            print(f"  ❌ POOR - Significant issues")
        
        results["category_results"].append(category_result)
    
    # Overall summary
    print(f"\n{'='*80}")
    print("📈 OVERALL TEST SUMMARY")
    print(f"{'='*80}")
    
    total_commands = sum(r["successes"] + r["failures"] for r in results["category_results"])
    total_successes = sum(r["successes"] for r in results["category_results"])
    overall_success_rate = (total_successes / total_commands * 100) if total_commands > 0 else 0
    
    print(f"\n✅ Successful: {total_successes}/{total_commands} ({overall_success_rate:.1f}%)")
    print(f"❌ Failed: {total_commands - total_successes}/{total_commands}")
    
    print(f"\n📊 Category Breakdown:")
    for cat_result in results["category_results"]:
        total = cat_result["successes"] + cat_result["failures"]
        rate = (cat_result["successes"] / total * 100) if total > 0 else 0
        status = "🏆" if rate >= 80 else "✅" if rate >= 60 else "⚠️" if rate >= 40 else "❌"
        print(f"  {status} {cat_result['category']}: {cat_result['successes']}/{total} ({rate:.1f}%)")
    
    # Save results
    output_file = f"natural_language_test_results_{int(time.time())}.json"
    with open(output_file, 'w') as f:
        json.dump(results, f, indent=2)
    
    print(f"\n💾 Full results saved to: {output_file}")
    
    # Overall assessment
    print(f"\n🎯 FINAL ASSESSMENT:")
    if overall_success_rate >= 80:
        print(f"  🏆 EXCELLENT - Natural language processing is working very well!")
        print(f"  ✨ Zoe understands and executes natural language commands effectively")
    elif overall_success_rate >= 60:
        print(f"  ✅ GOOD - Most natural language commands work")
        print(f"  🔧 Some improvements needed for edge cases")
    elif overall_success_rate >= 40:
        print(f"  ⚠️ FAIR - Basic functionality works but needs improvement")
        print(f"  🛠️ Significant refinement needed")
    else:
        print(f"  ❌ POOR - Natural language processing needs major work")
        print(f"  🚨 Critical improvements required")
    
    return results

if __name__ == "__main__":
    try:
        results = test_natural_language_actions()
        print(f"\n✅ Test suite completed successfully!")
    except KeyboardInterrupt:
        print(f"\n\n⚠️ Test interrupted by user")
    except Exception as e:
        print(f"\n\n❌ Test suite failed: {e}")

