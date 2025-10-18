#!/usr/bin/env python3
"""
Test complete add + remove flow for admin user
"""

import asyncio
import httpx

async def test_flow():
    """Complete test: Add 3 items, remove 1, verify 2 remain"""
    
    user_id = "admin"
    base_url = "http://localhost:8000"
    
    print("="*80)
    print("🧪 COMPLETE FLOW TEST: Add → Remove → Verify")
    print("="*80)
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        
        # Step 1: Add 3 items
        print("\n📝 Step 1: Add 3 items")
        for item in ["Milk", "Bread", "Eggs"]:
            print(f"   Adding: {item}")
            response = await client.post(
                f"{base_url}/api/chat/?user_id={user_id}",
                json={"message": f"Add {item} to shopping list"},
                timeout=60.0
            )
            if response.status_code == 200:
                print(f"   ✅ Added")
            await asyncio.sleep(0.5)
        
        # Step 2: Check list
        print("\n📋 Step 2: Verify all 3 items added")
        list_response = await client.get(f"{base_url}/api/lists/shopping?user_id={user_id}")
        if list_response.status_code == 200:
            data = list_response.json()
            items = data.get("lists", [{}])[0].get("items", [])
            print(f"   Items ({len(items)}):")
            for item in items:
                print(f"      • {item.get('text')}")
        
        # Step 3: Remove 1 item
        print("\n🗑️  Step 3: Remove 'Bread'")
        remove_msg = "Remove Bread from my shopping list"
        print(f"   Message: {remove_msg}")
        
        response = await client.post(
            f"{base_url}/api/chat/?user_id={user_id}",
            json={"message": remove_msg},
            timeout=60.0
        )
        
        if response.status_code == 200:
            data = response.json()
            zoe_response = data.get("response", "")
            print(f"\n🤖 Zoe: {zoe_response[:200]}")
            
            if "✅" in zoe_response and "removed" in zoe_response.lower():
                print(f"\n✅ Removal confirmed in response")
            else:
                print(f"\n❌ Removal not confirmed")
        
        # Step 4: Final verification
        print("\n📋 Step 4: Verify 2 items remain (Milk, Eggs)")
        list_response = await client.get(f"{base_url}/api/lists/shopping?user_id={user_id}")
        if list_response.status_code == 200:
            data = list_response.json()
            items = data.get("lists", [{}])[0].get("items", [])
            print(f"   Final items ({len(items)}):")
            for item in items:
                print(f"      • {item.get('text')}")
            
            has_milk = any("milk" in item.get('text', '').lower() for item in items)
            has_eggs = any("eggs" in item.get('text', '').lower() for item in items)
            has_bread = any("bread" in item.get('text', '').lower() for item in items)
            
            print(f"\n📊 Final state:")
            print(f"   Milk: {'✅' if has_milk else '❌'}")
            print(f"   Eggs: {'✅' if has_eggs else '❌'}")
            print(f"   Bread: {'❌ (removed)' if not has_bread else '✅ (should be removed!)'}")
            
            if has_milk and has_eggs and not has_bread and len(items) == 2:
                print(f"\n🎉 PERFECT: Add + Remove working correctly!")
                return True
            else:
                print(f"\n❌ Unexpected state")
                return False

if __name__ == "__main__":
    success = asyncio.run(test_flow())
    exit(0 if success else 1)

