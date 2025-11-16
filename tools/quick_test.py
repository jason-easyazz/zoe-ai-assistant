#!/usr/bin/env python3
"""Quick validation test for Zoe system"""
import asyncio
import httpx
import time

async def test_queries():
    """Test 5 key queries"""
    tests = [
        "add milk to shopping list",
        "what time is it",
        "schedule meeting tomorrow at 3pm",
        "who is John",
        "tell me a joke"
    ]
    
    results = {"passed": 0, "failed": 0, "tests": []}
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        for query in tests:
            try:
                start = time.time()
                response = await client.post(
                    "http://localhost:8000/api/chat",
                    json={"message": query},
                    headers={"X-Session-ID": "dev-localhost"}
                )
                elapsed = time.time() - start
                
                if response.status_code == 200:
                    results["passed"] += 1
                    status = "✅ PASS"
                else:
                    results["failed"] += 1
                    status = "❌ FAIL"
                
                results["tests"].append({
                    "query": query,
                    "status": status,
                    "time": f"{elapsed:.2f}s",
                    "code": response.status_code
                })
                
                print(f"{status} | {elapsed:5.2f}s | {query}")
                
            except Exception as e:
                results["failed"] += 1
                results["tests"].append({
                    "query": query,
                    "status": "❌ ERROR",
                    "error": str(e)
                })
                print(f"❌ ERROR | {query} | {str(e)[:50]}")
    
    print("\n" + "="*60)
    print(f"Results: {results['passed']}/5 passed ({results['passed']*20}%)")
    print("="*60)
    
    return results

if __name__ == "__main__":
    asyncio.run(test_queries())

