#!/usr/bin/env python3
"""
Test Zoe Code Execution via Chat Interface
Tests natural language prompts that should trigger code execution
"""

import asyncio
import httpx
import json
import sys

CHAT_API_URL = "http://localhost:8000"
USER_ID = "test_user"

async def get_session():
    """Get or create a test session"""
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            # Try to create a session
            response = await client.post(
                f"{CHAT_API_URL}/api/auth/session",
                json={"user_id": USER_ID, "username": USER_ID}
            )
            if response.status_code == 200:
                data = response.json()
                return data.get("session_id", "test_session")
        except:
            pass
    return "test_session"  # Fallback

async def send_chat_message(message: str, stream: bool = False, session_id: str = None):
    """Send a chat message and get response"""
    if not session_id:
        session_id = await get_session()
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        url = f"{CHAT_API_URL}/api/chat"
        params = {"stream": "true"} if stream else {}
        
        payload = {
            "message": message,
            "user_id": USER_ID,
            "session_id": session_id
        }
        
        headers = {
            "X-Session-ID": session_id,
            "Content-Type": "application/json"
        }
        
        if stream:
            # Stream response
            async with client.stream("POST", url, json=payload, headers=headers, params=params) as response:
                print(f"   📡 Response status: {response.status_code}")
                if response.status_code != 200:
                    error_text = await response.aread()
                    return {
                        "response": f"Error {response.status_code}: {error_text.decode()[:200]}",
                        "code_blocks": [],
                        "tool_calls": []
                    }
                
                full_response = ""
                code_blocks = []
                tool_calls = []
                raw_lines = []
                
                async for line in response.aiter_lines():
                    raw_lines.append(line)
                    if line.startswith("data: "):
                        try:
                            data = json.loads(line[6:])
                            event_type = data.get("type", "")
                            
                            # Debug: print event types
                            if event_type not in ["session_start", "session_end"]:
                                print(f"   📨 Event: {event_type}")
                            
                            if event_type == "content" or event_type == "message_delta":
                                content = data.get("content") or data.get("delta", "")
                                full_response += content
                                print(content, end="", flush=True)
                            
                            # Check for code blocks in accumulated response
                            if "```typescript" in full_response or "```python" in full_response:
                                import re
                                code_pattern = r'```(?:typescript|python)\n(.*?)```'
                                matches = re.findall(code_pattern, full_response, re.DOTALL)
                                code_blocks = matches
                            
                            # Check for tool calls
                            if "[TOOL_CALL:" in full_response:
                                import re
                                tool_pattern = r'\[TOOL_CALL:([^:]+):'
                                matches = re.findall(tool_pattern, full_response)
                                tool_calls = matches
                        except json.JSONDecodeError as e:
                            # Debug: show non-JSON lines
                            if not line.startswith("data: "):
                                print(f"   ⚠️  Non-JSON line: {line[:100]}")
                
                print("\n")  # New line after streaming
                
                # Debug: show summary
                if not full_response:
                    print(f"   ⚠️  No content received. Raw lines: {len(raw_lines)}")
                    if raw_lines:
                        print(f"   📋 First few lines:")
                        for line in raw_lines[:5]:
                            print(f"      {line[:100]}")
                
                return {
                    "response": full_response,
                    "code_blocks": code_blocks,
                    "tool_calls": tool_calls
                }
        else:
            # Non-streaming response
            response = await client.post(url, json=payload, headers=headers)
            if response.status_code == 200:
                data = response.json()
                return {
                    "response": data.get("response", ""),
                    "code_blocks": [],
                    "tool_calls": []
                }
            else:
                return {
                    "response": f"Error: {response.status_code} - {response.text}",
                    "code_blocks": [],
                    "tool_calls": []
                }

