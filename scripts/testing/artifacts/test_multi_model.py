#!/usr/bin/env python3
import asyncio
import sys
sys.path.append('/home/pi/zoe/services/zoe-core')
from ai_router import ai_router

async def test():
    # Test simple request (should use 1b model)
    print("Testing simple request...")
    result = await ai_router.route_request("What time is it?")
    print(f"Model: {result['model']}")
    print(f"Response: {result['response'][:200]}...\n")
    
    # Test medium request (should use 3b model)
    print("Testing medium request...")
    result = await ai_router.route_request("Create a backup script")
    print(f"Model: {result['model']}")
    print(f"Response: {result['response'][:200]}...\n")
    
    # Test complex request (should use Claude if API key set)
    print("Testing complex request...")
    result = await ai_router.route_request(
        "Analyze the Zoe system architecture and suggest performance improvements",
        {"mode": "developer"}
    )
    print(f"Model: {result['model']}")
    print(f"Response: {result['response'][:200]}...\n")

if __name__ == "__main__":
    asyncio.run(test())
