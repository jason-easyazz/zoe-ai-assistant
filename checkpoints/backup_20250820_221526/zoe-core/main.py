from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from datetime import datetime
import httpx
import json
import sqlite3
import os
from typing import Optional, List, Dict

app = FastAPI(title="Zoe AI Assistant API", version="3.1")

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Database setup
DB_PATH = os.getenv("DATABASE_PATH", "/app/data/zoe.db")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://zoe-ollama:11434")

def init_db():
    """Initialize database tables"""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    c.execute('''CREATE TABLE IF NOT EXISTS events
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  title TEXT NOT NULL,
                  date TEXT,
                  time TEXT,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS conversations
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_message TEXT,
                  assistant_response TEXT,
                  timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS tasks
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  title TEXT NOT NULL,
                  completed BOOLEAN DEFAULT 0,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    
    conn.commit()
    conn.close()

init_db()

# Pydantic models
class ChatMessage(BaseModel):
    message: str

class ChatResponse(BaseModel):
    response: str
    detected_events: Optional[List[Dict]] = []
    detected_tasks: Optional[List[str]] = []

class Event(BaseModel):
    title: str
    date: Optional[str] = None
    time: Optional[str] = None

@app.get("/")
async def root():
    return {"message": "Zoe AI Assistant API v3.1"}

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "services": {
            "api": "running",
            "database": os.path.exists(DB_PATH),
            "ollama": await check_ollama()
        }
    }

async def check_ollama():
    """Check if Ollama is accessible"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{OLLAMA_URL}/api/tags", timeout=5.0)
            return response.status_code == 200
    except:
        return False

async def get_ollama_response(message: str) -> str:
    """Get response from Ollama"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{OLLAMA_URL}/api/generate",
                json={
                    "model": "llama3.2:3b",
                    "prompt": f"""You are Zoe, a friendly and helpful AI assistant living on a Raspberry Pi. 
You're warm, engaging, and always eager to help. Keep responses conversational and natural.

User says: {message}

Zoe's response:""",
                    "stream": False
                },
                timeout=30.0
            )
            if response.status_code == 200:
                return response.json().get("response", "I'm having trouble thinking right now.")
            else:
                return "I'm having trouble connecting to my brain right now."
    except Exception as e:
        print(f"Ollama error: {e}")
        return "I'm having trouble thinking right now. Please try again."

@app.post("/api/chat", response_model=ChatResponse)
async def chat(message: ChatMessage):
    """Main chat endpoint with Ollama integration"""
    try:
        # Get AI response
        response_text = await get_ollama_response(message.message)
        
        # Detect events and tasks
        detected_events = []
        detected_tasks = []
        
        lower_msg = message.message.lower()
        if any(word in lower_msg for word in ['meeting', 'appointment', 'birthday']):
            detected_events.append({"type": "event", "text": message.message})
        
        if any(word in lower_msg for word in ['todo', 'task', 'remind me']):
            detected_tasks.append(message.message)
        
        # Save to database
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("INSERT INTO conversations (user_message, assistant_response) VALUES (?, ?)",
                  (message.message, response_text))
        conn.commit()
        conn.close()
        
        return ChatResponse(
            response=response_text,
            detected_events=detected_events,
            detected_tasks=detected_tasks
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/events")
async def get_events():
    """Get all events"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT * FROM events ORDER BY created_at DESC")
    events = c.fetchall()
    conn.close()
    
    return {"events": [
        {"id": e[0], "title": e[1], "date": e[2], "time": e[3], "created_at": e[4]}
        for e in events
    ]}

@app.post("/api/events")
async def create_event(event: Event):
    """Create a new event"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO events (title, date, time) VALUES (?, ?, ?)",
              (event.title, event.date, event.time))
    conn.commit()
    event_id = c.lastrowid
    conn.close()
    
    return {"id": event_id, "message": "Event created successfully"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

# Developer API Endpoints
@app.get("/api/developer/tasks")
async def get_developer_tasks():
    """Get all developer tasks"""
    return {
        "tasks": [
            {"id": 1, "title": "Event Clusters", "status": "complete", "priority": "high"},
            {"id": 2, "title": "Glass-Morphic UI", "status": "complete", "priority": "high"},
            {"id": 3, "title": "Developer Dashboard", "status": "complete", "priority": "medium"},
            {"id": 4, "title": "Voice Integration", "status": "pending", "priority": "medium"},
            {"id": 5, "title": "Memory System", "status": "pending", "priority": "low"}
        ]
    }

@app.post("/api/developer/execute")
async def execute_command(command: dict):
    """Execute safe developer commands"""
    allowed_commands = ["docker ps", "git status", "df -h", "uptime"]
    cmd = command.get("command")
    
    if any(cmd.startswith(allowed) for allowed in allowed_commands):
        import subprocess
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        return {"output": result.stdout, "error": result.stderr}
    else:
        return {"error": "Command not allowed"}

@app.get("/api/system/metrics")
async def get_system_metrics():
    """Get system performance metrics"""
    import psutil
    
    return {
        "cpu_percent": psutil.cpu_percent(),
        "memory_percent": psutil.virtual_memory().percent,
        "disk_usage": psutil.disk_usage('/').percent,
        "uptime": time.time() - psutil.boot_time()
    }

@app.get("/api/logs")
async def get_logs(service: str = "zoe-core", lines: int = 50):
    """Get container logs"""
    import subprocess
    
    cmd = f"docker logs {service} --tail {lines}"
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    
    return {"logs": result.stdout.split('\n')}
