import os
from fastapi import FastAPI
import httpx
import asyncio

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
    
    try:
        async with httpx.AsyncClient() as client:
            print("Sending request to Ollama...")
            response = await client.post(
                f"{OLLAMA_URL}/api/generate",
                json={
                    "model": "llama3.2:1b",
                    "prompt": f"You are Zoe, a helpful AI assistant. Be brief and friendly. Respond to: {message}",
                    "stream": False
                },
                timeout=30.0
            )
            print(f"Ollama response status: {response.status_code}")
            
            if response.status_code == 200:
                ai_response = response.json()
                result = ai_response.get("response", "Sorry, I couldn't generate a response.")
                print(f"AI response: {result[:100]}...")
                return {"response": result}
            else:
                print(f"Ollama error status: {response.status_code}")
                
    except Exception as e:
        print(f"Exception occurred: {type(e).__name__}: {str(e)}")
        import traceback
        traceback.print_exc()
    
    return {"response": "I'm having trouble connecting to my AI brain right now. Please try again!"}

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
