#!/usr/bin/env python3
"""
Update the chat endpoint to include calendar functionality
"""

# The new enhanced chat endpoint
ENHANCED_CHAT_ENDPOINT = '''@app.post("/api/chat")
async def chat(data: dict):
    message = data.get("message", "")
    user_id = data.get("user_id", "default")
    print(f"Received message: {message}")
    
    # Check for calendar events in the message
    detected_event = extract_event_from_text(message)
    event_created = None
    
    if detected_event:
        title, event_date, description = detected_event
        print(f"📅 Detected event: {title} on {event_date}")
        
        # Create a simple in-memory event (no database yet, just for demo)
        display_date = format_date_display(event_date, "AU")
        event_created = {
            "title": title,
            "date": display_date,
            "created": True
        }
        print(f"✅ Event created: {title} on {display_date}")
    
    try:
        # Enhance the prompt based on whether we created an event
        if event_created:
            enhanced_prompt = f"""You are Zoe, a helpful AI assistant. The user said: "{message}"

I've detected and created a calendar event:
- Event: {event_created['title']}
- Date: {event_created['date']}

Respond enthusiastically that you've added their event to the calendar. Be brief, friendly, and confirm the details."""
        else:
            enhanced_prompt = f"You are Zoe, a helpful AI assistant. Be brief and friendly. Respond to: {message}"
        
        async with httpx.AsyncClient() as client:
            print("Sending request to Ollama...")
            response = await client.post(
                f"{OLLAMA_URL}/api/generate",
                json={
                    "model": "llama3.2:1b",
                    "prompt": enhanced_prompt,
                    "stream": False
                },
                timeout=30.0
            )
            print(f"Ollama response status: {response.status_code}")
            
            if response.status_code == 200:
                ai_response = response.json()
                result = ai_response.get("response", "Sorry, I couldn't generate a response.")
                print(f"AI response: {result[:100]}...")
                
                # Include event info in response if created
                response_data = {"response": result}
                if event_created:
                    response_data["event_created"] = event_created
                
                return response_data
            else:
                print(f"Ollama error status: {response.status_code}")
                
    except Exception as e:
        print(f"Exception occurred: {type(e).__name__}: {str(e)}")
        import traceback
        traceback.print_exc()
    
    # Fallback response
    if event_created:
        return {
            "response": f"✅ I've added '{event_created['title']}' to your calendar for {event_created['date']}! 📅",
            "event_created": event_created
        }
    else:
        return {"response": "I'm having trouble connecting to my AI brain right now. Please try again!"}'''

print("📝 Enhanced chat endpoint ready!")
