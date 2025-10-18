#!/usr/bin/env python3
"""
Test Enhanced MEM Agent remove function directly
"""

import asyncio
import httpx
import json

async def test_remove_direct():
    """Test calling Enhanced MEM Agent for removal"""
    
    mem_agent_url = "http://localhost:11435"
    
    test_queries = [
        "Remove dog treats from my shopping list",
        "remove 1 of the dog treats from the list",
        "Delete milk from shopping",
        "remove it"
    ]
    
    print("="*80)
    print("🔧 TESTING: Enhanced MEM Agent Remove Patterns")
    print("="*80)
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        for query in test_queries:
            print(f"\n{'='*80}")
            print(f"📝 Query: {query}")
            print(f"{'='*80}")
            
            try:
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
                    experts = data.get("experts", [])
                    actions = data.get("actions_executed", 0)
                    
                    print(f"📊 Experts: {len(experts)}")
                    print(f"📊 Actions executed: {actions}")
                    
                    if experts:
                        for expert in experts:
                            print(f"\n   Expert: {expert.get('expert')}")
                            print(f"   Intent: {expert.get('intent')}")
                            print(f"   Confidence: {expert.get('confidence')}")
                            print(f"   Action taken: {expert.get('action_taken')}")
                            print(f"   Message: {expert.get('message', '')[:150]}")
                            
                            result = expert.get('result', {})
                            if result.get('error'):
                                print(f"   ⚠️  Error: {result.get('error')}")
                    
                    if actions > 0:
                        print(f"\n✅ Action executed successfully")
                    else:
                        print(f"\n❌ No actions executed")
                else:
                    print(f"❌ HTTP Error: {response.status_code}")
                    
            except Exception as e:
                print(f"❌ Exception: {e}")

if __name__ == "__main__":
    asyncio.run(test_remove_direct())

