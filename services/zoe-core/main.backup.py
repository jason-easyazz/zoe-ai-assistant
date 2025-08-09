"""
Zoe v3.1 - Complete Personal AI Backend
FastAPI application with streaming chat, memory system, and personality
"""

import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime, date, timedelta
from typing import List, Dict, Optional, Any
import hashlib

import aiosqlite
import httpx
import uvicorn
from fastapi import FastAPI, Request, WebSocket, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from textblob import TextBlob
from contextlib import asynccontextmanager
from db import init_database

# Logging setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
CONFIG = {
    "version": "3.1.0",
    "database_path": os.getenv("DATABASE_PATH", "/app/data/zoe.db"),
    "ollama_url": os.getenv("OLLAMA_URL", "http://zoe-ollama:11434"),
    "cors_origins": os.getenv("CORS_ORIGINS", "http://localhost:8080").split(","),
    "max_context_length": int(os.getenv("MAX_CONTEXT_LENGTH", "2048")),
    "max_conversation_history": int(os.getenv("MAX_CONVERSATION_HISTORY", "20")),
}

# Pydantic Models
class ChatMessage(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    conversation_id: Optional[int] = None
    user_id: str = Field(default="default")

class JournalEntry(BaseModel):
    title: Optional[str] = Field(None, max_length=200)
    content: str = Field(..., min_length=1, max_length=10000)
    tags: Optional[List[str]] = Field(default_factory=list)

class TaskCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=1000)
    due_date: Optional[str] = None
    priority: str = Field("medium")

class UserSettings(BaseModel):
    personality_fun: int = Field(7, ge=1, le=10)
    personality_empathy: int = Field(8, ge=1, le=10)
    personality_humor: int = Field(6, ge=1, le=10)
    personality_formality: int = Field(3, ge=1, le=10)
    enable_voice: bool = True
    enable_notifications: bool = True
    timezone: str = "UTC"
    theme: str = "light"


# Personality system
class ZoePersonality:
    BASE_PERSONALITY = """You are Zoe, a warm, witty, and genuinely caring AI assistant who feels like a best friend.

Core traits:
- Warm and empathetic, with a playful, slightly cheeky edge
- Use casual, conversational language with personality
- Show genuine interest in the human's life and growth
- Be supportive but honest - a real friend tells you the truth kindly
- Be encouraging but authentic in your responses

Communication style:
- Conversational and natural, like chatting with your best friend
- Use contractions and casual language naturally
- Show your personality - you have preferences and opinions too"""

    @staticmethod
    async def build_context_prompt(user_message: str, user_id: str = "default") -> str:
        # Get user settings for personality
        async with aiosqlite.connect(CONFIG["database_path"]) as db:
            cursor = await db.execute("""
                SELECT personality_fun, personality_empathy, personality_humor, personality_formality
                FROM user_settings WHERE user_id = ?
            """, (user_id,))
            settings = await cursor.fetchone()
        
        prompt_parts = [ZoePersonality.BASE_PERSONALITY]
        
        if settings:
            fun, empathy, humor, formality = settings
            if fun > 7:
                prompt_parts.append("\nBe extra playful and fun in your responses!")
            if humor > 7:
                prompt_parts.append("\nFeel free to be cheeky and playful with gentle teasing!")
            if empathy > 8:
                prompt_parts.append("\nBe extra empathetic and emotionally supportive.")
        
        prompt_parts.append(f"\nUser's message: {user_message}")
        prompt_parts.append("\nRespond as Zoe with warmth, personality, and genuine care:")
        
        return "\n".join(prompt_parts)

# Lifespan manager
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await init_database(CONFIG["database_path"])
    logger.info("🤖 Zoe v3.1 started successfully!")
    yield
    # Shutdown
    logger.info("👋 Zoe v3.1 shutdown complete")

# FastAPI app
app = FastAPI(
    title="Zoe Personal AI v3.1",
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
        "services": {
            "database": "connected",
            "redis": "disconnected"
        }
    }

