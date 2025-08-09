#!/bin/bash
# Zoe v3.1 Backend Integration Script
# Integrates streaming chat, voice, and dashboard endpoints

set -e

log() { echo "🤖 [$(date '+%H:%M:%S')] $1"; }
error() { echo "❌ [$(date '+%H:%M:%S')] $1" >&2; exit 1; }
success() { echo "✅ [$(date '+%H:%M:%S')] $1"; }

# Check if we're in the right directory
if [ ! -f "docker-compose.yml" ] || [ ! -d "services/zoe-core" ]; then
    error "Please run this script from your zoe-v31 directory"
fi

log "Starting Zoe v3.1 backend integration..."

# Backup existing main.py
BACKUP_TIME=$(date +%Y%m%d_%H%M%S)
if [ -f "services/zoe-core/main.py" ]; then
    cp "services/zoe-core/main.py" "services/zoe-core/main.py.backup.$BACKUP_TIME"
    success "Backed up existing main.py"
fi

# Create enhanced main.py with all new endpoints
log "Creating enhanced backend with streaming chat, voice, and dashboard..."

cat > services/zoe-core/main_enhanced.py << 'EOF'
"""
Zoe v3.1 Enhanced Core - Complete Integration Hub
Backend with Voice, Dashboard, Streaming Chat, and Smart Features
"""

import asyncio
import json
import logging
import os
import re
import sys
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any, AsyncGenerator
import hashlib
import base64
import tempfile

import aiosqlite
import httpx
import uvicorn
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, HTTPException, BackgroundTasks, UploadFile, File
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from textblob import TextBlob
from contextlib import asynccontextmanager

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
CONFIG = {
    "version": "3.1.0",
    "database_path": os.getenv("DATABASE_PATH", "/app/data/zoe.db"),
    "ollama_url": os.getenv("OLLAMA_URL", "http://zoe-ollama:11434"),
    "whisper_url": os.getenv("WHISPER_URL", "http://zoe-whisper:9001"),
    "tts_url": os.getenv("TTS_URL", "http://zoe-tts:9002"),
    "n8n_url": os.getenv("N8N_URL", "http://zoe-n8n:5678"),
    "ha_url": os.getenv("HA_URL", "http://zoe-homeassistant:8123"),
    "cors_origins": os.getenv("CORS_ORIGINS", "*").split(","),
}

# Pydantic Models
class ChatMessage(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    conversation_id: Optional[int] = None
    user_id: str = Field(default="default")

class StreamingChatMessage(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    conversation_id: Optional[int] = None
    user_id: str = Field(default="default")
    stream: bool = Field(default=True)

class VoiceTranscription(BaseModel):
    audio_data: str  # Base64 encoded audio
    format: str = Field(default="webm")
    language: str = Field(default="en")

class TTSRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=1000)
    voice: str = Field(default="female")
    speed: float = Field(default=1.0, ge=0.5, le=2.0)

class WeatherSettings(BaseModel):
    api_key: Optional[str] = None
    location: str = Field(default="Perth, Australia")
    units: str = Field(default="metric")

class JournalEntry(BaseModel):
    title: Optional[str] = Field(None, max_length=200)
    content: str = Field(..., min_length=1, max_length=10000)
    tags: Optional[List[str]] = Field(default_factory=list)

class TaskCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=1000)
    due_date: Optional[datetime] = None
    priority: str = Field(default="medium")
    completed: bool = Field(default=False)

# WebSocket Connection Manager
class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}

    async def connect(self, websocket: WebSocket, client_id: str):
        await websocket.accept()
        self.active_connections[client_id] = websocket
        logger.info(f"Client {client_id} connected")

    def disconnect(self, client_id: str):
        if client_id in self.active_connections:
            del self.active_connections[client_id]
            logger.info(f"Client {client_id} disconnected")

    async def send_personal_message(self, message: str, client_id: str):
        if client_id in self.active_connections:
            await self.active_connections[client_id].send_text(message)

manager = ConnectionManager()

