#!/usr/bin/env python3
"""
Add Dog Treats to the default user's shopping list
"""

import asyncio
import httpx

async def add_to_default_user():
    """Add Dog Treats to default user via chat API"""
    
    print("="*80)
    print("🐕 Adding Dog Treats to DEFAULT user's shopping list")
    print("="*80)
    
    user_id = "default"
    base_url = "http://localhost:8000"
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        test_message = "Add Dog treats to my shopping list"
        
        print(f"\n👤 User: default")
        print(f"📝 Message: {test_message}")
        print(f"⏳ Sending...")
        
        try:
            response = await client.post(
                f"{base_url}/api/chat/?user_id={user_id}",
                json={"message": test_message},
                timeout=60.0
            )
            
            if response.status_code == 200:
                data = response.json()
                zoe_response = data.get("response", "")
                print(f"\n🤖 Zoe: {zoe_response}")
                
                # Now check the shopping list
                print(f"\n📋 Verifying shopping list...")
                list_response = await client.get(f"{base_url}/api/lists/shopping?user_id={user_id}")
                
                if list_response.status_code == 200:
                    list_data = list_response.json()
                    items = list_data.get("lists", [{}])[0].get("items", [])
                    print(f"\n🛒 Shopping List ({len(items)} items):")
                    for item in items:
                        status = "✓" if item.get("completed") else "○"
                        print(f"   {status} {item.get('text')}")
                    
                    if any("dog treats" in item.get("text", "").lower() for item in items):
                        print(f"\n✅ SUCCESS: Dog Treats is now in your shopping list!")
                        return True
                    else:
                        print(f"\n❌ Dog Treats not found in list")
                        return False
            else:
                print(f"❌ HTTP Error: {response.status_code}")
                return False
                
        except Exception as e:
            print(f"❌ Error: {e}")
            import traceback
            traceback.print_exc()
            return False

if __name__ == "__main__":
    asyncio.run(add_to_default_user())

