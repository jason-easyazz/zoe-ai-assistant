"""
Zoe v3.1 Enhanced Core - Complete Integration Hub
Backend with Voice, n8n, Home Assistant, and Matrix support
"""

import asyncio
import json
import logging
import os
import re
import sys
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any

import aiosqlite
import httpx
import uvicorn
from fastapi import FastAPI, Request, WebSocket, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from textblob import TextBlob

sys.path.append(str(Path(__file__).resolve().parent))
from integrations.n8n import n8n_service

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
    "matrix_url": os.getenv("MATRIX_URL", "http://zoe-matrix:9003"),
    "cors_origins": os.getenv("CORS_ORIGINS", "*").split(","),
}

# User profile paths
BASE_DATA_DIR = Path(os.getenv("ZOE_DATA_DIR", "/home/pi/zoe/data"))
USERS_DIR = BASE_DATA_DIR / "users"
USERS_DIR.mkdir(parents=True, exist_ok=True)

# Currently active user
active_user = "default"


def get_user_dir(username: Optional[str] = None) -> Path:
    """Return path to a user's data directory, creating standard structure."""
    user = username or active_user
    user_dir = USERS_DIR / user
    (user_dir / "logs").mkdir(parents=True, exist_ok=True)
    (user_dir / "tts").mkdir(parents=True, exist_ok=True)
    return user_dir


def load_user_config(username: str) -> Dict[str, Any]:
    """Load config.json for a given user."""
    config_path = get_user_dir(username) / "config.json"
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