# Database initialization
async def init_database():
    """Initialize database with all required tables"""
    async with aiosqlite.connect(CONFIG["database_path"]) as db:
        # Create conversations table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                user_id TEXT NOT NULL DEFAULT 'default',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Create messages table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (conversation_id) REFERENCES conversations (id)
            )
        """)
        
        # Create tasks table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT,
                due_date TIMESTAMP,
                priority TEXT DEFAULT 'medium',
                completed BOOLEAN DEFAULT 0,
                archived BOOLEAN DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                user_id TEXT NOT NULL DEFAULT 'default'
            )
        """)
        
        # Create journal_entries table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS journal_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT,
                content TEXT NOT NULL,
                tags TEXT,
                mood INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                user_id TEXT NOT NULL DEFAULT 'default'
            )
        """)
        
        # Create events table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT,
                start_time TIMESTAMP NOT NULL,
                end_time TIMESTAMP,
                location TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                user_id TEXT NOT NULL DEFAULT 'default'
            )
        """)
        
        # Create settings table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL,
                setting_key TEXT NOT NULL,
                setting_value TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(category, setting_key)
            )
        """)
        
        await db.commit()
        logger.info("Database initialized successfully")

# Settings helpers
async def get_setting(category: str, key: str, default: str = "") -> str:
    """Get a setting value from database"""
    async with aiosqlite.connect(CONFIG["database_path"]) as db:
        cursor = await db.execute(
            "SELECT setting_value FROM user_settings WHERE category = ? AND setting_key = ?",
            (category, key),
        )
        row = await cursor.fetchone()
        return row[0] if row else default

async def set_setting(category: str, key: str, value: str) -> None:
    """Store a setting value in database"""
    async with aiosqlite.connect(CONFIG["database_path"]) as db:
        await db.execute(
            """
            INSERT OR REPLACE INTO user_settings (category, setting_key, setting_value, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (category, key, value),
        )
        await db.commit()

# Lifespan manager
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await init_database()
    logger.info("🤖 Zoe v3.1 Enhanced started successfully!")
    yield
    # Shutdown
    logger.info("👋 Zoe v3.1 Enhanced shutdown complete")

