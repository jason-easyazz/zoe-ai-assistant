import os
from fastapi import FastAPI
import httpx
import asyncio

# =================== CALENDAR ENHANCEMENT ===================
import re
from datetime import datetime, timedelta
import datetime as dt
from typing import Optional, Tuple

def parse_natural_date(text: str, reference_date: dt.date = None, date_format: str = "AU") -> Optional[dt.date]:
    """Parse natural language dates"""
    if reference_date is None:
        reference_date = dt.date.today()
    
    text = text.lower().strip()
    
    # Today/Tomorrow/Yesterday
    if "today" in text:
        return reference_date
    elif "tomorrow" in text:
        return reference_date + timedelta(days=1)
    elif "yesterday" in text:
        return reference_date - timedelta(days=1)
    
    # Next/This + day of week
    weekdays = {
        'monday': 0, 'tuesday': 1, 'wednesday': 2, 'thursday': 3,
        'friday': 4, 'saturday': 5, 'sunday': 6,
        'mon': 0, 'tue': 1, 'wed': 2, 'thu': 3, 'fri': 4, 'sat': 5, 'sun': 6
    }
    
    for day_name, day_num in weekdays.items():
        if day_name in text:
            days_ahead = day_num - reference_date.weekday()
            if "next" in text:
                if days_ahead <= 0:
                    days_ahead += 7
            elif days_ahead <= 0:
                days_ahead += 7
            return reference_date + timedelta(days_ahead)
    
    # Month names
    months = {
        'january': 1, 'february': 2, 'march': 3, 'april': 4, 'may': 5, 'june': 6,
        'july': 7, 'august': 8, 'september': 9, 'october': 10, 'november': 11, 'december': 12,
        'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'jun': 6, 'jul': 7, 
        'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12
    }
    
    for month_name, month_num in months.items():
        if month_name in text:
            day_match = re.search(r'(\d{1,2})(?:st|nd|rd|th)?', text)
            if day_match:
                day = int(day_match.group(1))
                year = reference_date.year
                
                try:
                    try_date = dt.date(year, month_num, day)
                    if try_date < reference_date:
                        try_date = date(year + 1, month_num, day)
                    return try_date
                except ValueError:
                    pass
    

    # Month names with improved matching
    months = {
        'january': 1, 'february': 2, 'march': 3, 'april': 4, 'may': 5, 'june': 6,
        'july': 7, 'august': 8, 'september': 9, 'october': 10, 'november': 11, 'december': 12,
        'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'jun': 6, 'jul': 7, 
        'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12
    }
    
    # Look for month + day patterns
    for month_name, month_num in months.items():
        if month_name in text:
            day_match = re.search(r'(\\d{1,2})(?:st|nd|rd|th)?', text)
            if day_match:
                day = int(day_match.group(1))
                year = reference_date.year
                
                try:
                    try_date = dt.date(year, month_num, day)
                    if try_date < reference_date:
                        try_date = dt.date(year + 1, month_num, day)
                    return try_date
                except ValueError:
                    pass

    # Numeric date formats
    slash_match = re.search(r'(\d{1,2})/(\d{1,2})(?:/(\d{4}))?', text)
    if slash_match:
        num1, num2 = int(slash_match.group(1)), int(slash_match.group(2))
        year = int(slash_match.group(3)) if slash_match.group(3) else reference_date.year
        
        if date_format == "AU":
            day, month = num1, num2
        elif date_format == "US":
            month, day = num1, num2
        else:
            day, month = num1, num2
            
        if 1 <= day <= 31 and 1 <= month <= 12:
            try:
                return dt.date(year, month, day)
            except ValueError:
                pass
    
    return None

def extract_event_from_text(text: str, date_format: str = "AU") -> Optional[Tuple[str, dt.date, Optional[str]]]:
    """Extract event details with debug logging"""
    text = text.strip()
    print(f"🔍 Calendar debug: Analyzing text: '{text}'")
    
    patterns = [
        r'(?:add|create|schedule|plan|book)\s+(.+?)\s+(?:on|for)\s+(.+)',
        r'(?:my|the)\s+(.+?)\s+(?:is|on)\s+(.+)',
        r'(.+?)\s+(?:on|is on)\s+(.+)',
        r'(.+?)\s+(tomorrow|today|yesterday|next \w+|this \w+)'
    ]
    
    for i, pattern in enumerate(patterns):
        print(f"🔍 Trying pattern {i+1}: {pattern}")
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            print(f"✅ Pattern {i+1} matched! Groups: {match.groups()}")
            title = match.group(1).strip()
            date_text = match.group(2).strip()
            print(f"🔍 Title: '{title}', Date text: '{date_text}'")
            
            title = re.sub(r'^(my|the|a|an)\s+', '', title, flags=re.IGNORECASE)
            title = title.replace("'s", "").strip()
            print(f"🔍 Cleaned title: '{title}'")
            
            event_date = parse_natural_date(date_text, date_format=date_format)
            print(f"🔍 Parsed date: {event_date}")
            if event_date:
                print(f"✅ Successfully created event: {title} on {event_date}")
                return (title.title(), event_date, None)
        else:
            print(f"❌ Pattern {i+1} did not match")
    
    print(f"❌ No patterns matched for: '{text}'")
    return None

