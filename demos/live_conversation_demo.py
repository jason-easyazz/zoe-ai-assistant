#!/usr/bin/env python3
"""
Live Conversation Demo - Shows real Zoe interactions with verification
"""
import requests
import json
import time
from typing import Dict, List, Tuple

BASE_URL = "http://localhost:8000"
USER_ID = "demo_user"

def chat(message: str, stream: bool = False) -> Dict:
    """Send a chat message to Zoe"""
    response = requests.post(
        f"{BASE_URL}/api/chat",
        json={
            "message": message,
            "user_id": USER_ID,
            "stream": stream
        },
        headers={"Content-Type": "application/json"},
        timeout=30
    )
    return response.json()

def verify_memory_stored(fact_key: str, expected_value: str) -> bool:
    """Verify that a fact was stored in memory"""
    # Query Zoe to retrieve the fact
    query = f"What is my {fact_key.replace('_', ' ')}?"
    response = chat(query)
    response_text = response.get("response", "").lower()
    
    # Check if expected value is in response
    expected_lower = expected_value.lower()
    return expected_lower in response_text

def demo_memory_storage():
    """Demo: Store and retrieve personal facts"""
    print("\n" + "="*70)
    print("DEMO 1: Memory Storage & Retrieval")
    print("="*70)
    
    facts = [
        ("favorite_color", "blue"),
        ("favorite_food", "sushi"),
        ("favorite_movie", "The Matrix"),
        ("occupation", "software engineer"),
        ("hobby", "photography")
    ]
    
    print("\n📝 STORING FACTS:")
    print("-" * 70)
    stored_count = 0
    for key, value in facts:
        message = f"My {key.replace('_', ' ')} is {value}"
        print(f"\n👤 User: {message}")
        
        response = chat(message)
        response_text = response.get("response", "")
        response_time = response.get("response_time", 0)
        
        print(f"🤖 Zoe: {response_text[:200]}")
        print(f"⏱️  Time: {response_time:.2f}s")
        
        # Verify storage
        time.sleep(1)  # Give time for storage
        if verify_memory_stored(key, value):
            print(f"✅ VERIFIED: Fact stored correctly!")
            stored_count += 1
        else:
            print(f"❌ FAILED: Fact not found in memory")
    
    print(f"\n📊 RESULT: {stored_count}/{len(facts)} facts stored successfully")
    return stored_count == len(facts)

def demo_multi_turn_conversation():
    """Demo: Multi-turn conversation with context"""
    print("\n" + "="*70)
    print("DEMO 2: Multi-Turn Conversation")
    print("="*70)
    
    conversation = [
        ("I'm planning a trip to Japan next month", None),
        ("What should I pack?", "japan"),
        ("I'm going in December", None),
        ("What's the weather like there in December?", "december"),
        ("What about restaurants?", "japan"),
    ]
    
    print("\n💬 CONVERSATION:")
    print("-" * 70)
    
    success_count = 0
    for i, (message, expected_keyword) in enumerate(conversation, 1):
        print(f"\n[Turn {i}]")
        print(f"👤 User: {message}")
        
        response = chat(message)
        response_text = response.get("response", "")
        response_time = response.get("response_time", 0)
        
        print(f"🤖 Zoe: {response_text[:300]}")
        print(f"⏱️  Time: {response_time:.2f}s")
        
        if expected_keyword:
            if expected_keyword.lower() in response_text.lower():
                print(f"✅ VERIFIED: Response relevant to '{expected_keyword}'")
                success_count += 1
            else:
                print(f"⚠️  WARNING: Response may not be contextually relevant")
        
        time.sleep(0.5)
    
    print(f"\n📊 RESULT: {success_count}/{len([c for c in conversation if c[1]])} turns contextually relevant")
    return success_count >= len([c for c in conversation if c[1]]) * 0.8

def demo_action_execution():
    """Demo: Action execution (lists, calendar, etc.)"""
    print("\n" + "="*70)
    print("DEMO 3: Action Execution")
    print("="*70)
    
    actions = [
        ("Add milk to my shopping list", "milk", "shopping"),
        ("Add eggs to shopping list", "eggs", "shopping"),
        ("What's on my shopping list?", None, None),
        ("Create a calendar event for dentist appointment tomorrow at 2pm", "dentist", None),
    ]
    
    print("\n🎯 ACTIONS:")
    print("-" * 70)
    
    success_count = 0
    for message, expected_item, list_type in actions:
        print(f"\n👤 User: {message}")
        
        response = chat(message)
        response_text = response.get("response", "")
        response_time = response.get("response_time", 0)
        routing = response.get("routing", "")
        
        print(f"🤖 Zoe: {response_text[:200]}")
        print(f"⏱️  Time: {response_time:.2f}s | Routing: {routing}")
        
        # Verify action was executed
        if expected_item:
            if expected_item.lower() in response_text.lower() or "added" in response_text.lower() or "created" in response_text.lower():
                print(f"✅ VERIFIED: Action executed (mentions '{expected_item}')")
                success_count += 1
            else:
                print(f"❌ FAILED: Action may not have executed")
        else:
            # Query action - check if it returns results
            if "list" in response_text.lower() or "event" in response_text.lower() or "appointment" in response_text.lower():
                print(f"✅ VERIFIED: Query returned relevant information")
                success_count += 1
        
        time.sleep(1)
    
    print(f"\n📊 RESULT: {success_count}/{len(actions)} actions executed successfully")
    return success_count == len(actions)