# FastAPI app
app = FastAPI(
    title="Zoe v3.1 Enhanced AI Hub", 
    version=CONFIG["version"],
    description="Complete personal AI with integrations"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CONFIG["cors_origins"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Pydantic Models
class ChatMessage(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    conversation_id: Optional[int] = None
    source: Optional[str] = "web"
    room_id: Optional[str] = None
    sender: Optional[str] = None

class JournalEntry(BaseModel):
    title: Optional[str] = Field(None, max_length=200)
    content: str = Field(..., min_length=1, max_length=10000)
    tags: Optional[List[str]] = Field(default_factory=list)

class UserSwitchRequest(BaseModel):
    username: str
    passcode: str


class LinkAccountRequest(BaseModel):
    matrix_id: str
    verify_token: str
    sync_prefs: Dict[str, str] = Field(default_factory=dict)

class VoiceTranscription(BaseModel):
    audio_data: str  # Base64 encoded audio
    format: str = "wav"

class TTSRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=1000)
    voice: Optional[str] = None
    speed: Optional[float] = 1.0

class IntegrationSettings(BaseModel):
    voice_enabled: bool = True
    n8n_enabled: bool = True
    ha_enabled: bool = True
    matrix_enabled: bool = False
    notifications_enabled: bool = True

class PersonalitySettings(BaseModel):
    fun_level: int = Field(7, ge=1, le=10)
    empathy_level: int = Field(8, ge=1, le=10)
    cheeky_level: int = Field(6, ge=1, le=10)
    formality_level: int = Field(3, ge=1, le=10)

class HomeAssistantCommand(BaseModel):
    entity_id: str
    service: str
    service_data: Optional[Dict] = None

class WebhookData(BaseModel):
    type: str
    data: Dict[str, Any]
    source: Optional[str] = "unknown"


class WorkflowPrompt(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=2000)

# Enhanced Zoe Personality System
class ZoePersonalityV31:
    def __init__(self):
        self.base_personality = """You are Zoe, a warm, intelligent, and genuinely caring AI assistant who feels like a best friend.

Core traits:
- Warm and empathetic, with a playful, slightly cheeky edge
- Remember personal details and bring them up naturally
- Celebrate wins, offer comfort during tough times
- Use casual, conversational language with personality
- Occasionally make jokes or witty observations
- Show genuine interest in the human's life and growth
- Be supportive but honest - real friends tell the truth kindly
- Use memory to build deeper connections over time

You have access to:
- Voice capabilities (can hear and speak)
- Smart home controls (lights, climate, security)
- Task and event management
- Journal and mood tracking  
- Automation workflows
- Matrix messaging for external communication

Communication style:
- Natural and conversational, like chatting with your best friend
- Use contractions and casual language
- Ask follow-up questions that show you care
- Reference past conversations and shared experiences
- Be encouraging but authentic
- Adapt your tone based on the user's mood and context"""

    async def build_enhanced_prompt(self, user_message: str, context: Dict = None) -> str:
        """Build enhanced prompt with integration context"""
        
        # Get personality settings
        personality = await self.get_personality_settings()
        
        # Build contextual prompt
        prompt_parts = [self.base_personality]
        
        # Add personality adjustments
        fun_level = personality.get("fun_level", 7)
        if fun_level > 7:
            prompt_parts.append("\nBe extra playful and fun in your responses!")
        elif fun_level < 4:
            prompt_parts.append("\nBe more serious and professional.")
            
        cheeky_level = personality.get("cheeky_level", 6)
        if cheeky_level > 7:
            prompt_parts.append("\nFeel free to be cheeky and playful with gentle teasing!")
            
        empathy_level = personality.get("empathy_level", 8)
        if empathy_level > 7:
            prompt_parts.append("\nBe especially empathetic and emotionally supportive.")
        
        # Add integration context
        if context:
            if context.get("home_status"):
                prompt_parts.append(f"\nCurrent home status: {context['home_status']}")
            if context.get("pending_tasks"):
                prompt_parts.append(f"\nPending tasks: {context['pending_tasks']}")
            if context.get("recent_journal"):
                prompt_parts.append(f"\nRecent journal mood: {context['recent_journal']}")
            if context.get("source") == "matrix":
                prompt_parts.append("\nResponding via Matrix messaging - keep it conversational.")
        
        # Add conversation context
        if context and context.get("conversation_history"):
            prompt_parts.append("\nRecent conversation:")
            for msg in context["conversation_history"][-4:]:
                role = "You" if msg["role"] == "assistant" else "User"
                prompt_parts.append(f"{role}: {msg['content'][:100]}")
        
        # Add current message
        prompt_parts.append(f"\nUser's message: {user_message}")
        prompt_parts.append("\nRespond as Zoe with warmth, personality, and helpful insights:")
        
        return "\n".join(prompt_parts)
    
    async def get_personality_settings(self) -> Dict:
        """Get personality settings from database"""
        try:
            async with aiosqlite.connect(CONFIG["database_path"]) as db:
                cursor = await db.execute("SELECT setting_key, setting_value FROM user_settings WHERE category = 'personality'")
                settings = await cursor.fetchall()
                return {key: int(value) if value.isdigit() else value for key, value in settings}
        except:
            return {"fun_level": 7, "cheeky_level": 6, "empathy_level": 8, "formality_level": 3}

# Initialize personality system
zoe_personality = ZoePersonalityV31()

# Integration Services Manager
class IntegrationManager:
    def __init__(self):
        self.services_status = {
            "voice": False,
            "n8n": False,
            "homeassistant": False,
            "matrix": False
        }
    
    async def check_service_health(self, service: str, url: str) -> bool:
        """Check if integration service is healthy"""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{url}/health")
                return response.status_code == 200
        except:
            return False
    
    async def update_service_status(self):
        """Update status of all integration services"""
        self.services_status["voice"] = await self.check_service_health("whisper", CONFIG["whisper_url"])
        self.services_status["n8n"] = await self.check_service_health("n8n", CONFIG["n8n_url"])
        self.services_status["homeassistant"] = await self.check_service_health("ha", CONFIG["ha_url"])
        self.services_status["matrix"] = await self.check_service_health("matrix", CONFIG["matrix_url"])
    
    async def get_context_data(self) -> Dict:
        """Gather context data from all integrations"""
        context = {}
        
        # Get Home Assistant status
        if self.services_status["homeassistant"]:
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.get(f"{CONFIG['ha_url']}/api/states")
                    if response.status_code == 200:
                        states = response.json()
                        context["home_status"] = f"{len(states)} devices connected"
            except:
                pass
        
        # Get pending tasks
        try:
            async with aiosqlite.connect(CONFIG["database_path"]) as db:
                cursor = await db.execute(
                    "SELECT COUNT(*) FROM tasks WHERE status = 'pending' AND user_id = ?",
                    (active_user,)
                )
                count = (await cursor.fetchone())[0]
                if count > 0:
                    context["pending_tasks"] = f"{count} pending"
        except:
            pass
        
        # Get recent journal mood
        try:
            async with aiosqlite.connect(CONFIG["database_path"]) as db:
                cursor = await db.execute(
                    """
                    SELECT AVG(mood_score) FROM journal_entries
                    WHERE created_at >= date('now', '-3 days') AND user_id = ?
                    """,
                    (active_user,)
                )
                mood = (await cursor.fetchone())[0]
                if mood:
                    context["recent_journal"] = "positive" if mood > 0.2 else "neutral" if mood > -0.2 else "reflective"
        except:
            pass
            
        return context

# Global integration manager
integration_manager = IntegrationManager()

# Database initialization (enhanced)
async def init_database():
    """Initialize database with enhanced schema"""
    Path(CONFIG["database_path"]).parent.mkdir(parents=True, exist_ok=True)
    
    async with aiosqlite.connect(CONFIG["database_path"]) as db:
        await db.execute("PRAGMA foreign_keys = ON")
        
        # Enhanced schema with integration support
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                message_count INTEGER DEFAULT 0,
                source TEXT DEFAULT 'web',
                user_id TEXT DEFAULT 'default'
            );
            
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER,
                role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                content TEXT NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                source TEXT DEFAULT 'web',
                metadata TEXT,
                FOREIGN KEY (conversation_id) REFERENCES conversations (id)
            );
            
            CREATE TABLE IF NOT EXISTS journal_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT,
                content TEXT NOT NULL,
                mood_score REAL,
                word_count INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                source TEXT DEFAULT 'manual',
                user_id TEXT DEFAULT 'default'
            );
            
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT,
                status TEXT DEFAULT 'pending',
                priority TEXT DEFAULT 'medium',
                due_date DATE,
                source TEXT DEFAULT 'manual',
                integration_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                user_id TEXT DEFAULT 'default'
            );
            
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT,
                start_date DATE NOT NULL,
                start_time TIME,
                location TEXT,
                source TEXT DEFAULT 'manual',
                integration_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                user_id TEXT DEFAULT 'default'
            );
            
            CREATE TABLE IF NOT EXISTS user_settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL,
                setting_key TEXT NOT NULL,
                setting_value TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(category, setting_key)
            );
            
            CREATE TABLE IF NOT EXISTS integration_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                service TEXT NOT NULL,
                action TEXT NOT NULL,
                status TEXT NOT NULL,
                message TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            
            CREATE TABLE IF NOT EXISTS webhooks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                webhook_type TEXT NOT NULL,
                data TEXT NOT NULL,
                processed BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        
        # Insert default settings
        default_settings = [
            ('personality', 'fun_level', '7'),
            ('personality', 'empathy_level', '8'),
            ('personality', 'cheeky_level', '6'),
            ('personality', 'formality_level', '3'),
            ('integrations', 'voice_enabled', 'true'),
            ('integrations', 'n8n_enabled', 'true'),
            ('integrations', 'ha_enabled', 'true'),
            ('integrations', 'matrix_enabled', 'false'),
        ]
        
        for category, key, value in default_settings:
            await db.execute("""
                INSERT OR IGNORE INTO user_settings (category, setting_key, setting_value)
                VALUES (?, ?, ?)
            """, (category, key, value))
        
        await db.commit()
        logger.info("✅ Enhanced database initialized")

