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

# ============================================
# AUTONOMOUS SYSTEM INTEGRATION
# ============================================

# Import the autonomous router
try:
    from routers import autonomous
    app.include_router(autonomous.router)
    print("✅ Autonomous developer system loaded")
except Exception as e:
    print(f"⚠️ Could not load autonomous system: {e}")

# Add developer dashboard endpoint
@app.get("/api/developer/dashboard")
async def developer_dashboard():
    """Get complete developer dashboard data"""
    
    # Get system overview
    system_data = {}
    try:
        import docker
        client = docker.from_env()
        containers = []
        for c in client.containers.list(all=True):
            if c.name.startswith('zoe-'):
                containers.append({
                    "name": c.name,
                    "status": c.status,
                    "health": "healthy" if c.status == "running" else "unhealthy"
                })
        system_data["containers"] = containers
    except:
        system_data["containers"] = []
    
    # Get pending tasks
    tasks = []
    try:
        conn = sqlite3.connect("/app/data/developer_tasks.db")
        cursor = conn.cursor()
        cursor.execute("""
            SELECT task_id, title, priority, status 
            FROM tasks 
            WHERE status IN ('pending', 'in_progress')
            ORDER BY 
                CASE priority 
                    WHEN 'critical' THEN 1 
                    WHEN 'high' THEN 2 
                    WHEN 'medium' THEN 3 
                    WHEN 'low' THEN 4 
                END
            LIMIT 5
        """)
        for row in cursor.fetchall():
            tasks.append({
                "task_id": row[0],
                "title": row[1],
                "priority": row[2],
                "status": row[3]
            })
        conn.close()
    except:
        pass
    
    # Get recent solutions
    solutions = []
    try:
        conn = sqlite3.connect("/app/data/knowledge.db")
        cursor = conn.cursor()
        cursor.execute("""
            SELECT problem, solution, timestamp 
            FROM proven_solutions 
            ORDER BY timestamp DESC 
            LIMIT 3
        """)
        for row in cursor.fetchall():
            solutions.append({
                "problem": row[0],
                "solution": row[1],
                "timestamp": row[2]
            })
        conn.close()
    except:
        pass
    
    return {
        "system": system_data,
        "tasks": tasks,
        "recent_solutions": solutions,
        "claude_available": bool(os.getenv("CLAUDE_API_KEY"))
    }

# Chat endpoint with autonomous capabilities
@app.post("/api/developer/chat")
async def developer_chat(request: dict):
    """Enhanced chat that can execute fixes"""
    message = request.get("message", "")
    
    # Check for action keywords
    action_keywords = {
        "fix": "execute_fix",
        "deploy": "deploy_feature",
        "debug": "debug_issue",
        "optimize": "optimize_performance",
        "backup": "create_backup",
        "test": "run_tests"
    }
    
    action = None
    for keyword, action_type in action_keywords.items():
        if keyword in message.lower():
            action = action_type
            break
    
    response = {
        "message": message,
        "action_detected": action,
        "response": "",
        "execution_result": None
    }
    
    # If action detected, prepare for execution
    if action:
        response["response"] = f"I'll {action.replace('_', ' ')} for you. Analyzing the system..."
        
        # Get system context
        try:
            import httpx
            async with httpx.AsyncClient() as client:
                overview = await client.get("http://localhost:8000/api/developer/system/overview")
                diagnostics = await client.get("http://localhost:8000/api/developer/system/diagnostics")
                
                response["execution_result"] = {
                    "system_healthy": overview.status_code == 200,
                    "issues_found": diagnostics.json().get("issues", []) if diagnostics.status_code == 200 else [],
                    "ready_to_execute": True
                }
        except:
            response["execution_result"] = {"error": "Could not analyze system"}
    else:
        response["response"] = "I understand. How can I help you improve the Zoe system?"
    
    return response