def demo_complex_queries():
    """Demo: Complex queries requiring reasoning"""
    print("\n" + "="*70)
    print("DEMO 4: Complex Queries")
    print("="*70)
    
    queries = [
        ("What are the key differences between machine learning and deep learning?", ["machine learning", "deep learning", "difference"]),
        ("Explain how neural networks work in simple terms", ["neural", "network", "simple"]),
        ("What should I know about Python for data science?", ["python", "data science"]),
    ]
    
    print("\n🧠 COMPLEX QUERIES:")
    print("-" * 70)
    
    success_count = 0
    for query, expected_keywords in queries:
        print(f"\n👤 User: {query}")
        
        start_time = time.time()
        response = chat(query)
        elapsed = time.time() - start_time
        response_text = response.get("response", "")
        
        print(f"🤖 Zoe: {response_text[:400]}...")
        print(f"⏱️  Time: {elapsed:.2f}s")
        
        # Check if response contains expected keywords
        keywords_found = sum(1 for kw in expected_keywords if kw.lower() in response_text.lower())
        if keywords_found >= len(expected_keywords) * 0.7:
            print(f"✅ VERIFIED: Response covers {keywords_found}/{len(expected_keywords)} key topics")
            success_count += 1
        else:
            print(f"⚠️  WARNING: Response may be incomplete")
    
    print(f"\n📊 RESULT: {success_count}/{len(queries)} queries answered comprehensively")
    return success_count >= len(queries) * 0.8

def demo_confidence_formatting():
    """Demo: P0-2 Confidence formatting"""
    print("\n" + "="*70)
    print("DEMO 5: Confidence Formatting (P0-2)")
    print("="*70)
    
    queries = [
        ("What is my favorite color?", True),  # Should have high confidence
        ("What did I say 3 months ago?", False),  # Should have low confidence
        ("What is 2+2?", True),  # Should have high confidence
    ]
    
    print("\n🎯 CONFIDENCE DEMONSTRATION:")
    print("-" * 70)
    
    confidence_phrases = ["based on", "i'm not sure", "i don't know", "uncertain", "confident"]
    success_count = 0
    
    for query, should_be_confident in queries:
        print(f"\n👤 User: {query}")
        
        response = chat(query)
        response_text = response.get("response", "")
        
        print(f"🤖 Zoe: {response_text[:200]}")
        
        # Check for confidence markers
        has_confidence_marker = any(phrase in response_text.lower() for phrase in confidence_phrases)
        
        if should_be_confident and not has_confidence_marker:
            print(f"✅ VERIFIED: High confidence response (no qualifiers)")
            success_count += 1
        elif not should_be_confident and has_confidence_marker:
            print(f"✅ VERIFIED: Low confidence response (has qualifiers)")
            success_count += 1
        else:
            print(f"⚠️  NOTE: Confidence formatting may need adjustment")
    
    print(f"\n📊 RESULT: {success_count}/{len(queries)} confidence markers appropriate")
    return success_count >= len(queries) * 0.7

def main():
    """Run all demos"""
    print("\n" + "="*70)
    print("🎬 LIVE ZOE CONVERSATION DEMOS")
    print("="*70)
    print("\nTesting Zoe's capabilities with real conversations...")
    print("Verifying that requests are fulfilled correctly...\n")
    
    results = {}
    
    # Run all demos
    results["memory"] = demo_memory_storage()
    time.sleep(2)
    
    results["multi_turn"] = demo_multi_turn_conversation()
    time.sleep(2)
    
    results["actions"] = demo_action_execution()
    time.sleep(2)
    
    results["complex"] = demo_complex_queries()
    time.sleep(2)
    
    results["confidence"] = demo_confidence_formatting()
    
    # Summary
    print("\n" + "="*70)
    print("📊 FINAL RESULTS")
    print("="*70)
    
    total_demos = len(results)
    passed_demos = sum(1 for v in results.values() if v)
    pass_rate = (passed_demos / total_demos) * 100
    
    for demo, passed in results.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{demo.replace('_', ' ').title()}: {status}")
    
    print(f"\n🎯 OVERALL PASS RATE: {pass_rate:.1f}% ({passed_demos}/{total_demos})")
    
    if pass_rate == 100:
        print("\n🎉🎉🎉 100% PASS RATE ACHIEVED! 🎉🎉🎉")
    elif pass_rate >= 80:
        print(f"\n✅ Excellent! {pass_rate:.1f}% pass rate")
    else:
        print(f"\n⚠️  Needs improvement: {pass_rate:.1f}% pass rate")
    
    return pass_rate

if __name__ == "__main__":
    try:
        pass_rate = main()
        exit(0 if pass_rate == 100 else 1)
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        exit(1)