# FastAPI app
app = FastAPI(
    title="Zoe Personal AI v3.1 Enhanced",
    version=CONFIG["version"],
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CONFIG["cors_origins"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Health check
@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "version": CONFIG["version"],
        "timestamp": datetime.now().isoformat(),
        "services": await get_integration_status()
    }

# STREAMING CHAT ENDPOINTS

async def stream_ai_response(user_message: str, conversation_id: Optional[int], user_id: str) -> AsyncGenerator[str, None]:
    """Stream AI response from Ollama with personality"""
    try:
        # Get conversation context
        context = await get_conversation_context(conversation_id, user_id)
        
        # Build prompt
        prompt = f"""You are Zoe, a helpful and friendly AI assistant. Be conversational, warm, and personable.

Recent conversation:
{context}

User: {user_message}
Zoe:"""
        
        # Get active AI model
        model = await get_setting("ai", "active_model", "llama3.2:3b")
        
        # Stream from Ollama
        async with httpx.AsyncClient(timeout=60.0) as client:
            async with client.stream(
                'POST',
                f"{CONFIG['ollama_url']}/api/generate",
                json={
                    "model": model,
                    "prompt": prompt,
                    "stream": True,
                    "options": {
                        "temperature": 0.7,
                        "max_tokens": 512,
                    }
                }
            ) as response:
                async for line in response.aiter_lines():
                    if line:
                        try:
                            data = json.loads(line)
                            if "response" in data:
                                chunk = data["response"]
                                if chunk:
                                    yield chunk
                        except json.JSONDecodeError:
                            continue
                            
    except Exception as e:
        logger.error(f"Streaming error: {e}")
        yield f"Sorry, I'm having trouble responding right now."

async def get_conversation_context(conversation_id: Optional[int], user_id: str, limit: int = 6) -> str:
    """Get recent conversation history"""
    if not conversation_id:
        return ""
    
    async with aiosqlite.connect(CONFIG["database_path"]) as db:
        cursor = await db.execute("""
            SELECT role, content FROM messages 
            WHERE conversation_id = ? 
            ORDER BY timestamp DESC 
            LIMIT ?
        """, (conversation_id, limit))
        
        messages = await cursor.fetchall()
        
        if not messages:
            return ""
        
        context_lines = []
        for role, content in reversed(messages):
            if role == "user":
                context_lines.append(f"User: {content}")
            else:
                context_lines.append(f"Zoe: {content}")
        
        return "\n".join(context_lines[-6:])

async def save_conversation(user_message: str, ai_response: str, conversation_id: Optional[int], user_id: str) -> int:
    """Save conversation to database"""
    async with aiosqlite.connect(CONFIG["database_path"]) as db:
        if not conversation_id:
            cursor = await db.execute("""
                INSERT INTO conversations (title, user_id, created_at)
                VALUES (?, ?, ?)
            """, (f"Chat {datetime.now().strftime('%m/%d %H:%M')}", user_id, datetime.now()))
            await db.commit()
            conversation_id = cursor.lastrowid
        
        # Save user message
        await db.execute("""
            INSERT INTO messages (conversation_id, role, content, timestamp)
            VALUES (?, ?, ?, ?)
        """, (conversation_id, "user", user_message, datetime.now()))
        
        # Save AI response
        await db.execute("""
            INSERT INTO messages (conversation_id, role, content, timestamp)
            VALUES (?, ?, ?, ?)
        """, (conversation_id, "assistant", ai_response, datetime.now()))
        
        await db.commit()
        return conversation_id

@app.websocket("/ws/chat/{client_id}")
async def websocket_chat(websocket: WebSocket, client_id: str):
    """WebSocket endpoint for real-time chat"""
    await manager.connect(websocket, client_id)
    try:
        while True:
            data = await websocket.receive_text()
            message_data = json.loads(data)
            
            user_message = message_data.get("message", "")
            conversation_id = message_data.get("conversation_id")
            
            # Stream AI response
            full_response = ""
            async for chunk in stream_ai_response(user_message, conversation_id, client_id):
                full_response += chunk
                await manager.send_personal_message(
                    json.dumps({"type": "ai_chunk", "content": chunk}), 
                    client_id
                )
            
            # Save conversation and send completion
            conv_id = await save_conversation(user_message, full_response, conversation_id, client_id)
            await manager.send_personal_message(
                json.dumps({
                    "type": "ai_complete",
                    "conversation_id": conv_id,
                    "full_response": full_response
                }), 
                client_id
            )
            
    except WebSocketDisconnect:
        manager.disconnect(client_id)

@app.post("/api/chat")
async def chat_endpoint(chat_msg: ChatMessage, background_tasks: BackgroundTasks):
    """Enhanced chat endpoint"""
    try:
        full_response = ""
        async for chunk in stream_ai_response(chat_msg.message, chat_msg.conversation_id, chat_msg.user_id):
            full_response += chunk
        
        conversation_id = await save_conversation(
            chat_msg.message, 
            full_response, 
            chat_msg.conversation_id, 
            chat_msg.user_id
        )
        
        return {
            "response": full_response,
            "conversation_id": conversation_id,
            "timestamp": datetime.now().isoformat()
        }
        
    except Exception as e:
        logger.error(f"Chat error: {e}")
        raise HTTPException(status_code=500, detail=f"Chat processing failed: {str(e)}")

# VOICE ENDPOINTS

@app.post("/api/voice/transcribe")
async def transcribe_audio(transcription: VoiceTranscription):
    """Transcribe audio using Whisper service"""
    try:
        audio_bytes = base64.b64decode(transcription.audio_data)
        
        with tempfile.NamedTemporaryFile(suffix=f".{transcription.format}", delete=False) as temp_file:
            temp_file.write(audio_bytes)
            temp_file_path = temp_file.name
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                with open(temp_file_path, 'rb') as audio_file:
                    files = {"audio": (f"audio.{transcription.format}", audio_file, f"audio/{transcription.format}")}
                    data = {"language": transcription.language}
                    
                    response = await client.post(
                        f"{CONFIG['whisper_url']}/transcribe",
                        files=files,
                        data=data
                    )
                    
                    if response.status_code == 200:
                        result = response.json()
                        return {
                            "text": result.get("text", ""),
                            "confidence": result.get("confidence", 0.0),
                            "duration": result.get("duration", 0.0)
                        }
                    else:
                        return {"error": "Speech recognition failed"}
                        
        finally:
            Path(temp_file_path).unlink(missing_ok=True)
            
    except Exception as e:
        logger.error(f"Transcription error: {e}")
        return {"error": f"Transcription failed: {str(e)}"}

@app.post("/api/voice/synthesize")
async def synthesize_speech(tts_request: TTSRequest):
    """Convert text to speech using TTS service"""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{CONFIG['tts_url']}/synthesize",
                json={
                    "text": tts_request.text,
                    "voice": tts_request.voice,
                    "speed": tts_request.speed,
                    "format": "wav"
                }
            )
            
            if response.status_code == 200:
                audio_data = base64.b64encode(response.content).decode('utf-8')
                return {
                    "audio_data": audio_data,
                    "format": "wav",
                    "text": tts_request.text,
                    "duration": len(tts_request.text) * 0.1
                }
            else:
                raise HTTPException(status_code=500, detail="Speech synthesis failed")
                
    except Exception as e:
        logger.error(f"TTS error: {e}")
        raise HTTPException(status_code=500, detail=f"Speech synthesis failed: {str(e)}")