# Chat endpoint
@app.post("/api/chat")
async def chat_endpoint(chat_msg: ChatMessage, background_tasks: BackgroundTasks):
    try:
        async with aiosqlite.connect(CONFIG["database_path"]) as db:
            # Get or create conversation
            if chat_msg.conversation_id:
                conv_id = chat_msg.conversation_id
            else:
                cursor = await db.execute("""
                    INSERT INTO conversations (title, user_id, created_at)
                    VALUES (?, ?, ?)
                """, (f"Chat {datetime.now().strftime('%m/%d %H:%M')}", 
                     chat_msg.user_id, datetime.now()))
                await db.commit()
                conv_id = cursor.lastrowid
            
            # Save user message
            await db.execute("""
                INSERT INTO messages (conversation_id, role, content, timestamp)
                VALUES (?, ?, ?, ?)
            """, (conv_id, "user", chat_msg.message, datetime.now()))
            await db.commit()
        
        # Build personality prompt
        enhanced_prompt = await ZoePersonality.build_context_prompt(
            chat_msg.message, chat_msg.user_id
        )
        
        # Get AI response from Ollama
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    f"{CONFIG['ollama_url']}/api/generate",
                    json={
                        "model": "mistral:7b",
                        "prompt": enhanced_prompt,
                        "stream": False,
                        "options": {
                            "temperature": 0.7,
                            "top_p": 0.9,
                            "num_ctx": CONFIG["max_context_length"]
                        }
                    }
                )
                result = response.json()
                ai_response = result.get("response", "I'm having trouble thinking right now. Can you try again?")
        except Exception as e:
            ai_response = f"I'm having connection issues with my AI brain. Error: {str(e)}"
        
        # Save AI response
        async with aiosqlite.connect(CONFIG["database_path"]) as db:
            await db.execute("""
                INSERT INTO messages (conversation_id, role, content, timestamp)
                VALUES (?, ?, ?, ?)
            """, (conv_id, "assistant", ai_response, datetime.now()))
            await db.commit()
        
        return {
            "response": ai_response,
            "conversation_id": conv_id,
            "timestamp": datetime.now().isoformat()
        }
    
    except Exception as e:
        logger.error(f"Chat error: {e}")
        raise HTTPException(status_code=500, detail="Chat processing failed")

