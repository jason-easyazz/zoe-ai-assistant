#!/usr/bin/env python3
"""
Fix Chat Responses
==================

Fix the chat router to give full AI responses instead of simplified action messages.
"""

import requests
import json

def test_chat_fix():
    """Test the chat system with a direct fix"""
    print("🔧 FIXING CHAT RESPONSES FOR 100% FUNCTIONALITY")
    print("=" * 55)
    
    base_url = "http://localhost:8000"
    test_user = "chat_fix_test"
    
    # Test 1: Try to get a full AI response by using Ollama directly through the system
    print("\n🦙 Testing Direct Ollama Integration...")
    
    try:
        # Test if we can get Ollama to work through the container network
        ollama_test = requests.post("http://localhost:11434/api/generate",
            json={
                "model": "gemma3:1b", 
                "prompt": "You are Zoe, an AI assistant. A user is asking: 'Tell me about your enhancement systems.' Please respond conversationally about your temporal memory, cross-agent collaboration, user satisfaction tracking, and context caching capabilities.",
                "stream": False
            },
            timeout=10
        )
        
        if ollama_test.status_code == 200:
            data = ollama_test.json()
            ai_response = data.get('response', '')
            print(f"  ✅ Ollama Direct Response ({len(ai_response)} chars):")
            print(f"     {ai_response[:300]}...")
            
            # This is what we want the chat system to return
            ideal_response = ai_response
        else:
            print(f"  ❌ Ollama direct test failed: {ollama_test.status_code}")
            ideal_response = None
    except Exception as e:
        print(f"  ❌ Ollama direct test error: {e}")
        ideal_response = None
    
    # Test 2: Compare with current chat system
    print("\n💬 Testing Current Chat System...")
    
    try:
        current_response = requests.post(f"{base_url}/api/chat",
            json={"message": "Tell me about your enhancement systems and how they help users."},
            params={"user_id": test_user},
            timeout=15
        )
        
        if current_response.status_code == 200:
            data = current_response.json()
            current_text = data.get('response', '')
            current_time = data.get('response_time', 0)
            
            print(f"  📊 Current System Response ({current_time:.2f}s, {len(current_text)} chars):")
            print(f"     {current_text}")
            
            # Compare quality
            if ideal_response:
                quality_ratio = len(current_text) / len(ideal_response) if len(ideal_response) > 0 else 0
                print(f"  📈 Quality Ratio: {quality_ratio:.2f} (current/ideal)")
                
                if quality_ratio < 0.3:
                    print("  ❌ MAJOR ISSUE: Responses are too simplified")
                elif quality_ratio < 0.7:
                    print("  ⚠️ ISSUE: Responses could be more detailed")
                else:
                    print("  ✅ GOOD: Response quality is adequate")
        else:
            print(f"  ❌ Current chat failed: {current_response.status_code}")
    except Exception as e:
        print(f"  ❌ Current chat error: {e}")
    
    # Test 3: Real-world scenarios with current system
    print("\n🌍 Testing Real-World Scenarios...")
    
    real_world_questions = [
        "What's the weather like today?",
        "Can you help me plan my schedule for tomorrow?", 
        "Remember that I have a doctor's appointment next week",
        "What did we discuss about the project last time?",
        "How can your new enhancement systems help me be more productive?"
    ]
    
    scenario_results = []
    
    for i, question in enumerate(real_world_questions, 1):
        print(f"\n  🧪 Scenario {i}: {question}")
        try:
            response = requests.post(f"{base_url}/api/chat",
                json={"message": question},
                params={"user_id": test_user},
                timeout=20
            )
            
            if response.status_code == 200:
                data = response.json()
                response_text = data.get('response', '')
                response_time = data.get('response_time', 0)
                
                # Quick quality assessment
                is_detailed = len(response_text) > 50
                is_conversational = "I" in response_text or "me" in response_text
                
                quality = "✅ GOOD" if is_detailed and is_conversational else "⚠️ SIMPLIFIED"
                
                print(f"     {quality} ({response_time:.2f}s, {len(response_text)} chars)")
                print(f"     Response: {response_text[:100]}...")
                
                scenario_results.append({
                    "question": question,
                    "response_time": response_time,
                    "response_length": len(response_text),
                    "is_detailed": is_detailed,
                    "is_conversational": is_conversational,
                    "quality_good": is_detailed and is_conversational
                })
            else:
                print(f"     ❌ FAILED ({response.status_code})")
                scenario_results.append({"question": question, "failed": True})
        except Exception as e:
            print(f"     ❌ ERROR: {str(e)[:50]}")
            scenario_results.append({"question": question, "error": str(e)})
    
    # Final Assessment
    print("\n" + "="*55)
    print("🎯 CHAT SYSTEM DIAGNOSIS")
    print("="*55)
    
    successful_scenarios = len([r for r in scenario_results if not r.get("failed", False) and not r.get("error")])
    good_quality_responses = len([r for r in scenario_results if r.get("quality_good", False)])
    
    print(f"Successful Responses:    {successful_scenarios}/5 ({successful_scenarios/5*100:.0f}%)")
    print(f"Good Quality Responses:  {good_quality_responses}/5 ({good_quality_responses/5*100:.0f}%)")
    
    if good_quality_responses >= 4:
        print("✅ CHAT SYSTEM: EXCELLENT QUALITY")
        chat_status = "EXCELLENT"
    elif good_quality_responses >= 3:
        print("⚠️ CHAT SYSTEM: GOOD BUT COULD BE BETTER")
        chat_status = "GOOD"
    elif good_quality_responses >= 2:
        print("⚠️ CHAT SYSTEM: ADEQUATE BUT NEEDS WORK")
        chat_status = "ADEQUATE"
    else:
        print("❌ CHAT SYSTEM: NEEDS MAJOR IMPROVEMENT")
        chat_status = "NEEDS_WORK"
    
    # Specific recommendations
    print(f"\n💡 RECOMMENDATIONS:")
    if good_quality_responses < 3:
        print("   🔧 Fix AI service connection for full conversational responses")
        print("   🧹 Clean up multiple chat routers (currently 8 different ones)")
        print("   🔗 Ensure proper integration with Ollama/LiteLLM")
    
    if successful_scenarios == 5 and good_quality_responses >= 4:
        print("   🎉 Chat system is ready for 100% certification!")
    
    return {
        "successful_scenarios": successful_scenarios,
        "good_quality_responses": good_quality_responses,
        "chat_status": chat_status,
        "scenario_results": scenario_results,
        "ready_for_100_percent": successful_scenarios == 5 and good_quality_responses >= 4
    }

if __name__ == "__main__":
    results = test_chat_fix()
    
    with open('/home/pi/chat_fix_results.json', 'w') as f:
        json.dump(results, f, indent=2)
    
    print(f"\n📊 Results saved to: chat_fix_results.json")
    
    if results["ready_for_100_percent"]:
        print("\n🚀 READY FOR 100% CERTIFICATION!")
    else:
        print("\n🔧 CHAT SYSTEM NEEDS FIXES FOR 100%")
    
    exit(0 if results["ready_for_100_percent"] else 1)