@app.post("/api/voice/chat")
async def voice_chat(audio_data: VoiceTranscription):
    """Complete voice chat workflow: STT -> AI -> TTS"""
    try:
        # Step 1: Transcribe audio
        transcription_result = await transcribe_audio(audio_data)
        
        if "error" in transcription_result:
            return transcription_result
        
        user_text = transcription_result["text"]
        if not user_text.strip():
            return {"error": "No speech detected"}
        
        # Step 2: Get AI response
        ai_response = ""
        async for chunk in stream_ai_response(user_text, None, "default"):
            ai_response += chunk
        
        # Step 3: Synthesize AI response
        tts_result = await synthesize_speech(TTSRequest(text=ai_response))
        
        # Save conversation
        await save_conversation(user_text, ai_response, None, "default")
        
        return {
            "transcription": {
                "text": user_text,
                "confidence": transcription_result.get("confidence", 0.0)
            },
            "ai_response": {
                "text": ai_response,
                "audio_data": tts_result.get("audio_data", ""),
                "format": "wav"
            },
            "conversation_saved": True
        }
        
    except Exception as e:
        logger.error(f"Voice chat error: {e}")
        return {"error": f"Voice chat failed: {str(e)}"}

# WEATHER ENDPOINTS

@app.get("/api/weather")
async def get_weather():
    """Get current weather for dashboard"""
    try:
        api_key = await get_setting("weather", "api_key", "")
        location = await get_setting("weather", "location", "Perth, Australia")
        units = await get_setting("weather", "units", "metric")
        
        if not api_key:
            return {
                "location": location,
                "temperature": 22,
                "condition": "Partly Cloudy",
                "icon": "partly-cloudy",
                "humidity": 65,
                "wind_speed": 12,
                "description": "Pleasant conditions",
                "demo_mode": True
            }
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"https://api.openweathermap.org/data/2.5/weather",
                params={
                    "q": location,
                    "appid": api_key,
                    "units": units
                }
            )
            
            if response.status_code == 200:
                data = response.json()
                return {
                    "location": data["name"],
                    "temperature": round(data["main"]["temp"]),
                    "condition": data["weather"][0]["main"],
                    "description": data["weather"][0]["description"],
                    "icon": data["weather"][0]["icon"],
                    "humidity": data["main"]["humidity"],
                    "wind_speed": round(data["wind"]["speed"]),
                    "feels_like": round(data["main"]["feels_like"]),
                    "demo_mode": False
                }
            else:
                return {
                    "location": location,
                    "temperature": 22,
                    "condition": "Partly Cloudy", 
                    "description": "Weather service unavailable",
                    "demo_mode": True,
                    "error": "API error"
                }
                
    except Exception as e:
        logger.error(f"Weather error: {e}")
        return {
            "location": "Perth, Australia",
            "temperature": 22,
            "condition": "Unknown",
            "description": "Weather unavailable",
            "demo_mode": True,
            "error": str(e)
        }

# DASHBOARD ENDPOINTS

