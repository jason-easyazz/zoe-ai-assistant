#!/usr/bin/env python3
"""
Check shopping list for all users to find Dog Treats
"""

import asyncio
import httpx
import json

async def check_shopping():
    """Check shopping lists for common user IDs"""
    
    base_url = "http://localhost:8000"
    
    # Common user IDs to check
    user_ids = [
        "default",
        "test_dog_treats_user", 
        "test_teddy_user",
        "admin",
        "user1"
    ]
    
    print("="*80)
    print("🔍 Checking Shopping Lists for All Users")
    print("="*80)
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        for user_id in user_ids:
            print(f"\n📋 User: {user_id}")
            print("-"*80)
            
            try:
                response = await client.get(f"{base_url}/api/lists/shopping?user_id={user_id}")
                
                if response.status_code == 200:
                    data = response.json()
                    lists = data.get("lists", [])
                    
                    if lists:
                        for lst in lists:
                            items = lst.get("items", [])
                            print(f"   List: {lst.get('name', 'Unnamed')}")
                            print(f"   Items: {len(items)}")
                            
                            if items:
                                for item in items:
                                    print(f"      • {item.get('text')} {'✓' if item.get('completed') else '○'}")
                            else:
                                print(f"      (empty)")
                    else:
                        print(f"   No shopping lists found")
                else:
                    print(f"   Error: {response.status_code}")
                    
            except Exception as e:
                print(f"   Exception: {e}")

if __name__ == "__main__":
    asyncio.run(check_shopping())

