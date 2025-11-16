#!/usr/bin/env python3
"""
Check admin user's shopping lists
"""

import asyncio
import httpx
import json

async def check_admin_shopping():
    """Check what shopping lists admin has"""
    
    user_id = "admin"
    base_url = "http://localhost:8000"
    
    print("="*80)
    print(f"🔍 Checking Shopping Lists for user: {user_id}")
    print("="*80)
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(f"{base_url}/api/lists/shopping?user_id={user_id}")
        
        if response.status_code == 200:
            data = response.json()
            lists = data.get("lists", [])
            
            print(f"\n📊 Total shopping lists: {len(lists)}")
            print()
            
            for i, lst in enumerate(lists, 1):
                print(f"List #{i}:")
                print(f"  ID: {lst.get('id')}")
                print(f"  Name: {lst.get('name')}")
                print(f"  Type: {lst.get('list_type')}")
                print(f"  Created: {lst.get('created_at')}")
                print(f"  Items: {len(lst.get('items', []))}")
                
                items = lst.get('items', [])
                if items:
                    for item in items:
                        status = "✓" if item.get("completed") else "○"
                        print(f"    {status} {item.get('text')}")
                else:
                    print(f"    (empty)")
                print()

if __name__ == "__main__":
    asyncio.run(check_admin_shopping())