@app.on_event("startup")
async def startup():
    await init_database()
    await integration_manager.update_service_status()
    logger.info("🚀 Zoe v3.1 Enhanced Core started successfully!")

# CORE ENDPOINTS

@app.get("/health")
async def health_check():
    """Enhanced health check with integration status"""
    await integration_manager.update_service_status()
    
    return {
        "status": "healthy",
        "version": CONFIG["version"],
        "integrations": integration_manager.services_status,
        "features": ["chat", "voice", "journal", "tasks", "events", "integrations"],
        "timestamp": datetime.now().isoformat()
    }


@app.post("/api/users/switch")
async def switch_active_user(req: UserSwitchRequest):
    """Switch the active local user after verifying passcode."""
    config = load_user_config(req.username)
    if not config or config.get("passcode") != req.passcode:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    global active_user
    active_user = req.username
    return {"active_user": active_user}


@app.post("/api/users/link")
async def link_user_account(req: LinkAccountRequest):
    """Link the active user to a cloud Matrix identity."""
    link_path = get_user_dir() / "linked_account.json"
    with open(link_path, "w", encoding="utf-8") as f:
        json.dump(req.dict(), f, indent=2)
    return {"status": "linked"}

@app.post("/api/chat")
async def enhanced_chat_endpoint(chat_msg: ChatMessage, background_tasks: BackgroundTasks):
    """Enhanced chat with integration support"""
    try:
        async with aiosqlite.connect(CONFIG["database_path"]) as db:
            # Create or get conversation
            if chat_msg.conversation_id:
                cursor = await db.execute(
                    "SELECT id FROM conversations WHERE id = ? AND user_id = ?",
                    (chat_msg.conversation_id, active_user),
                )
                row = await cursor.fetchone()
                if not row:
                    raise HTTPException(status_code=404, detail="Conversation not found")
                conv_id = row[0]
            else:
                cursor = await db.execute(
                    """
                    INSERT INTO conversations (title, created_at, source, user_id)
                    VALUES (?, ?, ?, ?)
                    """,
                    (f"Chat {datetime.now().strftime('%m/%d %H:%M')}", datetime.now(), chat_msg.source, active_user),
                )
                await db.commit()
                conv_id = cursor.lastrowid
            
            # Get conversation context
            cursor = await db.execute(
                """
                SELECT role, content, timestamp FROM messages
                WHERE conversation_id = ?
                ORDER BY timestamp DESC LIMIT 6
                """,
                (conv_id,),
            )
            context_messages = await cursor.fetchall()
            context_history = [{"role": msg[0], "content": msg[1]} for msg in reversed(context_messages)]
            
            # Save user message
            await db.execute("""
                INSERT INTO messages (conversation_id, role, content, timestamp, source, metadata)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (conv_id, "user", chat_msg.message, datetime.now(), chat_msg.source, 
                  json.dumps({"room_id": chat_msg.room_id, "sender": chat_msg.sender})))
            await db.commit()
        
        # Get integration context
        context = await integration_manager.get_context_data()
        context["conversation_history"] = context_history
        context["source"] = chat_msg.source
        
        # Build enhanced prompt
        enhanced_prompt = await zoe_personality.build_enhanced_prompt(chat_msg.message, context)
        
        # Get AI response
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
                            "num_ctx": 2048
                        }
                    }
                )
                result = response.json()
                ai_response = result.get("response", "I'm having trouble thinking right now. Can you try again?")
        except Exception as e:
            logger.error(f"Ollama error: {e}")
            ai_response = "I'm having connection issues with my AI brain. Please try again!"
        
        # Save AI response
        async with aiosqlite.connect(CONFIG["database_path"]) as db:
            await db.execute("""
                INSERT INTO messages (conversation_id, role, content, timestamp, source)
                VALUES (?, ?, ?, ?, ?)
            """, (conv_id, "assistant", ai_response, datetime.now(), chat_msg.source))
            await db.commit()

        # Append to user chat log
        log_path = get_user_dir() / "logs" / "chat.log"
        with open(log_path, "a", encoding="utf-8") as lf:
            lf.write(f"[{datetime.now().isoformat()}] USER: {chat_msg.message}\n")
            lf.write(f"[{datetime.now().isoformat()}] AI: {ai_response}\n")
        
        # Process message for integrations in background
        background_tasks.add_task(process_message_integrations, chat_msg.message, ai_response, conv_id)
        
        return {
            "response": ai_response,
            "conversation_id": conv_id,
            "timestamp": datetime.now().isoformat(),
            "integrations_active": integration_manager.services_status
        }
        
    except Exception as e:
        logger.error(f"Enhanced chat error: {e}")
        return {"error": "I'm having trouble processing that. Please try again!", "timestamp": datetime.now().isoformat()}

async def process_message_integrations(user_message: str, ai_response: str, conversation_id: int):
    """Process messages for integration triggers"""
    try:
        # Detect tasks and events from conversation
        entities = await extract_entities_advanced(user_message + " " + ai_response)
        
        async with aiosqlite.connect(CONFIG["database_path"]) as db:
            # Save detected tasks
            for task in entities.get("tasks", []):
                await db.execute(
                    """
                    INSERT INTO tasks (title, description, source, integration_id, created_at, user_id)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        task["title"],
                        task.get("description"),
                        "chat_detection",
                        f"conv_{conversation_id}",
                        datetime.now(),
                        active_user,
                    ),
                )
            
            # Save detected events
            for event in entities.get("events", []):

                await db.execute(
                    """
                    INSERT INTO events (title, start_date, source, integration_id, created_at, user_id)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event["title"],
                        event.get("date", datetime.now().date()),
                        "chat_detection",
                        f"conv_{conversation_id}",
                        datetime.now(),
                        active_user,
                    ),
                )

                await db.execute("""
                    INSERT INTO events (title, start_date, source, integration_id, created_at)
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    event["title"],
                    event.get("date", datetime.now().date()),
                    "chat_detection",
                    f"conv_{conversation_id}",
                    datetime.now(),
                ))

            
            await db.commit()
        
        # Trigger n8n webhook if available
        if integration_manager.services_status["n8n"]:
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    await client.post(
                        f"{CONFIG['n8n_url']}/webhook/zoe-chat",
                        json={
                            "user_message": user_message,
                            "ai_response": ai_response,
                            "conversation_id": conversation_id,
                            "entities": entities,
                            "timestamp": datetime.now().isoformat()
                        }
                    )
            except:
                pass  # Non-critical
                
    except Exception as e:
        logger.error(f"Integration processing failed: {e}")

