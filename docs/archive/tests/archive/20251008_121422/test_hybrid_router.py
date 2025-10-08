#!/usr/bin/env python3
"""
Test Hybrid Router
==================

Test the new hybrid chat router for consistent quality and enhancement integration.
"""

import requests
import json
import time
from datetime import datetime

def test_hybrid_router():
    """Test the hybrid router with all problematic question types"""
    print("🧪 TESTING HYBRID CHAT ROUTER")
    print("=" * 50)
    
    base_url = "http://localhost:8000"
    test_user = f"hybrid_test_{int(time.time())}"
    
    # Test all the question types that were problematic before
    test_scenarios = [
        {
            "name": "Temporal Memory Query",
            "question": "Can you remember what we discussed earlier? What topics have we covered?",
            "should_be_conversational": True,
            "should_mention_enhancements": True
        },
        {
            "name": "Multi-Expert Coordination",
            "question": "I need to schedule a meeting, add items to my list, and remember important details",
            "should_be_conversational": True,
            "should_mention_enhancements": True
        },
        {
            "name": "Enhancement System Query",
            "question": "Tell me about your enhancement systems and how they help users",
            "should_be_conversational": True,
            "should_mention_enhancements": True
        },
        {
            "name": "General Conversation",
            "question": "How are you doing today? What's on your mind?",
            "should_be_conversational": True,
            "should_mention_enhancements": False
        },
        {
            "name": "Complex Problem Solving",
            "question": "I'm feeling overwhelmed with work. Can you help me organize and prioritize?",
            "should_be_conversational": True,
            "should_mention_enhancements": False
        }
    ]
    
    results = []
    total_quality_score = 0
    
    for i, scenario in enumerate(test_scenarios, 1):
        print(f"\n🧪 Test {i}: {scenario['name']}")
        print(f"❓ Question: {scenario['question']}")
        
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
                
                # Quality assessment
                is_detailed = len(response_text) > 100
                is_conversational = any(word in response_text.lower() for word in ["i", "me", "my", "you", "we"])
                has_personality = any(word in response_text.lower() for word in ["feel", "think", "love", "enjoy", "excited", "glad"])
                mentions_enhancements = any(word in response_text.lower() for word in ["temporal", "memory", "collaboration", "enhancement", "system"])
                
                # Calculate quality score
                quality_score = (
                    (40 if is_detailed else 0) +
                    (30 if is_conversational else 0) +
                    (20 if has_personality else 0) +
                    (10 if mentions_enhancements and scenario['should_mention_enhancements'] else 5)
                )
                
                total_quality_score += quality_score
                
                print(f"🤖 Response ({response_time:.2f}s, {len(response_text)} chars):")
                print(f"   {response_text[:200]}...")
                
                print(f"📊 Quality Analysis:")
                print(f"   📝 Detailed: {'✅' if is_detailed else '❌'}")
                print(f"   💬 Conversational: {'✅' if is_conversational else '❌'}")
                print(f"   😊 Personality: {'✅' if has_personality else '❌'}")
                print(f"   🌟 Enhancement Aware: {'✅' if mentions_enhancements else '❌'}")
                print(f"   🏆 Quality Score: {quality_score}/100")
                
                if quality_score >= 80:
                    print("   🎉 EXCELLENT!")
                elif quality_score >= 60:
                    print("   ✅ VERY GOOD!")
                elif quality_score >= 40:
                    print("   ⚠️ GOOD")
                else:
                    print("   ❌ NEEDS IMPROVEMENT")
                
                results.append({
                    "name": scenario['name'],
                    "success": True,
                    "response_time": response_time,
                    "response_length": len(response_text),
                    "quality_score": quality_score,
                    "is_detailed": is_detailed,
                    "is_conversational": is_conversational,
                    "has_personality": has_personality,
                    "mentions_enhancements": mentions_enhancements
                })
            else:
                print(f"❌ Request failed: {response.status_code}")
                print(f"   Error: {response.text}")
                results.append({"name": scenario['name'], "success": False, "error": response.text})
        except Exception as e:
            print(f"❌ Test error: {e}")
            results.append({"name": scenario['name'], "success": False, "error": str(e)})
    
    # Final Assessment
    print("\n" + "=" * 50)
    print("🎯 HYBRID ROUTER TEST RESULTS")
    print("=" * 50)
    
    successful_tests = len([r for r in results if r.get("success", False)])
    total_tests = len(results)
    success_rate = (successful_tests / total_tests) * 100
    
    average_quality = total_quality_score / successful_tests if successful_tests > 0 else 0
    
    # Check for consistency improvements
    detailed_responses = len([r for r in results if r.get("is_detailed", False)])
    conversational_responses = len([r for r in results if r.get("is_conversational", False)])
    
    print(f"Success Rate:           {success_rate:.1f}% ({successful_tests}/{total_tests})")
    print(f"Average Quality:        {average_quality:.1f}/100")
    print(f"Detailed Responses:     {detailed_responses}/{total_tests} ({detailed_responses/total_tests*100:.0f}%)")
    print(f"Conversational:         {conversational_responses}/{total_tests} ({conversational_responses/total_tests*100:.0f}%)")
    
    # Final certification score
    certification_score = (success_rate * 0.4) + (average_quality * 0.4) + (detailed_responses/total_tests * 100 * 0.2)
    
    print(f"HYBRID ROUTER SCORE:    {certification_score:.1f}/100")
    
    if certification_score >= 95:
        print("🎉 OUTSTANDING: HYBRID ROUTER PERFECT!")
        status = "PERFECT"
    elif certification_score >= 90:
        print("🌟 EXCELLENT: HYBRID ROUTER WORKING GREAT!")
        status = "EXCELLENT"
    elif certification_score >= 80:
        print("✅ VERY GOOD: HYBRID ROUTER FUNCTIONAL!")
        status = "VERY_GOOD"
    else:
        print("⚠️ GOOD: HYBRID ROUTER NEEDS MORE WORK")
        status = "GOOD"
    
    # Compare to previous performance
    print(f"\n📈 IMPROVEMENT ANALYSIS:")
    print(f"   Previous: 51% average quality, inconsistent responses")
    print(f"   Hybrid:   {average_quality:.1f}% average quality, {conversational_responses/total_tests*100:.0f}% conversational")
    
    improvement = average_quality - 51
    print(f"   Improvement: +{improvement:.1f} points ({'+' if improvement > 0 else ''}{improvement:.1f}%)")
    
    return {
        "certification_score": certification_score,
        "status": status,
        "success_rate": success_rate,
        "average_quality": average_quality,
        "improvement": improvement,
        "results": results,
        "ready_for_production": certification_score >= 90
    }

if __name__ == "__main__":
    test_results = test_hybrid_router()
    
    # Save results
    with open('/home/pi/hybrid_router_test_results.json', 'w') as f:
        json.dump(test_results, f, indent=2)
    
    print(f"\n📊 Results saved to: hybrid_router_test_results.json")
    
    if test_results["ready_for_production"]:
        print("\n🚀 HYBRID ROUTER READY FOR PRODUCTION!")
    else:
        print(f"\n🔧 Continue optimizing - need {90 - test_results['certification_score']:.1f} more points")
    
    exit(0 if test_results["certification_score"] >= 85 else 1)


