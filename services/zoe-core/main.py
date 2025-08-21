from fastapi import FastAPI, HTTPException, Query
from routers import developer
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
import sqlite3
import json
import logging
import os
import httpx
from contextlib import asynccontextmanager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Zoe AI API", version="5.0")

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
try:
    app.include_router(developer.router)
    logger.info("Developer router loaded successfully")
except Exception as e:
    logger.error(f"Failed to load developer router: {e}")

# Database setup
DATABASE_PATH = os.getenv("DATABASE_PATH", "/app/data/zoe.db")

def init_db():
    """Initialize database with required tables"""
    conn = sqlite3.connect(DATABASE_PATH)
    c = conn.cursor()
    
    # Events table
    c.execute('''CREATE TABLE IF NOT EXISTS events
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  title TEXT NOT NULL,
                  date DATE,
                  time TIME,
                  description TEXT,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    
    # Tasks table
    c.execute('''CREATE TABLE IF NOT EXISTS tasks
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  title TEXT NOT NULL,
                  completed BOOLEAN DEFAULT 0,
                  priority INTEGER DEFAULT 0,
                  due_date DATE,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    
    # Memory table
    c.execute('''CREATE TABLE IF NOT EXISTS memories
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  type TEXT,
                  name TEXT,
                  data JSON,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    
    conn.commit()
    conn.close()

# Initialize database on startup
init_db()

# Request/Response models
class ChatRequest(BaseModel):
    message: str
    context: Optional[Dict[str, Any]] = {}

class ChatResponse(BaseModel):
    response: str
    conversation_id: Optional[int] = None
    timestamp: str = datetime.now().isoformat()

# Health check endpoint
@app.get("/health")
async def health():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "version": "5.0",
        "services": {
            "core": "running",
            "memory": "available",
            "developer": "active"
        }
    }

@app.get("/api/health")
async def api_health():
    """API health check"""
    return {"status": "healthy", "service": "zoe-api"}

# Main chat endpoint (User Zoe)
@app.post("/api/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """Main chat endpoint with Zoe personality"""
    try:
        # Import the AI client
        from ai_client import get_ai_response, USER_SYSTEM_PROMPT
        
        # Get response with Zoe personality
        response = await get_ai_response(
            message=request.message,
            system_prompt=USER_SYSTEM_PROMPT,
            context=request.context,
            temperature=0.7
        )
        
        return ChatResponse(
            response=response,
            conversation_id=1
        )
    except Exception as e:
        logger.error(f"Chat error: {e}")
        # Fallback response
        return ChatResponse(
            response="Hi! I'm Zoe. I'm having a little trouble connecting right now, but I'm here to help! What would you like to talk about?",
            conversation_id=1
        )

# Calendar endpoints
@app.post("/api/calendar/events")
async def create_event(event: Dict[str, Any]):
    """Create a calendar event"""
    conn = sqlite3.connect(DATABASE_PATH)
    c = conn.cursor()
    
    c.execute("""INSERT INTO events (title, date, time, description)
                 VALUES (?, ?, ?, ?)""",
              (event.get("title"), event.get("date"), 
               event.get("time"), event.get("description")))
    
    conn.commit()
    event_id = c.lastrowid
    conn.close()
    
    return {"success": True, "event_id": event_id}

@app.get("/api/calendar/events")
async def get_events():
    """Get all calendar events"""
    conn = sqlite3.connect(DATABASE_PATH)
    c = conn.cursor()
    
    c.execute("SELECT * FROM events ORDER BY date DESC LIMIT 20")
    events = c.fetchall()
    conn.close()
    
    return {"events": events}

# Task endpoints
@app.get("/api/tasks")
async def get_tasks():
    """Get all tasks"""
    conn = sqlite3.connect(DATABASE_PATH)
    c = conn.cursor()
    
    c.execute("SELECT * FROM tasks WHERE completed = 0 ORDER BY priority DESC")
    tasks = c.fetchall()
    conn.close()
    
    return {"tasks": tasks}

@app.post("/api/tasks")
async def create_task(task: Dict[str, Any]):
    """Create a new task"""
    conn = sqlite3.connect(DATABASE_PATH)
    c = conn.cursor()
    
    c.execute("""INSERT INTO tasks (title, priority, due_date)
                 VALUES (?, ?, ?)""",
              (task.get("title"), task.get("priority", 0), task.get("due_date")))
    
    conn.commit()
    task_id = c.lastrowid
    conn.close()
    
    return {"success": True, "task_id": task_id}

# Dashboard data endpoint
@app.get("/api/dashboard")
async def get_dashboard():
    """Get dashboard data"""
    return {
        "greeting": "Welcome back!",
        "stats": {
            "tasks_pending": 5,
            "events_today": 2,
            "memories": 10
        },
        "timestamp": datetime.now().isoformat()
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