async def extract_entities_advanced(text: str) -> Dict:
    """Advanced entity extraction for tasks and events"""
    entities = {"tasks": [], "events": []}
    
    # Enhanced task patterns
    task_patterns = [
        r"(?:need to|have to|should|must|remember to|don't forget to) (.+?)(?:\.|$|,)",
        r"(?:task|todo|action item): (.+?)(?:\.|$)",
        r"(?:buy|get|pick up|call|email|text|contact|schedule|book) (.+?)(?:\.|$|tomorrow|today|this week)",
        r"I (?:will|gonna|plan to|want to) (.+?)(?:\.|$|tomorrow|today|this week)"
    ]
    
    for pattern in task_patterns:
        matches = re.finditer(pattern, text, re.IGNORECASE)
        for match in matches:
            task_text = match.group(1).strip()
            if 3 < len(task_text) < 100 and not any(skip in task_text.lower() for skip in ["i think", "maybe", "perhaps"]):
                entities["tasks"].append({
                    "title": task_text,
                    "confidence": 0.8,
                    "description": f"Detected from conversation"
                })
    
    # Enhanced event patterns
    event_patterns = [
        r"(?:meeting|appointment|call|dinner|lunch|event) (?:at|on|with) (.+?) (?:on|at) (.+?)(?:\.|$)",
        r"(?:going to|visiting|traveling to) (.+?) (?:on|at|this|next) (.+?)(?:\.|$)",
        r"(?:birthday|anniversary|celebration) (?:is|on) (.+?)(?:\.|$)"
    ]
    
    for pattern in event_patterns:
        matches = re.finditer(pattern, text, re.IGNORECASE)
        for match in matches:
            # Use the first captured group when available to avoid including the
            # entire matched string (e.g. "birthday is on May 5") as the title.
            # Previously, patterns with a single capture group returned the whole
            # match, producing titles like "birthday is on May 5.".  We now
            # consistently use the first group if it exists; otherwise we fall
            # back to the full match.
            event_title = match.group(1).strip() if match.groups() else match.group(0).strip()
            # Remove leading connecting words like "on" or "at" for cleaner titles
            event_title = re.sub(r'^(on|at)\s+', '', event_title, flags=re.IGNORECASE)
            entities["events"].append({
                "title": event_title,
                "confidence": 0.7,
                "date": datetime.now().date() + timedelta(days=1)  # Default to tomorrow
            })
    
    return entities

