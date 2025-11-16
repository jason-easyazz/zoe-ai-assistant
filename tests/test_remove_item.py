#!/usr/bin/env python3
"""
Test the remove item functionality
"""

import asyncio
import httpx

async def test_remove():
    """Test removing dog treats from admin's list"""
    
    user_id = "admin"
    base_url = "http://localhost:8000"
    
    print("="*80)
    print("🗑️  Testing: Remove Dog Treats from Admin's Shopping List")
    print("="*80)
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        # First, check what's in the list
        print("\n📋 Step 1: Check current list")
        list_response = await client.get(f"{base_url}/api/lists/shopping?user_id={user_id}")
        if list_response.status_code == 200:
            data = list_response.json()
            items = data.get("lists", [{}])[0].get("items", [])
            print(f"   Current items ({len(items)}):")
            for item in items:
                print(f"      • {item.get('text')} (ID: {item.get('id')})")
        
        # Test removal
        print("\n📋 Step 2: Remove 'dog treats'")
        test_message = "Remove dog treats from my shopping list"
        print(f"   Message: {test_message}")
        
        response = await client.post(
            f"{base_url}/api/chat/?user_id={user_id}",
            json={"message": test_message},
            timeout=60.0
        )
        
        if response.status_code == 200:
            data = response.json()
            zoe_response = data.get("response", "")
            print(f"\n🤖 Zoe: {zoe_response}")
            
            # Check if it shows the remaining items
            if "chicken pie" in zoe_response.lower():
                print(f"\n✅ PASS: Response shows remaining items (Chicken Pie)")
            elif "empty" in zoe_response.lower() and "chicken pie" not in zoe_response.lower():
                print(f"\n❌ FAIL: Response incorrectly says list is empty")
            else:
                print(f"\n⚠️  Unclear: Check response above")
        
        # Verify in database
        print("\n📋 Step 3: Verify in database")
        list_response = await client.get(f"{base_url}/api/lists/shopping?user_id={user_id}")
        if list_response.status_code == 200:
            data = list_response.json()
            items = data.get("lists", [{}])[0].get("items", [])
            print(f"   Final items ({len(items)}):")
            for item in items:
                print(f"      • {item.get('text')}")
            
            # Should have Chicken Pie only (one Dog Treats removed)
            dog_treats_count = sum(1 for item in items if "dog treats" in item.get('text', '').lower())
            chicken_pie_count = sum(1 for item in items if "chicken pie" in item.get('text', '').lower())
            
            print(f"\n📊 Item counts:")
            print(f"   Dog Treats: {dog_treats_count}")
            print(f"   Chicken Pie: {chicken_pie_count}")
            
            if dog_treats_count == 1 and chicken_pie_count == 1:
                print(f"\n✅ PERFECT: 1 Dog Treats removed, 1 remains + Chicken Pie!")
            elif dog_treats_count == 0 and chicken_pie_count == 1:
                print(f"\n⚠️  Both Dog Treats removed, only Chicken Pie remains")
            else:
                print(f"\n❌ Unexpected state")

if __name__ == "__main__":
    asyncio.run(test_remove())