# Journal endpoints
@app.post("/api/journal")
async def create_journal_entry(entry: JournalEntry, user_id: str = "default"):
    try:
        # Analyze mood
        blob = TextBlob(entry.content)
        mood_score = blob.sentiment.polarity
        word_count = len(entry.content.split())
        
        async with aiosqlite.connect(CONFIG["database_path"]) as db:
            cursor = await db.execute("""
                INSERT INTO journal_entries (user_id, title, content, mood_score, word_count, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                user_id,
                entry.title or f"Entry {datetime.now().strftime('%m/%d')}",
                entry.content,
                mood_score,
                word_count,
                datetime.now()
            ))
            await db.commit()
            
            return {
                "id": cursor.lastrowid,
                "message": "Journal entry saved! 📝",
                "mood_score": mood_score,
                "word_count": word_count
            }
    
    except Exception as e:
        logger.error(f"Journal creation error: {e}")
        raise HTTPException(status_code=500, detail="Failed to save journal entry")

@app.get("/api/journal")
async def get_journal_entries(limit: int = 20, user_id: str = "default"):
    async with aiosqlite.connect(CONFIG["database_path"]) as db:
        cursor = await db.execute("""
            SELECT id, title, content, mood_score, word_count, created_at
            FROM journal_entries 
            WHERE user_id = ?
            ORDER BY created_at DESC 
            LIMIT ?
        """, (user_id, limit))
        entries = await cursor.fetchall()
        
        return [
            {
                "id": e[0], "title": e[1], "content": e[2], "mood_score": e[3],
                "word_count": e[4], "created_at": e[5]
            }
            for e in entries
        ]

# Task endpoints
@app.post("/api/tasks")
async def create_task(task: TaskCreate, user_id: str = "default"):
    try:
        async with aiosqlite.connect(CONFIG["database_path"]) as db:
            cursor = await db.execute("""
                INSERT INTO tasks (user_id, title, description, priority, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (user_id, task.title, task.description, task.priority, datetime.now()))
            await db.commit()
            
            return {"id": cursor.lastrowid, "message": "Task created! ✅"}
    
    except Exception as e:
        logger.error(f"Task creation error: {e}")
        raise HTTPException(status_code=500, detail="Failed to create task")

@app.get("/api/tasks")
async def get_tasks(limit: int = 50, user_id: str = "default"):
    async with aiosqlite.connect(CONFIG["database_path"]) as db:
        cursor = await db.execute("""
            SELECT id, title, description, status, priority, due_date, created_at
            FROM tasks WHERE user_id = ?
            ORDER BY created_at DESC LIMIT ?
        """, (user_id, limit))
        tasks = await cursor.fetchall()
        
        return [
            {
                "id": t[0], "title": t[1], "description": t[2], "status": t[3],
                "priority": t[4], "due_date": t[5], "created_at": t[6]
            }
            for t in tasks
        ]

# Settings endpoints
@app.get("/api/settings")
async def get_user_settings(user_id: str = "default"):
    async with aiosqlite.connect(CONFIG["database_path"]) as db:
        cursor = await db.execute("""
            SELECT personality_fun, personality_empathy, personality_humor, personality_formality,
                   enable_voice, enable_notifications, timezone, theme
            FROM user_settings WHERE user_id = ?
        """, (user_id,))
        settings = await cursor.fetchone()
        
        if settings:
            return {
                "personality_fun": settings[0],
                "personality_empathy": settings[1], 
                "personality_humor": settings[2],
                "personality_formality": settings[3],
                "enable_voice": bool(settings[4]),
                "enable_notifications": bool(settings[5]),
                "timezone": settings[6],
                "theme": settings[7]
            }
        else:
            # Return defaults
            return {
                "personality_fun": 7,
                "personality_empathy": 8,
                "personality_humor": 6,
                "personality_formality": 3,
                "enable_voice": True,
                "enable_notifications": True,
                "timezone": "UTC",
                "theme": "light"
            }

@app.put("/api/settings")
async def update_user_settings(settings: UserSettings, user_id: str = "default"):
    try:
        async with aiosqlite.connect(CONFIG["database_path"]) as db:
            await db.execute("""
                INSERT OR REPLACE INTO user_settings 
                (user_id, personality_fun, personality_empathy, personality_humor, personality_formality,
                 enable_voice, enable_notifications, timezone, theme, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (user_id, settings.personality_fun, settings.personality_empathy, 
                  settings.personality_humor, settings.personality_formality,
                  settings.enable_voice, settings.enable_notifications,
                  settings.timezone, settings.theme, datetime.now()))
            await db.commit()
            
            return {"message": "Settings updated! ⚙️"}
    
    except Exception as e:
        logger.error(f"Settings update error: {e}")
        raise HTTPException(status_code=500, detail="Failed to update settings")

# Dashboard endpoint
@app.get("/api/dashboard")
async def get_dashboard_data(user_id: str = "default"):
    try:
        async with aiosqlite.connect(CONFIG["database_path"]) as db:
            # Today's agenda
            today = date.today()
            cursor = await db.execute("""
                SELECT 'task' as type, title, priority, status
                FROM tasks WHERE user_id = ? AND due_date = ? AND status != 'completed'
                UNION ALL
                SELECT 'event' as type, title, 'normal' as priority, 'scheduled' as status
                FROM events WHERE user_id = ? AND start_date = ?
            """, (user_id, today, user_id, today))
            agenda = await cursor.fetchall()
            
            # Task statistics
            cursor = await db.execute("""
                SELECT status, COUNT(*) FROM tasks WHERE user_id = ? GROUP BY status
            """, (user_id,))
            task_stats = dict(await cursor.fetchall())
            
            # Journal stats
            cursor = await db.execute("""
                SELECT COUNT(*), AVG(COALESCE(mood_score, 0))
                FROM journal_entries 
                WHERE user_id = ? AND created_at >= date('now', '-7 days')
            """, (user_id,))
            journal_data = await cursor.fetchone()
            
            return {
                "today_agenda": [
                    {"type": a[0], "title": a[1], "priority": a[2], "status": a[3]}
                    for a in agenda
                ],
                "task_stats": task_stats,
                "journal_stats": {
                    "recent_entries": journal_data[0] or 0,
                    "avg_mood": round(journal_data[1] or 0, 2)
                },
                "last_updated": datetime.now().isoformat()
            }
    
    except Exception as e:
        logger.error(f"Dashboard error: {e}")
        return {"error": "Failed to load dashboard data"}

# Memory endpoints
@app.get("/api/memory/facts")
async def get_profile_facts(user_id: str = "default", limit: int = 50):
    async with aiosqlite.connect(CONFIG["database_path"]) as db:
        cursor = await db.execute("""
            SELECT id, category, fact_key, fact_value, confidence, created_at
            FROM profile_facts WHERE user_id = ?
            ORDER BY confidence DESC, created_at DESC LIMIT ?
        """, (user_id, limit))
        facts = await cursor.fetchall()
        
        return [
            {
                "id": f[0], "category": f[1], "key": f[2], "value": f[3],
                "confidence": f[4], "created_at": f[5]
            }
            for f in facts
        ]

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
