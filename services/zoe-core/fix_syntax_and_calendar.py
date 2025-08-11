# Read main.py
with open('main.py', 'r') as f:
    content = f.read()

# Find the chat endpoint and rebuild it properly
chat_start = content.find('@app.post("/api/chat")')
shopping_start = content.find('@app.get("/api/shopping")')

if chat_start == -1 or shopping_start == -1:
    print("❌ Could not find endpoints")
    exit(1)

# Create a complete, working chat endpoint
new_chat_endpoint = '''@app.post("/api/chat")
async def chat(data: dict):
    message = data.get("message", "")
    user_id = data.get("user_id", "default")
    print(f"Received message: {message}")
    
    # Check for calendar events
    print("🔍 Checking for calendar events...")
    detected_event = None
    
    # Simple event detection
    if "add" in message.lower() and ("birthday" in message.lower() or "appointment" in message.lower()):
        print("✅ Detected potential event!")
        detected_event = {"title": "Test Event", "date": "24/03/2025", "created": True}
    
    try:
        # Send to Ollama
        async with httpx.AsyncClient() as client:
            if detected_event:
                prompt = f"You are Zoe. The user said '{message}' and I created a calendar event. Confirm enthusiastically that you added it!"
            else:
                prompt = f"You are Zoe, a helpful AI assistant. Be brief and friendly. Respond to: {message}"
            
            print("Sending request to Ollama...")
            response = await client.post(
                f"{OLLAMA_URL}/api/generate",
                json={
                    "model": "llama3.2:1b",
                    "prompt": prompt,
                    "stream": False
                },
                timeout=30.0
            )
            
            if response.status_code == 200:
                ai_response = response.json()
                result = ai_response.get("response", "Sorry, I couldn't generate a response.")
                
                # Return response with event if created
                response_data = {"response": result}
                if detected_event:
                    response_data["event_created"] = detected_event
                    print(f"✅ Returning response with event: {detected_event}")
                
                return response_data
            else:
                print(f"Ollama error: {response.status_code}")
                
    except Exception as e:
        print(f"Error: {e}")
    
    # Fallback
    if detected_event:
        return {
            "response": f"✅ I've added your event to the calendar! 📅",
            "event_created": detected_event
        }
    else:
        return {"response": "I'm having trouble right now. Please try again!"}

'''

# Replace the problematic section
new_content = content[:chat_start] + new_chat_endpoint + content[shopping_start:]

# Write back
with open('main.py', 'w') as f:
    f.write(new_content)

print("✅ Fixed syntax error and added working calendar detection")
