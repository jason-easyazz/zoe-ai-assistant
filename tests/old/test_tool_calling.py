#!/usr/bin/env python3
"""
Test script to verify tool calling functionality
"""

import asyncio
import httpx
import json

async def test_tool_calling():
    """Test the complete tool calling flow"""
    
    print("🧪 Testing Zoe's Tool Calling System")
    print("=" * 50)
    
    # Test 1: Check MCP server tools
    print("\n1. Testing MCP Server Tools...")
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "http://localhost:8003/tools/list",
                json={"_auth_token": "default", "_session_id": "default"},
                timeout=5.0
            )
            
            if response.status_code == 200:
                tools_data = response.json()
                print(f"✅ MCP Server: {tools_data['total_tools']} tools available")
                print(f"   Categories: {tools_data['categories']}")
            else:
                print(f"❌ MCP Server error: {response.status_code}")
                return
                
    except Exception as e:
        print(f"❌ MCP Server connection failed: {e}")
        return
    
    # Test 2: Direct tool execution
    print("\n2. Testing Direct Tool Execution...")
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "http://localhost:8003/tools/add_to_list",
                json={
                    "list_name": "test_shopping",
                    "task_text": "test item",
                    "priority": "medium",
                    "_auth_token": "default",
                    "_session_id": "default"
                },
                timeout=5.0
            )
            
            if response.status_code == 200:
                result = response.json()
                print(f"✅ Direct tool execution: {result['message']}")
            else:
                print(f"❌ Direct tool execution failed: {response.status_code}")
                
    except Exception as e:
        print(f"❌ Direct tool execution error: {e}")
    
    # Test 3: Chat with tool calling
    print("\n3. Testing Chat with Tool Calling...")
    test_messages = [
        "Add bread to shopping list",
        "What tools do you have?",
        "Turn on the living room light"
    ]
    
    for message in test_messages:
        print(f"\n   Testing: '{message}'")
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    "http://localhost:8000/api/chat",
                    json={"message": message, "user_id": "test_user"},
                    timeout=30.0
                )
                
                if response.status_code == 200:
                    result = response.json()
                    print(f"   ✅ Response: {result['response'][:100]}...")
                    print(f"   ⏱️  Response time: {result['response_time']:.2f}s")
                    print(f"   🎯 Routing: {result['routing']}")
                else:
                    print(f"   ❌ Chat error: {response.status_code}")
                    
        except Exception as e:
            print(f"   ❌ Chat error: {e}")
    
    print("\n" + "=" * 50)
    print("🎯 Tool Calling Test Complete!")

if __name__ == "__main__":
    asyncio.run(test_tool_calling())

