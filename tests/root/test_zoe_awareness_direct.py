#!/usr/bin/env python3
"""
Direct Zoe Awareness Test
========================

Test Zoe's self-awareness by directly calling the AI client with user data.
"""

import asyncio
import httpx
import json
import sys
import os

# Add the zoe-core path to import the AI client
sys.path.append('/home/pi/zoe/services/zoe-core')

async def test_zoe_awareness_direct():
    """Test Zoe's awareness by directly calling the AI client"""
    print("🧠 Testing Zoe's Self-Awareness Directly")
    print("=" * 50)
    
    # First, get the calendar data
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.get("http://localhost:8000/api/calendar/events?user_id=memory_test_user")
            if response.status_code == 200:
                events = response.json().get("events", [])
                print(f"✅ Retrieved {len(events)} calendar events")
                
                # Import the AI client
                from ai_client import get_ai_response
                
                # Test with calendar question
                print("\n🗣️ Testing calendar awareness...")
                context = {
                    "user_id": "memory_test_user",
                    "mode": "user"
                }
                
                response = await get_ai_response("What events do I have tomorrow?", context)
                print(f"Zoe: {response}")
                
                # Check if Zoe mentioned the specific events
                if any(event.get('title').lower() in response.lower() for event in events):
                    print("\n🎉 SUCCESS! Zoe can access and reference calendar data!")
                else:
                    print("\n❌ Zoe didn't reference the specific calendar events")
                    print("This suggests the user data fetching isn't working properly.")
                
                # Test with a different question
                print("\n🗣️ Testing general awareness...")
                response = await get_ai_response("Tell me about yourself", context)
                print(f"Zoe: {response}")
                
            else:
                print(f"❌ Failed to retrieve calendar events: {response.status_code}")
                
        except Exception as e:
            print(f"❌ Error: {e}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_zoe_awareness_direct())
