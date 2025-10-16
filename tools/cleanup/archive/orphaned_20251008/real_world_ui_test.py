#!/usr/bin/env python3
"""
Real-World UI Chat Test
=======================

Test enhancement systems with real-world questions through UI chat.
"""

import requests
import json
import time
from datetime import datetime

def test_real_world_scenarios():
    """Test with real-world questions users would actually ask"""
    print("🌍 REAL-WORLD UI CHAT TEST - ENHANCEMENT SYSTEMS")
    print("=" * 65)
    
    base_url = "http://localhost:8000"
    test_user = f"real_world_user_{int(time.time())}"
    
    # Real-world test scenarios
    scenarios = [
        {
            "category": "Temporal Memory",
            "question": "What did we talk about yesterday? Can you remember our previous conversation about the project timeline?",
            "expected_behavior": "Should reference previous conversations, show temporal awareness",
            "success_criteria": ["previous", "yesterday", "remember", "conversation", "timeline"]
        },
        {
            "category": "Multi-Expert Coordination", 
            "question": "I need to plan my week. Can you schedule a team meeting for Thursday at 2pm, add 'prepare quarterly report' to my task list, and remember that Q4 planning is my top priority?",
            "expected_behavior": "Should coordinate calendar, lists, and memory experts",
            "success_criteria": ["schedule", "meeting", "task", "list", "remember", "priority", "quarterly"]
        },
        {
            "category": "Self-Awareness & Learning",
            "question": "How are you feeling today? What have you learned from helping users with these new enhancement systems?",
            "expected_behavior": "Should show self-awareness and learning from interactions",
            "success_criteria": ["feeling", "learned", "helping", "users", "enhancement", "systems"]
        },
        {
            "category": "Complex Problem Solving",
            "question": "I'm stressed about an upcoming presentation. Can you help me organize my thoughts, schedule practice time, and remind me of key points I should cover?",
            "expected_behavior": "Should coordinate multiple systems to provide comprehensive help",
            "success_criteria": ["organize", "schedule", "practice", "remind", "presentation", "help"]
        },
        {
            "category": "Temporal Context Awareness",
            "question": "Based on our conversation history, what topics do I seem most interested in? What patterns do you notice in my questions?",
            "expected_behavior": "Should analyze conversation patterns and show temporal intelligence",
            "success_criteria": ["conversation", "history", "topics", "interested", "patterns", "notice"]
        },
        {
            "category": "Satisfaction and Adaptation",
            "question": "How satisfied do you think I am with your responses? Are you adapting to my communication style?",
            "expected_behavior": "Should reference satisfaction tracking and adaptation capabilities",
            "success_criteria": ["satisfied", "responses", "adapting", "communication", "style"]
        }
    ]
    
    print(f"\n👤 Testing as user: {test_user}")
    print(f"🕐 Test time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    results = {}
    total_quality_score = 0
    
    for i, scenario in enumerate(scenarios, 1):
        print(f"\n" + "="*65)
        print(f"🧪 SCENARIO {i}: {scenario['category']}")
        print("="*65)
        print(f"❓ User Question: {scenario['question']}")
        print(f"🎯 Expected: {scenario['expected_behavior']}")
        
        try:
            start_time = time.time()
            response = requests.post(f"{base_url}/api/chat",
                json={"message": scenario['question']},
                params={"user_id": test_user},
                timeout=25
            )
            response_time = time.time() - start_time
            
            if response.status_code == 200:
                data = response.json()
                response_text = data.get('response', '')
                
                # Analyze response quality
                keyword_matches = sum(1 for keyword in scenario['success_criteria'] 
                                    if keyword.lower() in response_text.lower())
                
                # Quality metrics
                is_detailed = len(response_text) > 100
                is_conversational = any(word in response_text.lower() for word in ["i", "me", "my", "you", "we", "feel"])
                has_enhancement_awareness = any(word in response_text.lower() for word in ["temporal", "memory", "enhancement", "system"])
                
                # Calculate quality score
                quality_score = (
                    (keyword_matches / len(scenario['success_criteria'])) * 40 +  # 40% for keyword relevance
                    (30 if is_detailed else 0) +  # 30% for detailed response
                    (20 if is_conversational else 0) +  # 20% for conversational tone
                    (10 if has_enhancement_awareness else 0)  # 10% for enhancement awareness
                )
                
                print(f"\n🤖 Zoe's Response ({response_time:.2f}s):")
                print(f"   {response_text}")
                
                print(f"\n📊 Response Analysis:")
                print(f"   ⏱️ Response Time: {response_time:.2f}s")
                print(f"   📝 Length: {len(response_text)} characters")
                print(f"   🎯 Keyword Matches: {keyword_matches}/{len(scenario['success_criteria'])} ({keyword_matches/len(scenario['success_criteria'])*100:.1f}%)")
                print(f"   💬 Conversational: {'✅' if is_conversational else '❌'}")
                print(f"   📄 Detailed: {'✅' if is_detailed else '❌'}")
                print(f"   🌟 Enhancement Aware: {'✅' if has_enhancement_awareness else '❌'}")
                print(f"   🏆 Quality Score: {quality_score:.1f}/100")
                
                results[scenario['category']] = {
                    "success": True,
                    "response_time": response_time,
                    "response_length": len(response_text),
                    "keyword_matches": keyword_matches,
                    "total_keywords": len(scenario['success_criteria']),
                    "quality_score": quality_score,
                    "is_conversational": is_conversational,
                    "is_detailed": is_detailed,
                    "has_enhancement_awareness": has_enhancement_awareness,
                    "response_text": response_text
                }
                
                total_quality_score += quality_score
                
                # Immediate assessment
                if quality_score >= 80:
                    print("   🎉 EXCELLENT RESPONSE!")
                elif quality_score >= 60:
                    print("   ✅ GOOD RESPONSE")
                elif quality_score >= 40:
                    print("   ⚠️ ADEQUATE RESPONSE")
                else:
                    print("   ❌ NEEDS IMPROVEMENT")
            else:
                print(f"\n❌ Request failed: {response.status_code}")
                print(f"   Error: {response.text}")
                results[scenario['category']] = {"success": False, "error": response.text}
        except Exception as e:
            print(f"\n❌ Scenario error: {e}")
            results[scenario['category']] = {"success": False, "error": str(e)}
    
    # Final Assessment
    print("\n" + "="*65)
    print("🎯 REAL-WORLD UI CHAT TEST RESULTS")
    print("="*65)
    
    successful_scenarios = sum(1 for result in results.values() if result.get("success", False))
    total_scenarios = len(scenarios)
    success_rate = (successful_scenarios / total_scenarios) * 100
    
    average_quality = total_quality_score / len([r for r in results.values() if r.get("success", False)]) if successful_scenarios > 0 else 0
    
    for category, result in results.items():
        if result.get("success", False):
            quality = result.get("quality_score", 0)
            status = "🎉 EXCELLENT" if quality >= 80 else "✅ GOOD" if quality >= 60 else "⚠️ ADEQUATE" if quality >= 40 else "❌ POOR"
            print(f"{category:<30} {status} ({quality:.1f}/100)")
        else:
            print(f"{category:<30} ❌ FAILED")
    
    print("-" * 65)
    print(f"Success Rate:                  {success_rate:.1f}% ({successful_scenarios}/{total_scenarios})")
    print(f"Average Quality Score:         {average_quality:.1f}/100")
    
    # 100% Certification Assessment
    certification_score = (success_rate * 0.6) + (average_quality * 0.4)  # Weight success and quality
    
    print(f"100% Certification Score:      {certification_score:.1f}/100")
    
    if certification_score >= 95:
        print("\n🎉 ACHIEVEMENT: 100% CERTIFICATION ACHIEVED!")
        certification = "PERFECT"
    elif certification_score >= 90:
        print("\n🌟 ACHIEVEMENT: 95%+ EXCELLENT FUNCTIONALITY!")
        certification = "EXCELLENT"
    elif certification_score >= 80:
        print("\n✅ ACHIEVEMENT: 90%+ VERY GOOD FUNCTIONALITY!")
        certification = "VERY_GOOD"
    elif certification_score >= 70:
        print("\n⚠️ ACHIEVEMENT: 80%+ GOOD FUNCTIONALITY!")
        certification = "GOOD"
    else:
        print("\n❌ NEEDS SIGNIFICANT IMPROVEMENT")
        certification = "NEEDS_WORK"
    
    # Specific feedback
    print(f"\n🔍 SPECIFIC FEEDBACK:")
    if average_quality < 50:
        print("   ⚠️ Chat responses are too simplified - need full AI integration")
    if success_rate < 100:
        print("   ⚠️ Some scenarios failing - need to fix error handling")
    if certification_score >= 95:
        print("   🎉 All systems working perfectly through UI chat!")
    
    return {
        "results": results,
        "success_rate": success_rate,
        "average_quality": average_quality,
        "certification_score": certification_score,
        "certification": certification,
        "ready_for_100_percent": certification_score >= 95
    }

if __name__ == "__main__":
    test_results = test_real_world_scenarios()
    
    # Save detailed results
    with open('/home/pi/real_world_ui_test_results.json', 'w') as f:
        json.dump(test_results, f, indent=2)
    
    print(f"\n📊 Detailed results saved to: real_world_ui_test_results.json")
    
    if test_results["ready_for_100_percent"]:
        print("\n🚀 READY FOR 100% CERTIFICATION!")
    else:
        needed = 95 - test_results["certification_score"]
        print(f"\n⚠️ Need {needed:.1f} more points for 100% certification")
    
    exit(0 if test_results["certification_score"] >= 90 else 1)