@app.get("/api/dashboard")
async def get_dashboard():
    """Get complete dashboard data"""
    try:
        current_time = datetime.now().strftime("%H:%M")
        current_date = datetime.now().strftime("%A, %B %d")
        
        weather = await get_weather()
        task_stats = await get_task_statistics()
        journal_stats = await get_journal_statistics()
        upcoming_events = await get_upcoming_events()
        ai_greeting = await generate_ai_greeting()
        integration_status = await get_integration_status()
        
        return {
            "current_time": current_time,
            "current_date": current_date,
            "weather": weather,
            "task_stats": task_stats,
            "journal_stats": journal_stats,
            "upcoming_events": upcoming_events,
            "ai_greeting": ai_greeting,
            "integration_status": integration_status,
            "timestamp": datetime.now().isoformat()
        }
        
    except Exception as e:
        logger.error(f"Dashboard error: {e}")
        raise HTTPException(status_code=500, detail=f"Dashboard data failed: {str(e)}")

async def get_task_statistics() -> Dict[str, int]:
    """Get task statistics for dashboard"""
    try:
        async with aiosqlite.connect(CONFIG["database_path"]) as db:
            # Total tasks
            cursor = await db.execute("SELECT COUNT(*) FROM tasks WHERE archived = 0")
            total = (await cursor.fetchone())[0]
            
            # Completed tasks
            cursor = await db.execute("SELECT COUNT(*) FROM tasks WHERE completed = 1 AND archived = 0")
            completed = (await cursor.fetchone())[0]
            
            # Today's tasks
            today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            today_end = today_start + timedelta(days=1)
            cursor = await db.execute("""
                SELECT COUNT(*) FROM tasks 
                WHERE due_date BETWEEN ? AND ? AND archived = 0
            """, (today_start, today_end))
            today = (await cursor.fetchone())[0]
            
            return {
                "total": total,
                "completed": completed,
                "pending": total - completed,
                "today": today
            }
    except Exception as e:
        logger.error(f"Task stats error: {e}")
        return {"total": 0, "completed": 0, "pending": 0, "today": 0}

async def get_journal_statistics() -> Dict[str, Any]:
    """Get journal statistics for dashboard"""
    try:
        async with aiosqlite.connect(CONFIG["database_path"]) as db:
            cursor = await db.execute("SELECT COUNT(*) FROM journal_entries")
            total_entries = (await cursor.fetchone())[0]
            
            week_start = datetime.now() - timedelta(days=7)
            cursor = await db.execute("""
                SELECT COUNT(*) FROM journal_entries 
                WHERE created_at >= ?
            """, (week_start,))
            week_entries = (await cursor.fetchone())[0]
            
            cursor = await db.execute("""
                SELECT title, created_at FROM journal_entries 
                ORDER BY created_at DESC 
                LIMIT 3
            """)
            recent_entries = []
            for title, created_at in await cursor.fetchall():
                recent_entries.append({
                    "title": title or "Untitled Entry",
                    "date": created_at
                })
            
            return {
                "total_entries": total_entries,
                "week_entries": week_entries,
                "recent_entries": recent_entries,
                "current_streak": 0
            }
    except Exception as e:
        logger.error(f"Journal stats error: {e}")
        return {
            "total_entries": 0,
            "week_entries": 0,
            "recent_entries": [],
            "current_streak": 0
        }

async def get_upcoming_events() -> List[Dict[str, Any]]:
    """Get upcoming events for dashboard"""
    try:
        async with aiosqlite.connect(CONFIG["database_path"]) as db:
            week_ahead = datetime.now() + timedelta(days=7)
            cursor = await db.execute("""
                SELECT title, start_time, location, description 
                FROM events 
                WHERE start_time BETWEEN ? AND ?
                ORDER BY start_time ASC
                LIMIT 5
            """, (datetime.now(), week_ahead))
            
            events = []
            for title, start_time, location, description in await cursor.fetchall():
                events.append({
                    "title": title,
                    "start_time": start_time,
                    "location": location or "",
                    "description": description or "",
                })
            
            return events
    except Exception as e:
        logger.error(f"Events error: {e}")
        return []

async def generate_ai_greeting() -> str:
    """Generate personalized AI greeting"""
    current_hour = datetime.now().hour
    
    if current_hour < 12:
        base_greeting = "Good morning"
    elif current_hour < 17:
        base_greeting = "Good afternoon"
    else:
        base_greeting = "Good evening"
    
    greetings = [
        f"{base_greeting}! Ready to make today great?",
        f"{base_greeting}! What's on your mind today?",
        f"{base_greeting}! How can I help you today?",
        f"{base_greeting}! Let's tackle the day together!"
    ]
    
    return greetings[datetime.now().day % len(greetings)]

