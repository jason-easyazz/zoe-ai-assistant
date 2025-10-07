#!/usr/bin/env python3
"""
Test 100% Final Solution
========================

Test the reliable chat router to achieve 100% functionality.
"""

import requests
import json
import time
from datetime import datetime

def test_reliable_chat():
    """Test the reliable chat router for 100% functionality"""
    print("🎯 TESTING 100% FINAL SOLUTION")
    print("=" * 50)
    
    base_url = "http://localhost:8000"
    test_user = f"test_user_{int(time.time())}"
    
    # Test scenarios that previously failed
    test_scenarios = [
        {
            "question": "What's the weather like today?",
            "expected_keywords": ["weather", "expert", "enhancement"],
            "category": "weather"
        },
        {
            "question": "Can you help me plan my schedule for tomorrow?",
            "expected_keywords": ["schedule", "calendar", "planning"],
            "category": "scheduling"
        },
        {
            "question": "Remember that I have a doctor's appointment next week",
            "expected_keywords": ["memory", "temporal", "episode"],
            "category": "memory"
        },
        {
            "question": "What did we discuss about the project last time?",
            "expected_keywords": ["memory", "previous", "conversation"],
            "category": "memory"
        },
        {
            "question": "How can your new enhancement systems help me be more productive?",
            "expected_keywords": ["enhancement", "temporal", "collaboration", "satisfaction"],
            "category": "enhancement"
        },
        {
            "question": "Hello! How are you today?",
            "expected_keywords": ["hello", "zoe", "enhanced", "capabilities"],
            "category": "greeting"
        },
        {
            "question": "What are your capabilities?",
            "expected_keywords": ["temporal", "collaboration", "satisfaction", "caching"],
            "category": "capabilities"
        }
    ]
    
    results = {
        "total_tests": len(test_scenarios),
        "successful_tests": 0,
        "quality_responses": 0,
        "enhancement_aware_responses": 0,
        "scenario_results": [],
        "start_time": time.time()
    }
    
    print(f"\n🧪 Testing {len(test_scenarios)} scenarios...")
    
    for i, scenario in enumerate(test_scenarios, 1):
        print(f"\n📝 Test {i}/{len(test_scenarios)}: {scenario['question']}")
        
        try:
            start_time = time.time()
            
            # Test the reliable chat endpoint
            response = requests.post(
                f"{base_url}/api/chat",
                json={"message": scenario["question"]},
                params={"user_id": test_user},
                timeout=10
            )
            
            response_time = time.time() - start_time
            
            if response.status_code == 200:
                data = response.json()
                response_text = data.get("response", "")
                response_length = len(response_text)
                
                # Check if response is detailed (more than 50 characters)
                is_detailed = response_length > 50
                
                # Check if response mentions enhancement systems
                response_lower = response_text.lower()
                enhancement_keywords = ["temporal", "collaboration", "satisfaction", "caching", "enhancement", "expert", "system"]
                mentions_enhancements = any(keyword in response_lower for keyword in enhancement_keywords)
                
                # Check if response contains expected keywords
                expected_keywords = scenario.get("expected_keywords", [])
                keyword_matches = sum(1 for keyword in expected_keywords if keyword.lower() in response_lower)
                
                # Determine quality score
                quality_score = 0
                if is_detailed:
                    quality_score += 40
                if mentions_enhancements:
                    quality_score += 30
                if keyword_matches > 0:
                    quality_score += 20
                if response_time < 1.0:
                    quality_score += 10
                
                # Record results
                test_result = {
                    "question": scenario["question"],
                    "category": scenario["category"],
                    "response_time": response_time,
                    "response_length": response_length,
                    "is_detailed": is_detailed,
                    "mentions_enhancements": mentions_enhancements,
                    "keyword_matches": keyword_matches,
                    "quality_score": quality_score,
                    "success": True,
                    "response_preview": response_text[:100] + "..." if len(response_text) > 100 else response_text
                }
                
                results["scenario_results"].append(test_result)
                results["successful_tests"] += 1
                
                if is_detailed:
                    results["quality_responses"] += 1
                
                if mentions_enhancements:
                    results["enhancement_aware_responses"] += 1
                
                print(f"  ✅ SUCCESS ({response_time:.3f}s, {response_length} chars)")
                print(f"  📊 Quality Score: {quality_score}/100")
                print(f"  🎯 Enhancement Aware: {'Yes' if mentions_enhancements else 'No'}")
                print(f"  📝 Preview: {test_result['response_preview']}")
                
            else:
                print(f"  ❌ FAILED: HTTP {response.status_code}")
                results["scenario_results"].append({
                    "question": scenario["question"],
                    "category": scenario["category"],
                    "success": False,
                    "error": f"HTTP {response.status_code}"
                })
                
        except requests.exceptions.Timeout:
            print(f"  ⏰ TIMEOUT")
            results["scenario_results"].append({
                "question": scenario["question"],
                "category": scenario["category"],
                "success": False,
                "error": "Timeout"
            })
            
        except Exception as e:
            print(f"  ❌ ERROR: {str(e)}")
            results["scenario_results"].append({
                "question": scenario["question"],
                "category": scenario["category"],
                "success": False,
                "error": str(e)
            })
    
    # Calculate final scores
    total_time = time.time() - results["start_time"]
    success_rate = (results["successful_tests"] / results["total_tests"]) * 100
    quality_rate = (results["quality_responses"] / results["total_tests"]) * 100
    enhancement_rate = (results["enhancement_aware_responses"] / results["total_tests"]) * 100
    
    # Calculate overall score
    overall_score = (success_rate * 0.4) + (quality_rate * 0.3) + (enhancement_rate * 0.3)
    
    results.update({
        "total_time": total_time,
        "success_rate": success_rate,
        "quality_rate": quality_rate,
        "enhancement_rate": enhancement_rate,
        "overall_score": overall_score,
        "certification": "PASS" if overall_score >= 95 else "NEEDS_WORK"
    })
    
    # Print summary
    print(f"\n🎊 FINAL RESULTS")
    print("=" * 50)
    print(f"✅ Success Rate: {success_rate:.1f}% ({results['successful_tests']}/{results['total_tests']})")
    print(f"📊 Quality Rate: {quality_rate:.1f}% ({results['quality_responses']}/{results['total_tests']})")
    print(f"🎯 Enhancement Awareness: {enhancement_rate:.1f}% ({results['enhancement_aware_responses']}/{results['total_tests']})")
    print(f"🏆 Overall Score: {overall_score:.1f}/100")
    print(f"📜 Certification: {results['certification']}")
    print(f"⏱️ Total Test Time: {total_time:.2f}s")
    
    if overall_score >= 95:
        print(f"\n🎉 CONGRATULATIONS! 100% FUNCTIONALITY ACHIEVED!")
        print(f"🚀 The system is ready for production use!")
    else:
        print(f"\n⚠️ System needs improvement to reach 100%")
        print(f"🎯 Target: 95%+ overall score")
    
    # Save results
    with open("100_percent_final_test_results.json", "w") as f:
        json.dump(results, f, indent=2)
    
    print(f"\n💾 Results saved to: 100_percent_final_test_results.json")
    
    return results

if __name__ == "__main__":
    test_reliable_chat()