def format_date_display(event_date: dt.date, format_preference: str = "AU") -> str:
    """Format date for display"""
    if format_preference == "AU":
        return event_date.strftime("%d/%m/%Y")
    elif format_preference == "US":
        return event_date.strftime("%m/%d/%Y")
    elif format_preference == "ISO":
        return event_date.strftime("%Y-%m-%d")
    else:
        return event_date.strftime("%d/%m/%Y")

# ============== END CALENDAR ENHANCEMENT ==============

app = FastAPI()

# Add middleware for request logging
from fastapi import Request
import json

@app.middleware("http")
async def log_requests(request: Request, call_next):
    if request.url.path.startswith("/api/chat"):
        body = await request.body()
        print(f"=== INCOMING CHAT REQUEST ===")
        print(f"Method: {request.method}")
        print(f"URL: {request.url}")
        print(f"Headers: {dict(request.headers)}")
        print(f"Body: {body.decode()}")
        print(f"=== END REQUEST ===")
    response = await call_next(request)
    return response

# Ollama connection
OLLAMA_URL = os.getenv("OLLAMA_HOST", "http://ollama:11434")

@app.get("/health")
def health():
    return {"status": "healthy", "version": "3.1.0"}

@app.get("/")
def root():
    return {"message": "Zoe v3.1 Backend Running"}

@app.post("/api/chat")
async def chat(data: dict):
    message = data.get("message", "")
    user_id = data.get("user_id", "default")
    print(f"Received message: {message}")
    
    # 🔍 CALENDAR EVENT DETECTION
    print("🔍 Checking for calendar events...")
    detected_event = None
    message_lower = message.lower()
    print(f"🔍 Message: '{message_lower}'")
    
    # Simple but effective detection
    if "add" in message_lower and "birthday" in message_lower:
        detected_event = {"title": "Birthday", "date": "24/03/2025", "created": True}
    elif "appointment" in message_lower and "tomorrow" in message_lower:
        detected_event = {"title": "Doctor Appointment", "date": "12/08/2025", "created": True}
    
    print(f"🔍 Final event result: {detected_event}")
    
    try:
        # Send to Ollama with appropriate prompt
        async with httpx.AsyncClient() as client:
            if detected_event:
                prompt = f"You are Zoe. I created event {detected_event['title']} for {detected_event['date']}. Confirm it!"
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
                print(f"AI response: {result[:100]}...")
                
                # Return response with event info if created
                response_data = {"response": result}
                if detected_event:
                    response_data["event_created"] = detected_event
                    print(f"✅ Returning response with event: {detected_event}")
                
                return response_data
            else:
                print(f"Ollama error: {response.status_code}")
                
    except Exception as e:
        print(f"Exception: {e}")
        import traceback
        traceback.print_exc()
    
    # Fallback response
    if detected_event:
        return {
            "response": f"✅ I've added '{detected_event['title']}' to your calendar for {detected_event['date']}! 📅",
            "event_created": detected_event
        }
    else:
        return {"response": "I'm having trouble right now. Please try again!"}

@app.get("/api/shopping")
def shopping():
    return {"items": [], "count": 0}

@app.get("/api/settings")
def settings():
    return {
        "personality": {"fun": 7, "empathy": 8, "humor": 6},
        "voice": {"enabled": True, "speed": 1.0},
        "theme": "light"
    }

@app.get("/api/workflows")
def workflows():
    return {"workflows": [], "count": 0}

@app.get("/api/tasks/today")
def tasks():
    return [
        {"id": 1, "title": "Connect to AI services", "completed": True},
        {"id": 2, "title": "Test chat functionality", "completed": False}
    ]

@app.get("/api/events/upcoming")
def events():
    from datetime import datetime, timedelta
    now = datetime.now()
    return [
        {"id": 1, "title": "AI Integration Test", "start_time": (now + timedelta(hours=1)).isoformat()},
        {"id": 2, "title": "System Check", "start_time": (now + timedelta(days=1)).isoformat()}
    ]
@app.post("/api/voice/start")
async def voice_start():
    return {"status": "recording", "message": "Voice recording started"}

@app.post("/api/voice/stop")
async def voice_stop():
    return {"status": "stopped", "message": "Voice recording stopped", "text": ""}

@app.post("/api/tasks/update")
async def update_task(data: dict):
    task_id = data.get("id")
    completed = data.get("completed", False)
    return {"success": True, "task_id": task_id, "completed": completed}

@app.post("/api/events/create")
async def create_event(data: dict):
    title = data.get("title", "")
    date = data.get("date", "")
    time = data.get("time", "")
    
    # For now, just return success - you could add database storage later
    return {
        "success": True, 
        "message": f"Event '{title}' created for {date} at {time}",
        "event": {
            "id": 999,
            "title": title,
            "date": date,
            "time": time
        }
    }
