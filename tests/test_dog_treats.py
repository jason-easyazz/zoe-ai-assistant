#!/usr/bin/env python3
"""
Test the EXACT "Dog treats" failure from production
"""

import asyncio
import httpx
import sys

async def test_dog_treats():
    """Test adding dog treats to shopping list"""
    
    print("="*80)
    print("🐕 TESTING: Add Dog Treats to Shopping List")
    print("="*80)
    
    user_id = "test_dog_treats_user"
    base_url = "http://localhost:8000"
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        test_message = "Can you add, Dog treats to my shopping list"
        
        print(f"\n👤 User: {test_message}")
        print(f"⏳ Sending...")
        
        try:
            # Send message
            response = await client.post(
                f"{base_url}/api/chat/?user_id={user_id}",
                json={"message": test_message},
                timeout=60.0
            )
            
            if response.status_code == 200:
                data = response.json()
                zoe_response = data.get("response", "")
                print(f"\n🤖 Zoe: {zoe_response}")
                
                # Check if the action was executed properly
                if "✅" in zoe_response and "dog treats" in zoe_response.lower():
                    print(f"\n✅ PASS: Dog treats added successfully!")
                    print(f"   Response contains checkmark and item name")
                    return True
                elif "added" in zoe_response.lower() and "shopping" in zoe_response.lower():
                    print(f"\n✅ PASS: Dog treats added (different format)")
                    return True
                else:
                    print(f"\n❌ FAIL: Response doesn't confirm item was added")
                    print(f"   Expected: '✅ Added ... Dog treats ...'")
                    print(f"   Got: {zoe_response}")
                    
                    # Check if action was actually executed in backend
                    routing = data.get("routing", "")
                    actions = data.get("actions_executed", 0)
                    print(f"\n📊 Debug Info:")
                    print(f"   Routing: {routing}")
                    print(f"   Actions executed: {actions}")
                    return False
            else:
                print(f"❌ HTTP Error: {response.status_code}")
                print(f"   Response: {response.text}")
                return False
                
        except Exception as e:
            print(f"❌ Error: {e}")
            import traceback
            traceback.print_exc()
            return False


async def main():
    print("\n🔧 Testing Production Failure: Dog Treats Action Execution")
    print("="*80)
    
    success = await test_dog_treats()
    
    print("\n" + "="*80)
    if success:
        print("✅ TEST PASSED: Dog treats action working!")
    else:
        print("❌ TEST FAILED: Dog treats action not working")
    print("="*80)
    
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    asyncio.run(main())

