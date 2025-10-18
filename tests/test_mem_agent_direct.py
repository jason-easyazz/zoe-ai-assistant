#!/usr/bin/env python3
"""
Test Enhanced MEM Agent directly to see why it's not executing the action
"""

import asyncio
import httpx
import sys
import json

async def test_mem_agent_directly():
    """Test calling Enhanced MEM Agent service directly"""
    
    print("="*80)
    print("🔧 TESTING: Enhanced MEM Agent Direct Call")
    print("="*80)
    
    mem_agent_url = "http://localhost:11435"
    
    test_query = "Can you add, Dog treats to my shopping list"
    
    print(f"\n📤 Sending to Enhanced MEM Agent:")
    print(f"   Query: {test_query}")
    print(f"   URL: {mem_agent_url}/search")
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            payload = {
                "query": test_query,
                "user_id": "test_user",
                "max_results": 5,
                "execute_actions": True,
                "include_graph": True
            }
            
            print(f"\n📦 Payload: {json.dumps(payload, indent=2)}")
            
            response = await client.post(
                f"{mem_agent_url}/search",
                json=payload,
                timeout=30.0
            )
            
            print(f"\n📥 Response Status: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                print(f"\n📊 Response Data:")
                print(json.dumps(data, indent=2))
                
                # Check key fields
                experts = data.get("experts", [])
                actions_executed = data.get("actions_executed", 0)
                execution_summary = data.get("execution_summary", "")
                
                print(f"\n🎯 Summary:")
                print(f"   Experts called: {len(experts)}")
                print(f"   Actions executed: {actions_executed}")
                print(f"   Execution summary: {execution_summary}")
                
                if actions_executed > 0:
                    print(f"\n✅ PASS: Enhanced MEM Agent executed {actions_executed} action(s)")
                    for expert in experts:
                        print(f"\n   Expert: {expert.get('expert')}")
                        print(f"   Intent: {expert.get('intent')}")
                        print(f"   Confidence: {expert.get('confidence')}")
                        print(f"   Action taken: {expert.get('action_taken')}")
                        print(f"   Message: {expert.get('message', '')[:200]}")
                    return True
                else:
                    print(f"\n❌ FAIL: No actions executed")
                    if experts:
                        for expert in experts:
                            print(f"\n   Expert {expert.get('expert')}:")
                            print(f"     Confidence: {expert.get('confidence')}")
                            print(f"     Result: {expert.get('result', {})}")
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
    success = await test_mem_agent_directly()
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    asyncio.run(main())

