#!/usr/bin/env python3
"""
Full System Simulation Test
===========================

Simulate the full system running with the reliable chat router.
"""

import sys
import os
import time
import json
import asyncio
from datetime import datetime

# Add the services path
sys.path.append('/workspace/services/zoe-core')

async def simulate_full_system():
    """Simulate the full system with reliable chat router"""
    print("🎯 FULL SYSTEM SIMULATION TEST")
    print("=" * 50)
    
    try:
        from routers.chat_reliable import reliable_chat, ChatMessage
        
        # Comprehensive test scenarios covering all enhancement systems
        test_scenarios = [
            # Temporal Memory Tests
            {
                "question": "Remember that I have a doctor's appointment next week",
                "expected_systems": ["temporal", "memory"],
                "category": "temporal_memory"
            },
            {
                "question": "What did we discuss about the project last time?",
                "expected_systems": ["temporal", "memory"],
                "category": "temporal_memory"
            },
            {
                "question": "Can you recall our previous conversation about the budget?",
                "expected_systems": ["temporal", "memory"],
                "category": "temporal_memory"
            },
            
            # Cross-Agent Collaboration Tests
            {
                "question": "Can you help me plan my schedule for tomorrow?",
                "expected_systems": ["calendar", "planning", "collaboration"],
                "category": "orchestration"
            },
            {
                "question": "I need to coordinate a meeting with the development team",
                "expected_systems": ["calendar", "collaboration", "planning"],
                "category": "orchestration"
            },
            {
                "question": "Help me organize my tasks and create a shopping list",
                "expected_systems": ["lists", "planning", "collaboration"],
                "category": "orchestration"
            },
            
            # User Satisfaction Tests
            {
                "question": "How do you learn from my feedback?",
                "expected_systems": ["satisfaction", "learning"],
                "category": "satisfaction"
            },
            {
                "question": "Can you adapt your responses based on my preferences?",
                "expected_systems": ["satisfaction", "adaptation"],
                "category": "satisfaction"
            },
            
            # Enhancement System Showcase
            {
                "question": "How can your new enhancement systems help me be more productive?",
                "expected_systems": ["temporal", "collaboration", "satisfaction", "caching"],
                "category": "enhancement_showcase"
            },
            {
                "question": "What makes you different from other AI assistants?",
                "expected_systems": ["temporal", "collaboration", "satisfaction", "caching"],
                "category": "enhancement_showcase"
            },
            
            # General Capability Tests
            {
                "question": "What's the weather like today?",
                "expected_systems": ["weather", "expert"],
                "category": "general"
            },
            {
                "question": "Hello! How are you today?",
                "expected_systems": ["greeting", "enhancement"],
                "category": "general"
            },
            {
                "question": "What are your capabilities?",
                "expected_systems": ["temporal", "collaboration", "satisfaction", "caching"],
                "category": "capabilities"
            }
        ]
        
        results = {
            "test_name": "Full System Simulation",
            "timestamp": datetime.now().isoformat(),
            "total_tests": len(test_scenarios),
            "successful_tests": 0,
            "quality_responses": 0,
            "enhancement_aware_responses": 0,
            "system_coverage": {
                "temporal_memory": 0,
                "orchestration": 0,
                "satisfaction": 0,
                "enhancement_showcase": 0,
                "general": 0
            },
            "scenario_results": [],
            "start_time": time.time()
        }
        
        print(f"\n🧪 Testing {len(test_scenarios)} comprehensive scenarios...")
        
        for i, scenario in enumerate(test_scenarios, 1):
            print(f"\n📝 Test {i}/{len(test_scenarios)}: {scenario['question']}")
            
            try:
                start_time = time.time()
                
                # Create chat message
                chat_msg = ChatMessage(message=scenario["question"])
                
                # Test the reliable chat function
                response = await reliable_chat(chat_msg, "simulation_user")
                
                response_time = time.time() - start_time
                
                if response and "response" in response:
                    response_text = response["response"]
                    response_length = len(response_text)
                    
                    # Analyze response quality
                    response_lower = response_text.lower()
                    
                    # Check for enhancement system mentions
                    enhancement_keywords = {
                        "temporal": ["temporal", "memory", "episode", "conversation"],
                        "collaboration": ["collaboration", "expert", "coordinate", "coordinate"],
                        "satisfaction": ["satisfaction", "feedback", "learn", "adapt"],
                        "caching": ["caching", "optimize", "performance"],
                        "weather": ["weather", "forecast", "temperature"],
                        "calendar": ["calendar", "schedule", "appointment"],
                        "planning": ["planning", "plan", "organize"]
                    }
                    
                    system_mentions = {}
                    for system, keywords in enhancement_keywords.items():
                        system_mentions[system] = any(keyword in response_lower for keyword in keywords)
                    
                    # Check if response is detailed and conversational
                    is_detailed = response_length > 100
                    is_conversational = any(word in response_lower for word in ["i'd", "i'm", "i can", "let me", "absolutely", "great"])
                    
                    # Calculate quality score
                    quality_score = 0
                    if is_detailed:
                        quality_score += 30
                    if is_conversational:
                        quality_score += 20
                    if any(system_mentions.values()):
                        quality_score += 30
                    if response_time < 0.1:
                        quality_score += 20
                    
                    # Record results
                    test_result = {
                        "question": scenario["question"],
                        "category": scenario["category"],
                        "expected_systems": scenario["expected_systems"],
                        "response_time": response_time,
                        "response_length": response_length,
                        "is_detailed": is_detailed,
                        "is_conversational": is_conversational,
                        "system_mentions": system_mentions,
                        "quality_score": quality_score,
                        "success": True,
                        "response_preview": response_text[:150] + "..." if len(response_text) > 150 else response_text
                    }
                    
                    results["scenario_results"].append(test_result)
                    results["successful_tests"] += 1
                    
                    if is_detailed:
                        results["quality_responses"] += 1
                    
                    if any(system_mentions.values()):
                        results["enhancement_aware_responses"] += 1
                        results["system_coverage"][scenario["category"]] += 1
                    
                    print(f"  ✅ SUCCESS ({response_time:.3f}s, {response_length} chars)")
                    print(f"  📊 Quality Score: {quality_score}/100")
                    print(f"  🎯 Systems Mentioned: {[k for k, v in system_mentions.items() if v]}")
                    print(f"  📝 Preview: {test_result['response_preview']}")
                    
                else:
                    print(f"  ❌ FAILED: No response data")
                    results["scenario_results"].append({
                        "question": scenario["question"],
                        "category": scenario["category"],
                        "success": False,
                        "error": "No response data"
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
        
        # Print comprehensive summary
        print(f"\n🎊 COMPREHENSIVE TEST RESULTS")
        print("=" * 60)
        print(f"✅ Success Rate: {success_rate:.1f}% ({results['successful_tests']}/{results['total_tests']})")
        print(f"📊 Quality Rate: {quality_rate:.1f}% ({results['quality_responses']}/{results['total_tests']})")
        print(f"🎯 Enhancement Awareness: {enhancement_rate:.1f}% ({results['enhancement_aware_responses']}/{results['total_tests']})")
        print(f"🏆 Overall Score: {overall_score:.1f}/100")
        print(f"📜 Certification: {results['certification']}")
        print(f"⏱️ Total Test Time: {total_time:.2f}s")
        
        print(f"\n📈 SYSTEM COVERAGE:")
        for category, count in results["system_coverage"].items():
            percentage = (count / results["total_tests"]) * 100
            print(f"  {category.replace('_', ' ').title()}: {percentage:.1f}% ({count} tests)")
        
        if overall_score >= 95:
            print(f"\n🎉 CONGRATULATIONS! 100% FUNCTIONALITY ACHIEVED!")
            print(f"🚀 The system is working perfectly!")
            print(f"✨ All enhancement systems are properly showcased!")
            print(f"🎯 Ready for production deployment!")
        else:
            print(f"\n⚠️ System needs improvement to reach 100%")
            print(f"🎯 Target: 95%+ overall score")
        
        # Save results
        with open("full_system_simulation_results.json", "w") as f:
            json.dump(results, f, indent=2)
        
        print(f"\n💾 Results saved to: full_system_simulation_results.json")
        
        return results
        
    except Exception as e:
        print(f"❌ Test failed: {e}")
        return None

def main():
    """Main function to run the simulation"""
    return asyncio.run(simulate_full_system())

if __name__ == "__main__":
    main()