async def test_prompt(prompt: str, expected_pattern: str = None, session_id: str = None):
    """Test a natural language prompt"""
    print(f"\n{'='*70}")
    print(f"📝 Testing: {prompt}")
    print(f"{'='*70}")
    
    result = await send_chat_message(prompt, stream=True, session_id=session_id)
    
    response = result["response"]
    code_blocks = result["code_blocks"]
    tool_calls = result["tool_calls"]
    
    # Analyze response
    has_code = len(code_blocks) > 0
    has_tool_calls = len(tool_calls) > 0
    
    print(f"\n📊 Analysis:")
    print(f"   Response length: {len(response)} chars")
    print(f"   Code blocks found: {len(code_blocks)}")
    print(f"   Tool calls found: {len(tool_calls)}")
    
    if has_code:
        print(f"   ✅ CODE EXECUTION PATTERN DETECTED!")
        for i, code in enumerate(code_blocks, 1):
            print(f"\n   Code Block {i}:")
            print(f"   {'-'*60}")
            print(f"   {code[:200]}...")
            if len(code) > 200:
                print(f"   ... ({len(code) - 200} more chars)")
    elif has_tool_calls:
        print(f"   ⚠️  OLD TOOL CALL PATTERN DETECTED (should use code execution)")
        for tool in tool_calls:
            print(f"   - {tool}")
    else:
        print(f"   ℹ️  No code or tool calls detected (conversational response)")
    
    # Check for expected pattern
    if expected_pattern:
        if expected_pattern in response.lower():
            print(f"   ✅ Expected pattern found: '{expected_pattern}'")
        else:
            print(f"   ⚠️  Expected pattern not found: '{expected_pattern}'")
    
    return {
        "prompt": prompt,
        "has_code": has_code,
        "has_tool_calls": has_tool_calls,
        "code_blocks": code_blocks,
        "tool_calls": tool_calls,
        "response_length": len(response)
    }

async def main():
    """Run comprehensive tests"""
    print("="*70)
    print("Zoe Code Execution - Natural Language Testing")
    print("="*70)
    
    # Test prompts that should trigger code execution
    test_cases = [
        {
            "prompt": "Add bread to my shopping list",
            "expected": "code execution",
            "description": "Simple list addition"
        },
        {
            "prompt": "Create a calendar event for tomorrow at 2pm called 'Team Meeting'",
            "expected": "code execution",
            "description": "Calendar event creation"
        },
        {
            "prompt": "Show me all my lists",
            "expected": "code execution",
            "description": "List retrieval"
        },
        {
            "prompt": "Add milk and eggs to shopping list, then show me what's on it",
            "expected": "code execution",
            "description": "Multi-step operation"
        },
        {
            "prompt": "What can you do?",
            "expected": "code execution",
            "description": "Capability question"
        },
        {
            "prompt": "Search for people named John in my memory",
            "expected": "code execution",
            "description": "Memory search"
        }
    ]
    
    results = []
    session_id = await get_session()
    print(f"🔑 Using session: {session_id}")
    
    for i, test_case in enumerate(test_cases, 1):
        print(f"\n\n🧪 Test Case {i}/{len(test_cases)}: {test_case['description']}")
        
        try:
            result = await test_prompt(test_case["prompt"], test_case.get("expected"), session_id)
            results.append(result)
            
            # Small delay between tests
            await asyncio.sleep(2)
        except Exception as e:
            print(f"   ❌ Error: {e}")
            results.append({
                "prompt": test_case["prompt"],
                "error": str(e)
            })
    
    # Summary
    print("\n\n" + "="*70)
    print("Test Results Summary")
    print("="*70)
    
    total = len(results)
    code_execution_count = sum(1 for r in results if r.get("has_code"))
    tool_call_count = sum(1 for r in results if r.get("has_tool_calls"))
    conversational_count = total - code_execution_count - tool_call_count
    
    print(f"\n📊 Statistics:")
    print(f"   Total tests: {total}")
    print(f"   ✅ Code execution pattern: {code_execution_count}")
    print(f"   ⚠️  Old tool call pattern: {tool_call_count}")
    print(f"   💬 Conversational only: {conversational_count}")
    
    print(f"\n📈 Code Execution Adoption Rate: {code_execution_count/total*100:.1f}%")
    
    if code_execution_count > 0:
        print(f"\n✅ SUCCESS: Code execution pattern is working!")
        print(f"   The agent is writing TypeScript code instead of direct tool calls.")
    elif tool_call_count > 0:
        print(f"\n⚠️  WARNING: Still using old tool call pattern")
        print(f"   The agent needs better prompting to use code execution.")
    else:
        print(f"\nℹ️  INFO: No tool usage detected")
        print(f"   These prompts may not require tools, or agent needs better prompting.")
    
    return 0

if __name__ == "__main__":
    try:
        exit_code = asyncio.run(main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\n\n⚠️  Test interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

