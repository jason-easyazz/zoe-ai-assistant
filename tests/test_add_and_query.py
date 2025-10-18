#!/usr/bin/env python3
"""
Test add chocolate + query shopping list
"""

import asyncio
import httpx

async def test():
    user_id = "admin"
    base_url = "http://localhost:8000"
    
    print("="*80)
    print("🧪 Testing: Add Chocolate + Query Shopping List")
    print("="*80)
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        # First check current state
        print("\n📋 Step 1: Check current list")
        response = await client.get(f"{base_url}/api/lists/shopping?user_id={user_id}")
        if response.status_code == 200:
            data = response.json()
            items = data.get("lists", [{}])[0].get("items", [])
            print(f"   Current: {[item.get('text') for item in items]}")
        
        # Add chocolate
        print("\n📝 Step 2: Add Chocolate")
        response = await client.post(
            f"{base_url}/api/chat/?user_id={user_id}",
            json={"message": "Add Chocolate to the shopping list"},
            timeout=60.0
        )
        if response.status_code == 200:
            zoe_resp = response.json().get("response", "")
            print(f"🤖 Zoe: {zoe_resp[:200]}")
        
        # Query list
        print("\n📝 Step 3: Query 'What's on the shopping list'")
        response = await client.post(
            f"{base_url}/api/chat/?user_id={user_id}",
            json={"message": "What's on the shopping list"},
            timeout=60.0
        )
        if response.status_code == 200:
            zoe_resp = response.json().get("response", "")
            print(f"\n🤖 Zoe: {zoe_resp}")
            
            # Check if it shows the correct items
            has_milk = "milk" in zoe_resp.lower()
            has_eggs = "eggs" in zoe_resp.lower()
            has_chocolate = "chocolate" in zoe_resp.lower()
            
            print(f"\n📊 Items shown in response:")
            print(f"   Milk: {'✅' if has_milk else '❌'}")
            print(f"   Eggs: {'✅' if has_eggs else '❌'}")
            print(f"   Chocolate: {'✅' if has_chocolate else '❌'}")
            
            # Check for wrong items
            has_personal = "personal" in zoe_resp.lower() and "○ personal" in zoe_resp.lower()
            has_bucket = "bucket" in zoe_resp.lower() and "○ bucket" in zoe_resp.lower()
            
            if has_personal or has_bucket:
                print(f"\n❌ FAIL: Shows list names instead of items!")
                print(f"   Personal: {'❌ WRONG' if has_personal else 'OK'}")
                print(f"   Bucket: {'❌ WRONG' if has_bucket else 'OK'}")
            
            if has_milk and has_eggs and has_chocolate and not has_personal and not has_bucket:
                print(f"\n✅ PASS: Correct items displayed!")
                return True
            else:
                print(f"\n❌ FAIL: Incorrect items displayed")
                return False

if __name__ == "__main__":
    success = asyncio.run(test())
    exit(0 if success else 1)

