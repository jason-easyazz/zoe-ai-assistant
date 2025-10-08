#!/usr/bin/env python3
"""
Direct Ollama Test
==================

Test Zoe's self-awareness by calling Ollama directly with user data.
"""

import asyncio
import httpx
import json

async def test_direct_ollama():
    """Test Zoe with direct Ollama call including user data"""
    print("🧠 Testing Zoe with Direct Ollama Call")
    print("=" * 50)
    
    # First, get the calendar data
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.get("http://localhost:8000/api/calendar/events?user_id=memory_test_user")
            if response.status_code == 200:
                events = response.json().get("events", [])
                print(f"✅ Retrieved {len(events)} calendar events")
                
                # Build system prompt with user data
                system_prompt = """You are Zoe, a friendly assistant.

You have access to the following user data:

CALENDAR EVENTS:
"""
                for event in events:
                    system_prompt += f"- {event.get('title')} on {event.get('start_date')} at {event.get('start_time')} ({event.get('category')})\n"
                
                system_prompt += "\nUse this data to provide specific, helpful responses about the user's schedule, tasks, and information."
                
                # Test with calendar question
                user_message = "What's in my calendar?"
                
                # Build full prompt for Ollama
                full_prompt = f"{system_prompt}\n\nUser: {user_message}\nAssistant:"
                
                print(f"\n📝 Testing with prompt:")
                print(f"System: {system_prompt[:200]}...")
                print(f"User: {user_message}")
                
                # Call Ollama directly
                ollama_response = await client.post(
                    "http://localhost:11434/api/generate",
                    json={
                        "model": "llama3.2:3b",
                        "prompt": full_prompt,
                        "temperature": 0.7,
                        "stream": False
                    },
                    timeout=30.0
                )
                
                if ollama_response.status_code == 200:
                    zoe_response = ollama_response.json().get("response", "No response")
                    print(f"\n✅ Zoe's Response:")
                    print(f"Zoe: {zoe_response}")
                    
                    # Check if Zoe mentioned the specific events
                    if any(event.get('title').lower() in zoe_response.lower() for event in events):
                        print("\n🎉 SUCCESS! Zoe can access and reference calendar data!")
                    else:
                        print("\n❌ Zoe didn't reference the specific calendar events")
                else:
                    print(f"❌ Ollama call failed: {ollama_response.status_code}")
                    
            else:
                print(f"❌ Failed to retrieve calendar events: {response.status_code}")
                
        except Exception as e:
            print(f"❌ Error: {e}")

if __name__ == "__main__":
    asyncio.run(test_direct_ollama())