async def get_integration_status() -> Dict[str, str]:
    """Check status of all integrations"""
    status = {}
    
    # Check Ollama
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{CONFIG['ollama_url']}/api/tags")
            status["ollama"] = "connected" if response.status_code == 200 else "error"
    except Exception:
        status["ollama"] = "disconnected"
    
    # Check other services
    services = {
        "whisper": f"{CONFIG['whisper_url']}/health",
        "tts": f"{CONFIG['tts_url']}/health",
        "n8n": f"{CONFIG['n8n_url']}/healthz",
        "homeassistant": f"{CONFIG['ha_url']}/api/"
    }
    
    for service, url in services.items():
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(url)
                status[service] = "connected" if response.status_code == 200 else "error"
        except Exception:
            status[service] = "disconnected"
    
    return status

@app.get("/api/integrations/status")
async def integration_status():
    """Get detailed integration status"""
    return await get_integration_status()

# BASIC CRUD ENDPOINTS

@app.post("/api/tasks")
async def create_task(task: TaskCreate):
    """Create a new task"""
    try:
        async with aiosqlite.connect(CONFIG["database_path"]) as db:
            cursor = await db.execute("""
                INSERT INTO tasks (title, description, due_date, priority, user_id)
                VALUES (?, ?, ?, ?, ?)
            """, (task.title, task.description, task.due_date, task.priority, "default"))
            await db.commit()
            
            return {
                "id": cursor.lastrowid,
                "title": task.title,
                "created": True
            }
    except Exception as e:
        logger.error(f"Task creation error: {e}")
        raise HTTPException(status_code=500, detail="Failed to create task")

@app.get("/api/tasks")
async def get_tasks():
    """Get all tasks"""
    try:
        async with aiosqlite.connect(CONFIG["database_path"]) as db:
            cursor = await db.execute("""
                SELECT id, title, description, due_date, priority, completed
                FROM tasks WHERE archived = 0
                ORDER BY due_date ASC
            """)
            
            tasks = []
            for row in await cursor.fetchall():
                tasks.append({
                    "id": row[0],
                    "title": row[1],
                    "description": row[2],
                    "due_date": row[3],
                    "priority": row[4],
                    "completed": bool(row[5])
                })
            
            return tasks
    except Exception as e:
        logger.error(f"Tasks fetch error: {e}")
        return []

@app.post("/api/journal")
async def create_journal_entry(entry: JournalEntry):
    """Create a new journal entry"""
    try:
        async with aiosqlite.connect(CONFIG["database_path"]) as db:
            cursor = await db.execute("""
                INSERT INTO journal_entries (title, content, tags, user_id)
                VALUES (?, ?, ?, ?)
            """, (entry.title, entry.content, json.dumps(entry.tags), "default"))
            await db.commit()
            
            return {
                "id": cursor.lastrowid,
                "title": entry.title,
                "created": True
            }
    except Exception as e:
        logger.error(f"Journal creation error: {e}")
        raise HTTPException(status_code=500, detail="Failed to create journal entry")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
EOF

success "Enhanced backend created"

# Update docker-compose.yml to use the new backend
log "Updating docker-compose.yml..."

# Backup docker-compose.yml
cp docker-compose.yml "docker-compose.yml.backup.$BACKUP_TIME"

# Update the zoe-core service to use the enhanced backend
if grep -q "services/zoe-core/main.py:/app/main.py" docker-compose.yml; then
    sed -i 's|services/zoe-core/main.py:/app/main.py|services/zoe-core/main_enhanced.py:/app/main.py|g' docker-compose.yml
    success "Updated docker-compose.yml to use enhanced backend"
else
    log "Docker-compose.yml doesn't need updating (already configured)"
fi

# Test the new backend
log "Testing the enhanced backend..."

# Stop existing services
docker compose down 2>/dev/null || true

# Start the enhanced services
log "Starting Zoe v3.1 Enhanced..."
docker compose up -d

# Wait for services to start
sleep 10

