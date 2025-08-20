from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List, Optional, Dict
import sqlite3
import json
import os
from datetime import datetime
import httpx

app = FastAPI(title="Zoe AI Assistant API", version="4.0")

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Database setup
DB_PATH = "/app/data/zoe.db"

def init_db():
    """Initialize database"""
    os.makedirs("/app/data", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Events table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            start_date DATE NOT NULL,
            start_time TIME,
            cluster_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    conn.commit()
    conn.close()

# Initialize on startup
init_db()

# Import memory system if available
try:
    from memory_system import MemorySystem
    memory = MemorySystem()
    HAS_MEMORY = True
except:
    HAS_MEMORY = False
    print("Memory system not available")

# Import routers if available
try:
    from routers import memory as memory_router
    if HAS_MEMORY:
        app.include_router(memory_router.router)
except:
    print("Memory router not available")

# Basic health check
@app.get("/")
async def root():
    return {"message": "Zoe AI Assistant API v4.0", "status": "running"}

@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "version": "4.0",
        "services": {
            "core": "running",
            "memory": "available" if HAS_MEMORY else "not loaded"
        }
    }

# Chat endpoint
class ChatMessage(BaseModel):
    message: str

@app.post("/api/chat")
async def chat(msg: ChatMessage):
    """Basic chat endpoint"""
    return {
        "response": f"I heard you say: {msg.message}",
        "status": "success"
    }

# Calendar endpoints
@app.get("/api/calendar/events")
async def get_events():
    """Get all calendar events"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM events ORDER BY start_date DESC LIMIT 10")
    events = cursor.fetchall()
    conn.close()
    
    return {
        "events": [
            {
                "id": e[0],
                "title": e[1],
                "date": e[2],
                "time": e[3]
            } for e in events
        ]
    }

class EventCreate(BaseModel):
    title: str
    date: str
    time: Optional[str] = None

@app.post("/api/calendar/event")
async def create_event(event: EventCreate):
    """Create a new event"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO events (title, start_date, start_time) VALUES (?, ?, ?)",
        (event.title, event.date, event.time)
    )
    conn.commit()
    event_id = cursor.lastrowid
    conn.close()
    
    return {"id": event_id, "status": "created"}

# Memory endpoints (if available)
if HAS_MEMORY:
    @app.post("/api/memory/person")
    async def add_person(name: str, facts: List[str] = []):
        """Add person to memory"""
        try:
            result = memory.add_person(name, facts)
            return result
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    @app.get("/api/memory/search")
    async def search_memory(query: str):
        """Search memories"""
        try:
            results = memory.search_memories(query)
            return {"results": results}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

# Voice endpoints (stub for now)
@app.post("/api/voice/transcribe")
async def transcribe_audio(file: UploadFile = File(...)):
    """Forward to Whisper service"""
    try:
        async with httpx.AsyncClient() as client:
            files = {"file": (file.filename, await file.read(), file.content_type)}
            response = await client.post("http://zoe-whisper:9001/transcribe", files=files)
            return response.json()
    except:
        return {"error": "Whisper service not available"}

# Developer status
@app.get("/api/developer/status")
async def developer_status():
    """Get system status for developer dashboard"""
    return {
        "status": "operational",
        "services": {
            "core": "healthy",
            "ollama": check_service("zoe-ollama", 11434),
            "redis": check_service("zoe-redis", 6379),
            "whisper": check_service("zoe-whisper", 9001),
            "tts": check_service("zoe-tts", 9002)
        },
        "timestamp": datetime.now().isoformat()
    }

def check_service(host, port):
    """Check if a service is running"""
    import socket
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        result = sock.connect_ex((host, port))
        sock.close()
        return "healthy" if result == 0 else "offline"
    except:
        return "error"

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
