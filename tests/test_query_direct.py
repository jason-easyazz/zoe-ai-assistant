#!/usr/bin/env python3
"""
Test mem-agent query directly
"""

import asyncio
import httpx
import json

async def test():
    mem_agent_url = "http://localhost:11435"
    
    print("="*80)
    print("🔧 Testing: Query Shopping List Directly")
    print("="*80)
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        query = "What's on the shopping list"
        
        print(f"\n📝 Query: {query}")
        
        payload = {
            "query": query,
            "user_id": "admin",
            "max_results": 5,
            "execute_actions": True
        }
        
        response = await client.post(
            f"{mem_agent_url}/search",
            json=payload,
            timeout=30.0
        )
        
        if response.status_code == 200:
            data = response.json()
            print(f"\n📊 Full Response:")
            print(json.dumps(data, indent=2))
            
            # Check what items are in the response
            experts = data.get("experts", [])
            if experts:
                for expert in experts:
                    result = expert.get("result", {})
                    expert_data = result.get("data", {})
                    items = expert_data.get("items", [])
                    
                    print(f"\n📋 Items returned ({len(items)}):")
                    for item in items:
                        print(f"   • {item.get('text')}")

if __name__ == "__main__":
    asyncio.run(test())