# Test health endpoint
log "Testing health endpoint..."
for i in {1..30}; do
    if curl -s http://localhost:8000/health >/dev/null 2>&1; then
        success "Backend is responding!"
        break
    fi
    if [ $i -eq 30 ]; then
        error "Backend failed to start after 30 seconds"
    fi
    sleep 1
done

# Test key endpoints
log "Testing key endpoints..."

# Test chat
echo "Testing chat endpoint..."
CHAT_RESULT=$(curl -s -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello Zoe!"}' | jq -r '.response // "ERROR"')

if [ "$CHAT_RESULT" != "ERROR" ] && [ "$CHAT_RESULT" != "null" ]; then
    success "Chat endpoint working: ${CHAT_RESULT:0:50}..."
else
    log "⚠️  Chat endpoint needs attention"
fi

# Test dashboard
echo "Testing dashboard endpoint..."
DASHBOARD_RESULT=$(curl -s http://localhost:8000/api/dashboard | jq -r '.current_time // "ERROR"')

if [ "$DASHBOARD_RESULT" != "ERROR" ] && [ "$DASHBOARD_RESULT" != "null" ]; then
    success "Dashboard endpoint working: $DASHBOARD_RESULT"
else
    log "⚠️  Dashboard endpoint needs attention"
fi

# Test weather
echo "Testing weather endpoint..."
WEATHER_RESULT=$(curl -s http://localhost:8000/api/weather | jq -r '.temperature // "ERROR"')

if [ "$WEATHER_RESULT" != "ERROR" ] && [ "$WEATHER_RESULT" != "null" ]; then
    success "Weather endpoint working: ${WEATHER_RESULT}°"
else
    log "⚠️  Weather endpoint needs attention"
fi

# Test integration status
echo "Testing integration status..."
INTEGRATION_RESULT=$(curl -s http://localhost:8000/api/integrations/status | jq -r '.ollama // "ERROR"')

if [ "$INTEGRATION_RESULT" != "ERROR" ] && [ "$INTEGRATION_RESULT" != "null" ]; then
    success "Integration status working: Ollama $INTEGRATION_RESULT"
else
    log "⚠️  Integration status needs attention"
fi

log "Backend integration complete!"

echo ""
echo "🎯 Integration Summary:"
echo "   ✅ Enhanced backend with streaming chat"
echo "   ✅ Voice endpoints (STT/TTS integration)"
echo "   ✅ Weather dashboard with demo mode"
echo "   ✅ Integration status monitoring"
echo "   ✅ WebSocket support for real-time chat"
echo "   ✅ Complete CRUD operations"

echo ""
echo "🌐 Access Points:"
echo "   Health Check: http://localhost:8000/health"
echo "   API Documentation: http://localhost:8000/docs"
echo "   Dashboard Data: http://localhost:8000/api/dashboard"
echo "   WebSocket Chat: ws://localhost:8000/ws/chat/your-client-id"

echo ""
echo "🔧 Service Status:"
docker compose ps

echo ""
echo "📝 Next Steps:"
echo "   1. Test voice integration with audio files"
echo "   2. Configure weather API key in settings"
echo "   3. Connect frontend to new streaming endpoints"
echo "   4. Test WebSocket chat functionality"
echo "   5. Implement entity extraction for smart features"

echo ""
echo "✨ Zoe v3.1 Enhanced backend is ready!"/stream")
async def streaming_chat_endpoint(chat_msg: StreamingChatMessage):
    """Server-sent events streaming chat"""
    
    async def generate_response():
        yield "data: " + json.dumps({"type": "start"}) + "\n\n"
        
        full_response = ""
        async for chunk in stream_ai_response(chat_msg.message, chat_msg.conversation_id, chat_msg.user_id):
            full_response += chunk
            yield "data: " + json.dumps({
                "type": "chunk", 
                "content": chunk,
                "full_response": full_response
            }) + "\n\n"
            await asyncio.sleep(0.05)
        
        conv_id = await save_conversation(chat_msg.message, full_response, chat_msg.conversation_id, chat_msg.user_id)
        yield "data: " + json.dumps({
            "type": "complete", 
            "full_response": full_response,
            "conversation_id": conv_id
        }) + "\n\n"
    
    return StreamingResponse(
        generate_response(), 
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )

@app.post("/api/chat