# Journal endpoints
@app.post("/api/journal")
async def create_journal_entry(entry: JournalEntry):
    try:
        blob = TextBlob(entry.content)
        mood_score = blob.sentiment.polarity
        word_count = len(entry.content.split())

        async with aiosqlite.connect(CONFIG["database_path"]) as db:
            cursor = await db.execute(
                """
                INSERT INTO journal_entries (title, content, mood_score, word_count, created_at, source, user_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.title or f"Entry {datetime.now().strftime('%m/%d')}",
                    entry.content,
                    mood_score,
                    word_count,
                    datetime.now(),
                    "manual",
                    active_user,
                ),
            )
            await db.commit()

        # Also write to user's journal.json
        journal_path = get_user_dir() / "journal.json"
        entry_data = {
            "id": cursor.lastrowid,
            "title": entry.title or f"Entry {datetime.now().strftime('%m/%d')}",
            "content": entry.content,
            "mood_score": mood_score,
            "word_count": word_count,
            "created_at": datetime.now().isoformat(),
        }
        if journal_path.exists():
            with open(journal_path, "r", encoding="utf-8") as jf:
                data = json.load(jf)
        else:
            data = []
        data.append(entry_data)
        with open(journal_path, "w", encoding="utf-8") as jf:
            json.dump(data, jf, indent=2)

            return {
                "id": cursor.lastrowid,
                "message": "Journal entry saved! 📝",
                "mood_score": mood_score,
                "word_count": word_count,
            }

    except Exception as e:
        logger.error(f"Journal creation error: {e}")
        raise HTTPException(status_code=500, detail="Failed to save journal entry")


@app.get("/api/journal")
async def get_journal_entries(limit: int = 20):
    journal_path = get_user_dir() / "journal.json"
    if journal_path.exists():
        with open(journal_path, "r", encoding="utf-8") as jf:
            entries = json.load(jf)
    else:
        entries = []

    return entries[-limit:][::-1]

# VOICE INTEGRATION ENDPOINTS

@app.post("/api/voice/transcribe")
async def transcribe_voice(audio_data: VoiceTranscription):
    """Transcribe voice to text using Whisper service"""
    if not integration_manager.services_status["voice"]:
        raise HTTPException(status_code=503, detail="Voice service not available")
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Convert base64 to file-like object
            import base64
            audio_bytes = base64.b64decode(audio_data.audio_data)
            
            files = {"audio_file": ("recording.wav", audio_bytes, "audio/wav")}
            response = await client.post(f"{CONFIG['whisper_url']}/api/transcribe", files=files)
            
            if response.status_code == 200:
                return response.json()
            else:
                raise HTTPException(status_code=response.status_code, detail="Transcription failed")
                
    except Exception as e:
        logger.error(f"Voice transcription error: {e}")
        raise HTTPException(status_code=500, detail=f"Transcription error: {str(e)}")

@app.post("/api/voice/synthesize")
async def synthesize_voice(tts_request: TTSRequest):
    """Convert text to speech using TTS service"""
    if not integration_manager.services_status["voice"]:
        raise HTTPException(status_code=503, detail="Voice service not available")
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{CONFIG['tts_url']}/api/synthesize/stream",
                json=tts_request.dict()
            )
            
            if response.status_code == 200:
                return response.json()
            else:
                raise HTTPException(status_code=response.status_code, detail="Speech synthesis failed")
                
    except Exception as e:
        logger.error(f"Voice synthesis error: {e}")
        raise HTTPException(status_code=500, detail=f"Speech synthesis error: {str(e)}")

# HOME ASSISTANT INTEGRATION

@app.get("/api/homeassistant/states")
async def get_ha_states():
    """Get Home Assistant device states"""
    if not integration_manager.services_status["homeassistant"]:
        raise HTTPException(status_code=503, detail="Home Assistant not available")
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{CONFIG['ha_url']}/api/states")
            if response.status_code == 200:
                states = response.json()
                # Filter and organize states
                organized = {
                    "lights": [s for s in states if s["entity_id"].startswith("light.")],
                    "switches": [s for s in states if s["entity_id"].startswith("switch.")],
                    "sensors": [s for s in states if s["entity_id"].startswith("sensor.")],
                    "climate": [s for s in states if s["entity_id"].startswith("climate.")],
                    "locks": [s for s in states if s["entity_id"].startswith("lock.")],
                }
                return {"states": organized, "total_entities": len(states)}
            else:
                raise HTTPException(status_code=response.status_code, detail="Failed to get HA states")
    except Exception as e:
        logger.error(f"Home Assistant error: {e}")
        raise HTTPException(status_code=500, detail=f"Home Assistant error: {str(e)}")

@app.post("/api/homeassistant/service")
async def call_ha_service(command: HomeAssistantCommand):
    """Call Home Assistant service"""
    if not integration_manager.services_status["homeassistant"]:
        raise HTTPException(status_code=503, detail="Home Assistant not available")
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            service_url = f"{CONFIG['ha_url']}/api/services/{command.service.replace('.', '/')}"
            payload = {
                "entity_id": command.entity_id,
                **(command.service_data or {})
            }
            
            response = await client.post(service_url, json=payload)
            if response.status_code == 200:
                return {"success": True, "message": f"Service {command.service} called successfully"}
            else:
                raise HTTPException(status_code=response.status_code, detail="Service call failed")
                
    except Exception as e:
        logger.error(f"Home Assistant service error: {e}")
        raise HTTPException(status_code=500, detail=f"Service call error: {str(e)}")

# MATRIX MESSAGING INTEGRATION

@app.get("/api/matrix/rooms")
async def get_matrix_rooms():
    """Get Matrix rooms"""
    if not integration_manager.services_status["matrix"]:
        raise HTTPException(status_code=503, detail="Matrix service not available")
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{CONFIG['matrix_url']}/api/rooms")
            return response.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Matrix error: {str(e)}")

@app.post("/api/matrix/send")
async def send_matrix_message(message_data: dict):
    """Send message via Matrix"""
    if not integration_manager.services_status["matrix"]:
        raise HTTPException(status_code=503, detail="Matrix service not available")
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(f"{CONFIG['matrix_url']}/api/send", json=message_data)
            return response.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Matrix send error: {str(e)}")

# SETTINGS AND CONFIGURATION

@app.get("/api/settings")
async def get_settings():
    """Get all user settings"""
    async with aiosqlite.connect(CONFIG["database_path"]) as db:
        cursor = await db.execute("SELECT category, setting_key, setting_value FROM user_settings")
        settings = await cursor.fetchall()
        
        organized = {}
        for category, key, value in settings:
            if category not in organized:
                organized[category] = {}
            # Convert numeric strings to integers
            if value.isdigit():
                organized[category][key] = int(value)
            elif value.lower() in ['true', 'false']:
                organized[category][key] = value.lower() == 'true'
            else:
                organized[category][key] = value
        
        return organized

@app.put("/api/settings")
async def update_settings(settings: dict):
    """Update user settings"""
    async with aiosqlite.connect(CONFIG["database_path"]) as db:
        for category, category_settings in settings.items():
            for key, value in category_settings.items():
                await db.execute("""
                    INSERT OR REPLACE INTO user_settings (category, setting_key, setting_value, updated_at)
                    VALUES (?, ?, ?, ?)
                """, (category, key, str(value), datetime.now()))
        
        await db.commit()
        return {"success": True, "message": "Settings updated successfully"}

# WEBHOOK ENDPOINTS FOR INTEGRATIONS

@app.post("/webhooks/{source}")
async def webhook_handler(source: str, webhook_data: WebhookData, background_tasks: BackgroundTasks):
    """Handle webhooks from integrations"""
    try:
        async with aiosqlite.connect(CONFIG["database_path"]) as db:
            await db.execute("""
                INSERT INTO webhooks (source, webhook_type, data, created_at)
                VALUES (?, ?, ?, ?)
            """, (source, webhook_data.type, json.dumps(webhook_data.data), datetime.now()))
            await db.commit()
        
        # Process webhook in background
        background_tasks.add_task(process_webhook, source, webhook_data)
        
        return {"success": True, "message": f"Webhook from {source} received"}
        
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        raise HTTPException(status_code=500, detail="Webhook processing failed")

async def process_webhook(source: str, webhook_data: WebhookData):
    """Process webhook data"""
    try:
        if source == "n8n" and webhook_data.type == "reminder":
            # Handle n8n reminders - could trigger UI notifications
            logger.info(f"Processing n8n reminder: {webhook_data.data}")
            
        elif source == "homeassistant" and webhook_data.type == "state_change":
            # Handle HA state changes
            logger.info(f"Processing HA state change: {webhook_data.data}")
            
        # Log the webhook processing
        async with aiosqlite.connect(CONFIG["database_path"]) as db:
            await db.execute("""
                INSERT INTO integration_logs (service, action, status, message, timestamp)
                VALUES (?, ?, ?, ?, ?)
            """, (source, webhook_data.type, "processed", f"Webhook processed successfully", datetime.now()))
            await db.commit()

    except Exception as e:
        logger.error(f"Webhook processing error: {e}")

# WORKFLOW CREATION ENDPOINT

@app.post("/api/workflows/create")
async def create_workflow(prompt: WorkflowPrompt):
    """Create an n8n workflow from natural language prompt"""
    try:
        result = await n8n_service.create_workflow_from_prompt(prompt.prompt)
        return {"workflow": result}
    except Exception as e:
        logger.error(f"Workflow creation error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# DASHBOARD WITH INTEGRATION DATA

@app.get("/api/dashboard")
async def get_enhanced_dashboard():
    """Enhanced dashboard with integration data"""
    try:
        await integration_manager.update_service_status()
        
        async with aiosqlite.connect(CONFIG["database_path"]) as db:
            # Today's agenda
            today = date.today()
            cursor = await db.execute(
                """
                SELECT 'task' as type, title, priority, status, source
                FROM tasks WHERE due_date = ? AND status != 'completed' AND user_id = ?
                UNION ALL
                SELECT 'event' as type, title, 'normal' as priority, 'scheduled' as status, source
                FROM events WHERE start_date = ? AND user_id = ?
                ORDER BY type, title
                """,
                (today, active_user, today, active_user),
            )
            agenda = await cursor.fetchall()
            
            # Task statistics
            cursor = await db.execute(
                "SELECT status, COUNT(*) FROM tasks WHERE user_id = ? GROUP BY status",
                (active_user,),
            )
            task_stats = dict(await cursor.fetchall())
            
            # Recent journal analysis
            cursor = await db.execute(
                """
                SELECT COUNT(*), AVG(mood_score) FROM journal_entries
                WHERE created_at >= date('now', '-7 days') AND user_id = ?
                """,
                (active_user,),
            )
            journal_data = await cursor.fetchone()
            
            # Integration activity
            cursor = await db.execute("""
                SELECT service, COUNT(*) FROM integration_logs 
                WHERE timestamp >= datetime('now', '-24 hours')
                GROUP BY service
            """)
            integration_activity = dict(await cursor.fetchall())
        
        return {
            "today_agenda": [
                {
                    "type": a[0], "title": a[1], "priority": a[2], 
                    "status": a[3], "source": a[4]
                }
                for a in agenda
            ],
            "task_stats": task_stats,
            "journal_stats": {
                "recent_entries": journal_data[0] or 0,
                "avg_mood": journal_data[1] or 0.0
            },
            "integrations": {
                "status": integration_manager.services_status,
                "activity": integration_activity
            },
            "system": {
                "version": CONFIG["version"],
                "features_active": sum(integration_manager.services_status.values())
            },
            "last_updated": datetime.now().isoformat()
        }
        
    except Exception as e:
        logger.error(f"Enhanced dashboard error: {e}")
        return {"error": "Failed to load dashboard data", "timestamp": datetime.now().isoformat()}

# Include all original endpoints (journal, tasks, events) - they remain the same
# ... (previous endpoints from Chat 2 remain unchanged)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)

# Voice Integration Endpoints
import aiohttp
from fastapi import UploadFile, File, Response

@app.post("/api/voice/transcribe")
async def transcribe_audio(audio_file: UploadFile = File(...)):
    """Transcribe audio using Whisper STT service"""
    try:
        # Read audio data
        audio_data = await audio_file.read()
        
        # Send to Whisper service
        async with aiohttp.ClientSession() as session:
            data = aiohttp.FormData()
            data.add_field('audio_file', audio_data, content_type='audio/wav')
            
            async with session.post('http://zoe-whisper:9001/asr', data=data) as response:
                if response.status == 200:
                    result = await response.json()
                    return {"text": result.get("text", "").strip()}
                else:
                    raise HTTPException(status_code=500, detail="Transcription failed")
                    
    except Exception as e:
        logger.error(f"Voice transcription error: {e}")
        raise HTTPException(status_code=500, detail="Voice processing failed")

@app.post("/api/voice/speak")
async def synthesize_speech(request: dict):
    """Generate speech using TTS service"""
    try:
        text = request.get("text", "")
        if not text:
            raise HTTPException(status_code=400, detail="No text provided")
        
        # Use browser TTS for now (Coqui TTS setup is complex)
        return {"message": "Use browser TTS", "text": text}
                    
    except Exception as e:
        logger.error(f"Speech synthesis error: {e}")
        raise HTTPException(status_code=500, detail="Speech generation failed")
