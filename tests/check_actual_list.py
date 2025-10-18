#!/usr/bin/env python3
"""
Check what's actually in the shopping list
"""

import asyncio
import httpx
import json

async def check_list():
    """Check actual shopping list contents"""
    
    user_id = "test_dog_treats_user"
    base_url = "http://localhost:8000"
    
    print("="*80)
    print("🔍 Checking Actual Shopping List Contents")
    print("="*80)
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        # Try different endpoints
        endpoints = [
            f"{base_url}/api/lists/?user_id={user_id}",
            f"{base_url}/api/lists/shopping?user_id={user_id}",
            f"{base_url}/api/lists/tasks?user_id={user_id}",
        ]
        
        for endpoint in endpoints:
            print(f"\n📡 GET {endpoint}")
            try:
                response = await client.get(endpoint)
                print(f"   Status: {response.status_code}")
                
                if response.status_code == 200:
                    data = response.json()
                    print(f"   Response:")
                    print(json.dumps(data, indent=4))
                else:
                    print(f"   Error: {response.text}")
            except Exception as e:
                print(f"   Exception: {e}")

if __name__ == "__main__":
    asyncio.run(check_list())

