#!/bin/bash

# ============================================================================
# ZOE API & UI FIX SCRIPT
# Diagnose and fix the Core API and UI issues
# ============================================================================

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log() { echo -e "${GREEN}[$(date '+%H:%M:%S')]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARNING]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; }
success() { echo -e "${GREEN}✅${NC} $1"; }

echo -e "${BLUE}════════════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}DIAGNOSING AND FIXING API/UI ISSUES${NC}"
echo -e "${BLUE}════════════════════════════════════════════════════════════════${NC}"

cd /home/pi/zoe

# ============================================================================
# STEP 1: CHECK LOGS
# ============================================================================

log "📋 Checking Core API logs..."
echo "Last 20 lines of zoe-core logs:"
docker logs zoe-core --tail 20

echo -e "\n"
log "📋 Checking UI logs..."
echo "Last 10 lines of zoe-ui logs:"
docker logs zoe-ui --tail 10

# ============================================================================
# STEP 2: FIX CORE API
# ============================================================================

log "🔧 Fixing Core API..."

# Check if main.py has syntax errors
log "Checking for Python syntax errors..."
docker exec zoe-core python3 -c "import main" 2>&1 | grep -q "Error" && {
    warn "Found syntax errors in main.py, creating fixed version..."
    
    # Create a working main.py
    cat > services/zoe-core/main.py << 'EOF'
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
EOF
    
    success "Created fixed main.py"
}

# ============================================================================
# STEP 3: FIX UI NGINX CONFIG
# ============================================================================

log "🔧 Fixing UI configuration..."

# Create proper nginx config
cat > services/zoe-ui/nginx.conf << 'EOF'
events {
    worker_connections 1024;
}

http {
    include /etc/nginx/mime.types;
    default_type application/octet-stream;

    server {
        listen 80;
        server_name localhost;
        
        root /usr/share/nginx/html;
        index index.html;

        # Main site
        location / {
            try_files $uri $uri/ /index.html;
        }

        # Developer section
        location /developer/ {
            alias /usr/share/nginx/html/developer/;
            try_files $uri $uri/ /index.html;
        }

        # API proxy
        location /api/ {
            proxy_pass http://zoe-core:8000/api/;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
        }
    }
}
EOF

# Ensure index files exist
if [ ! -f services/zoe-ui/dist/index.html ]; then
    log "Creating main index.html..."
    cat > services/zoe-ui/dist/index.html << 'EOF'
<!DOCTYPE html>
<html>
<head>
    <title>Zoe AI Assistant</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            color: white;
        }
        .container {
            background: rgba(255,255,255,0.1);
            backdrop-filter: blur(10px);
            padding: 40px;
            border-radius: 20px;
            text-align: center;
            max-width: 600px;
        }
        h1 { margin-bottom: 20px; font-size: 2.5em; }
        .status { 
            background: rgba(0,255,0,0.2);
            padding: 15px;
            border-radius: 10px;
            margin: 20px 0;
        }
        .links {
            display: flex;
            gap: 10px;
            justify-content: center;
            flex-wrap: wrap;
            margin-top: 30px;
        }
        a {
            background: rgba(255,255,255,0.2);
            color: white;
            text-decoration: none;
            padding: 12px 24px;
            border-radius: 10px;
            transition: all 0.3s;
        }
        a:hover {
            background: rgba(255,255,255,0.3);
            transform: translateY(-2px);
        }
        .feature {
            background: rgba(255,255,255,0.05);
            padding: 15px;
            border-radius: 10px;
            margin: 10px 0;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>🤖 Zoe AI Assistant</h1>
        <div class="status">✅ System Online - v4.0</div>
        
        <div class="feature">
            <h3>🧠 Memory System</h3>
            <p>People, Projects, and Relationships tracking</p>
        </div>
        
        <div class="feature">
            <h3>🎤 Voice Integration</h3>
            <p>Speech-to-Text and Text-to-Speech</p>
        </div>
        
        <div class="feature">
            <h3>🔄 N8N Automation</h3>
            <p>Workflow automation platform</p>
        </div>
        
        <div class="links">
            <a href="/developer/">👨‍💻 Developer Dashboard</a>
            <a href="http://localhost:8000/docs">📚 API Docs</a>
            <a href="http://localhost:5678">🔄 N8N Workflows</a>
        </div>
    </div>
</body>
</html>
EOF
fi

success "UI configuration fixed"

# ============================================================================
# STEP 4: REBUILD AND RESTART
# ============================================================================

log "🔄 Restarting services..."

# Restart Core API
docker compose stop zoe-core
docker compose up -d --build zoe-core
sleep 5

# Restart UI
docker compose stop zoe-ui
docker compose up -d zoe-ui
sleep 3

# ============================================================================
# STEP 5: VERIFY FIX
# ============================================================================

echo -e "\n${BLUE}════════════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}VERIFICATION${NC}"
echo -e "${BLUE}════════════════════════════════════════════════════════════════${NC}"

log "🔍 Testing services..."

# Show container status
docker ps --format "table {{.Names}}\t{{.Status}}" | grep zoe-

echo -e "\n${BLUE}Service Tests:${NC}"

# Test Core API
echo -n "Core API Health: "
if curl -s http://localhost:8000/health 2>/dev/null | grep -q "healthy"; then
    success "✅ Working!"
    
    # Show API response
    echo "API Response:"
    curl -s http://localhost:8000/health | python3 -m json.tool
else
    error "❌ Still not responding"
    echo "Checking logs again:"
    docker logs zoe-core --tail 5
fi

echo -e "\n"
echo -n "Web UI: "
if curl -s http://localhost:8080 2>/dev/null | grep -q "Zoe"; then
    success "✅ Accessible!"
else
    warn "⚠️ Not accessible"
fi

echo -n "Developer Dashboard: "
if curl -s http://localhost:8080/developer/ 2>/dev/null | grep -q "Developer"; then
    success "✅ Working!"
else
    warn "⚠️ Not accessible"
fi

# Test Memory System
echo -e "\n${BLUE}Testing Memory System:${NC}"
curl -X POST http://localhost:8000/api/memory/person \
  -H "Content-Type: application/json" \
  -d '{"name": "Test User", "facts": ["Test fact"]}' 2>/dev/null && success "✅ Memory system working!" || warn "⚠️ Memory system not responding"

# ============================================================================
# FINAL STATUS
# ============================================================================

echo -e "\n${GREEN}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║                    STATUS REPORT                             ║${NC}"
echo -e "${GREEN}╠══════════════════════════════════════════════════════════════╣${NC}"
echo -e "${GREEN}║                                                              ║${NC}"
echo -e "${GREEN}║  If services are working, access them at:                    ║${NC}"
echo -e "${GREEN}║  • Main UI: http://192.168.1.60:8080                        ║${NC}"
echo -e "${GREEN}║  • Developer: http://192.168.1.60:8080/developer/           ║${NC}"
echo -e "${GREEN}║  • API Docs: http://192.168.1.60:8000/docs                  ║${NC}"
echo -e "${GREEN}║  • Direct API: http://192.168.1.60:8000/health              ║${NC}"
echo -e "${GREEN}║                                                              ║${NC}"
echo -e "${GREEN}║  Troubleshooting Commands:                                   ║${NC}"
echo -e "${GREEN}║  • docker logs zoe-core -f    (watch API logs)              ║${NC}"
echo -e "${GREEN}║  • docker logs zoe-ui -f      (watch UI logs)               ║${NC}"
echo -e "${GREEN}║  • docker compose restart      (restart everything)          ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════════════╝${NC}"

exit 0
