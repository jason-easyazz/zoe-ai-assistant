#!/usr/bin/env python3
"""
Test the EXACT conversation that failed in production
User walked Teddy, then asked "Who did i walk this morning" - Zoe didn't remember!
"""

import asyncio
import httpx
import sys

async def test_teddy_conversation():
    """Test the exact conversation from production that revealed the bug"""
    
    print("="*80)
    print("🐕 TESTING: Teddy Walking Conversation (Production Failure)")
    print("="*80)
    
    user_id = "test_teddy_user"
    base_url = "http://localhost:8000"
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        conversation = [
            ("Not bad, walked Teddy, and Dad came over", "Should acknowledge walking Teddy"),
            ("Who did i walk this morning", "Should recall Teddy from earlier in conversation"),
        ]
        
        for i, (user_msg, expected) in enumerate(conversation):
            print(f"\n{'='*80}")
            print(f"Turn {i+1}/{len(conversation)}")
            print(f"{'='*80}")
            print(f"👤 User: {user_msg}")
            print(f"🎯 Expected: {expected}")
            print(f"⏳ Sending...")
            
            try:
                # Send message
                response = await client.post(
                    f"{base_url}/api/chat/?user_id={user_id}",
                    json={"message": user_msg},
                    timeout=60.0
                )
                
                if response.status_code == 200:
                    data = response.json()
                    zoe_response = data.get("response", "")
                    print(f"\n🤖 Zoe: {zoe_response}")
                    
                    # Test SPECIFIC to the second turn - should mention "Teddy"
                    if i == 1:  # "Who did i walk this morning"
                        if "teddy" in zoe_response.lower():
                            print(f"\n✅ PASS: Zoe correctly recalled Teddy!")
                            return True
                        else:
                            print(f"\n❌ FAIL: Zoe didn't recall Teddy!")
                            print(f"   Expected response to contain 'Teddy'")
                            print(f"   Got: {zoe_response}")
                            return False
                else:
                    print(f"❌ HTTP Error: {response.status_code}")
                    print(f"   Response: {response.text}")
                    return False
                    
            except Exception as e:
                print(f"❌ Error: {e}")
                return False
            
            # Small delay between turns
            await asyncio.sleep(1)
    
    print(f"\n❌ FAIL: Test didn't reach final assertion")
    return False


async def main():
    print("\n🔧 Testing Production Failure: Temporal Memory Recall")
    print("="*80)
    
    success = await test_teddy_conversation()
    
    print("\n" + "="*80)
    if success:
        print("✅ TEST PASSED: Temporal memory recall is working!")
    else:
        print("❌ TEST FAILED: Temporal memory recall not working")
    print("="*80)
    
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    asyncio.run(main())

